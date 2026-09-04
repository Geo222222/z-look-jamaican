"""Evidence-first read model for the ZLJ shadow intelligence cockpit.

This module never originates market facts, predictions, expert qualification,
capital decisions, risk authority, or execution. It projects durable ZLJ state
into an operator-friendly shape and fails toward UNAVAILABLE/COLLECTING when
supporting evidence is absent or invalid.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..context.contracts import MarketContextContractError, MarketContextFrame
from ..evaluation.question_journal import QuestionOutcomeJournal, validate_question_outcome_journal
from ..evaluation.question_outcome import QuestionBoundOutcome, QuestionOutcomeError
from ..models.question_experts import QuestionExpertError, QuestionExpertRegistrySnapshot
from ..prediction.question_bound import QuestionBoundPrediction, QuestionPredictionError
from ..prediction.question_journal import QuestionPredictionJournal, validate_question_prediction_journal
from ..questions.catalog import question_catalog_v1
from ..questions.evolution import REVERSAL_ROOT_PATH_RESOLVER_IMPLEMENTATION_REF, reversal_question_v1_1
from ..questions.readiness import RESOLVER_READY_IMPLEMENTATIONS_V1
from ..representation.contracts import RepresentationContractError, RepresentationFrame


SHADOW_INTELLIGENCE_SCHEMA_VERSION = "1.0"


def _json(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _safe_path(root: Path, relative: object) -> Optional[Path]:
    candidate = (root / str(relative or "")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _decimal(value: object) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _fmt(value: Optional[Decimal], suffix: str = "") -> Optional[str]:
    if value is None:
        return None
    return "%s%s" % (format(value.normalize(), "f"), suffix)


def _count_jsonl(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def _evidence(artifact_type: str, artifact_id: str, content_hash: str, *, role: str = "SOURCE") -> Dict[str, Any]:
    return {
        "artifact_type": str(artifact_type),
        "artifact_id": str(artifact_id),
        "content_hash": str(content_hash),
        "role": str(role),
    }


def _unavailable(label: str, reason: str) -> Dict[str, Any]:
    return {
        "label": label,
        "statement": "Unavailable",
        "status": "UNAVAILABLE",
        "reason": reason,
        "evidence": [],
    }


def _story(label: str, statement: str, evidence: Sequence[Mapping[str, Any]], *, status: str = "OBSERVED", reason: str = "") -> Dict[str, Any]:
    return {
        "label": label,
        "statement": statement,
        "status": status,
        "reason": reason,
        "evidence": [dict(item) for item in evidence],
    }


def _latest_market_context(root: Path) -> Tuple[Optional[MarketContextFrame], Optional[Dict[str, Any]], Optional[str]]:
    index = _json(root / "state/market_context.json")
    items = index.get("items")
    if not isinstance(items, list) or not items:
        return None, None, "no durable Market Context frame is available"
    valid_items = [item for item in items if isinstance(item, Mapping)]
    valid_items.sort(key=lambda item: (int(item.get("cutoff_at_ns", -1)), int(item.get("known_at_ns", -1)), str(item.get("context_id", ""))))
    for item in reversed(valid_items):
        path = _safe_path(root, item.get("path"))
        if path is None or not path.is_file():
            continue
        document = _json(path)
        try:
            context = MarketContextFrame.from_wire(document.get("context", {}))
        except (MarketContextContractError, ValueError, TypeError):
            continue
        if str(item.get("context_content_hash", "")) != context.content_hash():
            continue
        return context, _evidence("MARKET_CONTEXT", context.context_id, context.content_hash()), None
    return None, None, "Market Context index exists but no valid referenced context could be loaded"


def _latest_derivative_frames(root: Path) -> Dict[str, RepresentationFrame]:
    index = _json(root / "state/representations.json")
    items = index.get("items")
    if not isinstance(items, list):
        return {}
    output: Dict[str, RepresentationFrame] = {}
    for item in items:
        if not isinstance(item, Mapping) or item.get("representation_type") != "DERIVATIVE_STATE":
            continue
        path = _safe_path(root, item.get("path"))
        if path is None or not path.is_file():
            continue
        document = _json(path)
        try:
            frame = RepresentationFrame.from_wire(document.get("frame", {}))
        except (RepresentationContractError, ValueError, TypeError):
            continue
        if frame.content_hash() != str(item.get("frame_content_hash", "")):
            continue
        current = output.get(frame.instrument.base_asset)
        if current is None or (frame.cutoff_at_ns, frame.known_at_ns, frame.frame_id) > (current.cutoff_at_ns, current.known_at_ns, current.frame_id):
            output[frame.instrument.base_asset] = frame
    return output


def _spot_members(context: MarketContextFrame, base_asset: str) -> Tuple[Tuple[str, Mapping[str, Any]], ...]:
    members = context.state.get("members")
    if not isinstance(members, Mapping):
        return ()
    result = []
    for instrument_id, raw in members.items():
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("market_type")) == "SPOT" and str(raw.get("base_asset")) == base_asset:
            result.append((str(instrument_id), raw))
    return tuple(sorted(result, key=lambda item: item[0]))


def _asset_story(context: Optional[MarketContextFrame], context_evidence: Optional[Mapping[str, Any]], derivatives: Mapping[str, RepresentationFrame], base_asset: str) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    evidence = () if context_evidence is None else (context_evidence,)
    if context is None:
        rows.append(_unavailable("Spot flow", "qualified Market Context is unavailable"))
        rows.append(_unavailable("Spot return", "qualified Market Context is unavailable"))
    else:
        spots = _spot_members(context, base_asset)
        if len(spots) != 1:
            rows.append(_unavailable("Spot flow", "expected one unambiguous spot expression for %s; found %d" % (base_asset, len(spots))))
            rows.append(_unavailable("Spot return", "expected one unambiguous spot expression for %s; found %d" % (base_asset, len(spots))))
        else:
            instrument_id, member = spots[0]
            flow = _decimal(member.get("reported_flow_ratio"))
            if flow is None:
                rows.append(_unavailable("Spot flow", "reported-flow ratio is unavailable in current Market Context"))
            else:
                direction = "buy-side reported flow stronger" if flow > 0 else "sell-side reported flow stronger" if flow < 0 else "reported buy/sell flow balanced"
                rows.append(_story(
                    "Spot flow",
                    "%s (%s)" % (direction, _fmt(flow)),
                    evidence,
                    reason="provider-reported side semantics; not an aggressor-side or causal demand claim",
                ))
            latest_return = _decimal(member.get("latest_return_bps"))
            if latest_return is None:
                rows.append(_unavailable("Spot return", "latest point-in-time return is unavailable"))
            else:
                rows.append(_story("Spot return", "%s over current context history" % (_fmt(latest_return, " bps")), evidence))

        derivative_state = derivatives.get(base_asset)
        relationships = context.state.get("derivatives")
        relationship_items = relationships.get("relationships") if isinstance(relationships, Mapping) else None
        matching = []
        if isinstance(relationship_items, list):
            member_ids = {item[0] for item in _spot_members(context, base_asset)}
            for item in relationship_items:
                if isinstance(item, Mapping) and str(item.get("spot_instrument_id")) in member_ids:
                    matching.append(item)
        if len(matching) == 1:
            basis = _decimal(matching[0].get("basis_bps"))
            if basis is not None:
                label = "premium" if basis > 0 else "discount" if basis < 0 else "flat basis"
                rows.append(_story("Futures basis", "%s %s" % (label, _fmt(abs(basis), " bps")), evidence, reason="current basis only; widening/narrowing requires temporal relationship evidence"))
            else:
                rows.append(_unavailable("Futures basis", "current relationship has no valid basis value"))
        elif matching:
            rows.append(_unavailable("Futures basis", "multiple spot-derivative relationships exist; no single relationship was selected"))
        else:
            rows.append(_unavailable("Futures basis", "no qualified spot-derivative relationship exists in current Market Context"))

        if derivative_state is None:
            rows.extend([
                _unavailable("Funding", "no durable qualified derivative-state representation is available"),
                _unavailable("Open interest", "no durable qualified derivative-state representation is available"),
                _unavailable("Derivative liquidity", "no qualified derivative-liquidity trajectory is available"),
            ])
        else:
            derivative_ev = (_evidence("REPRESENTATION_FRAME", derivative_state.frame_id, derivative_state.content_hash()),)
            funding = derivative_state.state.get("funding")
            funding_value = _decimal(funding.get("value")) if isinstance(funding, Mapping) else None
            if funding_value is None:
                rows.append(_unavailable("Funding", "funding is unavailable in the latest derivative-state representation"))
            else:
                rows.append(_story("Funding", "current provider-native funding rate %s" % _fmt(funding_value), derivative_ev, reason="elevated/normal requires a qualified contextual baseline"))
            oi = derivative_state.state.get("open_interest")
            oi_value = _decimal(oi.get("value")) if isinstance(oi, Mapping) else None
            if oi_value is None:
                rows.append(_unavailable("Open interest", "open interest is unavailable in the latest derivative-state representation"))
            else:
                rows.append(_story("Open interest", "current provider-native OI %s" % _fmt(oi_value), derivative_ev, reason="provider-native unit; accelerating requires compatible temporal history"))
            rows.append(_unavailable("Derivative liquidity", "current v1 derivative-state frame does not prove a derivative-liquidity trajectory"))

    return {"subject_id": "ASSET.%s" % base_asset, "label": base_asset, "rows": rows}


def _market_story(context: Optional[MarketContextFrame], context_evidence: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if context is None:
        return {"label": "MARKET", "rows": [
            _unavailable("Breadth", "Market Context unavailable"),
            _unavailable("Correlation", "Market Context unavailable"),
            _unavailable("Volatility", "Market Context unavailable"),
            _unavailable("Leadership", "Market Context unavailable"),
        ]}
    ev = () if context_evidence is None else (context_evidence,)
    market = context.state.get("market") if isinstance(context.state.get("market"), Mapping) else {}
    members = context.state.get("members") if isinstance(context.state.get("members"), Mapping) else {}
    rows = []
    breadth = _decimal(market.get("breadth_positive"))
    rows.append(_story("Breadth", "%s of represented return-bearing instruments positive" % _fmt(breadth), ev) if breadth is not None else _unavailable("Breadth", "breadth unavailable"))
    corr = _decimal(market.get("median_absolute_pairwise_correlation"))
    rows.append(_story("Correlation", "median absolute pairwise correlation %s" % _fmt(corr), ev, reason="current level only; increasing/decreasing requires market-wide temporal experience") if corr is not None else _unavailable("Correlation", "pairwise correlation unavailable"))
    vol = _decimal(market.get("median_realized_volatility_bps"))
    rows.append(_story("Volatility", "median realized volatility %s" % _fmt(vol, " bps"), ev, reason="current level only; expanding/contracting requires market-wide temporal experience") if vol is not None else _unavailable("Volatility", "realized volatility unavailable"))
    ranked = []
    for instrument_id, member in members.items():
        if not isinstance(member, Mapping):
            continue
        value = _decimal(member.get("latest_return_bps"))
        if value is not None:
            ranked.append((value, str(instrument_id)))
    if ranked:
        leader = max(ranked, key=lambda item: (item[0], item[1]))
        rows.append(_story("Leadership", "%s leads current represented cross-section (%s)" % (leader[1], _fmt(leader[0], " bps")), ev, reason="point-in-time leadership; concentration trend requires market-wide temporal experience"))
    else:
        rows.append(_unavailable("Leadership", "no member return history is available"))
    return {"label": "MARKET", "rows": rows}


def _predictions(root: Path) -> Tuple[List[QuestionBoundPrediction], List[str], List[Mapping[str, Any]]]:
    errors = validate_question_prediction_journal(root)
    if errors:
        return [], list(errors), []
    output: List[QuestionBoundPrediction] = []
    raw_entries: List[Mapping[str, Any]] = []
    try:
        entries = QuestionPredictionJournal(root).entries()
    except Exception as exc:
        return [], [str(exc)], []
    for entry in entries:
        try:
            prediction = QuestionBoundPrediction.from_wire(entry.get("prediction", {}))
        except (QuestionPredictionError, ValueError, TypeError) as exc:
            return [], ["invalid prediction entry: %s" % exc], []
        output.append(prediction)
        raw_entries.append(entry)
    return output, [], raw_entries


def _outcomes(root: Path) -> Tuple[List[QuestionBoundOutcome], List[str], List[Mapping[str, Any]]]:
    errors = validate_question_outcome_journal(root)
    if errors:
        return [], list(errors), []
    output: List[QuestionBoundOutcome] = []
    raw_entries: List[Mapping[str, Any]] = []
    try:
        entries = QuestionOutcomeJournal(root).entries()
    except Exception as exc:
        return [], [str(exc)], []
    for entry in entries:
        try:
            outcome = QuestionBoundOutcome.from_wire(entry.get("outcome", {}))
        except (QuestionOutcomeError, ValueError, TypeError) as exc:
            return [], ["invalid outcome entry: %s" % exc], []
        output.append(outcome)
        raw_entries.append(entry)
    return output, [], raw_entries


def _question_definitions() -> List[Dict[str, Any]]:
    definitions = [item for item in question_catalog_v1() if item.question_id != "ECONOMIC_ROOT_REVERSAL_60S"]
    definitions.append(reversal_question_v1_1())
    resolver_by_id = dict(RESOLVER_READY_IMPLEMENTATIONS_V1)
    resolver_by_id["ECONOMIC_ROOT_REVERSAL_60S"] = REVERSAL_ROOT_PATH_RESOLVER_IMPLEMENTATION_REF
    result = []
    for question in definitions:
        result.append({
            "question_ref": question.question_ref,
            "question_definition_hash": question.content_hash(),
            "question_id": question.question_id,
            "family": question.family.value,
            "scope": question.scope.value,
            "asks": question.asks,
            "horizon_ns": int(question.horizon_ns),
            "answer_kind": question.outcome.answer_kind.value,
            "outcome_metric_id": question.outcome.metric_id,
            "resolver_policy_id": question.outcome.resolver_policy_id,
            "resolver_implementation_ref": resolver_by_id.get(question.question_id),
            "lifecycle": "RESOLVER_READY" if question.question_id in resolver_by_id else "DEFINED",
        })
    return sorted(result, key=lambda item: (item["family"], item["question_ref"]))


def _active_questions(predictions: Sequence[QuestionBoundPrediction], outcomes: Sequence[QuestionBoundOutcome], entries: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    outcome_by_prediction = {item.prediction_id: item for item in outcomes}
    entry_hash = {}
    for entry in entries:
        prediction = entry.get("prediction")
        if isinstance(prediction, Mapping):
            entry_hash[str(prediction.get("prediction_id", ""))] = str(entry.get("entry_hash", ""))
    rows = []
    for prediction in sorted(predictions, key=lambda item: (item.created_at_ns, item.prediction_id), reverse=True):
        outcome = outcome_by_prediction.get(prediction.prediction_id)
        rows.append({
            "prediction_id": prediction.prediction_id,
            "prediction_content_hash": prediction.content_hash(),
            "prediction_journal_entry_hash": entry_hash.get(prediction.prediction_id),
            "question_ref": prediction.question_ref,
            "subject_id": prediction.subject_id,
            "cutoff_at_ns": prediction.cutoff_at_ns,
            "created_at_ns": prediction.created_at_ns,
            "resolves_at_ns": prediction.resolves_at_ns,
            "answer": dict(prediction.answer),
            "model_refs": list(prediction.model_refs),
            "status": "RESOLVED" if outcome is not None and outcome.status == "RESOLVED" else "UNRESOLVABLE" if outcome is not None else "AWAITING_OUTCOME",
            "outcome_id": None if outcome is None else outcome.outcome_id,
            "realized_answer": None if outcome is None else outcome.realized_answer,
            "resolver_implementation_ref": None if outcome is None else outcome.resolver_implementation_ref,
        })
    return rows[:50]


def _optional_expert_registry(root: Path) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    path = root / "state/question_expert_registry.json"
    if not path.is_file():
        return [], "no durable question-expert registry snapshot has been activated"
    document = _json(path)
    raw = document.get("registry") if isinstance(document.get("registry"), Mapping) else document
    try:
        registry = QuestionExpertRegistrySnapshot.from_wire(raw)
    except (QuestionExpertError, ValueError, TypeError) as exc:
        return [], "durable question-expert registry is invalid: %s" % exc
    rows = []
    for entry in registry.entries:
        definition = entry.definition
        rows.append({
            "expert_ref": definition.definition_ref,
            "expert_id": definition.expert_id,
            "family": definition.family,
            "lifecycle": entry.lifecycle_state,
            "question_refs": [binding.question_ref for binding in definition.question_bindings],
            "supported_subject_ids": list(definition.supported_subject_ids),
            "qualification_evidence_refs": list(entry.qualification_evidence_refs),
            "competence": {"status": "COLLECTING", "metric": None, "value": None, "sample_count": 0},
        })
    return rows, None


def _expert_rows(root: Path, predictions: Sequence[QuestionBoundPrediction], outcomes: Sequence[QuestionBoundOutcome]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    registered, registry_reason = _optional_expert_registry(root)
    if registered:
        # Competence remains unset until a question-bound evaluator is qualified.
        outcome_ids = {item.prediction_id for item in outcomes if item.status == "RESOLVED"}
        prediction_by_model: Dict[str, List[QuestionBoundPrediction]] = defaultdict(list)
        for prediction in predictions:
            for ref in prediction.model_refs:
                prediction_by_model[str(ref)].append(prediction)
        for row in registered:
            candidates = prediction_by_model.get(str(row["expert_ref"]), []) + prediction_by_model.get(str(row["expert_id"]), [])
            row["competence"]["sample_count"] = sum(1 for item in candidates if item.prediction_id in outcome_ids)
        return registered, registry_reason

    # Journaled model refs are shown as observed participants but are not silently
    # promoted into the question-expert registry or assigned competence.
    resolved_ids = {item.prediction_id for item in outcomes if item.status == "RESOLVED"}
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: {"predictions": 0, "resolved": 0})
    for prediction in predictions:
        for ref in prediction.model_refs:
            key = str(ref)
            counts[key]["predictions"] += 1
            if prediction.prediction_id in resolved_ids:
                counts[key]["resolved"] += 1
    rows = []
    for ref, count in sorted(counts.items()):
        rows.append({
            "expert_ref": ref,
            "expert_id": ref,
            "family": "UNREGISTERED",
            "lifecycle": "JOURNALED_MODEL_REF_ONLY",
            "question_refs": [],
            "supported_subject_ids": [],
            "qualification_evidence_refs": [],
            "prediction_count": count["predictions"],
            "competence": {"status": "COLLECTING", "metric": None, "value": None, "sample_count": count["resolved"]},
        })
    return rows, registry_reason


def build_shadow_intelligence_snapshot(root: Path) -> Dict[str, Any]:
    root = root.resolve()
    context, context_evidence, context_error = _latest_market_context(root)
    derivative_frames = _latest_derivative_frames(root)
    predictions, prediction_errors, prediction_entries = _predictions(root)
    outcomes, outcome_errors, outcome_entries = _outcomes(root)
    experts, expert_registry_reason = _expert_rows(root, predictions, outcomes)
    eligible_experiences = _count_jsonl(root / "memory/market_experiences.jsonl")
    resolved_count = sum(1 for item in outcomes if item.status == "RESOLVED")
    unresolvable_count = sum(1 for item in outcomes if item.status == "UNRESOLVABLE")

    return {
        "contract": {
            "name": "zlj-shadow-intelligence",
            "schema_version": SHADOW_INTELLIGENCE_SCHEMA_VERSION,
            "generated_at_ns": time.time_ns(),
            "mode": "PROSPECTIVE_SHADOW",
            "read_only": True,
            "capital_authority": False,
            "risk_authority": False,
            "execution_authority": False,
            "truth_policy": "SHOW_ONLY_DURABLE_EVIDENCE; ABSENCE_OR_INVALIDITY_SURFACES_AS_UNAVAILABLE_OR_COLLECTING",
        },
        "market_story": {
            "subjects": [
                _asset_story(context, context_evidence, derivative_frames, "BTC"),
                _asset_story(context, context_evidence, derivative_frames, "ETH"),
            ],
            "market": _market_story(context, context_evidence),
            "context_status": "AVAILABLE" if context is not None else "UNAVAILABLE",
            "context_reason": context_error,
        },
        "historical_context": {
            "status": "NOT_QUALIFIED",
            "comparable_experiences": None,
            "eligible_experience_records": eligible_experiences,
            "similarity_policy": None,
            "reason": "a versioned comparable-experience similarity policy has not yet been qualified",
        },
        "questions": {
            "registry": _question_definitions(),
            "active": _active_questions(predictions, outcomes, prediction_entries),
        },
        "experts": {
            "status": "AVAILABLE" if experts else "COLLECTING",
            "registry_reason": expert_registry_reason,
            "items": experts,
            "competence_policy": "NO_PERCENTAGE_UNTIL_QUESTION_BOUND_EVALUATION_METRIC_AND_SAMPLE_EVIDENCE_ARE_QUALIFIED",
        },
        "learning": {
            "prediction_journal_status": "VALID" if not prediction_errors else "INVALID",
            "prediction_journal_errors": prediction_errors,
            "prediction_count": len(predictions),
            "outcome_journal_status": "VALID" if not outcome_errors else "INVALID",
            "outcome_journal_errors": outcome_errors,
            "outcome_count": len(outcomes),
            "resolved_outcome_count": resolved_count,
            "unresolvable_outcome_count": unresolvable_count,
            "awaiting_outcome_count": max(0, len(predictions) - len(outcomes)),
        },
        "evidence": {
            "latest_market_context": context_evidence,
            "prediction_journal_last_entry_hash": None if not prediction_entries else prediction_entries[-1].get("entry_hash"),
            "outcome_journal_last_entry_hash": None if not outcome_entries else outcome_entries[-1].get("entry_hash"),
        },
    }
