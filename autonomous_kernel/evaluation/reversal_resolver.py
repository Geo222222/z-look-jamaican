from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence, Tuple

from ..experience.contracts import ExperienceTimescale, MarketExperienceFrame
from ..experience.root_path import EconomicRootPathExperience, RootPathExperienceStore
from ..prediction.factory import PredictionFactoryError, representation_target_price
from ..prediction.question_bound import QuestionBoundPrediction, QuestionPredictionError
from ..prediction.question_journal import QuestionPredictionJournal, validate_question_prediction_journal
from ..questions.evolution import (
    MATERIAL_REVERSAL_MIN_FORWARD_ABS_BPS,
    MATERIAL_REVERSAL_MIN_FORWARD_TO_TRAILING_RATIO,
    MATERIAL_REVERSAL_MIN_TRAILING_ABS_BPS,
    REVERSAL_MATERIAL_RESOLVER_IMPLEMENTATION_REF,
    REVERSAL_MATERIAL_RESOLVER_POLICY_ID,
    REVERSAL_QUESTION_V1_1_REF,
    REVERSAL_QUESTION_V1_2_REF,
    REVERSAL_ROOT_PATH_RESOLVER_IMPLEMENTATION_REF,
    REVERSAL_ROOT_PATH_RESOLVER_POLICY_ID,
)
from ..representation.contracts import RepresentationFrame
from .question_outcome import (
    QuestionBoundOutcome,
    ResolutionEvidenceRef,
    build_question_outcome_id,
)
from .question_resolvers import QuestionOutcomePendingError, QuestionResolverError


SECOND = 1_000_000_000
TRAILING_WINDOW_NS = 60 * SECOND
TRAILING_GRID_NS = 10 * SECOND
BPS = Decimal("10000")


class ReversalResolverError(QuestionResolverError):
    pass


def _journaled_prediction(root: Path, prediction_id: str) -> Tuple[QuestionBoundPrediction, str, int]:
    errors = validate_question_prediction_journal(root)
    if errors:
        raise ReversalResolverError("question prediction journal invalid: " + "; ".join(errors))
    matches = [
        entry
        for entry in QuestionPredictionJournal(root).entries()
        if entry.get("prediction", {}).get("prediction_id") == prediction_id
    ]
    if len(matches) != 1:
        raise ReversalResolverError("prediction must appear exactly once in question prediction journal")
    entry = matches[0]
    try:
        prediction = QuestionBoundPrediction.from_wire(entry.get("prediction", {}))
    except (QuestionPredictionError, ValueError, TypeError) as exc:
        raise ReversalResolverError("journaled question prediction is invalid: %s" % exc) from exc
    return prediction, str(entry.get("entry_hash", "")), int(entry.get("journaled_at_ns", -1))


def _bound_market_experience(
    prediction: QuestionBoundPrediction,
    experience: MarketExperienceFrame,
) -> None:
    refs = [
        ref
        for ref in prediction.artifact_refs
        if ref.artifact_type == "MARKET_EXPERIENCE" and ref.artifact_id == experience.experience_id
    ]
    if len(refs) != 1:
        raise ReversalResolverError("prediction does not bind the supplied Market Experience")
    ref = refs[0]
    if ref.content_hash != experience.content_hash():
        raise ReversalResolverError("Market Experience content hash differs from prediction lineage")
    if ref.known_at_ns != experience.known_at_ns or ref.status != experience.status:
        raise ReversalResolverError("Market Experience prediction reference differs from recovered experience")
    if experience.status != "QUALIFIED":
        raise ReversalResolverError("prospective reversal requires qualified Market Experience")
    if experience.economic_root_id != prediction.subject_id:
        raise ReversalResolverError("Market Experience economic root differs from prediction subject")
    if experience.cutoff_at_ns != prediction.cutoff_at_ns:
        raise ReversalResolverError("Market Experience cutoff differs from prediction cutoff")


