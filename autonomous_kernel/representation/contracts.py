from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..observation.instruments import CanonicalInstrument
from ..operations import canonical_hash


REPRESENTATION_SCHEMA_VERSION = "1.0"
REPRESENTATION_TYPES = {"INSTRUMENT_STATE", "DERIVATIVE_STATE"}
REPRESENTATION_STATUSES = {"QUALIFIED", "DEGRADED", "UNAVAILABLE"}


class RepresentationContractError(ValueError):
    pass


def _string_tuple(values: Sequence[str], field: str) -> Tuple[str, ...]:
    result = tuple(str(value) for value in values)
    if any(not value for value in result) or len(set(result)) != len(result):
        raise RepresentationContractError("%s must contain unique non-empty values" % field)
    return result


@dataclass(frozen=True)
class RepresentationFrame:
    frame_id: str
    representation_type: str
    instrument: CanonicalInstrument
    window_start_ns: int
    cutoff_at_ns: int
    known_at_ns: int
    latest_source_event_at_ns: int
    status: str
    builder_version: str
    parameters: Mapping[str, Any]
    state: Mapping[str, Any]
    source_observation_ids: Tuple[str, ...]
    source_content_hashes: Tuple[str, ...]
    source_providers: Tuple[str, ...]
    source_venues: Tuple[str, ...]
    schema_version: str = REPRESENTATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REPRESENTATION_SCHEMA_VERSION:
            raise RepresentationContractError("unsupported representation schema")
        if not self.frame_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in self.frame_id):
            raise RepresentationContractError("frame_id must be non-empty and file-safe")
        if self.representation_type not in REPRESENTATION_TYPES:
            raise RepresentationContractError("representation_type is invalid")
        if self.status not in REPRESENTATION_STATUSES:
            raise RepresentationContractError("representation status is invalid")
        if not self.builder_version:
            raise RepresentationContractError("builder_version is required")
        if self.window_start_ns < 0 or self.cutoff_at_ns < self.window_start_ns:
            raise RepresentationContractError("representation window is invalid")
        if self.known_at_ns < self.window_start_ns or self.known_at_ns > self.cutoff_at_ns:
            raise RepresentationContractError("known_at_ns must be inside the point-in-time window")
        if self.latest_source_event_at_ns < 0:
            raise RepresentationContractError("latest_source_event_at_ns must be non-negative")
        if not isinstance(self.parameters, Mapping) or not isinstance(self.state, Mapping):
            raise RepresentationContractError("parameters and state must be mappings")
        ids = _string_tuple(self.source_observation_ids, "source_observation_ids")
        hashes = _string_tuple(self.source_content_hashes, "source_content_hashes")
        if len(ids) != len(hashes):
            raise RepresentationContractError("source observation ids and hashes must align")
        if not ids:
            raise RepresentationContractError("representation requires source observations")
        for digest in hashes:
            if len(digest) != 64:
                raise RepresentationContractError("source content hash must be SHA-256 hex")
            try:
                int(digest, 16)
            except ValueError as exc:
                raise RepresentationContractError("source content hash must be hexadecimal") from exc
        _string_tuple(self.source_providers, "source_providers")
        _string_tuple(self.source_venues, "source_venues")

    def source_set_hash(self) -> str:
        return canonical_hash(
            {
                "observation_ids": list(self.source_observation_ids),
                "content_hashes": list(self.source_content_hashes),
            }
        )

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "frame_id": self.frame_id,
            "representation_type": self.representation_type,
            "instrument": self.instrument.to_wire(),
            "window": {
                "start_ns": int(self.window_start_ns),
                "cutoff_at_ns": int(self.cutoff_at_ns),
                "known_at_ns": int(self.known_at_ns),
                "latest_source_event_at_ns": int(self.latest_source_event_at_ns),
            },
            "status": self.status,
            "builder_version": self.builder_version,
            "parameters": dict(self.parameters),
            "state": dict(self.state),
            "lineage": {
                "source_observation_ids": list(self.source_observation_ids),
                "source_content_hashes": list(self.source_content_hashes),
                "source_set_hash": self.source_set_hash(),
                "providers": list(self.source_providers),
                "venues": list(self.source_venues),
            },
        }

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        value = self.body()
        value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}
        return value

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "RepresentationFrame":
        instrument = value.get("instrument")
        window = value.get("window")
        lineage = value.get("lineage")
        if not isinstance(instrument, Mapping) or not isinstance(window, Mapping) or not isinstance(lineage, Mapping):
            raise RepresentationContractError("representation envelope is malformed")
        item = cls(
            schema_version=str(value.get("schema_version", "")),
            frame_id=str(value.get("frame_id", "")),
            representation_type=str(value.get("representation_type", "")),
            instrument=CanonicalInstrument(
                canonical_id=str(instrument.get("canonical_id", "")),
                asset_class=str(instrument.get("asset_class", "")),
                market_type=str(instrument.get("market_type", "")),
                base_asset=str(instrument.get("base_asset", "")),
                quote_asset=str(instrument.get("quote_asset", "")),
                settlement_asset=instrument.get("settlement_asset"),
                expiry=instrument.get("expiry"),
            ),
            window_start_ns=int(window.get("start_ns", -1)),
            cutoff_at_ns=int(window.get("cutoff_at_ns", -1)),
            known_at_ns=int(window.get("known_at_ns", -1)),
            latest_source_event_at_ns=int(window.get("latest_source_event_at_ns", -1)),
            status=str(value.get("status", "")),
            builder_version=str(value.get("builder_version", "")),
            parameters=value.get("parameters") if isinstance(value.get("parameters"), Mapping) else {},
            state=value.get("state") if isinstance(value.get("state"), Mapping) else {},
            source_observation_ids=tuple(str(item) for item in lineage.get("source_observation_ids", [])),
            source_content_hashes=tuple(str(item) for item in lineage.get("source_content_hashes", [])),
            source_providers=tuple(str(item) for item in lineage.get("providers", [])),
            source_venues=tuple(str(item) for item in lineage.get("venues", [])),
        )
        if lineage.get("source_set_hash") != item.source_set_hash():
            raise RepresentationContractError("representation source_set_hash mismatch")
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("content_hash") != item.content_hash():
            raise RepresentationContractError("representation content hash mismatch")
        return item
