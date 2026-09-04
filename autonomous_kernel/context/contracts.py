from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence, Tuple

from ..operations import canonical_hash


CONTEXT_SCHEMA_VERSION = "1.0"
CONTEXT_TYPES = {"MARKET_CONTEXT"}
CONTEXT_STATUSES = {"QUALIFIED", "DEGRADED", "UNAVAILABLE"}


class MarketContextContractError(ValueError):
    pass


def _digest(value: str, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise MarketContextContractError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise MarketContextContractError("%s must be hexadecimal" % field) from exc
    return text


def _strings(values: Sequence[str], field: str, *, unique: bool = False) -> Tuple[str, ...]:
    result = tuple(str(value) for value in values)
    if any(not value for value in result):
        raise MarketContextContractError("%s must contain non-empty values" % field)
    if unique and len(set(result)) != len(result):
        raise MarketContextContractError("%s must contain unique values" % field)
    return result


@dataclass(frozen=True)
class MarketContextFrame:
    """Immutable Z9 context derived only from point-in-time Z2 frames."""

    context_id: str
    context_type: str
    cutoff_at_ns: int
    known_at_ns: int
    status: str
    builder_version: str
    parameters: Mapping[str, Any]
    state: Mapping[str, Any]
    source_frame_ids: Tuple[str, ...]
    source_frame_hashes: Tuple[str, ...]
    source_instrument_ids: Tuple[str, ...]
    schema_version: str = CONTEXT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONTEXT_SCHEMA_VERSION:
            raise MarketContextContractError("unsupported market-context schema")
        if not self.context_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in self.context_id):
            raise MarketContextContractError("context_id must be non-empty and file-safe")
        if self.context_type not in CONTEXT_TYPES:
            raise MarketContextContractError("context_type is invalid")
        if self.status not in CONTEXT_STATUSES:
            raise MarketContextContractError("context status is invalid")
        if self.cutoff_at_ns < 0 or self.known_at_ns < 0 or self.known_at_ns > self.cutoff_at_ns:
            raise MarketContextContractError("context timing is invalid")
        if not self.builder_version:
            raise MarketContextContractError("builder_version is required")
        if not isinstance(self.parameters, Mapping) or not isinstance(self.state, Mapping):
            raise MarketContextContractError("parameters and state must be mappings")

        ids = _strings(self.source_frame_ids, "source_frame_ids", unique=True)
        hashes = _strings(self.source_frame_hashes, "source_frame_hashes")
        instruments = _strings(self.source_instrument_ids, "source_instrument_ids")
        if not ids or len(ids) != len(hashes) or len(ids) != len(instruments):
            raise MarketContextContractError("source frame lineage arrays must be non-empty and aligned")
        for digest in hashes:
            _digest(digest, "source_frame_hash")

        members = self.state.get("members")
        if not isinstance(members, Mapping) or not members:
            raise MarketContextContractError("context state requires member summaries")
        for instrument_id, member in members.items():
            if not instrument_id or not isinstance(member, Mapping):
                raise MarketContextContractError("context member summary is malformed")
            frame_id = str(member.get("frame_id", ""))
            frame_hash = str(member.get("frame_content_hash", ""))
            if frame_id not in ids:
                raise MarketContextContractError("context member frame is absent from lineage")
            _digest(frame_hash, "member frame_content_hash")
            index = ids.index(frame_id)
            if frame_hash != hashes[index] or str(instrument_id) != instruments[index]:
                raise MarketContextContractError("context member lineage does not bind exact frame")

    def source_set_hash(self) -> str:
        return canonical_hash({
            "frame_ids": list(self.source_frame_ids),
            "frame_hashes": list(self.source_frame_hashes),
            "instrument_ids": list(self.source_instrument_ids),
        })

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "context_id": self.context_id,
            "context_type": self.context_type,
            "cutoff_at_ns": int(self.cutoff_at_ns),
            "known_at_ns": int(self.known_at_ns),
            "status": self.status,
            "builder_version": self.builder_version,
            "parameters": dict(self.parameters),
            "state": dict(self.state),
            "lineage": {
                "source_frame_ids": list(self.source_frame_ids),
                "source_frame_hashes": list(self.source_frame_hashes),
                "source_instrument_ids": list(self.source_instrument_ids),
                "source_set_hash": self.source_set_hash(),
            },
            "authority": {
                "capital_decision": False,
                "risk_authorization": False,
                "external_execution": False,
                "source_truth_owner": "Z1/Z2",
            },
        }

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        value = self.body()
        value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}
        return value

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "MarketContextFrame":
        lineage = value.get("lineage")
        if not isinstance(lineage, Mapping):
            raise MarketContextContractError("market-context lineage is malformed")
        item = cls(
            schema_version=str(value.get("schema_version", "")),
            context_id=str(value.get("context_id", "")),
            context_type=str(value.get("context_type", "")),
            cutoff_at_ns=int(value.get("cutoff_at_ns", -1)),
            known_at_ns=int(value.get("known_at_ns", -1)),
            status=str(value.get("status", "")),
            builder_version=str(value.get("builder_version", "")),
            parameters=value.get("parameters") if isinstance(value.get("parameters"), Mapping) else {},
            state=value.get("state") if isinstance(value.get("state"), Mapping) else {},
            source_frame_ids=tuple(str(item) for item in lineage.get("source_frame_ids", [])),
            source_frame_hashes=tuple(str(item) for item in lineage.get("source_frame_hashes", [])),
            source_instrument_ids=tuple(str(item) for item in lineage.get("source_instrument_ids", [])),
        )
        if lineage.get("source_set_hash") != item.source_set_hash():
            raise MarketContextContractError("market-context source_set_hash mismatch")
        authority = value.get("authority")
        if not isinstance(authority, Mapping) or authority.get("capital_decision") is not False or authority.get("risk_authorization") is not False or authority.get("external_execution") is not False:
            raise MarketContextContractError("Z9 authority boundary is invalid")
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("content_hash") != item.content_hash():
            raise MarketContextContractError("market-context content hash mismatch")
        return item
