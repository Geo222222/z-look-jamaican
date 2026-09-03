from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Tuple

from ..operations import canonical_hash


ASSEMBLY_SCHEMA_VERSION = "1.0"
WEIGHT_POLICY_ID = "EVIDENCE_SHRUNK_BOUNDED_RELIABILITY_V1"
INTERVAL_POLICY_ID = "CONSERVATIVE_COMPONENT_ENVELOPE_V1"


class AssemblyContractError(ValueError):
    pass


def _decimal(value: Any, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AssemblyContractError("%s must be decimal-compatible" % field) from exc
    if not number.is_finite():
        raise AssemblyContractError("%s must be finite" % field)
    return number


def _digest(value: str, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise AssemblyContractError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise AssemblyContractError("%s must be hexadecimal" % field) from exc
    return text


@dataclass(frozen=True)
class AssemblyReceipt:
    receipt_id: str
    assembly_at_ns: int
    mode: str
    evidence_class: str
    representation_frame_id: str
    representation_content_hash: str
    prediction_at_ns: int
    horizon_ns: int
    resolves_at_ns: int
    target_metric: str
    assembled_prediction_id: str
    assembled_prediction_content_hash: str
    contributors: Tuple[Mapping[str, Any], ...]
    weight_policy_id: str = WEIGHT_POLICY_ID
    interval_policy_id: str = INTERVAL_POLICY_ID
    schema_version: str = ASSEMBLY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ASSEMBLY_SCHEMA_VERSION:
            raise AssemblyContractError("unsupported assembly schema")
        if not self.receipt_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in self.receipt_id):
            raise AssemblyContractError("receipt_id must be non-empty and file-safe")
        if self.mode not in {"PROSPECTIVE_SHADOW", "HISTORICAL_REPLAY"}:
            raise AssemblyContractError("assembly mode is invalid")
        expected_class = "FORWARD_EVALUABLE" if self.mode == "PROSPECTIVE_SHADOW" else "RESEARCH_ONLY"
        if self.evidence_class != expected_class:
            raise AssemblyContractError("assembly evidence class does not match mode")
        if self.assembly_at_ns < 0 or self.prediction_at_ns < 0 or self.horizon_ns <= 0:
            raise AssemblyContractError("assembly timing is invalid")
        if self.resolves_at_ns != self.prediction_at_ns + self.horizon_ns:
            raise AssemblyContractError("assembly resolves_at_ns mismatch")
        if self.mode == "PROSPECTIVE_SHADOW" and self.assembly_at_ns >= self.resolves_at_ns:
            raise AssemblyContractError("prospective assembly must occur before resolution")
        if not self.representation_frame_id or not self.target_metric or not self.assembled_prediction_id:
            raise AssemblyContractError("assembly identity fields are required")
        _digest(self.representation_content_hash, "representation_content_hash")
        _digest(self.assembled_prediction_content_hash, "assembled_prediction_content_hash")
        if self.weight_policy_id != WEIGHT_POLICY_ID or self.interval_policy_id != INTERVAL_POLICY_ID:
            raise AssemblyContractError("assembly policy identity is invalid")
        if len(self.contributors) < 2:
            raise AssemblyContractError("adaptive assembly requires at least two contributors")

        refs = []
        weights = []
        for index, contributor in enumerate(self.contributors):
            if not isinstance(contributor, Mapping):
                raise AssemblyContractError("contributor %d must be a mapping" % index)
            model_ref = str(contributor.get("model_ref", ""))
            if not model_ref:
                raise AssemblyContractError("contributor %d lacks model_ref" % index)
            refs.append(model_ref)
            _digest(str(contributor.get("component_prediction_hash", "")), "component_prediction_hash")
            _digest(str(contributor.get("registry_event_hash", "")), "registry_event_hash")
            profile_hash = contributor.get("competence_profile_hash")
            if profile_hash is not None:
                _digest(str(profile_hash), "competence_profile_hash")
            sample_strength = _decimal(contributor.get("sample_strength"), "sample_strength")
            if sample_strength < 0 or sample_strength > 1:
                raise AssemblyContractError("sample_strength must be in 0..1")
            raw_score = _decimal(contributor.get("raw_weight_score"), "raw_weight_score")
            if raw_score < Decimal("0.5") or raw_score > Decimal("1.5"):
                raise AssemblyContractError("raw_weight_score must remain within conservative bounds")
            weight = _decimal(contributor.get("normalized_weight"), "normalized_weight")
            if weight <= 0 or weight >= 1:
                raise AssemblyContractError("each normalized_weight must be strictly between 0 and 1")
            weights.append(weight)
            skill = contributor.get("skill")
            if skill is not None:
                skill_value = _decimal(skill, "skill")
                if skill_value < 0 or skill_value > 1:
                    raise AssemblyContractError("skill must be in 0..1")
        if refs != sorted(refs) or len(refs) != len(set(refs)):
            raise AssemblyContractError("contributors must be unique and sorted by model_ref")
        if sum(weights, Decimal("0")) != Decimal("1"):
            raise AssemblyContractError("normalized contributor weights must sum exactly to 1")

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "assembly_at_ns": int(self.assembly_at_ns),
            "mode": self.mode,
            "evidence_class": self.evidence_class,
            "representation": {
                "frame_id": self.representation_frame_id,
                "content_hash": self.representation_content_hash,
            },
            "prediction_contract": {
                "prediction_at_ns": int(self.prediction_at_ns),
                "horizon_ns": int(self.horizon_ns),
                "resolves_at_ns": int(self.resolves_at_ns),
                "target_metric": self.target_metric,
            },
            "assembled_prediction": {
                "prediction_id": self.assembled_prediction_id,
                "content_hash": self.assembled_prediction_content_hash,
            },
            "policies": {
                "weight_policy_id": self.weight_policy_id,
                "interval_policy_id": self.interval_policy_id,
                "weight_bounds": {"minimum_raw_score": "0.5", "maximum_raw_score": "1.5"},
                "weak_evidence_behavior": "SHRINK_TOWARD_EQUAL_INFLUENCE",
            },
            "contributors": [dict(item) for item in self.contributors],
        }

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        value = self.body()
        value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}
        return value
