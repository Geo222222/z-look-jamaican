from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

from ..experience.contracts import MarketExperienceFrame
from ..representation.contracts import RepresentationFrame
from .question_outcome import QuestionBoundOutcome, ResolutionEvidenceRef, build_question_outcome_id
from .question_resolvers import (
    QuestionOutcomePendingError,
    QuestionResolverError,
    _baseline_spot_frame,
    _bound_market_experience,
    _journaled_prediction,
)


LIQUIDITY_RESOLVER_POLICY_ID = "FIRST_QUALIFIED_BOOK_STATE_AT_OR_AFTER_TARGET_V1"
LIQUIDITY_RESOLVER_IMPLEMENTATION_REF = "autonomous_kernel.evaluation.liquidity_resolver.liquidity_v1"
SUPPORTED_LIQUIDITY_QUESTION = "ECONOMIC_ROOT_LIQUIDITY_DETERIORATION_30S"
DEPTH_BAND_BPS = "10"


def _decimal(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise QuestionResolverError("%s must be decimal-compatible" % field) from exc
    if not number.is_finite():
        raise QuestionResolverError("%s must be finite" % field)
    return number


def _qualified_book_metrics(
    frame: RepresentationFrame,
    *,
    frozen_venues: Optional[Sequence[str]] = None,
) -> Tuple[Tuple[str, ...], Decimal, Decimal]:
    """Return frozen-venue cross spread and quote-notional depth at +/-10 bps.

    Venue identity is part of the measurement contract. A future frame may
    contain additional venues, but they cannot improve the measured outcome;
    every baseline venue must remain independently qualified and measurable.
    """
    venue_states = frame.state.get("venue_states")
    if not isinstance(venue_states, Mapping) or not venue_states:
        raise QuestionResolverError("liquidity resolver requires venue book state")

    if frozen_venues is None:
        eligible = []
        for raw_venue, raw_state in venue_states.items():
            if not isinstance(raw_state, Mapping):
                continue
            book = raw_state.get("book")
            if not isinstance(book, Mapping) or book.get("status") != "QUALIFIED":
                continue
            bands = book.get("depth_bands_bps")
            if isinstance(bands, Mapping) and isinstance(bands.get(DEPTH_BAND_BPS), Mapping):
                eligible.append(str(raw_venue))
        venues = tuple(sorted(set(eligible)))
        if not venues:
            raise QuestionResolverError("liquidity resolver requires qualified 10-bps book depth")
    else:
        venues = tuple(str(item) for item in frozen_venues)
        if not venues or len(set(venues)) != len(venues):
            raise QuestionResolverError("frozen liquidity venue set must be unique and non-empty")

    best_bids = []
    best_asks = []
    total_quote_depth = Decimal("0")
    for venue in venues:
        raw_state = venue_states.get(venue)
        if not isinstance(raw_state, Mapping):
            raise QuestionResolverError("frozen liquidity venue missing from frame: %s" % venue)
        book = raw_state.get("book")
        if not isinstance(book, Mapping) or book.get("status") != "QUALIFIED":
            raise QuestionResolverError("frozen liquidity venue is not qualified: %s" % venue)
        bands = book.get("depth_bands_bps")
        band = bands.get(DEPTH_BAND_BPS) if isinstance(bands, Mapping) else None
        if not isinstance(band, Mapping):
            raise QuestionResolverError("frozen liquidity venue lacks 10-bps depth: %s" % venue)
        bid = _decimal(book.get("best_bid"), "%s best_bid" % venue)
        ask = _decimal(book.get("best_ask"), "%s best_ask" % venue)
        bid_depth = _decimal(band.get("bid_quote_notional"), "%s bid_quote_notional" % venue)
        ask_depth = _decimal(band.get("ask_quote_notional"), "%s ask_quote_notional" % venue)
        if bid <= 0 or ask <= 0 or bid_depth < 0 or ask_depth < 0:
            raise QuestionResolverError("liquidity book metrics must be positive prices and non-negative depth")
        best_bids.append(bid)
        best_asks.append(ask)
        total_quote_depth += bid_depth + ask_depth

    cross_bid = max(best_bids)
    cross_ask = min(best_asks)
    if cross_bid >= cross_ask:
        raise QuestionResolverError("frozen venue set is crossed or locked")
    midpoint = (cross_bid + cross_ask) / Decimal("2")
    spread_bps = (cross_ask - cross_bid) / midpoint * Decimal("10000")
    return venues, spread_bps, total_quote_depth


def liquidity_cutoff_is_examinable(frame: RepresentationFrame) -> bool:
    """True iff the cutoff frame can legally support the frozen resolver baseline."""
    if frame.representation_type != "INSTRUMENT_STATE":
        return False
    if frame.status != "QUALIFIED":
        return False
    try:
        venues, _spread, _depth = _qualified_book_metrics(frame)
    except QuestionResolverError:
        return False
    return bool(venues)


def _select_forward_liquidity_frame(
    prediction,
    baseline: RepresentationFrame,
    frozen_venues: Sequence[str],
    frames: Sequence[RepresentationFrame],
) -> Optional[Tuple[RepresentationFrame, Decimal, Decimal]]:
    upper = prediction.resolves_at_ns + prediction.max_resolution_lag_ns
    eligible = sorted(
        (
            frame
            for frame in frames
            if frame.instrument == baseline.instrument
            and frame.representation_type == "INSTRUMENT_STATE"
            and frame.status == "QUALIFIED"
            and frame.known_at_ns >= prediction.resolves_at_ns
            and frame.known_at_ns <= upper
        ),
        key=lambda item: (item.known_at_ns, item.cutoff_at_ns, item.frame_id, item.content_hash()),
    )
    for frame in eligible:
        try:
            _, spread, depth = _qualified_book_metrics(frame, frozen_venues=frozen_venues)
        except QuestionResolverError:
            continue
        return frame, spread, depth
    return None


def resolve_liquidity_question(
    root: Path,
    prediction_id: str,
    *,
    baseline_experience: MarketExperienceFrame,
    baseline_frames: Sequence[RepresentationFrame],
    forward_frames: Sequence[RepresentationFrame],
    now_at_ns: int,
) -> QuestionBoundOutcome:
    """Resolve executable-liquidity deterioration without venue/unit substitution.

    v1 freezes the qualified venue set present in the prediction-bound MICRO
    experience. Spread and +/-10-bps depth are recomputed over that exact set at
    the future endpoint. Depth is quote notional, never base units. Deterioration
    is true only when spread widens AND quote-notional depth falls.
    """
    root = root.resolve()
    prediction, entry_hash, journaled_at_ns = _journaled_prediction(root, prediction_id)
    if prediction.question_ref.split("@", 1)[0] != SUPPORTED_LIQUIDITY_QUESTION:
        raise QuestionResolverError("prediction question is not supported by liquidity resolver")
    if prediction.resolver_policy_id != LIQUIDITY_RESOLVER_POLICY_ID:
        raise QuestionResolverError("prediction resolver policy differs from liquidity resolver")
    if prediction.mode == "PROSPECTIVE_SHADOW" and journaled_at_ns >= prediction.resolves_at_ns:
        raise QuestionResolverError("late-journaled prospective prediction cannot become forward evidence")
    now = int(now_at_ns)
    if now < 0:
        raise QuestionResolverError("now_at_ns must be non-negative")

    _bound_market_experience(prediction, baseline_experience)
    baseline = _baseline_spot_frame(prediction, baseline_experience, baseline_frames)
    frozen_venues, baseline_spread, baseline_depth = _qualified_book_metrics(baseline)

    selected = _select_forward_liquidity_frame(
        prediction,
        baseline,
        frozen_venues,
        forward_frames,
    )
    outcome_id = build_question_outcome_id(
        prediction.prediction_id,
        prediction.resolver_policy_id,
        LIQUIDITY_RESOLVER_IMPLEMENTATION_REF,
    )
    if selected is not None:
        frame, forward_spread, forward_depth = selected
        if now < frame.known_at_ns:
            raise QuestionOutcomePendingError("selected forward liquidity evidence is not knowable at now_at_ns")
        deteriorated = 1 if forward_spread > baseline_spread and forward_depth < baseline_depth else 0
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
            resolver_implementation_ref=LIQUIDITY_RESOLVER_IMPLEMENTATION_REF,
            status="RESOLVED",
            cutoff_at_ns=prediction.cutoff_at_ns,
            target_resolves_at_ns=prediction.resolves_at_ns,
            max_resolution_lag_ns=prediction.max_resolution_lag_ns,
            decided_at_ns=frame.known_at_ns,
            realized_answer={"value": deteriorated},
            resolution_evidence=(
                ResolutionEvidenceRef(
                    evidence_family="SPOT_MICROSTRUCTURE",
                    artifact_type="REPRESENTATION_FRAME",
                    artifact_id=baseline.frame_id,
                    content_hash=baseline.content_hash(),
                    known_at_ns=baseline.known_at_ns,
                    role="BASELINE",
                    subject_ids=(prediction.subject_id,),
                ),
                ResolutionEvidenceRef(
                    evidence_family="SPOT_MICROSTRUCTURE",
                    artifact_type="REPRESENTATION_FRAME",
                    artifact_id=frame.frame_id,
                    content_hash=frame.content_hash(),
                    known_at_ns=frame.known_at_ns,
                    role="FORWARD",
                    subject_ids=(prediction.subject_id,),
                ),
            ),
        )

    window_closes = prediction.resolves_at_ns + prediction.max_resolution_lag_ns
    if now <= window_closes:
        raise QuestionOutcomePendingError("liquidity resolution window remains open and no eligible book frame exists")
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
        resolver_implementation_ref=LIQUIDITY_RESOLVER_IMPLEMENTATION_REF,
        status="UNRESOLVABLE",
        cutoff_at_ns=prediction.cutoff_at_ns,
        target_resolves_at_ns=prediction.resolves_at_ns,
        max_resolution_lag_ns=prediction.max_resolution_lag_ns,
        decided_at_ns=window_closes + 1,
        realized_answer=None,
        resolution_evidence=(),
    )
