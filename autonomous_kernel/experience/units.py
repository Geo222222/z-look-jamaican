from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional


AMOUNT_SEMANTICS_SCHEMA_VERSION = "1.0"


class AmountSemanticsError(ValueError):
    pass


class NativeAmountKind(str, Enum):
    BASE_ASSET = "BASE_ASSET"
    QUOTE_ASSET = "QUOTE_ASSET"
    CONTRACTS = "CONTRACTS"
    PROVIDER_NATIVE = "PROVIDER_NATIVE"


class ContractConvention(str, Enum):
    NONE = "NONE"
    LINEAR_BASE = "LINEAR_BASE"
    LINEAR_QUOTE = "LINEAR_QUOTE"
    INVERSE_QUOTE = "INVERSE_QUOTE"
    PROVIDER_NATIVE = "PROVIDER_NATIVE"


@dataclass(frozen=True)
class EconomicAmountSemantics:
    """Proof-carrying semantics for a numeric market amount.

    A number is not economically comparable merely because it has the same
    scalar type. Native spot base quantity, derivative contract counts, inverse
    contract counts, and provider-native open-interest units remain air-gapped
    until an explicit conversion rule has produced the same normalized unit.
    """

    instrument_id: str
    market_type: str
    provider: str
    venue: str
    native_kind: NativeAmountKind
    native_unit: str
    contract_convention: ContractConvention = ContractConvention.NONE
    contract_multiplier: Optional[str] = None
    multiplier_unit: Optional[str] = None
    normalized_unit: Optional[str] = None
    normalization_status: str = "RAW_NATIVE"
    conversion_rule_id: Optional[str] = None
    conversion_rule_hash: Optional[str] = None
    schema_version: str = AMOUNT_SEMANTICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AMOUNT_SEMANTICS_SCHEMA_VERSION:
            raise AmountSemanticsError("unsupported amount-semantics schema")
        for field, value in (
            ("instrument_id", self.instrument_id),
            ("market_type", self.market_type),
            ("provider", self.provider),
            ("venue", self.venue),
            ("native_unit", self.native_unit),
        ):
            if not str(value).strip():
                raise AmountSemanticsError(f"{field} is required")
        if self.normalization_status not in {"RAW_NATIVE", "QUALIFIED", "DEGRADED", "UNAVAILABLE"}:
            raise AmountSemanticsError("normalization_status is invalid")
        if self.native_kind is NativeAmountKind.CONTRACTS and self.contract_convention is ContractConvention.NONE:
            raise AmountSemanticsError("contract quantities require a contract convention")
        if self.normalization_status == "QUALIFIED":
            if not self.normalized_unit or not self.conversion_rule_id or not self.conversion_rule_hash:
                raise AmountSemanticsError("qualified normalization requires unit and conversion proof")
            if len(self.conversion_rule_hash) != 64:
                raise AmountSemanticsError("conversion_rule_hash must be SHA-256 hex")
            try:
                int(self.conversion_rule_hash, 16)
            except ValueError as exc:
                raise AmountSemanticsError("conversion_rule_hash must be hexadecimal") from exc

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "instrument_id": self.instrument_id,
            "market_type": self.market_type,
            "provider": self.provider,
            "venue": self.venue,
            "native_kind": self.native_kind.value,
            "native_unit": self.native_unit,
            "contract_convention": self.contract_convention.value,
            "contract_multiplier": self.contract_multiplier,
            "multiplier_unit": self.multiplier_unit,
            "normalized_unit": self.normalized_unit,
            "normalization_status": self.normalization_status,
            "conversion_rule_id": self.conversion_rule_id,
            "conversion_rule_hash": self.conversion_rule_hash,
        }

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "EconomicAmountSemantics":
        return cls(
            schema_version=str(value.get("schema_version", "")),
            instrument_id=str(value.get("instrument_id", "")),
            market_type=str(value.get("market_type", "")),
            provider=str(value.get("provider", "")),
            venue=str(value.get("venue", "")),
            native_kind=NativeAmountKind(str(value.get("native_kind", ""))),
            native_unit=str(value.get("native_unit", "")),
            contract_convention=ContractConvention(str(value.get("contract_convention", "NONE"))),
            contract_multiplier=None if value.get("contract_multiplier") is None else str(value.get("contract_multiplier")),
            multiplier_unit=None if value.get("multiplier_unit") is None else str(value.get("multiplier_unit")),
            normalized_unit=None if value.get("normalized_unit") is None else str(value.get("normalized_unit")),
            normalization_status=str(value.get("normalization_status", "RAW_NATIVE")),
            conversion_rule_id=None if value.get("conversion_rule_id") is None else str(value.get("conversion_rule_id")),
            conversion_rule_hash=None if value.get("conversion_rule_hash") is None else str(value.get("conversion_rule_hash")),
        )


def normalized_amounts_comparable(
    left: EconomicAmountSemantics,
    right: EconomicAmountSemantics,
) -> bool:
    """Return true only when both amounts are proof-normalized to one unit."""
    return (
        left.normalization_status == "QUALIFIED"
        and right.normalization_status == "QUALIFIED"
        and left.normalized_unit is not None
        and left.normalized_unit == right.normalized_unit
        and left.conversion_rule_hash is not None
        and right.conversion_rule_hash is not None
    )


def same_native_series_compatible(
    prior: EconomicAmountSemantics,
    current: EconomicAmountSemantics,
) -> bool:
    """Permit within-series change only under unchanged native semantics.

    This is intentionally stricter than normalized comparison: provider, venue,
    instrument, native unit, contract convention, and multiplier must all remain
    identical. A venue/rule migration creates a new series boundary.
    """
    return (
        prior.instrument_id == current.instrument_id
        and prior.provider == current.provider
        and prior.venue == current.venue
        and prior.native_kind is current.native_kind
        and prior.native_unit == current.native_unit
        and prior.contract_convention is current.contract_convention
        and prior.contract_multiplier == current.contract_multiplier
        and prior.multiplier_unit == current.multiplier_unit
    )


def frame_amount_semantics(frame_state: Mapping[str, Any], family: str) -> Optional[EconomicAmountSemantics]:
    container = frame_state.get("amount_semantics")
    if not isinstance(container, Mapping):
        return None
    raw = container.get(family)
    if not isinstance(raw, Mapping):
        return None
    try:
        return EconomicAmountSemantics.from_wire(raw)
    except (AmountSemanticsError, ValueError):
        return None
