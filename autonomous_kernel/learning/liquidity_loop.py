"""Bounded Liquidity 30s predict → resolve → score → competence loop.

This module does not train models, allocate capital, or change resolver truth.
It reuses QuestionPredictionJournal, QuestionOutcomeJournal, resolve_liquidity_question,
and sync_expert_learning. Prospective QUALIFIED experience remains blocked while live
Z9 is DEGRADED; predictions are HISTORICAL_REPLAY / RESEARCH_ONLY unless experience
is QUALIFIED.

A reported-side trade-flow Liquidity expert is deferred: provider side is
PROVIDER_REPORTED_SIDE_NOT_AGGRESSOR_INFERENCE.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..evaluation.liquidity_resolver import liquidity_cutoff_is_examinable, resolve_liquidity_question
from ..evaluation.question_journal import QuestionOutcomeJournal
from ..evaluation.question_resolvers import QuestionOutcomePendingError, QuestionResolverError
from ..experience.contracts import ExperienceTimescale
from ..experience.store import MarketExperienceStore
from ..experts.sync import sync_expert_learning
from ..intelligence.runtime import IntelligenceRuntime
from ..models.liquidity_baselines import liquidity_baseline_model_set
from ..prediction.question_bound import PredictionArtifactRef, QuestionBoundPrediction, build_question_bound_prediction
from ..prediction.question_journal import QuestionPredictionJournal
from ..questions.catalog import question_catalog_v1
from ..representation.contracts import RepresentationFrame
from .direction_loop import (
    FORBIDDEN_FIELDS,
    INSTRUMENT_ID,
    LAG_NS,
    MICRO_LOOKBACK_NS,
    SUBJECT_ID,
    _assert_no_economic_fields,
    _binary_answer,
    _experience_baseline_frame,
    _experience_for_cutoff,
    _observation_bounds,
    frozen_direction_registry,
    materialize_cutoff_frame,
    materialize_forward_frame,
)


LIQUIDITY_QUESTION_REF = "ECONOMIC_ROOT_LIQUIDITY_DETERIORATION_30S@1.0.0"
HORIZON_NS = 30_000_000_000


class LiquidityLoopError(RuntimeError):
    pass


def liquidity_question():
    matches = [item for item in question_catalog_v1() if item.question_ref == LIQUIDITY_QUESTION_REF]
    if len(matches) != 1:
        raise LiquidityLoopError("frozen Liquidity question is missing")
    return matches[0]


def _cutoffs(start_ns: int, end_ns: int) -> Tuple[int, ...]:
    first = start_ns + MICRO_LOOKBACK_NS
    last_forward = end_ns - LAG_NS
    values: List[int] = []
    cutoff = first
    while cutoff + HORIZON_NS <= last_forward:
        values.append(cutoff)
        cutoff += HORIZON_NS
    return tuple(values)


def liquidity_baseline_is_eligible(frame: RepresentationFrame) -> bool:
    if frame.instrument.canonical_id != INSTRUMENT_ID:
        return False
    return liquidity_cutoff_is_examinable(frame)


def _prediction_summary(entry: Mapping[str, Any], *, frame: Optional[RepresentationFrame] = None, experience=None) -> Dict[str, Any]:
    payload = entry.get("prediction") or {}
    timing = payload.get("timing") or {}
    refs = payload.get("artifact_refs") or []
    experience_id = None
    experience_status = None
    for ref in refs:
        if isinstance(ref, Mapping) and ref.get("artifact_type") == "MARKET_EXPERIENCE":
            experience_id = ref.get("artifact_id")
            experience_status = ref.get("status")
            break
    return {
        "prediction_id": payload.get("prediction_id"),
        "model_ref": (payload.get("model_refs") or [None])[0],
        "cutoff_at_ns": timing.get("cutoff_at_ns"),
        "created_at_ns": timing.get("created_at_ns"),
        "journaled_at_ns": entry.get("journaled_at_ns"),
        "resolves_at_ns": timing.get("resolves_at_ns"),
        "mode": payload.get("mode"),
        "content_hash": (payload.get("integrity") or {}).get("content_hash"),
        "experience_id": experience.experience_id if experience is not None else experience_id,
        "experience_status": experience.status if experience is not None else experience_status,
        "context_id": None if experience is None else experience.context_id,
        "context_hash": None if experience is None else experience.context_hash,
        "frame_id": None if frame is None else frame.frame_id,
        "frame_hash": None if frame is None else frame.content_hash(),
    }


def record_liquidity_predictions(root: Path, *, batch_id: str, frame: RepresentationFrame, experience) -> List[Mapping[str, Any]]:
    del batch_id
    if not liquidity_baseline_is_eligible(frame):
        return []
    registry = frozen_direction_registry()
    question = liquidity_question()
    status = "DEGRADED" if experience.status != "QUALIFIED" else "QUALIFIED"
    mode = "PROSPECTIVE_SHADOW" if status == "QUALIFIED" else "HISTORICAL_REPLAY"
    evidence_class = "FORWARD_EVALUABLE" if mode == "PROSPECTIVE_SHADOW" else "RESEARCH_ONLY"
    artifact_ref = PredictionArtifactRef(
        artifact_type="MARKET_EXPERIENCE",
        artifact_id=experience.experience_id,
        content_hash=experience.content_hash(),
        known_at_ns=int(experience.known_at_ns),
        status=status,
        timescales=(ExperienceTimescale.MICRO,),
        feature_families=("SPOT_MICROSTRUCTURE",),
        subject_ids=(SUBJECT_ID,),
    )
    created_at = max(int(frame.known_at_ns), int(experience.known_at_ns), int(frame.cutoff_at_ns))
    journal = QuestionPredictionJournal(root)
    recorded: List[Mapping[str, Any]] = []
    existing = set()
    for entry in journal.entries():
        payload = entry.get("prediction") or {}
        question_ref = (payload.get("question") or {}).get("question_ref")
        if question_ref != LIQUIDITY_QUESTION_REF:
            continue
        cutoff = int((payload.get("timing") or {}).get("cutoff_at_ns") or -1)
        for model_ref in payload.get("model_refs") or []:
            existing.add((cutoff, str(model_ref)))
    for model in liquidity_baseline_model_set():
        if (int(frame.cutoff_at_ns), model.definition.model_ref) in existing:
            match = next(
                entry
                for entry in journal.entries()
                if (entry.get("prediction") or {}).get("prediction_id")
                and int(((entry.get("prediction") or {}).get("timing") or {}).get("cutoff_at_ns") or -1) == int(frame.cutoff_at_ns)
                and model.definition.model_ref in ((entry.get("prediction") or {}).get("model_refs") or [])
                and ((entry.get("prediction") or {}).get("question") or {}).get("question_ref") == LIQUIDITY_QUESTION_REF
            )
            recorded.append(_prediction_summary(match, frame=frame, experience=experience))
            continue
        probability, decomposition = model.forecast_liquidity(frame)
        del decomposition
        prediction = build_question_bound_prediction(
            registry=registry,
            question=question,
            subject_id=SUBJECT_ID,
            mode=mode,
            evidence_class=evidence_class,
            cutoff_at_ns=int(frame.cutoff_at_ns),
            created_at_ns=created_at,
            answer=_binary_answer(probability),
            model_refs=(model.definition.model_ref,),
            artifact_refs=(artifact_ref,),
        )
        _assert_no_economic_fields(prediction.to_wire())
        if prediction.created_at_ns > prediction.cutoff_at_ns and prediction.created_at_ns > int(frame.known_at_ns):
            if prediction.created_at_ns >= prediction.resolves_at_ns:
                raise LiquidityLoopError("prediction created after the outcome becomes knowable")
        entry = journal.append(prediction, journaled_at_ns=int(frame.cutoff_at_ns) + 1)
        recorded.append(
            {
                "prediction_id": prediction.prediction_id,
                "model_ref": model.definition.model_ref,
                "cutoff_at_ns": prediction.cutoff_at_ns,
                "created_at_ns": prediction.created_at_ns,
                "journaled_at_ns": entry["journaled_at_ns"],
                "resolves_at_ns": prediction.resolves_at_ns,
                "mode": prediction.mode,
                "content_hash": prediction.content_hash(),
                "experience_id": experience.experience_id,
                "experience_status": experience.status,
                "context_id": experience.context_id,
                "context_hash": experience.context_hash,
                "frame_id": frame.frame_id,
                "frame_hash": frame.content_hash(),
            }
        )
    return recorded


def resolve_liquidity_prediction(
    root: Path,
    *,
    batch_id: str,
    prediction_id: str,
    baseline_frame: RepresentationFrame,
    experience,
    now_at_ns: int,
) -> Mapping[str, Any]:
    prediction = None
    for entry in QuestionPredictionJournal(root).entries():
        if entry.get("prediction", {}).get("prediction_id") == prediction_id:
            prediction = QuestionBoundPrediction.from_wire(entry["prediction"])
            break
    if prediction is None:
        raise LiquidityLoopError("prediction is not in the question journal")
    if now_at_ns < prediction.resolves_at_ns:
        return {"status": "PENDING", "prediction_id": prediction_id, "error": "horizon has not matured"}
    lineage_baseline = _experience_baseline_frame(root, experience)
    if (
        baseline_frame.frame_id != lineage_baseline.frame_id
        or baseline_frame.content_hash() != lineage_baseline.content_hash()
    ):
        raise LiquidityLoopError("supplied baseline frame diverges from experience lineage")
    if lineage_baseline.status != "QUALIFIED":
        raise LiquidityLoopError("Liquidity resolver requires a QUALIFIED baseline instrument state")
    forward = materialize_forward_frame(root, batch_id, prediction.resolves_at_ns)
    if forward is not None and forward.known_at_ns <= prediction.cutoff_at_ns:
        raise LiquidityLoopError("forward frame is not strictly later than prediction cutoff")
    frames = tuple(item for item in (forward,) if item is not None)
    try:
        outcome = resolve_liquidity_question(
            root,
            prediction_id,
            baseline_experience=experience,
            baseline_frames=(lineage_baseline,),
            forward_frames=frames,
            now_at_ns=int(now_at_ns),
        )
    except QuestionOutcomePendingError as exc:
        return {"status": "PENDING", "prediction_id": prediction_id, "error": str(exc)}
    except QuestionResolverError:
        raise
    _assert_no_economic_fields(outcome.to_wire())
    QuestionOutcomeJournal(root).append(outcome)
    return {
        "status": outcome.status,
        "prediction_id": prediction_id,
        "outcome_id": outcome.outcome_id,
        "decided_at_ns": outcome.decided_at_ns,
        "realized_answer": outcome.realized_answer,
        "content_hash": outcome.content_hash(),
        "forward_frame_id": None if forward is None else forward.frame_id,
        "forward_frame_hash": None if forward is None else forward.content_hash(),
    }


def _sync_liquidity(root: Path, known_at_ns: int) -> Mapping[str, Any]:
    sync_result = dict(sync_expert_learning(root, known_at_ns=int(known_at_ns)))
    competence = IntelligenceRuntime(root).state().get("competence")
    if isinstance(competence, Mapping):
        _assert_no_economic_fields(competence)
        sync_result["competence"] = competence
    from .liquidity_assembly import LiquidityAssemblyError, assemble_and_record_liquidity_question

    try:
        sync_result["liquidity_assembly"] = assemble_and_record_liquidity_question(root, known_at_ns=int(known_at_ns))
    except LiquidityAssemblyError as exc:
        sync_result["liquidity_assembly"] = {"status": "BLOCKED", "error": str(exc)}
        return sync_result
    from ..synthesis.contracts import MarketSynthesisError
    from ..synthesis.service import synthesize_and_record

    try:
        sync_result["market_synthesis"] = synthesize_and_record(root, known_at_ns=int(known_at_ns))
    except MarketSynthesisError as exc:
        sync_result["market_synthesis"] = {"status": "BLOCKED", "error": str(exc)}
    return sync_result


def process_canonical_liquidity_batch(root: Path, batch_id: str, *, sync: bool = True) -> Mapping[str, Any]:
    root = Path(root).resolve()
    start_ns, end_ns = _observation_bounds(root, batch_id)
    cutoffs = _cutoffs(start_ns, end_ns)
    if not cutoffs:
        raise LiquidityLoopError("canonical batch is too short for a Liquidity 30s independent cutoff")
    predictions: List[Mapping[str, Any]] = []
    experiences: Dict[int, Tuple[Any, Any]] = {}
    frames: Dict[int, RepresentationFrame] = {}
    skipped_invalid_cutoffs: List[Mapping[str, Any]] = []
    for cutoff in cutoffs:
        frame = materialize_cutoff_frame(root, batch_id, cutoff)
        if not liquidity_baseline_is_eligible(frame):
            reason = "UNQUALIFIED_OR_ILLEGAL_BASELINE"
            if frame.instrument.canonical_id != INSTRUMENT_ID:
                reason = "WRONG_INSTRUMENT"
            elif frame.status != "QUALIFIED":
                reason = "UNQUALIFIED_FRAME"
            elif frame.representation_type != "INSTRUMENT_STATE":
                reason = "WRONG_REPRESENTATION_TYPE"
            else:
                reason = "ILLEGAL_LIQUIDITY_BASELINE"
            skipped_invalid_cutoffs.append({"cutoff_at_ns": int(cutoff), "reason": reason})
            continue
        experience, context = _experience_for_cutoff(root, frame)
        frames[cutoff] = frame
        experiences[cutoff] = (experience, context)
        predictions.extend(record_liquidity_predictions(root, batch_id=batch_id, frame=frame, experience=experience))
    if not predictions:
        raise LiquidityLoopError("canonical batch has no legal Liquidity 30s cutoff that can be examined")
    outcomes: List[Mapping[str, Any]] = []
    store = MarketExperienceStore(root)
    for item in predictions:
        cutoff = int(item["cutoff_at_ns"])
        experience = store.load(str(item["experience_id"]))
        outcomes.append(
            resolve_liquidity_prediction(
                root,
                batch_id=batch_id,
                prediction_id=str(item["prediction_id"]),
                baseline_frame=frames[cutoff],
                experience=experience,
                now_at_ns=end_ns,
            )
        )
    sync_result: Optional[Mapping[str, Any]] = None
    if sync:
        sync_result = _sync_liquidity(root, end_ns)
    counts = {
        "predicted": len(predictions),
        "resolved": sum(1 for item in outcomes if item.get("status") == "RESOLVED"),
        "unresolvable": sum(1 for item in outcomes if item.get("status") == "UNRESOLVABLE"),
        "pending": sum(1 for item in outcomes if item.get("status") == "PENDING"),
        "skipped_invalid_cutoffs": len(skipped_invalid_cutoffs),
    }
    latest_cutoff = max(experiences) if experiences else None
    latest_context = experiences[latest_cutoff][1] if latest_cutoff is not None else None
    contextual_status = "INSUFFICIENT_CONTEXTUAL_SUPPORT"
    if latest_context is not None and str(getattr(latest_context, "status", "")) == "QUALIFIED":
        contextual_status = "QUALIFIED_CONTEXT_AVAILABLE"
    return {
        "status": "OK",
        "batch_id": batch_id,
        "question_ref": LIQUIDITY_QUESTION_REF,
        "horizon_ns": HORIZON_NS,
        "max_resolution_lag_ns": LAG_NS,
        "instrument_id": INSTRUMENT_ID,
        "subject_id": SUBJECT_ID,
        "observation_start_ns": start_ns,
        "observation_end_ns": end_ns,
        "cutoffs": list(cutoffs),
        "skipped_invalid_cutoffs": skipped_invalid_cutoffs,
        "predictions": predictions,
        "outcomes": outcomes,
        "counts": counts,
        "sync": sync_result,
        "deferred_experts": (
            {
                "model_id": "REPORTED-FLOW-LIQUIDITY",
                "status": "DEFERRED",
                "reason": "PROVIDER_REPORTED_SIDE_NOT_AGGRESSOR_INFERENCE",
            },
        ),
        "contextual_competence_status": contextual_status,
        "prospective_shadow_blocked_reason": None
        if any(item.get("mode") == "PROSPECTIVE_SHADOW" for item in predictions)
        else "Z9_OR_EXPERIENCE_NOT_QUALIFIED",
        "authority": {
            "capital_allocation": False,
            "economic_decision": False,
            "risk_authorization": False,
            "external_execution": False,
        },
    }


def process_canonical_liquidity_batches(root: Path, batch_ids: Sequence[str], *, sync: bool = True) -> Mapping[str, Any]:
    combined: Dict[str, Any] = {
        "status": "OK",
        "question_ref": LIQUIDITY_QUESTION_REF,
        "horizon_ns": HORIZON_NS,
        "instrument_id": INSTRUMENT_ID,
        "batches": [],
        "predictions": [],
        "outcomes": [],
        "skipped_invalid_cutoffs": [],
        "counts": {"predicted": 0, "resolved": 0, "unresolvable": 0, "pending": 0, "skipped_invalid_cutoffs": 0},
        "sync": None,
        "deferred_experts": (
            {
                "model_id": "REPORTED-FLOW-LIQUIDITY",
                "status": "DEFERRED",
                "reason": "PROVIDER_REPORTED_SIDE_NOT_AGGRESSOR_INFERENCE",
            },
        ),
        "contextual_competence_status": "INSUFFICIENT_CONTEXTUAL_SUPPORT",
        "prospective_shadow_blocked_reason": "Z9_OR_EXPERIENCE_NOT_QUALIFIED",
        "authority": {
            "capital_allocation": False,
            "economic_decision": False,
            "risk_authorization": False,
            "external_execution": False,
        },
    }
    last_end = 0
    for batch_id in batch_ids:
        result = process_canonical_liquidity_batch(root, str(batch_id), sync=False)
        combined["batches"].append(result)
        combined["predictions"].extend(result.get("predictions") or [])
        combined["outcomes"].extend(result.get("outcomes") or [])
        combined["skipped_invalid_cutoffs"].extend(result.get("skipped_invalid_cutoffs") or [])
        last_end = max(last_end, int(result.get("observation_end_ns") or 0))
        if result.get("contextual_competence_status") == "QUALIFIED_CONTEXT_AVAILABLE":
            combined["contextual_competence_status"] = "QUALIFIED_CONTEXT_AVAILABLE"
        if result.get("prospective_shadow_blocked_reason") is None:
            combined["prospective_shadow_blocked_reason"] = None
    counts = combined["counts"]
    for item in combined["outcomes"]:
        status = str(item.get("status") or "")
        if status == "RESOLVED":
            counts["resolved"] += 1
        elif status == "UNRESOLVABLE":
            counts["unresolvable"] += 1
        elif status == "PENDING":
            counts["pending"] += 1
    counts["predicted"] = len(combined["predictions"])
    counts["skipped_invalid_cutoffs"] = len(combined["skipped_invalid_cutoffs"])
    if sync and last_end:
        combined["sync"] = _sync_liquidity(root, last_end)
    return combined
