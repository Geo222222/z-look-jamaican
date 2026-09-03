from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..observation.instruments import CanonicalInstrument
from ..operations import canonical_hash


PREDICTION_SCHEMA_VERSION = "1.0"
PREDICTION_MODES = {"PROSPECTIVE_SHADOW", "HISTORICAL_REPLAY"}
TARGET_METRICS = {"ZLJ_AGGREGATE_MIDPOINT_RETURN_BPS_V1"}
EVIDENCE_CLASSES = {"FORWARD_EVALUABLE", "RESEARCH_ONLY"}
REPRESENTATION_STATUSES = {"QUALIFIED", "DEGRADED"}


class PredictionContractError(ValueError):
    pass


def _decimal_text(value: Any, field: str) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PredictionContractError("%s must be decimal-compatible" % field) from exc
    if not number.is_finite():
        raise PredictionContractError("%s must be finite" % field)
    return format(number, "f")


def _probability(value: Any) -> str:
    text = _decimal_text(value, "probability_positive")
    number = Decimal(text)
    if number < 0 or number > 1:
        raise PredictionContractError("probability_positive must be between 0 and 1")
    return text


def _refs(values: Sequence[str], field: str) -> Tuple[str, ...]:
    result = tuple(str(value) for value in values)
    if not result or any(not value for value in result) or len(set(result)) != len(result):
        raise PredictionContractError("%s must contain unique non-empty values" % field)
    return result


