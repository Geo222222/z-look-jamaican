from __future__ import annotations

from decimal import Decimal, localcontext
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from ..experience.contracts import ExperienceTimescale, MarketExperienceFrame
from ..prediction.factory import PredictionFactoryError, representation_target_price
from ..representation.contracts import RepresentationFrame
from .question_outcome import QuestionBoundOutcome, ResolutionEvidenceRef, build_question_outcome_id
from .question_resolvers import (
    QuestionOutcomePendingError,
    QuestionResolverError,
    _baseline_spot_frame,
    _bound_market_experience,
    _journaled_prediction,
)


FIXED_GRID_RESOLVER_POLICY_ID = "QUALIFIED_FIXED_GRID_AGGREGATE_MIDPOINT_PATH_V1"
FIXED_GRID_RESOLVER_IMPLEMENTATION_REF = "autonomous_kernel.evaluation.question_path_resolvers.fixed_grid_v1"
SUPPORTED_FIXED_GRID_QUESTIONS = {
    "ECONOMIC_ROOT_VOLATILITY_60S": ExperienceTimescale.SHORT,
    "ECONOMIC_ROOT_FRAGILITY_MAE_60S": ExperienceTimescale.MICRO,
}


def _price(frame: RepresentationFrame) -> Decimal:
    try:
        value, _ = representation_target_price(frame)
    except PredictionFactoryError as exc:
        raise QuestionResolverError("representation has no qualified midpoint: %s" % exc) from exc
    return Decimal(value)


def _resolution_grid(prediction) -> Tuple[int, ...]:
    # Both first catalog path questions preregister a 5-second resolution grid.
    # The grid width is part of the Question Definition parameters; prediction
    # identity binds that definition hash, so this implementation may support
    # only the exact v1 questions listed above.
    grid_ns = 5_000_000_000
    if prediction.horizon_ns % grid_ns != 0:
        raise QuestionResolverError("fixed-grid question horizon is not divisible by 5-second grid")
    return tuple(prediction.cutoff_at_ns + offset for offset in range(grid_ns, prediction.horizon_ns + 1, grid_ns))


def _select_grid_frames(prediction, baseline: RepresentationFrame, frames: Sequence[RepresentationFrame]) -> Tuple[RepresentationFrame, ...]:
    grid = _resolution_grid(prediction)
    selected: List[RepresentationFrame] = []
    used = set()
    for index, target in enumerate(grid):
        # Non-final slots stop one nanosecond before the next grid point so the
        # same observation can never satisfy two adjacent target times.
        policy_upper = target + prediction.max_resolution_lag_ns
        slot_upper = policy_upper if index == len(grid) - 1 else min(policy_upper, grid[index + 1] - 1)
        eligible = [
            frame
            for frame in frames
            if frame.frame_id not in used
            and frame.instrument == baseline.instrument
            and frame.representation_type == "INSTRUMENT_STATE"
            and frame.status == "QUALIFIED"
            and frame.known_at_ns >= target
            and frame.known_at_ns <= slot_upper
        ]
        if not eligible:
            return ()
        winner = sorted(eligible, key=lambda item: (item.known_at_ns, item.cutoff_at_ns, item.frame_id, item.content_hash()))[0]
        selected.append(winner)
        used.add(winner.frame_id)
    return tuple(selected)


def _volatility_bps(baseline: Decimal, path: Sequence[Decimal]) -> Decimal:
    prices = [baseline] + list(path)
    returns = [
        (prices[index] / prices[index - 1] - Decimal("1")) * Decimal("10000")
        for index in range(1, len(prices))
    ]
    if not returns:
        raise QuestionResolverError("volatility resolver requires forward returns")
    with localcontext() as context:
        context.prec = 50
        mean = sum(returns, Decimal("0")) / Decimal(len(returns))
        variance = sum((value - mean) ** 2 for value in returns) / Decimal(len(returns))
        return +variance.sqrt()


def _mae_bps(baseline: Decimal, path: Sequence[Decimal]) -> Decimal:
    returns = [(price / baseline - Decimal("1")) * Decimal("10000") for price in path]
    minimum = min(returns)
    return max(Decimal("0"), -minimum)


