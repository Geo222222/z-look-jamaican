"""Bounded Magnitude 30s predict → resolve → score → competence loop.

This module does not train models, allocate capital, or change resolver truth.
It reuses QuestionPredictionJournal, QuestionOutcomeJournal, resolve_midpoint_question,
and sync_expert_learning. Prospective QUALIFIED experience remains blocked while live
Z9 is DEGRADED; predictions are HISTORICAL_REPLAY / RESEARCH_ONLY unless experience
is QUALIFIED.

MARKET_WIDE_EXPERIENCE is required by the frozen Magnitude exam and is materialized
through the Market-Wide Experience Bridge from durable Z9 context history.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..evaluation.question_journal import QuestionOutcomeJournal
from ..evaluation.question_resolvers import QuestionOutcomePendingError, resolve_midpoint_question
from ..experience.bridge import MarketWideExperienceBridgeError, SHORT_LOOKBACK_NS, materialize_market_wide_experience
from ..experience.builder import TimescaleSpec, build_market_experience
from ..experience.contracts import ExperienceTimescale
from ..experience.market_wide import MarketWideExperienceState
from ..experience.store import MarketExperienceStore
from ..experts.sync import sync_expert_learning
from ..intelligence.runtime import IntelligenceRuntime
from ..models.baselines import NullPriorModel
from ..models.magnitude_baselines import MAGNITUDE_HORIZON_NS, magnitude_baseline_model_set
from ..prediction.question_bound import PredictionArtifactRef, QuestionBoundPrediction, build_question_bound_prediction
from ..prediction.question_journal import QuestionPredictionJournal
from ..questions.catalog import question_catalog_v1
from ..representation.contracts import RepresentationFrame
from ..representation.materialize import RepresentationMaterializationError, materialize_instrument_state
from .direction_loop import (
    FORBIDDEN_FIELDS,
    INSTRUMENT_ID,
    LAG_NS,
    MICRO_LOOKBACK_NS,
    SUBJECT_ID,
    _assert_no_economic_fields,
    _experience_baseline_frame,
    _load_frame,
    _observation_bounds,
    btc_spot_graph,
    frozen_direction_registry,
    materialize_cutoff_frame,
    materialize_forward_frame,
    _experience_for_cutoff as _direction_experience_for_cutoff,
)


MAGNITUDE_QUESTION_REF = "ECONOMIC_ROOT_MAGNITUDE_30S@1.0.0"
HORIZON_NS = MAGNITUDE_HORIZON_NS


class MagnitudeLoopError(RuntimeError):
    pass


def magnitude_question():
    matches = [item for item in question_catalog_v1() if item.question_ref == MAGNITUDE_QUESTION_REF]
    if len(matches) != 1:
        raise MagnitudeLoopError("frozen Magnitude question is missing")
    return matches[0]


def magnitude_model_set() -> Tuple[Any, ...]:
    return (NullPriorModel(),) + tuple(magnitude_baseline_model_set())


def _cutoffs(start_ns: int, end_ns: int) -> Tuple[int, ...]:
    first = start_ns + MICRO_LOOKBACK_NS
    last_forward = end_ns - LAG_NS
    values: List[int] = []
    cutoff = first
    while cutoff + HORIZON_NS <= last_forward:
        values.append(cutoff)
        cutoff += HORIZON_NS
    return tuple(values)


def magnitude_baseline_is_eligible(frame: RepresentationFrame) -> bool:
    return frame.instrument.canonical_id == INSTRUMENT_ID and frame.status == "QUALIFIED" and frame.representation_type == "INSTRUMENT_STATE"


def materialize_short_cutoff_frame(root: Path, batch_id: str, cutoff_at_ns: int) -> RepresentationFrame:
    artifact = materialize_instrument_state(
        root,
        batch_ids=(batch_id,),
        instrument_id=INSTRUMENT_ID,
        cutoff_at_ns=int(cutoff_at_ns),
        min_known_at_ns=int(cutoff_at_ns) - SHORT_LOOKBACK_NS,
        quality_statuses=("VALID",),
    )
    frame = _load_frame(root, artifact)
    if frame.known_at_ns > cutoff_at_ns or frame.cutoff_at_ns > cutoff_at_ns:
        raise MagnitudeLoopError("SHORT cutoff frame leaked post-cutoff evidence")
    if frame.window_start_ns < cutoff_at_ns - SHORT_LOOKBACK_NS:
        raise MagnitudeLoopError("SHORT cutoff frame lookback exceeds Magnitude SHORT timescale")
    return frame


def _experience_for_cutoff(root: Path, micro: RepresentationFrame, short: RepresentationFrame) -> Tuple[Any, Any, MarketWideExperienceState]:
    cutoff = int(micro.cutoff_at_ns)
    experience, context = _direction_experience_for_cutoff(root, micro)
    del experience
    try:
        market_wide = materialize_market_wide_experience(
            root,
            cutoff_at_ns=cutoff,
            window_start_ns=cutoff - SHORT_LOOKBACK_NS,
            timescale=ExperienceTimescale.SHORT,
        )
    except MarketWideExperienceBridgeError as exc:
        raise MagnitudeLoopError("MARKET_WIDE_EXPERIENCE unavailable at cutoff: %s" % exc) from exc
    if market_wide.known_at_ns > cutoff or market_wide.cutoff_at_ns > cutoff:
        raise MagnitudeLoopError("market-wide experience leaked past prediction cutoff")
    combined = build_market_experience(
        economic_root_id=SUBJECT_ID,
        graph=btc_spot_graph(),
        context=context,
        timescale_frames={
            ExperienceTimescale.MICRO: (micro,),
            ExperienceTimescale.SHORT: (short,),
        },
        timescale_specs=(
            TimescaleSpec(ExperienceTimescale.MICRO, MICRO_LOOKBACK_NS),
            TimescaleSpec(ExperienceTimescale.SHORT, SHORT_LOOKBACK_NS),
        ),
        cutoff_at_ns=cutoff,
    )
    MarketExperienceStore(root).persist(combined)
    return combined, context, market_wide


def _continuous_answer(value: Decimal) -> Dict[str, Any]:
    return {"value": format(value, "f")}


def _prediction_summary(entry: Mapping[str, Any], *, frame: Optional[RepresentationFrame] = None, experience=None) -> Dict[str, Any]:
    payload = entry.get("prediction") or {}
    timing = payload.get("timing") or {}
    refs = payload.get("artifact_refs") or []
    experience_id = None
    experience_status = None
    market_wide_id = None
    for ref in refs:
        if not isinstance(ref, Mapping):
            continue
        if ref.get("artifact_type") == "MARKET_EXPERIENCE":
            experience_id = ref.get("artifact_id")
            experience_status = ref.get("status")
        if ref.get("artifact_type") == "MARKET_WIDE_EXPERIENCE":
            market_wide_id = ref.get("artifact_id")
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
        "market_wide_experience_id": market_wide_id,
        "context_id": None if experience is None else experience.context_id,
        "context_hash": None if experience is None else experience.context_hash,
        "frame_id": None if frame is None else frame.frame_id,
        "frame_hash": None if frame is None else frame.content_hash(),
    }


def record_magnitude_predictions(
    root: Path,
    *,
    batch_id: str,
    frame: RepresentationFrame,
    experience,
    market_wide: MarketWideExperienceState,
) -> List[Mapping[str, Any]]:
    del batch_id
    if not magnitude_baseline_is_eligible(frame):
        return []
    registry = frozen_direction_registry()
    question = magnitude_question()
    status = "DEGRADED" if experience.status != "QUALIFIED" or market_wide.status != "QUALIFIED" else "QUALIFIED"
    mode = "PROSPECTIVE_SHADOW" if status == "QUALIFIED" else "HISTORICAL_REPLAY"
    evidence_class = "FORWARD_EVALUABLE" if mode == "PROSPECTIVE_SHADOW" else "RESEARCH_ONLY"
    experience_ref = PredictionArtifactRef(
        artifact_type="MARKET_EXPERIENCE",
        artifact_id=experience.experience_id,
        content_hash=experience.content_hash(),
        known_at_ns=int(experience.known_at_ns),
        status=experience.status,
        timescales=(ExperienceTimescale.MICRO, ExperienceTimescale.SHORT),
        feature_families=("SPOT_MICROSTRUCTURE",),
        subject_ids=(SUBJECT_ID,),
    )
    market_wide_ref = PredictionArtifactRef(
        artifact_type="MARKET_WIDE_EXPERIENCE",
        artifact_id=market_wide.market_wide_experience_id,
        content_hash=market_wide.content_hash(),
        known_at_ns=int(market_wide.known_at_ns),
        status=market_wide.status,
        timescales=(ExperienceTimescale.SHORT,),
        feature_families=("MARKET_WIDE_CONTEXT",),
        subject_ids=(SUBJECT_ID,),
    )
    created_at = max(int(frame.known_at_ns), int(experience.known_at_ns), int(market_wide.known_at_ns), int(frame.cutoff_at_ns))
    journal = QuestionPredictionJournal(root)
    recorded: List[Mapping[str, Any]] = []
    existing = set()
    for entry in journal.entries():
        payload = entry.get("prediction") or {}
        question_ref = (payload.get("question") or {}).get("question_ref")
        if question_ref != MAGNITUDE_QUESTION_REF:
            continue
        cutoff = int((payload.get("timing") or {}).get("cutoff_at_ns") or -1)
        for model_ref in payload.get("model_refs") or []:
            existing.add((cutoff, str(model_ref)))
    for model in magnitude_model_set():
        if (int(frame.cutoff_at_ns), model.definition.model_ref) in existing:
            match = next(
                entry
                for entry in journal.entries()
                if (entry.get("prediction") or {}).get("prediction_id")
                and int(((entry.get("prediction") or {}).get("timing") or {}).get("cutoff_at_ns") or -1) == int(frame.cutoff_at_ns)
                and model.definition.model_ref in ((entry.get("prediction") or {}).get("model_refs") or [])
                and ((entry.get("prediction") or {}).get("question") or {}).get("question_ref") == MAGNITUDE_QUESTION_REF
            )
            recorded.append(_prediction_summary(match, frame=frame, experience=experience))
            continue
        if hasattr(model, "forecast_magnitude"):
            expected, decomposition = model.forecast_magnitude(frame, market_wide)
            del decomposition
        else:
            expected, _probability, _low, _high = model.forecast(frame, HORIZON_NS)
        prediction = build_question_bound_prediction(
            registry=registry,
            question=question,
            subject_id=SUBJECT_ID,
            mode=mode,
            evidence_class=evidence_class,
            cutoff_at_ns=int(frame.cutoff_at_ns),
            created_at_ns=created_at,
            answer=_continuous_answer(expected),
            model_refs=(model.definition.model_ref,),
            artifact_refs=(experience_ref, market_wide_ref),
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
                "market_wide_experience_id": market_wide.market_wide_experience_id,
                "context_id": experience.context_id,
                "context_hash": experience.context_hash,
                "frame_id": frame.frame_id,
                "frame_hash": frame.content_hash(),
            }
        )
    return recorded


def resolve_magnitude_prediction(
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
        raise MagnitudeLoopError("prediction is not in the question journal")
    if now_at_ns < prediction.resolves_at_ns:
        return {"status": "PENDING", "prediction_id": prediction_id, "error": "horizon has not matured"}
    lineage_baseline = _experience_baseline_frame(root, experience)
    if (
        baseline_frame.frame_id != lineage_baseline.frame_id
        or baseline_frame.content_hash() != lineage_baseline.content_hash()
    ):
        raise MagnitudeLoopError("supplied baseline frame diverges from experience lineage")
    if lineage_baseline.status != "QUALIFIED":
        raise MagnitudeLoopError("Magnitude resolver requires a QUALIFIED baseline instrument state")
    forward = materialize_forward_frame(root, batch_id, prediction.resolves_at_ns)
    if forward is not None and forward.known_at_ns <= prediction.cutoff_at_ns:
        raise MagnitudeLoopError("forward frame is not strictly later than prediction cutoff")
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


def _sync_magnitude(root: Path, known_at_ns: int) -> Mapping[str, Any]:
    sync_result = dict(sync_expert_learning(root, known_at_ns=int(known_at_ns)))
    competence = IntelligenceRuntime(root).state().get("competence")
    if isinstance(competence, Mapping):
        _assert_no_economic_fields(competence)
        sync_result["competence"] = competence
    from .magnitude_assembly import MagnitudeAssemblyError, assemble_and_record_magnitude_question

    try:
        sync_result["magnitude_assembly"] = assemble_and_record_magnitude_question(root, known_at_ns=int(known_at_ns))
    except MagnitudeAssemblyError as exc:
        sync_result["magnitude_assembly"] = {"status": "BLOCKED", "error": str(exc)}
        return sync_result
    from ..synthesis.contracts import MarketSynthesisError
    from ..synthesis.service import synthesize_and_record

    try:
        sync_result["market_synthesis"] = synthesize_and_record(root, known_at_ns=int(known_at_ns))
    except MarketSynthesisError as exc:
        sync_result["market_synthesis"] = {"status": "BLOCKED", "error": str(exc)}
    return sync_result


def process_canonical_magnitude_batch(root: Path, batch_id: str, *, sync: bool = True) -> Mapping[str, Any]:
    root = Path(root).resolve()
    start_ns, end_ns = _observation_bounds(root, batch_id)
    cutoffs = _cutoffs(start_ns, end_ns)
    if not cutoffs:
        raise MagnitudeLoopError("canonical batch is too short for a Magnitude 30s independent cutoff")
    predictions: List[Mapping[str, Any]] = []
    experiences: Dict[int, Tuple[Any, Any, MarketWideExperienceState]] = {}
    frames: Dict[int, RepresentationFrame] = {}
    skipped_invalid_cutoffs: List[Mapping[str, Any]] = []
    for cutoff in cutoffs:
        try:
            frame = materialize_cutoff_frame(root, batch_id, cutoff)
        except Exception:
            skipped_invalid_cutoffs.append({"cutoff_at_ns": int(cutoff), "reason": "UNQUALIFIED_OR_ILLEGAL_BASELINE"})
            continue
        if not magnitude_baseline_is_eligible(frame):
            skipped_invalid_cutoffs.append({"cutoff_at_ns": int(cutoff), "reason": "UNQUALIFIED_OR_ILLEGAL_BASELINE"})
            continue
        try:
            short = materialize_short_cutoff_frame(root, batch_id, cutoff)
        except (RepresentationMaterializationError, MagnitudeLoopError):
            skipped_invalid_cutoffs.append({"cutoff_at_ns": int(cutoff), "reason": "SHORT_TIMESCALE_UNAVAILABLE"})
            continue
        try:
            experience, context, market_wide = _experience_for_cutoff(root, frame, short)
        except MagnitudeLoopError as exc:
            skipped_invalid_cutoffs.append({"cutoff_at_ns": int(cutoff), "reason": str(exc)})
            continue
        frames[cutoff] = frame
        experiences[cutoff] = (experience, context, market_wide)
        predictions.extend(
            record_magnitude_predictions(
                root,
                batch_id=batch_id,
                frame=frame,
                experience=experience,
                market_wide=market_wide,
            )
        )
    if not predictions:
        raise MagnitudeLoopError("canonical batch has no legal Magnitude 30s cutoff that can be examined")
    outcomes: List[Mapping[str, Any]] = []
    store = MarketExperienceStore(root)
    for item in predictions:
        cutoff = int(item["cutoff_at_ns"])
        experience = store.load(str(item["experience_id"]))
        outcomes.append(
            resolve_magnitude_prediction(
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
        sync_result = _sync_magnitude(root, end_ns)
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
        "question_ref": MAGNITUDE_QUESTION_REF,
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


def process_canonical_magnitude_batches(root: Path, batch_ids: Sequence[str], *, sync: bool = True) -> Mapping[str, Any]:
    combined: Dict[str, Any] = {
        "status": "OK",
        "question_ref": MAGNITUDE_QUESTION_REF,
        "horizon_ns": HORIZON_NS,
        "instrument_id": INSTRUMENT_ID,
        "batches": [],
        "predictions": [],
        "outcomes": [],
        "skipped_invalid_cutoffs": [],
        "counts": {"predicted": 0, "resolved": 0, "unresolvable": 0, "pending": 0, "skipped_invalid_cutoffs": 0},
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
        result = process_canonical_magnitude_batch(root, str(batch_id), sync=False)
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
        combined["sync"] = _sync_magnitude(root, last_end)
    return combined