@dataclass(frozen=True)
class Prediction:
    prediction_id: str
    mode: str
    evidence_class: str
    instrument: CanonicalInstrument
    representation_frame_id: str
    representation_content_hash: str
    representation_status: str
    prediction_at_ns: int
    created_at_ns: int
    horizon_ns: int
    resolves_at_ns: int
    target_metric: str
    reference_price: str
    reference_price_source: str
    expected_move_bps: str
    probability_positive: str
    interval_low_bps: Optional[str]
    interval_high_bps: Optional[str]
    model_refs: Tuple[str, ...]
    schema_version: str = PREDICTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PREDICTION_SCHEMA_VERSION:
            raise PredictionContractError("unsupported prediction schema")
        if not self.prediction_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in self.prediction_id):
            raise PredictionContractError("prediction_id must be non-empty and file-safe")
        if self.mode not in PREDICTION_MODES:
            raise PredictionContractError("prediction mode is invalid")
        if self.evidence_class not in EVIDENCE_CLASSES:
            raise PredictionContractError("prediction evidence_class is invalid")
        if self.target_metric not in TARGET_METRICS:
            raise PredictionContractError("target_metric is invalid")
        if self.representation_status not in REPRESENTATION_STATUSES:
            raise PredictionContractError("prediction cannot depend on unavailable representation")
        if not self.representation_frame_id or not self.reference_price_source:
            raise PredictionContractError("representation and reference-price identity are required")
        if len(self.representation_content_hash) != 64:
            raise PredictionContractError("representation_content_hash must be SHA-256 hex")
        try:
            int(self.representation_content_hash, 16)
        except ValueError as exc:
            raise PredictionContractError("representation_content_hash must be hexadecimal") from exc
        if self.prediction_at_ns < 0 or self.created_at_ns < 0 or self.horizon_ns <= 0:
            raise PredictionContractError("prediction timing is invalid")
        if self.resolves_at_ns != self.prediction_at_ns + self.horizon_ns:
            raise PredictionContractError("resolves_at_ns must equal prediction_at_ns + horizon_ns")
        if self.mode == "PROSPECTIVE_SHADOW":
            if self.evidence_class != "FORWARD_EVALUABLE":
                raise PredictionContractError("prospective prediction must be FORWARD_EVALUABLE")
            if self.representation_status != "QUALIFIED":
                raise PredictionContractError("prospective prediction requires QUALIFIED representation")
            if self.created_at_ns > self.resolves_at_ns:
                raise PredictionContractError("prospective prediction cannot be created after its resolution horizon")
        elif self.evidence_class != "RESEARCH_ONLY":
            raise PredictionContractError("historical replay must be RESEARCH_ONLY")
        reference_price = Decimal(_decimal_text(self.reference_price, "reference_price"))
        if reference_price <= 0:
            raise PredictionContractError("reference_price must be positive")
        _decimal_text(self.expected_move_bps, "expected_move_bps")
        _probability(self.probability_positive)
        low = None if self.interval_low_bps is None else Decimal(_decimal_text(self.interval_low_bps, "interval_low_bps"))
        high = None if self.interval_high_bps is None else Decimal(_decimal_text(self.interval_high_bps, "interval_high_bps"))
        if (low is None) != (high is None):
            raise PredictionContractError("prediction interval requires both low and high bounds")
        if low is not None and high is not None and low > high:
            raise PredictionContractError("prediction interval low cannot exceed high")
        _refs(self.model_refs, "model_refs")

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "prediction_id": self.prediction_id,
            "mode": self.mode,
            "evidence_class": self.evidence_class,
            "instrument": self.instrument.to_wire(),
            "representation": {
                "frame_id": self.representation_frame_id,
                "content_hash": self.representation_content_hash,
                "status": self.representation_status,
            },
            "timing": {
                "prediction_at_ns": int(self.prediction_at_ns),
                "created_at_ns": int(self.created_at_ns),
                "horizon_ns": int(self.horizon_ns),
                "resolves_at_ns": int(self.resolves_at_ns),
            },
            "target": {
                "metric": self.target_metric,
                "reference_price": self.reference_price,
                "reference_price_source": self.reference_price_source,
            },
            "forecast": {
                "expected_move_bps": self.expected_move_bps,
                "probability_positive": self.probability_positive,
                "interval_low_bps": self.interval_low_bps,
                "interval_high_bps": self.interval_high_bps,
            },
            "model_refs": list(self.model_refs),
        }

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        value = self.body()
        value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}
        return value

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "Prediction":
        instrument = value.get("instrument")
        representation = value.get("representation")
        timing = value.get("timing")
        target = value.get("target")
        forecast = value.get("forecast")
        if not all(isinstance(item, Mapping) for item in (instrument, representation, timing, target, forecast)):
            raise PredictionContractError("prediction envelope is malformed")
        item = cls(
            schema_version=str(value.get("schema_version", "")),
            prediction_id=str(value.get("prediction_id", "")),
            mode=str(value.get("mode", "")),
            evidence_class=str(value.get("evidence_class", "")),
            instrument=CanonicalInstrument(
                canonical_id=str(instrument.get("canonical_id", "")),
                asset_class=str(instrument.get("asset_class", "")),
                market_type=str(instrument.get("market_type", "")),
                base_asset=str(instrument.get("base_asset", "")),
                quote_asset=str(instrument.get("quote_asset", "")),
                settlement_asset=instrument.get("settlement_asset"),
                expiry=instrument.get("expiry"),
            ),
            representation_frame_id=str(representation.get("frame_id", "")),
            representation_content_hash=str(representation.get("content_hash", "")),
            representation_status=str(representation.get("status", "")),
            prediction_at_ns=int(timing.get("prediction_at_ns", -1)),
            created_at_ns=int(timing.get("created_at_ns", -1)),
            horizon_ns=int(timing.get("horizon_ns", -1)),
            resolves_at_ns=int(timing.get("resolves_at_ns", -1)),
            target_metric=str(target.get("metric", "")),
            reference_price=str(target.get("reference_price", "")),
            reference_price_source=str(target.get("reference_price_source", "")),
            expected_move_bps=str(forecast.get("expected_move_bps", "")),
            probability_positive=str(forecast.get("probability_positive", "")),
            interval_low_bps=None if forecast.get("interval_low_bps") is None else str(forecast.get("interval_low_bps")),
            interval_high_bps=None if forecast.get("interval_high_bps") is None else str(forecast.get("interval_high_bps")),
            model_refs=tuple(str(ref) for ref in value.get("model_refs", [])),
        )
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("content_hash") != item.content_hash():
            raise PredictionContractError("prediction content hash mismatch")
        return item