def resolve_fixed_grid_question(
    root: Path,
    prediction_id: str,
    *,
    baseline_experience: MarketExperienceFrame,
    baseline_frames: Sequence[RepresentationFrame],
    forward_frames: Sequence[RepresentationFrame],
    now_at_ns: int,
) -> QuestionBoundOutcome:
    root = root.resolve()
    prediction, entry_hash, journaled_at_ns = _journaled_prediction(root, prediction_id)
    question_id = prediction.question_ref.split("@", 1)[0]
    baseline_timescale = SUPPORTED_FIXED_GRID_QUESTIONS.get(question_id)
    if baseline_timescale is None:
        raise QuestionResolverError("prediction question is not supported by fixed-grid resolver")
    if prediction.resolver_policy_id != FIXED_GRID_RESOLVER_POLICY_ID:
        raise QuestionResolverError("prediction resolver policy differs from fixed-grid resolver")
    if prediction.mode == "PROSPECTIVE_SHADOW" and journaled_at_ns >= prediction.resolves_at_ns:
        raise QuestionResolverError("late-journaled prospective prediction cannot become forward evidence")
    now = int(now_at_ns)
    if now < 0:
        raise QuestionResolverError("now_at_ns must be non-negative")

    _bound_market_experience(prediction, baseline_experience)
    baseline = _baseline_spot_frame(
        prediction,
        baseline_experience,
        baseline_frames,
        timescale=baseline_timescale,
    )
    baseline_price = _price(baseline)
    selected = _select_grid_frames(prediction, baseline, forward_frames)
    final_close = prediction.resolves_at_ns + prediction.max_resolution_lag_ns
    outcome_id = build_question_outcome_id(
        prediction.prediction_id,
        prediction.resolver_policy_id,
        FIXED_GRID_RESOLVER_IMPLEMENTATION_REF,
    )

    if not selected:
        if now <= final_close:
            raise QuestionOutcomePendingError("fixed-grid resolution remains open and required grid evidence is incomplete")
        return QuestionBoundOutcome(
            outcome_id=outcome_id,
            prediction_id=prediction.prediction_id,
            prediction_content_hash=prediction.content_hash(),
            prediction_journal_entry_hash=entry_hash,
            question_ref=prediction.question_ref,
            question_definition_hash=prediction.question_definition_hash,
            question_registry_hash=prediction.question_registry_hash,
            subject_id=prediction.subject_id,
            answer_kind=prediction.answer_kind,
            outcome_metric_id=prediction.outcome_metric_id,
            resolver_policy_id=prediction.resolver_policy_id,
            resolver_implementation_ref=FIXED_GRID_RESOLVER_IMPLEMENTATION_REF,
            status="UNRESOLVABLE",
            cutoff_at_ns=prediction.cutoff_at_ns,
            target_resolves_at_ns=prediction.resolves_at_ns,
            max_resolution_lag_ns=prediction.max_resolution_lag_ns,
            decided_at_ns=final_close + 1,
            realized_answer=None,
            resolution_evidence=(),
        )

    max_known = max(frame.known_at_ns for frame in selected)
    if now < max_known:
        raise QuestionOutcomePendingError("fixed-grid evidence is not fully knowable at now_at_ns")
    path_prices = tuple(_price(frame) for frame in selected)
    if question_id == "ECONOMIC_ROOT_VOLATILITY_60S":
        realized = {"value": format(_volatility_bps(baseline_price, path_prices), "f")}
    elif question_id == "ECONOMIC_ROOT_FRAGILITY_MAE_60S":
        realized = {"value": format(_mae_bps(baseline_price, path_prices), "f")}
    else:
        raise QuestionResolverError("unsupported fixed-grid question")

    evidence = [
        ResolutionEvidenceRef(
            evidence_family="SPOT_MICROSTRUCTURE",
            artifact_type="REPRESENTATION_FRAME",
            artifact_id=baseline.frame_id,
            content_hash=baseline.content_hash(),
            known_at_ns=baseline.known_at_ns,
            role="BASELINE",
            subject_ids=(prediction.subject_id,),
        )
    ]
    evidence.extend(
        ResolutionEvidenceRef(
            evidence_family="SPOT_MICROSTRUCTURE",
            artifact_type="REPRESENTATION_FRAME",
            artifact_id=frame.frame_id,
            content_hash=frame.content_hash(),
            known_at_ns=frame.known_at_ns,
            role="FORWARD",
            subject_ids=(prediction.subject_id,),
        )
        for frame in selected
    )
    return QuestionBoundOutcome(
        outcome_id=outcome_id,
        prediction_id=prediction.prediction_id,
        prediction_content_hash=prediction.content_hash(),
        prediction_journal_entry_hash=entry_hash,
        question_ref=prediction.question_ref,
        question_definition_hash=prediction.question_definition_hash,
        question_registry_hash=prediction.question_registry_hash,
        subject_id=prediction.subject_id,
        answer_kind=prediction.answer_kind,
        outcome_metric_id=prediction.outcome_metric_id,
        resolver_policy_id=prediction.resolver_policy_id,
        resolver_implementation_ref=FIXED_GRID_RESOLVER_IMPLEMENTATION_REF,
        status="RESOLVED",
        cutoff_at_ns=prediction.cutoff_at_ns,
        target_resolves_at_ns=prediction.resolves_at_ns,
        max_resolution_lag_ns=prediction.max_resolution_lag_ns,
        decided_at_ns=max_known,
        realized_answer=realized,
        resolution_evidence=tuple(evidence),
    )