def _load_bound_root_path(
    root: Path,
    prediction: QuestionBoundPrediction,
    experience: MarketExperienceFrame,
) -> EconomicRootPathExperience:
    refs = [ref for ref in prediction.artifact_refs if ref.artifact_type == "ECONOMIC_ROOT_PATH"]
    if len(refs) != 1:
        raise ReversalResolverError("reversal prediction requires exactly one Economic Root Path")
    ref = refs[0]
    store = RootPathExperienceStore(root)
    ok, errors = store.verify()
    if not ok:
        raise ReversalResolverError("Economic Root Path store invalid: " + "; ".join(errors))
    try:
        path_state = store.load(ref.artifact_id)
    except Exception as exc:
        raise ReversalResolverError("prediction-bound Economic Root Path is not durably recoverable") from exc
    if path_state.content_hash() != ref.content_hash:
        raise ReversalResolverError("Economic Root Path content hash differs from prediction lineage")
    if path_state.known_at_ns != ref.known_at_ns or path_state.status != ref.status:
        raise ReversalResolverError("Economic Root Path prediction reference differs from recovered path")
    if path_state.status != "QUALIFIED" or ref.status != "QUALIFIED":
        raise ReversalResolverError("prospective reversal requires qualified Economic Root Path")
    if path_state.economic_root_id != prediction.subject_id:
        raise ReversalResolverError("Economic Root Path subject differs from prediction")
    if path_state.timescale is not ExperienceTimescale.SHORT:
        raise ReversalResolverError("reversal requires SHORT Economic Root Path")
    if path_state.cutoff_at_ns != prediction.cutoff_at_ns:
        raise ReversalResolverError("Economic Root Path cutoff differs from prediction cutoff")
    if path_state.window_start_ns != prediction.cutoff_at_ns - TRAILING_WINDOW_NS:
        raise ReversalResolverError("Economic Root Path trailing window differs from reversal contract")
    if path_state.grid_interval_ns != TRAILING_GRID_NS:
        raise ReversalResolverError("Economic Root Path grid differs from reversal contract")
    if path_state.missing_target_ns:
        raise ReversalResolverError("qualified reversal root path cannot have missing targets")
    if path_state.baseline_experience_id != experience.experience_id or path_state.baseline_experience_hash != experience.content_hash():
        raise ReversalResolverError("Economic Root Path does not bind exact prediction-time Market Experience")
    expected_targets = tuple(range(path_state.window_start_ns, path_state.cutoff_at_ns + 1, TRAILING_GRID_NS))
    if tuple(point.target_at_ns for point in path_state.points) != expected_targets:
        raise ReversalResolverError("Economic Root Path does not contain exact reversal trailing grid")
    if not path_state.points:
        raise ReversalResolverError("Economic Root Path has no trailing evidence")
    return path_state


def _select_forward_frame(
    prediction: QuestionBoundPrediction,
    path_state: EconomicRootPathExperience,
    frames: Sequence[RepresentationFrame],
) -> Optional[RepresentationFrame]:
    upper = prediction.resolves_at_ns + prediction.max_resolution_lag_ns
    eligible = [
        frame
        for frame in frames
        if frame.instrument.canonical_id == path_state.instrument_id
        and frame.instrument.market_type == "SPOT"
        and frame.representation_type == "INSTRUMENT_STATE"
        and frame.status == "QUALIFIED"
        and frame.known_at_ns >= prediction.resolves_at_ns
        and frame.known_at_ns <= upper
    ]
    if not eligible:
        return None
    return sorted(
        eligible,
        key=lambda item: (item.known_at_ns, item.cutoff_at_ns, item.frame_id, item.content_hash()),
    )[0]


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _material_reversal(trailing_return: Decimal, forward_return: Decimal) -> int:
    """Apply the immutable v1.2 materiality contract in basis-point space."""
    trailing_bps = trailing_return * BPS
    forward_bps = forward_return * BPS
    trailing_floor = Decimal(MATERIAL_REVERSAL_MIN_TRAILING_ABS_BPS)
    forward_floor = Decimal(MATERIAL_REVERSAL_MIN_FORWARD_ABS_BPS)
    relative_floor = Decimal(MATERIAL_REVERSAL_MIN_FORWARD_TO_TRAILING_RATIO)

    if abs(trailing_bps) < trailing_floor:
        return 0
    trailing_sign = _sign(trailing_return)
    forward_sign = _sign(forward_return)
    if trailing_sign == 0 or forward_sign == 0 or trailing_sign == forward_sign:
        return 0
    material_forward_threshold = max(forward_floor, abs(trailing_bps) * relative_floor)
    return 1 if abs(forward_bps) >= material_forward_threshold else 0


def _resolver_contract(prediction: QuestionBoundPrediction) -> Tuple[str, str]:
    if prediction.question_ref == REVERSAL_QUESTION_V1_1_REF:
        return REVERSAL_ROOT_PATH_RESOLVER_POLICY_ID, REVERSAL_ROOT_PATH_RESOLVER_IMPLEMENTATION_REF
    if prediction.question_ref == REVERSAL_QUESTION_V1_2_REF:
        return REVERSAL_MATERIAL_RESOLVER_POLICY_ID, REVERSAL_MATERIAL_RESOLVER_IMPLEMENTATION_REF
    raise ReversalResolverError("prediction is not a supported reversal question version")


