from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from typing import Optional, Sequence, Tuple

from ..representation.contracts import RepresentationFrame
from .contracts import Prediction


class PredictionFactoryError(ValueError):
    pass


def representation_target_price(frame: RepresentationFrame) -> Tuple[str, str]:
    """Return the canonical v1 target price and its explicit derivation rule."""
    aggregate = frame.state.get("aggregate")
    if not isinstance(aggregate, dict):
        raise PredictionFactoryError("representation lacks aggregate state")
    bid = aggregate.get("cross_venue_best_bid")
    ask = aggregate.get("cross_venue_best_ask")
    if aggregate.get("cross_venue_book_state") == "NORMAL" and bid is not None and ask is not None:
        midpoint = (Decimal(str(bid)) + Decimal(str(ask))) / Decimal("2")
        if midpoint <= 0:
            raise PredictionFactoryError("cross-venue midpoint is not positive")
        return format(midpoint, "f"), "CROSS_VENUE_BBO_MIDPOINT_V1"
    mean_midpoint = aggregate.get("mean_venue_midpoint")
    if mean_midpoint is not None:
        midpoint = Decimal(str(mean_midpoint))
        if midpoint <= 0:
            raise PredictionFactoryError("mean venue midpoint is not positive")
        return format(midpoint, "f"), "MEAN_QUALIFIED_VENUE_MIDPOINT_V1"
    raise PredictionFactoryError("representation has no qualified midpoint target")


def _decimal_text(value: object, field: str) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PredictionFactoryError("%s must be decimal-compatible" % field) from exc
    if not number.is_finite():
        raise PredictionFactoryError("%s must be finite" % field)
    return format(number, "f")


def create_prediction(
    frame: RepresentationFrame,
    *,
    mode: str,
    prediction_at_ns: int,
    created_at_ns: int,
    horizon_ns: int,
    expected_move_bps: object,
    probability_positive: object,
    model_refs: Sequence[str],
    interval_low_bps: Optional[object] = None,
    interval_high_bps: Optional[object] = None,
    prediction_id: Optional[str] = None,
) -> Prediction:
    if frame.status == "UNAVAILABLE":
        raise PredictionFactoryError("unavailable representation cannot produce a prediction")
    prediction_time = int(prediction_at_ns)
    if prediction_time < frame.known_at_ns:
        raise PredictionFactoryError("prediction_at_ns cannot precede representation known_at_ns")
    horizon = int(horizon_ns)
    if horizon <= 0:
        raise PredictionFactoryError("horizon_ns must be positive")
    reference_price, reference_source = representation_target_price(frame)
    expected = _decimal_text(expected_move_bps, "expected_move_bps")
    probability = _decimal_text(probability_positive, "probability_positive")
    low = None if interval_low_bps is None else _decimal_text(interval_low_bps, "interval_low_bps")
    high = None if interval_high_bps is None else _decimal_text(interval_high_bps, "interval_high_bps")
    refs = tuple(str(ref) for ref in model_refs)
    if prediction_id is None:
        material = "|".join(
            [
                frame.content_hash(),
                mode,
                str(prediction_time),
                str(horizon),
                expected,
                probability,
                low or "",
                high or "",
            ]
            + list(refs)
        )
        prediction_id = "PRED-%s" % hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    evidence_class = "FORWARD_EVALUABLE" if mode == "PROSPECTIVE_SHADOW" else "RESEARCH_ONLY"
    return Prediction(
        prediction_id=str(prediction_id),
        mode=mode,
        evidence_class=evidence_class,
        instrument=frame.instrument,
        representation_frame_id=frame.frame_id,
        representation_content_hash=frame.content_hash(),
        representation_status=frame.status,
        prediction_at_ns=prediction_time,
        created_at_ns=int(created_at_ns),
        horizon_ns=horizon,
        resolves_at_ns=prediction_time + horizon,
        target_metric="ZLJ_AGGREGATE_MIDPOINT_RETURN_BPS_V1",
        reference_price=reference_price,
        reference_price_source=reference_source,
        expected_move_bps=expected,
        probability_positive=probability,
        interval_low_bps=low,
        interval_high_bps=high,
        model_refs=refs,
    )
