from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Tuple

from ..operations import canonical_hash


OUTCOME_SCHEMA_VERSION = "1.0"
OUTCOME_STATUSES = {"RESOLVED", "UNRESOLVABLE"}
RESOLUTION_POLICY_ID = "FIRST_QUALIFIED_FRAME_AT_OR_AFTER_TARGET_WITHIN_LAG_V1"


class OutcomeContractError(ValueError):
    pass


def _decimal_text(value: Any, field: str) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise OutcomeContractError("%s must be decimal-compatible" % field) from exc
    if not number.is_finite():
        raise OutcomeContractError("%s must be finite" % field)
    return format(number, "f")


def _digest(value: str, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise OutcomeContractError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise OutcomeContractError("%s must be hexadecimal" % field) from exc
    return text


@dataclass(frozen=True)
class PredictionOutcome:
    outcome_id: str
    prediction_id: str
    prediction_content_hash: str
    prediction_journal_entry_hash: str
    evidence_class: str
    target_metric: str
    model_refs: Tuple[str, ...]
    status: str
    target_resolves_at_ns: int
    max_resolution_lag_ns: int
    resolution_policy_id: str
    decided_at_ns: int
    reference_price: str
    reference_price_source: str
    resolution_frame_id: Optional[str]
    resolution_frame_content_hash: Optional[str]
    resolution_known_at_ns: Optional[int]
    realized_price: Optional[str]
    realized_price_source: Optional[str]
    realized_return_bps: Optional[str]
    forecast_error_bps: Optional[str]
    actual_positive: Optional[int]
    schema_version: str = OUTCOME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != OUTCOME_SCHEMA_VERSION:
            raise OutcomeContractError("unsupported outcome schema")
        if not self.outcome_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in self.outcome_id):
            raise OutcomeContractError("outcome_id must be non-empty and file-safe")
        if not self.prediction_id:
            raise OutcomeContractError("prediction_id is required")
        _digest(self.prediction_content_hash, "prediction_content_hash")
        _digest(self.prediction_journal_entry_hash, "prediction_journal_entry_hash")
        if self.status not in OUTCOME_STATUSES:
            raise OutcomeContractError("outcome status is invalid")
        if self.resolution_policy_id != RESOLUTION_POLICY_ID:
            raise OutcomeContractError("resolution policy is invalid")
        if self.target_resolves_at_ns < 0 or self.max_resolution_lag_ns < 0 or self.decided_at_ns < 0:
            raise OutcomeContractError("outcome timing is invalid")
        reference = Decimal(_decimal_text(self.reference_price, "reference_price"))
        if reference <= 0 or not self.reference_price_source:
            raise OutcomeContractError("positive reference price and source are required")
        if not self.model_refs or any(not ref for ref in self.model_refs) or len(set(self.model_refs)) != len(self.model_refs):
            raise OutcomeContractError("model_refs must contain unique non-empty values")

        resolution_values = (
            self.resolution_frame_id,
            self.resolution_frame_content_hash,
            self.resolution_known_at_ns,
            self.realized_price,
            self.realized_price_source,
            self.realized_return_bps,
            self.forecast_error_bps,
            self.actual_positive,
        )
        if self.status == "RESOLVED":
            if any(value is None for value in resolution_values):
                raise OutcomeContractError("resolved outcome requires complete resolution evidence")
            _digest(str(self.resolution_frame_content_hash), "resolution_frame_content_hash")
            known_at = int(self.resolution_known_at_ns)
            if known_at < self.target_resolves_at_ns or known_at > self.target_resolves_at_ns + self.max_resolution_lag_ns:
                raise OutcomeContractError("resolution frame lies outside declared resolution window")
            realized = Decimal(_decimal_text(self.realized_price, "realized_price"))
            if realized <= 0 or not self.realized_price_source:
                raise OutcomeContractError("resolved outcome requires positive realized price and source")
            _decimal_text(self.realized_return_bps, "realized_return_bps")
            _decimal_text(self.forecast_error_bps, "forecast_error_bps")
            if self.actual_positive not in (0, 1):
                raise OutcomeContractError("actual_positive must be 0 or 1")
        elif any(value is not None for value in resolution_values):
            raise OutcomeContractError("unresolvable outcome cannot claim resolution evidence")

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "outcome_id": self.outcome_id,
            "prediction": {
                "prediction_id": self.prediction_id,
                "content_hash": self.prediction_content_hash,
                "journal_entry_hash": self.prediction_journal_entry_hash,
                "evidence_class": self.evidence_class,
                "target_metric": self.target_metric,
                "model_refs": list(self.model_refs),
            },
            "resolution_policy": {
                "policy_id": self.resolution_policy_id,
                "target_resolves_at_ns": int(self.target_resolves_at_ns),
                "max_resolution_lag_ns": int(self.max_resolution_lag_ns),
            },
            "status": self.status,
            "decided_at_ns": int(self.decided_at_ns),
            "reference": {
                "price": self.reference_price,
                "source": self.reference_price_source,
            },
            "resolution": {
                "frame_id": self.resolution_frame_id,
                "frame_content_hash": self.resolution_frame_content_hash,
                "known_at_ns": self.resolution_known_at_ns,
                "realized_price": self.realized_price,
                "realized_price_source": self.realized_price_source,
                "realized_return_bps": self.realized_return_bps,
                "forecast_error_bps": self.forecast_error_bps,
                "actual_positive": self.actual_positive,
            },
        }

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        value = self.body()
        value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}
        return value

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "PredictionOutcome":
        prediction = value.get("prediction")
        policy = value.get("resolution_policy")
        reference = value.get("reference")
        resolution = value.get("resolution")
        if not all(isinstance(item, Mapping) for item in (prediction, policy, reference, resolution)):
            raise OutcomeContractError("outcome envelope is malformed")
        item = cls(
            schema_version=str(value.get("schema_version", "")),
            outcome_id=str(value.get("outcome_id", "")),
            prediction_id=str(prediction.get("prediction_id", "")),
            prediction_content_hash=str(prediction.get("content_hash", "")),
            prediction_journal_entry_hash=str(prediction.get("journal_entry_hash", "")),
            evidence_class=str(prediction.get("evidence_class", "")),
            target_metric=str(prediction.get("target_metric", "")),
            model_refs=tuple(str(ref) for ref in prediction.get("model_refs", [])),
            status=str(value.get("status", "")),
            target_resolves_at_ns=int(policy.get("target_resolves_at_ns", -1)),
            max_resolution_lag_ns=int(policy.get("max_resolution_lag_ns", -1)),
            resolution_policy_id=str(policy.get("policy_id", "")),
            decided_at_ns=int(value.get("decided_at_ns", -1)),
            reference_price=str(reference.get("price", "")),
            reference_price_source=str(reference.get("source", "")),
            resolution_frame_id=None if resolution.get("frame_id") is None else str(resolution.get("frame_id")),
            resolution_frame_content_hash=None if resolution.get("frame_content_hash") is None else str(resolution.get("frame_content_hash")),
            resolution_known_at_ns=None if resolution.get("known_at_ns") is None else int(resolution.get("known_at_ns")),
            realized_price=None if resolution.get("realized_price") is None else str(resolution.get("realized_price")),
            realized_price_source=None if resolution.get("realized_price_source") is None else str(resolution.get("realized_price_source")),
            realized_return_bps=None if resolution.get("realized_return_bps") is None else str(resolution.get("realized_return_bps")),
            forecast_error_bps=None if resolution.get("forecast_error_bps") is None else str(resolution.get("forecast_error_bps")),
            actual_positive=None if resolution.get("actual_positive") is None else int(resolution.get("actual_positive")),
        )
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("content_hash") != item.content_hash():
            raise OutcomeContractError("outcome content hash mismatch")
        return item
