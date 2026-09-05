"""Bounded Direction 10s predict → resolve → score → competence loop.

This module does not train models, assemble beliefs, or allocate capital.
Prospective QUALIFIED experience is blocked while live Z9 is DEGRADED; the
loop therefore records HISTORICAL_REPLAY / RESEARCH_ONLY predictions that are
still point-in-time sealed to cutoff T.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..context.service import ContextMaterializationError, materialize_market_context
from ..evaluation.question_journal import QuestionOutcomeJournal
from ..evaluation.question_resolvers import (
    QuestionOutcomePendingError,
    resolve_midpoint_question,
)
from ..experience.builder import TimescaleSpec, build_market_experience
from ..experience.contracts import ExperienceTimescale, MarketExperienceFrame
from ..experience.economic_graph import EconomicInstrumentGraph, EconomicInstrumentNode, InstrumentRole
from ..experience.store import MarketExperienceStore
from ..experts.sync import sync_expert_learning
from ..models.baselines import baseline_model_set
from ..observation.instruments import CanonicalInstrument
from ..prediction.question_bound import PredictionArtifactRef, QuestionBoundPrediction, build_question_bound_prediction
from ..intelligence.runtime import IntelligenceRuntime
from ..prediction.question_journal import QuestionPredictionJournal
from ..questions.catalog import default_question_registry_v1, question_catalog_v1
from ..questions.certification import build_question_registry_v1_qualified
from ..representation.contracts import RepresentationFrame
from ..representation.materialize import (
    RepresentationMaterializationError,
    load_canonical_batch,
    materialize_instrument_state,
)


DIRECTION_QUESTION_REF = "ECONOMIC_ROOT_DIRECTION_10S@1.0.0"
INSTRUMENT_ID = "CRYPTO.SPOT.BTC-USD"
SUBJECT_ID = "ASSET.BTC"
HORIZON_NS = 10_000_000_000
LAG_NS = 2_000_000_000
MICRO_LOOKBACK_NS = 10_000_000_000
FORBIDDEN_FIELDS = (
    "pnl",
    "p_and_l",
    "position_size",
    "capital_allocation",
    "buy",
    "sell",
    "hold",
    "order",
)


class DirectionLoopError(RuntimeError):
    pass


def frozen_direction_registry():
    base = default_question_registry_v1(registered_at_ns=0, effective_at_ns=0)
    return build_question_registry_v1_qualified(base, known_at_ns=0, effective_at_ns=0)


def direction_question():
    matches = [item for item in question_catalog_v1() if item.question_ref == DIRECTION_QUESTION_REF]
    if len(matches) != 1:
        raise DirectionLoopError("frozen Direction question is missing")
    return matches[0]


def btc_spot_graph() -> EconomicInstrumentGraph:
    node = EconomicInstrumentNode(
        node_id="BTC-USD-SPOT",
        instrument=CanonicalInstrument(
            canonical_id=INSTRUMENT_ID,
            asset_class="CRYPTO",
            market_type="SPOT",
            base_asset="BTC",
            quote_asset="USD",
            settlement_asset="USD",
        ),
        role=InstrumentRole.SPOT,
        economic_root_id=SUBJECT_ID,
        quote_family_id="QUOTE.USD",
    )
    return EconomicInstrumentGraph(
        graph_id="ZLJ-BTC-SPOT-DIRECTION-V1",
        graph_version="1.0",
        effective_at_ns=0,
        known_at_ns=0,
        nodes=(node,),
        relationships=(),
    )


def _assert_no_economic_fields(value: Mapping[str, Any], *, path: str = "") -> None:
    for key, item in value.items():
        lowered = str(key).lower()
        if any(token in lowered for token in FORBIDDEN_FIELDS):
            raise DirectionLoopError("economic field %s is forbidden on %s" % (key, path or "artifact"))
        if isinstance(item, Mapping):
            _assert_no_economic_fields(item, path=("%s.%s" % (path, key) if path else str(key)))


def _load_frame(root: Path, artifact: Mapping[str, Any]) -> RepresentationFrame:
    return RepresentationFrame.from_wire(artifact["frame"])


def _load_stored_frame(root: Path, frame_id: str) -> RepresentationFrame:
    path = Path(root).resolve() / "artifacts/market_data/representations" / (frame_id + ".json")
    if not path.is_file():
        raise DirectionLoopError("baseline representation %s is missing from the store" % frame_id)
    document = json.loads(path.read_text(encoding="utf-8"))
    return RepresentationFrame.from_wire(document["frame"])


def _experience_baseline_frame(root: Path, experience: MarketExperienceFrame) -> RepresentationFrame:
    views = [view for view in experience.views if view.timescale is ExperienceTimescale.MICRO]
    if len(views) != 1 or len(views[0].source_frames) != 1:
        raise DirectionLoopError("Direction experience must bind exactly one MICRO source frame")
    return _load_stored_frame(root, views[0].source_frames[0].frame_id)


def _observation_bounds(root: Path, batch_id: str) -> Tuple[int, int]:
    _, observations = load_canonical_batch(root, batch_id)
    matching = [item for item in observations if item.instrument.canonical_id == INSTRUMENT_ID]
    if not matching:
        raise DirectionLoopError("canonical batch has no BTC-USD observations")
    known = [int(item.known_at_ns) for item in matching]
    return min(known), max(known)


def _cutoffs(start_ns: int, end_ns: int) -> Tuple[int, ...]:
    first = start_ns + MICRO_LOOKBACK_NS
    last_forward = end_ns - LAG_NS
    values: List[int] = []
    cutoff = first
    while cutoff + HORIZON_NS <= last_forward:
        values.append(cutoff)
        cutoff += HORIZON_NS
    return tuple(values)


def _binary_answer(probability: Decimal) -> Dict[str, Any]:
    clamped = min(Decimal("0.95"), max(Decimal("0.05"), probability))
    return {"value": 1 if clamped >= Decimal("0.5") else 0, "probability_1": format(clamped, "f")}


def materialize_cutoff_frame(root: Path, batch_id: str, cutoff_at_ns: int) -> RepresentationFrame:
    artifact = materialize_instrument_state(
        root,
        batch_ids=(batch_id,),
        instrument_id=INSTRUMENT_ID,
        cutoff_at_ns=int(cutoff_at_ns),
        min_known_at_ns=int(cutoff_at_ns) - MICRO_LOOKBACK_NS,
        quality_statuses=("VALID",),
    )
    frame = _load_frame(root, artifact)
    if frame.known_at_ns > cutoff_at_ns or frame.cutoff_at_ns > cutoff_at_ns:
        raise DirectionLoopError("cutoff frame leaked post-cutoff evidence")
    if frame.window_start_ns < cutoff_at_ns - MICRO_LOOKBACK_NS:
        raise DirectionLoopError("cutoff frame lookback exceeds Direction MICRO timescale")
    return frame


def materialize_forward_frame(root: Path, batch_id: str, resolves_at_ns: int) -> Optional[RepresentationFrame]:
    """Rebuild the first instrument state knowable inside the frozen lag window.

    Forward truth needs a reconstructable book at or after the horizon. That
    requires the pre-horizon snapshot/delta lineage; it must not include ticks
    known after the lag cutoff.
    """
    cutoff = int(resolves_at_ns) + LAG_NS
    try:
        artifact = materialize_instrument_state(
            root,
            batch_ids=(batch_id,),
            instrument_id=INSTRUMENT_ID,
            cutoff_at_ns=cutoff,
            min_known_at_ns=None,
            quality_statuses=("VALID",),
        )
    except RepresentationMaterializationError:
        return None
    frame = _load_frame(root, artifact)
    if frame.known_at_ns < resolves_at_ns or frame.known_at_ns > cutoff:
        return None
    return frame


def _experience_for_cutoff(root: Path, frame: RepresentationFrame) -> Any:
    cutoff = int(frame.cutoff_at_ns)
    try:
        context_result = materialize_market_context(root, cutoff_at_ns=cutoff)
        context = context_result.context
    except ContextMaterializationError as exc:
        raise DirectionLoopError("Z9 context unavailable at cutoff: %s" % exc) from exc
    if context.known_at_ns > cutoff or context.cutoff_at_ns > cutoff:
        raise DirectionLoopError("Z9 context leaked past prediction cutoff")
    experience = build_market_experience(
        economic_root_id=SUBJECT_ID,
        graph=btc_spot_graph(),
        context=context,
        timescale_frames={ExperienceTimescale.MICRO: (frame,)},
        timescale_specs=(TimescaleSpec(ExperienceTimescale.MICRO, MICRO_LOOKBACK_NS),),
        cutoff_at_ns=cutoff,
    )
    MarketExperienceStore(root).persist(experience)
    return experience, context


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


def record_direction_predictions(root: Path, *, batch_id: str, frame: RepresentationFrame, experience) -> List[Mapping[str, Any]]:
    if frame.status != "QUALIFIED":
        return []
    registry = frozen_direction_registry()
    question = direction_question()
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
        if question_ref != DIRECTION_QUESTION_REF:
            continue
        cutoff = int((payload.get("timing") or {}).get("cutoff_at_ns") or -1)
        for model_ref in payload.get("model_refs") or []:
            existing.add((cutoff, str(model_ref)))
    for model in baseline_model_set():
        if (int(frame.cutoff_at_ns), model.definition.model_ref) in existing:
            match = next(
                entry
                for entry in journal.entries()
                if (entry.get("prediction") or {}).get("prediction_id")
                and int(((entry.get("prediction") or {}).get("timing") or {}).get("cutoff_at_ns") or -1) == int(frame.cutoff_at_ns)
                and model.definition.model_ref in ((entry.get("prediction") or {}).get("model_refs") or [])
            )
            recorded.append(_prediction_summary(match, frame=frame))
            continue
        expected, probability, _low, _high = model.forecast(frame, HORIZON_NS)
        del expected
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


def resolve_direction_prediction(
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
        raise DirectionLoopError("prediction is not in the question journal")
    if now_at_ns < prediction.resolves_at_ns:
        return {"status": "PENDING", "prediction_id": prediction_id, "error": "horizon has not matured"}
    lineage_baseline = _experience_baseline_frame(root, experience)
    if (
        baseline_frame.frame_id != lineage_baseline.frame_id
        or baseline_frame.content_hash() != lineage_baseline.content_hash()
    ):
        raise DirectionLoopError("supplied baseline frame diverges from experience lineage")
    if lineage_baseline.status != "QUALIFIED":
        raise DirectionLoopError("Direction resolver requires a QUALIFIED baseline instrument state")
    forward = materialize_forward_frame(root, batch_id, prediction.resolves_at_ns)
    if forward is not None and forward.known_at_ns <= prediction.cutoff_at_ns:
        raise DirectionLoopError("forward frame is not strictly later than prediction cutoff")
    frames = tuple(item for item in (forward,) if item is not None)
    try:
        outcome = resolve_midpoint_question(
            root,
            prediction_id,
            baseline_experience=experience,
            baseline_frames=(lineage_baseline,),
            forward_frames=frames,
            now_at_ns=int(now_at_ns),
        )
    except QuestionOutcomePendingError as exc:
        return {"status": "PENDING", "prediction_id": prediction_id, "error": str(exc)}
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


def prediction_id_resolves(root: Path, prediction_id: str) -> int:
    for entry in QuestionPredictionJournal(root).entries():
        payload = entry.get("prediction") or {}
        if payload.get("prediction_id") == prediction_id:
            return int((payload.get("timing") or {}).get("resolves_at_ns", -1))
    raise DirectionLoopError("unknown prediction_id")


def process_canonical_direction_batch(root: Path, batch_id: str, *, sync: bool = True) -> Mapping[str, Any]:
    root = Path(root).resolve()
    start_ns, end_ns = _observation_bounds(root, batch_id)
    cutoffs = _cutoffs(start_ns, end_ns)
    if not cutoffs:
        raise DirectionLoopError("canonical batch is too short for a Direction 10s independent cutoff")
    predictions: List[Mapping[str, Any]] = []
    experiences: Dict[int, Tuple[Any, Any]] = {}
    frames: Dict[int, RepresentationFrame] = {}
    skipped_unqualified_cutoffs: List[int] = []
    for cutoff in cutoffs:
        frame = materialize_cutoff_frame(root, batch_id, cutoff)
        if frame.status != "QUALIFIED":
            skipped_unqualified_cutoffs.append(int(cutoff))
            continue
        experience, context = _experience_for_cutoff(root, frame)
        frames[cutoff] = frame
        experiences[cutoff] = (experience, context)
        predictions.extend(record_direction_predictions(root, batch_id=batch_id, frame=frame, experience=experience))
    if not predictions:
        raise DirectionLoopError("canonical batch has no QUALIFIED Direction 10s cutoff that can be examined")
    outcomes: List[Mapping[str, Any]] = []
    store = MarketExperienceStore(root)
    for item in predictions:
        cutoff = int(item["cutoff_at_ns"])
        experience = store.load(str(item["experience_id"]))
        outcomes.append(
            resolve_direction_prediction(
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
        sync_result = dict(sync_expert_learning(root, known_at_ns=int(end_ns)))
        competence = IntelligenceRuntime(root).state().get("competence")
        if isinstance(competence, Mapping):
            _assert_no_economic_fields(competence)
            sync_result["competence"] = competence
        from .direction_assembly import DirectionAssemblyError, assemble_and_record_direction_question

        try:
            sync_result["direction_assembly"] = assemble_and_record_direction_question(root, known_at_ns=int(end_ns))
        except DirectionAssemblyError as exc:
            sync_result["direction_assembly"] = {"status": "BLOCKED", "error": str(exc)}
    counts = {
        "predicted": len(predictions),
        "resolved": sum(1 for item in outcomes if item.get("status") == "RESOLVED"),
        "unresolvable": sum(1 for item in outcomes if item.get("status") == "UNRESOLVABLE"),
        "pending": sum(1 for item in outcomes if item.get("status") == "PENDING"),
    }
    latest_cutoff = max(experiences) if experiences else None
    latest_context = experiences[latest_cutoff][1] if latest_cutoff is not None else None
    contextual_status = "INSUFFICIENT_CONTEXTUAL_SUPPORT"
    if latest_context is not None and str(getattr(latest_context, "status", "")) == "QUALIFIED":
        contextual_status = "QUALIFIED_CONTEXT_AVAILABLE"
    return {
        "status": "OK",
        "batch_id": batch_id,
        "question_ref": DIRECTION_QUESTION_REF,
        "horizon_ns": HORIZON_NS,
        "instrument_id": INSTRUMENT_ID,
        "subject_id": SUBJECT_ID,
        "observation_start_ns": start_ns,
        "observation_end_ns": end_ns,
        "cutoffs": list(cutoffs),
        "skipped_unqualified_cutoffs": skipped_unqualified_cutoffs,
        "predictions": predictions,
        "outcomes": outcomes,
        "counts": counts,
        "sync": sync_result,
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


def process_canonical_direction_batches(root: Path, batch_ids: Sequence[str], *, sync: bool = True) -> Mapping[str, Any]:
    combined: Dict[str, Any] = {
        "status": "OK",
        "question_ref": DIRECTION_QUESTION_REF,
        "horizon_ns": HORIZON_NS,
        "instrument_id": INSTRUMENT_ID,
        "batches": [],
        "predictions": [],
        "outcomes": [],
        "skipped_unqualified_cutoffs": [],
        "counts": {"predicted": 0, "resolved": 0, "unresolvable": 0, "pending": 0},
        "sync": None,
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
        result = process_canonical_direction_batch(root, str(batch_id), sync=False)
        combined["batches"].append(result)
        combined["predictions"].extend(result.get("predictions") or [])
        combined["outcomes"].extend(result.get("outcomes") or [])
        combined["skipped_unqualified_cutoffs"].extend(result.get("skipped_unqualified_cutoffs") or [])
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
    if sync and last_end:
        combined["sync"] = dict(sync_expert_learning(root, known_at_ns=int(last_end)))
        competence = IntelligenceRuntime(root).state().get("competence")
        if isinstance(competence, Mapping):
            _assert_no_economic_fields(competence)
            combined["sync"]["competence"] = competence
        from .direction_assembly import DirectionAssemblyError, assemble_and_record_direction_question

        try:
            combined["sync"]["direction_assembly"] = assemble_and_record_direction_question(root, known_at_ns=int(last_end))
            from ..synthesis.contracts import MarketSynthesisError as _SynthesisError
            from ..synthesis.service import synthesize_and_record

            try:
                combined["sync"]["market_synthesis"] = synthesize_and_record(root, known_at_ns=int(last_end))
            except _SynthesisError as exc:
                combined["sync"]["market_synthesis"] = {"status": "BLOCKED", "error": str(exc)}
        except DirectionAssemblyError as exc:
            combined["sync"]["direction_assembly"] = {"status": "BLOCKED", "error": str(exc)}
    return combined


def _family_learning_slice(
    predictions: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    question_ref: str,
) -> Mapping[str, Any]:
    family_predictions = [
        entry
        for entry in predictions
        if ((entry.get("prediction") or {}).get("question") or {}).get("question_ref") == question_ref
    ]
    prediction_ids = {(entry.get("prediction") or {}).get("prediction_id") for entry in family_predictions}
    family_outcomes = [
        entry
        for entry in outcomes
        if (entry.get("outcome") or {}).get("prediction_id") in prediction_ids
    ]
    family_counts = {"RESOLVED": 0, "UNRESOLVABLE": 0, "PENDING": 0}
    for entry in family_outcomes:
        status = str((entry.get("outcome") or {}).get("status") or "")
        if status in family_counts:
            family_counts[status] += 1
    family_counts["PENDING"] = max(0, len(family_predictions) - family_counts["RESOLVED"] - family_counts["UNRESOLVABLE"])
    latest_family_prediction = family_predictions[-1]["prediction"] if family_predictions else None
    latest_family_outcome = None if not family_outcomes else family_outcomes[-1].get("outcome")
    return {
        "question_ref": question_ref,
        "prediction_count": len(family_predictions),
        "outcome_count": len(family_outcomes),
        "status_counts": family_counts,
        "latest_prediction": None
        if not isinstance(latest_family_prediction, Mapping)
        else {
            "prediction_id": latest_family_prediction.get("prediction_id"),
            "question_ref": (latest_family_prediction.get("question") or {}).get("question_ref"),
            "model_refs": latest_family_prediction.get("model_refs"),
            "cutoff_at_ns": (latest_family_prediction.get("timing") or {}).get("cutoff_at_ns"),
            "horizon_ns": (latest_family_prediction.get("timing") or {}).get("horizon_ns"),
            "mode": latest_family_prediction.get("mode"),
            "answer": latest_family_prediction.get("answer"),
        },
        "latest_outcome": latest_family_outcome,
    }


def question_learning_projection(root: Path) -> Mapping[str, Any]:
    from .direction_assembly import direction_assembly_projection
    from .liquidity_assembly import liquidity_assembly_projection
    from .liquidity_loop import LIQUIDITY_QUESTION_REF as _LIQUIDITY_QUESTION_REF
    from .magnitude_assembly import magnitude_assembly_projection
    from .magnitude_loop import MAGNITUDE_QUESTION_REF as _MAGNITUDE_QUESTION_REF
    from ..synthesis.service import market_synthesis_projection

    root = Path(root).resolve()
    predictions = list(QuestionPredictionJournal(root).entries()) if (root / "memory/question_predictions.jsonl").is_file() else []
    outcomes = list(QuestionOutcomeJournal(root).entries()) if (root / "memory/question_outcomes.jsonl").is_file() else []
    latest_prediction = predictions[-1]["prediction"] if predictions else None
    status_counts = {"RESOLVED": 0, "UNRESOLVABLE": 0, "PENDING": 0}
    for entry in outcomes:
        status = str((entry.get("outcome") or {}).get("status") or "")
        if status in status_counts:
            status_counts[status] += 1
    pending = max(0, len(predictions) - status_counts["RESOLVED"] - status_counts["UNRESOLVABLE"])
    status_counts["PENDING"] = pending
    latest_outcome = None if not outcomes else outcomes[-1].get("outcome")
    runtime_state = IntelligenceRuntime(root).state()
    competence = runtime_state.get("competence") if isinstance(runtime_state, Mapping) else None
    experts: List[Mapping[str, Any]] = []
    if isinstance(competence, Mapping):
        for entry in competence.get("entries") or []:
            if not isinstance(entry, Mapping):
                continue
            sample_count = int(entry.get("sample_count") or 0)
            experts.append(
                {
                    "expert_ref": entry.get("expert_ref"),
                    "question_ref": entry.get("question_ref"),
                    "sample_count": sample_count,
                    "mean_score": entry.get("mean_score"),
                    "recent_score": entry.get("recent_score"),
                    "last_resolved_at_ns": entry.get("last_resolved_at_ns"),
                    "known_at_ns": competence.get("known_at_ns"),
                    "empirical_support": "MODEST_SCORED_SAMPLE" if sample_count else "NONE",
                    "mastery": False,
                }
            )
    direction_slice = _family_learning_slice(predictions, outcomes, DIRECTION_QUESTION_REF)
    liquidity_slice = dict(_family_learning_slice(predictions, outcomes, _LIQUIDITY_QUESTION_REF))
    liquidity_slice["horizon_ns"] = 30_000_000_000
    liquidity_slice["assembly"] = liquidity_assembly_projection(root)
    magnitude_slice = dict(_family_learning_slice(predictions, outcomes, _MAGNITUDE_QUESTION_REF))
    magnitude_slice["horizon_ns"] = 30_000_000_000
    magnitude_slice["assembly"] = magnitude_assembly_projection(root)
    del direction_slice
    return {
        "question_ref": DIRECTION_QUESTION_REF,
        "horizon_ns": HORIZON_NS,
        "prediction_count": len(predictions),
        "outcome_count": len(outcomes),
        "status_counts": status_counts,
        "latest_prediction": None
        if not isinstance(latest_prediction, Mapping)
        else {
            "prediction_id": latest_prediction.get("prediction_id"),
            "question_ref": (latest_prediction.get("question") or {}).get("question_ref"),
            "model_refs": latest_prediction.get("model_refs"),
            "cutoff_at_ns": (latest_prediction.get("timing") or {}).get("cutoff_at_ns"),
            "horizon_ns": (latest_prediction.get("timing") or {}).get("horizon_ns"),
            "mode": latest_prediction.get("mode"),
        },
        "latest_outcome": latest_outcome,
        "competence": None
        if not isinstance(competence, Mapping)
        else {
            "known_at_ns": competence.get("known_at_ns"),
            "content_hash": (competence.get("integrity") or {}).get("content_hash"),
            "entry_count": competence.get("entry_count"),
            "experts": experts,
            "mastery_claim": False,
        },
        "contextual_competence_status": "INSUFFICIENT_CONTEXTUAL_SUPPORT",
        "assembly": direction_assembly_projection(root),
        "liquidity": liquidity_slice,
        "liquidity_assembly": liquidity_slice["assembly"],
        "magnitude": magnitude_slice,
        "magnitude_assembly": magnitude_slice["assembly"],
        "market_synthesis": market_synthesis_projection(root),
        "authority": {
            "capital_allocation": False,
            "economic_decision": False,
            "adaptive_assembly_earned": False,
        },
    }
