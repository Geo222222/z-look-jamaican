from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence, Tuple

from ..experience.contracts import ExperienceTimescale, MarketExperienceFrame
from ..prediction.factory import PredictionFactoryError, representation_target_price
from ..prediction.question_bound import QuestionBoundPrediction, QuestionPredictionError
from ..prediction.question_journal import QuestionPredictionJournal, validate_question_prediction_journal
from ..representation.contracts import RepresentationFrame
from .question_outcome import QuestionBoundOutcome, ResolutionEvidenceRef, build_question_outcome_id

MIDPOINT_RESOLVER_POLICY_ID = "FIRST_QUALIFIED_AGGREGATE_MIDPOINT_AT_OR_AFTER_TARGET_V1"
MIDPOINT_RESOLVER_IMPLEMENTATION_REF = "autonomous_kernel.evaluation.question_resolvers.midpoint_v1"
SUPPORTED_MIDPOINT_QUESTIONS = {"ECONOMIC_ROOT_DIRECTION_10S", "ECONOMIC_ROOT_MAGNITUDE_30S"}

class QuestionResolverError(RuntimeError):
    pass

class QuestionOutcomePendingError(QuestionResolverError):
    pass

def _journaled_prediction(root: Path, prediction_id: str) -> Tuple[QuestionBoundPrediction, str, int]:
    errors = validate_question_prediction_journal(root)
    if errors:
        raise QuestionResolverError("question prediction journal invalid: " + "; ".join(errors))
    matches = [entry for entry in QuestionPredictionJournal(root).entries() if entry.get("prediction", {}).get("prediction_id") == prediction_id]
    if len(matches) != 1:
        raise QuestionResolverError("prediction must appear exactly once in question prediction journal")
    entry = matches[0]
    try:
        prediction = QuestionBoundPrediction.from_wire(entry.get("prediction", {}))
    except (QuestionPredictionError, ValueError, TypeError) as exc:
        raise QuestionResolverError("journaled question prediction is invalid: %s" % exc) from exc
    return prediction, str(entry.get("entry_hash", "")), int(entry.get("journaled_at_ns", -1))

def _bound_market_experience(prediction: QuestionBoundPrediction, experience: MarketExperienceFrame) -> None:
    refs = [ref for ref in prediction.artifact_refs if ref.artifact_type == "MARKET_EXPERIENCE" and ref.artifact_id == experience.experience_id]
    if len(refs) != 1:
        raise QuestionResolverError("prediction does not bind the supplied Market Experience")
    ref = refs[0]
    if ref.content_hash != experience.content_hash():
        raise QuestionResolverError("Market Experience content hash differs from prediction lineage")
    if experience.economic_root_id != prediction.subject_id:
        raise QuestionResolverError("Market Experience economic root differs from prediction subject")
    if experience.cutoff_at_ns != prediction.cutoff_at_ns:
        raise QuestionResolverError("Market Experience cutoff differs from prediction cutoff")
    if prediction.mode == "PROSPECTIVE_SHADOW" and experience.status != "QUALIFIED":
        raise QuestionResolverError("prospective resolution requires qualified baseline experience")

def _baseline_spot_frame(prediction: QuestionBoundPrediction, experience: MarketExperienceFrame, frames: Sequence[RepresentationFrame], *, timescale: ExperienceTimescale = ExperienceTimescale.MICRO) -> RepresentationFrame:
    views = [view for view in experience.views if view.timescale is timescale]
    if len(views) != 1:
        raise QuestionResolverError("resolver requires exactly one %s experience view" % timescale.value)
    source_refs = [ref for ref in views[0].source_frames if ref.representation_type == "INSTRUMENT_STATE" and ref.market_type == "SPOT" and ref.status == "QUALIFIED" and ref.cutoff_at_ns == prediction.cutoff_at_ns]
    instrument_ids = sorted({ref.instrument_id for ref in source_refs})
    if len(instrument_ids) != 1:
        raise QuestionResolverError("resolver requires one unambiguous qualified spot instrument at cutoff")
    instrument_id = instrument_ids[0]
    candidates = []
    for ref in source_refs:
        for frame in frames:
            if frame.frame_id != ref.frame_id:
                continue
            if frame.content_hash() != ref.frame_hash:
                raise QuestionResolverError("baseline representation hash differs from Market Experience lineage")
            if frame.instrument.canonical_id != instrument_id:
                raise QuestionResolverError("baseline representation instrument differs from Market Experience lineage")
            if frame.representation_type != "INSTRUMENT_STATE" or frame.status != "QUALIFIED":
                raise QuestionResolverError("baseline representation is not a qualified instrument state")
            candidates.append(frame)
    unique = {(item.frame_id, item.content_hash()): item for item in candidates}
    if len(unique) != 1:
        raise QuestionResolverError("exactly one baseline spot representation must be supplied")
    return next(iter(unique.values()))

def _select_forward_frame(prediction: QuestionBoundPrediction, baseline: RepresentationFrame, frames: Sequence[RepresentationFrame]) -> Optional[RepresentationFrame]:
    upper = prediction.resolves_at_ns + prediction.max_resolution_lag_ns
    eligible = [frame for frame in frames if frame.instrument == baseline.instrument and frame.representation_type == "INSTRUMENT_STATE" and frame.status == "QUALIFIED" and frame.known_at_ns >= prediction.resolves_at_ns and frame.known_at_ns <= upper]
    if not eligible:
        return None
    return sorted(eligible, key=lambda item: (item.known_at_ns, item.cutoff_at_ns, item.frame_id, item.content_hash()))[0]