def resolve_reversal_question(
    root: Path,
    prediction_id: str,
    *,
    baseline_experience: MarketExperienceFrame,
    forward_frames: Sequence[RepresentationFrame],
    now_at_ns: int,
) -> QuestionBoundOutcome:
    """Resolve sign-only v1.1 or material-reversal v1.2 from bound causal evidence.

    Both versions read the trailing return only from the exact Economic Root Path
    already bound into the journaled prediction. The forward endpoint is the
    first qualified representation for that exact canonical spot instrument at
    or after T+60s inside the declared resolution lag. v1.2 additionally applies
    fixed materiality thresholds preregistered in the question definition.
    """
    root = root.resolve()
    prediction, prediction_entry_hash, journaled_at_ns = _journaled_prediction(root, prediction_id)
    expected_policy, implementation_ref = _resolver_contract(prediction)
    if prediction.resolver_policy_id != expected_policy:
        raise ReversalResolverError("prediction resolver policy differs from reversal resolver version")
    if prediction.mode == "PROSPECTIVE_SHADOW" and journaled_at_ns >= prediction.resolves_at_ns:
        raise ReversalResolverError("late-journaled prospective prediction cannot become forward evidence")
    now = int(now_at_ns)
    if now < 0:
        raise ReversalResolverError("now_at_ns must be non-negative")

    _bound_market_experience(prediction, baseline_experience)
    path_state = _load_bound_root_path(root, prediction, baseline_experience)
    first_point = path_state.points[0]
    cutoff_point = path_state.points[-1]
    trailing_return = Decimal(cutoff_point.midpoint) / Decimal(first_point.midpoint) - Decimal("1")

    selected = _select_forward_frame(prediction, path_state, forward_frames)
    outcome_id = build_question_outcome_id(
        prediction.prediction_id,
        prediction.resolver_policy_id,
        implementation_ref,
    )
    if selected is not None:
        if now < selected.known_at_ns:
            raise QuestionOutcomePendingError("selected reversal forward evidence is not knowable at now_at_ns")
        try:
            realized_price_text, _ = representation_target_price(selected)
        except PredictionFactoryError as exc:
            raise ReversalResolverError("selected forward representation has no qualified midpoint: %s" % exc) from exc
        forward_return = Decimal(realized_price_text) / Decimal(cutoff_point.midpoint) - Decimal("1")
        if prediction.question_ref == REVERSAL_QUESTION_V1_2_REF:
            reversed_value = _material_reversal(trailing_return, forward_return)
        else:
            trailing_sign = _sign(trailing_return)
            forward_sign = _sign(forward_return)
            reversed_value = 1 if trailing_sign != 0 and forward_sign != 0 and trailing_sign != forward_sign else 0
        return QuestionBoundOutcome(
            outcome_id=outcome_id,
            prediction_id=prediction.prediction_id,
            prediction_content_hash=prediction.content_hash(),
            prediction_journal_entry_hash=prediction_entry_hash,
            question_ref=prediction.question_ref,
            question_definition_hash=prediction.question_definition_hash,
            question_registry_hash=prediction.question_registry_hash,
            subject_id=prediction.subject_id,
            answer_kind=prediction.answer_kind,
            outcome_metric_id=prediction.outcome_metric_id,
            resolver_policy_id=prediction.resolver_policy_id,
            resolver_implementation_ref=implementation_ref,
            status="RESOLVED",
            cutoff_at_ns=prediction.cutoff_at_ns,
            target_resolves_at_ns=prediction.resolves_at_ns,
            max_resolution_lag_ns=prediction.max_resolution_lag_ns,
            decided_at_ns=selected.known_at_ns,
            realized_answer={"value": reversed_value},
            resolution_evidence=(
                ResolutionEvidenceRef(
                    evidence_family="ECONOMIC_ROOT_PATH",
                    artifact_type="ECONOMIC_ROOT_PATH",
                    artifact_id=path_state.root_path_id,
                    content_hash=path_state.content_hash(),
                    known_at_ns=path_state.known_at_ns,
                    role="BASELINE",
                    subject_ids=(prediction.subject_id,),
                ),
                ResolutionEvidenceRef(
                    evidence_family="SPOT_MICROSTRUCTURE",
                    artifact_type="REPRESENTATION_FRAME",
                    artifact_id=selected.frame_id,
                    content_hash=selected.content_hash(),
                    known_at_ns=selected.known_at_ns,
                    role="FORWARD",
                    subject_ids=(prediction.subject_id,),
                ),
            ),
        )

    window_closes = prediction.resolves_at_ns + prediction.max_resolution_lag_ns
    if now <= window_closes:
        raise QuestionOutcomePendingError("reversal resolution window remains open and no eligible forward frame exists")
    return QuestionBoundOutcome(
        outcome_id=outcome_id,
        prediction_id=prediction.prediction_id,
        prediction_content_hash=prediction.content_hash(),
        prediction_journal_entry_hash=prediction_entry_hash,
        question_ref=prediction.question_ref,
        question_definition_hash=prediction.question_definition_hash,
        question_registry_hash=prediction.question_registry_hash,
        subject_id=prediction.subject_id,
        answer_kind=prediction.answer_kind,
        outcome_metric_id=prediction.outcome_metric_id,
        resolver_policy_id=prediction.resolver_policy_id,
        resolver_implementation_ref=implementation_ref,
        status="UNRESOLVABLE",
        cutoff_at_ns=prediction.cutoff_at_ns,
        target_resolves_at_ns=prediction.resolves_at_ns,
        max_resolution_lag_ns=prediction.max_resolution_lag_ns,
        decided_at_ns=window_closes + 1,
        realized_answer=None,
        resolution_evidence=(),
    )