def _resolved_value(prediction: QuestionBoundPrediction, baseline_price: Decimal, realized_price: Decimal):
    if prediction.question_ref.startswith("ECONOMIC_ROOT_DIRECTION_10S@"):
        return {"value": 1 if realized_price > baseline_price else 0}
    if prediction.question_ref.startswith("ECONOMIC_ROOT_MAGNITUDE_30S@"):
        return {"value": format((realized_price / baseline_price - Decimal("1")) * Decimal("10000"), "f")}
    raise QuestionResolverError("unsupported midpoint question")

def resolve_midpoint_question(root: Path, prediction_id: str, *, baseline_experience: MarketExperienceFrame, baseline_frames: Sequence[RepresentationFrame], forward_frames: Sequence[RepresentationFrame], now_at_ns: int) -> QuestionBoundOutcome:
    root = root.resolve()
    prediction, entry_hash, journaled_at_ns = _journaled_prediction(root, prediction_id)
    if prediction.question_ref.split("@", 1)[0] not in SUPPORTED_MIDPOINT_QUESTIONS:
        raise QuestionResolverError("prediction question is not supported by midpoint resolver")
    if prediction.resolver_policy_id != MIDPOINT_RESOLVER_POLICY_ID:
        raise QuestionResolverError("prediction resolver policy differs from midpoint resolver")
    if prediction.mode == "PROSPECTIVE_SHADOW" and journaled_at_ns >= prediction.resolves_at_ns:
        raise QuestionResolverError("late-journaled prospective prediction cannot become forward evidence")
    now = int(now_at_ns)
    if now < 0:
        raise QuestionResolverError("now_at_ns must be non-negative")
    _bound_market_experience(prediction, baseline_experience)
    baseline = _baseline_spot_frame(prediction, baseline_experience, baseline_frames, timescale=ExperienceTimescale.MICRO)
    try:
        baseline_price_text, _ = representation_target_price(baseline)
    except PredictionFactoryError as exc:
        raise QuestionResolverError("baseline representation has no qualified midpoint: %s" % exc) from exc
    baseline_price = Decimal(baseline_price_text)
    selected = _select_forward_frame(prediction, baseline, forward_frames)
    outcome_id = build_question_outcome_id(prediction.prediction_id, prediction.resolver_policy_id, MIDPOINT_RESOLVER_IMPLEMENTATION_REF)
    if selected is not None:
        if now < selected.known_at_ns:
            raise QuestionOutcomePendingError("selected forward evidence is not knowable at now_at_ns")
        try:
            realized_price_text, _ = representation_target_price(selected)
        except PredictionFactoryError as exc:
            raise QuestionResolverError("selected forward representation has no qualified midpoint: %s" % exc) from exc
        realized = Decimal(realized_price_text)
        return QuestionBoundOutcome(outcome_id=outcome_id, prediction_id=prediction.prediction_id, prediction_content_hash=prediction.content_hash(), prediction_journal_entry_hash=entry_hash, question_ref=prediction.question_ref, question_definition_hash=prediction.question_definition_hash, question_registry_hash=prediction.question_registry_hash, subject_id=prediction.subject_id, answer_kind=prediction.answer_kind, outcome_metric_id=prediction.outcome_metric_id, resolver_policy_id=prediction.resolver_policy_id, resolver_implementation_ref=MIDPOINT_RESOLVER_IMPLEMENTATION_REF, status="RESOLVED", cutoff_at_ns=prediction.cutoff_at_ns, target_resolves_at_ns=prediction.resolves_at_ns, max_resolution_lag_ns=prediction.max_resolution_lag_ns, decided_at_ns=selected.known_at_ns, realized_answer=_resolved_value(prediction, baseline_price, realized), resolution_evidence=(ResolutionEvidenceRef(evidence_family="SPOT_MICROSTRUCTURE", artifact_type="REPRESENTATION_FRAME", artifact_id=baseline.frame_id, content_hash=baseline.content_hash(), known_at_ns=baseline.known_at_ns, role="BASELINE", subject_ids=(prediction.subject_id,)), ResolutionEvidenceRef(evidence_family="SPOT_MICROSTRUCTURE", artifact_type="REPRESENTATION_FRAME", artifact_id=selected.frame_id, content_hash=selected.content_hash(), known_at_ns=selected.known_at_ns, role="FORWARD", subject_ids=(prediction.subject_id,))))
    window_closes = prediction.resolves_at_ns + prediction.max_resolution_lag_ns
    if now <= window_closes:
        raise QuestionOutcomePendingError("resolution window remains open and no eligible forward frame exists")
    return QuestionBoundOutcome(outcome_id=outcome_id, prediction_id=prediction.prediction_id, prediction_content_hash=prediction.content_hash(), prediction_journal_entry_hash=entry_hash, question_ref=prediction.question_ref, question_definition_hash=prediction.question_definition_hash, question_registry_hash=prediction.question_registry_hash, subject_id=prediction.subject_id, answer_kind=prediction.answer_kind, outcome_metric_id=prediction.outcome_metric_id, resolver_policy_id=prediction.resolver_policy_id, resolver_implementation_ref=MIDPOINT_RESOLVER_IMPLEMENTATION_REF, status="UNRESOLVABLE", cutoff_at_ns=prediction.cutoff_at_ns, target_resolves_at_ns=prediction.resolves_at_ns, max_resolution_lag_ns=prediction.max_resolution_lag_ns, decided_at_ns=window_closes + 1, realized_answer=None, resolution_evidence=())
