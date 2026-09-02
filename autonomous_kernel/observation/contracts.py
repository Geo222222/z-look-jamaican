from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..operations import canonical_hash
from .instruments import CanonicalInstrument


OBSERVATION_SCHEMA_VERSION = "1.0"
EVENT_TYPES = {
    "TRADE",
    "QUOTE",
    "BOOK_SNAPSHOT",
    "BOOK_DELTA",
    "CANDLE",
    "FUNDING",
    "OPEN_INTEREST",
    "LIQUIDATION",
    "INDEX_PRICE",
    "MARK_PRICE",
    "MICROSTRUCTURE_SUMMARY",
}
SEQUENCE_SCOPES = {"CONNECTION_GLOBAL", "CHANNEL", "INSTRUMENT", "PROVIDER_EVENT", "NONE"}
QUALITY_STATES = {"VALID", "DEGRADED", "STALE", "UNAVAILABLE"}
BOOK_SIDES = {"BID", "ASK"}
TRADE_SIDES = {"BUY", "SELL", "UNKNOWN"}


class ObservationContractError(ValueError):
    pass


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def raw_sha256(raw_event: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json_bytes(raw_event)).hexdigest()


def _decimal_text(value: Any, field: str, *, positive: bool = False, non_negative: bool = False) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ObservationContractError("%s must be decimal-compatible" % field) from exc
    if not number.is_finite():
        raise ObservationContractError("%s must be finite" % field)
    if positive and number <= 0:
        raise ObservationContractError("%s must be positive" % field)
    if non_negative and number < 0:
        raise ObservationContractError("%s must be non-negative" % field)
    return format(number, "f")


def normalize_trade_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    side = str(payload.get("side", "UNKNOWN")).upper()
    if side not in TRADE_SIDES:
        raise ObservationContractError("trade side must be BUY, SELL, or UNKNOWN")
    value: Dict[str, Any] = {
        "price": _decimal_text(payload.get("price"), "price", positive=True),
        "size": _decimal_text(payload.get("size"), "size", positive=True),
        "side": side,
    }
    if payload.get("trade_id") is not None:
        value["trade_id"] = str(payload["trade_id"])
    return value


def normalize_book_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    updates = payload.get("updates")
    if not isinstance(updates, Sequence) or isinstance(updates, (str, bytes)) or not updates:
        raise ObservationContractError("book payload requires non-empty updates")
    normalized = []
    for index, update in enumerate(updates):
        if not isinstance(update, Mapping):
            raise ObservationContractError("book update %d must be an object" % index)
        side = str(update.get("side", "")).upper()
        if side not in BOOK_SIDES:
            raise ObservationContractError("book side must be BID or ASK")
        normalized.append(
            {
                "side": side,
                "price": _decimal_text(update.get("price"), "book price", positive=True),
                "size": _decimal_text(update.get("size"), "book size", non_negative=True),
            }
        )
    value: Dict[str, Any] = {"updates": normalized}
    if payload.get("checksum") is not None:
        value["checksum"] = str(payload["checksum"])
    return value


def normalize_quote_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    bid = _decimal_text(payload.get("bid"), "bid", positive=True)
    ask = _decimal_text(payload.get("ask"), "ask", positive=True)
    if Decimal(bid) >= Decimal(ask):
        raise ObservationContractError("quote must have bid < ask")
    value: Dict[str, Any] = {"bid": bid, "ask": ask}
    if payload.get("bid_size") is not None:
        value["bid_size"] = _decimal_text(payload.get("bid_size"), "bid_size", non_negative=True)
    if payload.get("ask_size") is not None:
        value["ask_size"] = _decimal_text(payload.get("ask_size"), "ask_size", non_negative=True)
    return value


def normalize_candle_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    interval_ns = int(payload.get("interval_ns", 0))
    if interval_ns <= 0:
        raise ObservationContractError("candle interval_ns must be positive")
    start_at_ns = int(payload.get("start_at_ns", -1))
    end_at_ns = int(payload.get("end_at_ns", -1))
    if start_at_ns < 0 or end_at_ns <= start_at_ns:
        raise ObservationContractError("candle timestamps are invalid")
    return {
        "interval_ns": interval_ns,
        "start_at_ns": start_at_ns,
        "end_at_ns": end_at_ns,
        "open": _decimal_text(payload.get("open"), "open", positive=True),
        "high": _decimal_text(payload.get("high"), "high", positive=True),
        "low": _decimal_text(payload.get("low"), "low", positive=True),
        "close": _decimal_text(payload.get("close"), "close", positive=True),
        "volume": _decimal_text(payload.get("volume"), "volume", non_negative=True),
    }


def normalize_scalar_payload(event_type: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    field = {
        "FUNDING": "rate",
        "OPEN_INTEREST": "open_interest",
        "INDEX_PRICE": "price",
        "MARK_PRICE": "price",
    }[event_type]
    return {field: _decimal_text(payload.get(field), field)}


def normalize_payload(event_type: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    if event_type == "TRADE":
        return normalize_trade_payload(payload)
    if event_type in {"BOOK_SNAPSHOT", "BOOK_DELTA"}:
        return normalize_book_payload(payload)
    if event_type == "QUOTE":
        return normalize_quote_payload(payload)
    if event_type == "CANDLE":
        return normalize_candle_payload(payload)
    if event_type in {"FUNDING", "OPEN_INTEREST", "INDEX_PRICE", "MARK_PRICE"}:
        return normalize_scalar_payload(event_type, payload)
    if event_type == "LIQUIDATION":
        side = str(payload.get("side", "UNKNOWN")).upper()
        if side not in TRADE_SIDES:
            raise ObservationContractError("liquidation side is invalid")
        return {
            "price": _decimal_text(payload.get("price"), "price", positive=True),
            "size": _decimal_text(payload.get("size"), "size", positive=True),
            "side": side,
        }
    if event_type == "MICROSTRUCTURE_SUMMARY":
        if not isinstance(payload, Mapping):
            raise ObservationContractError("microstructure summary payload must be an object")
        return dict(payload)
    raise ObservationContractError("unsupported event_type: %s" % event_type)


def _validate_quality(quality: Mapping[str, Any]) -> Dict[str, Any]:
    status = str(quality.get("status", ""))
    if status not in QUALITY_STATES:
        raise ObservationContractError("quality status is invalid")
    action_permitted = quality.get("action_permitted")
    if not isinstance(action_permitted, bool):
        raise ObservationContractError("quality action_permitted must be boolean")
    if status != "VALID" and action_permitted:
        raise ObservationContractError("non-VALID observation cannot permit action")
    return dict(quality)


@dataclass(frozen=True)
class CanonicalObservation:
    observation_id: str
    instrument: CanonicalInstrument
    event_type: str
    provider: str
    venue: str
    provider_symbol: str
    channel: str
    source_event_at_ns: int
    received_at_ns: int
    known_at_ns: int
    sequence: Optional[str]
    sequence_scope: str
    stream_id: Optional[str]
    payload: Mapping[str, Any]
    quality: Mapping[str, Any]
    raw_event_sha256: str
    raw_ref: Optional[str]
    schema_version: str = OBSERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != OBSERVATION_SCHEMA_VERSION:
            raise ObservationContractError("unsupported canonical observation schema")
        if not self.observation_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in self.observation_id):
            raise ObservationContractError("observation_id must be non-empty and file-safe")
        if self.event_type not in EVENT_TYPES:
            raise ObservationContractError("event_type is invalid")
        for field in ("provider", "venue", "provider_symbol", "channel"):
            if not str(getattr(self, field)).strip():
                raise ObservationContractError("%s is required" % field)
        if self.source_event_at_ns < 0 or self.received_at_ns < 0 or self.known_at_ns < 0:
            raise ObservationContractError("timestamps must be non-negative epoch nanoseconds")
        if self.known_at_ns < self.received_at_ns:
            raise ObservationContractError("known_at_ns cannot be before received_at_ns")
        if self.sequence_scope not in SEQUENCE_SCOPES:
            raise ObservationContractError("sequence_scope is invalid")
        if self.sequence_scope != "NONE" and self.sequence is None:
            raise ObservationContractError("sequenced observation requires sequence")
        if self.sequence_scope == "NONE" and self.sequence is not None:
            raise ObservationContractError("sequence must be null when sequence_scope is NONE")
        if len(self.raw_event_sha256) != 64:
            raise ObservationContractError("raw_event_sha256 must be SHA-256 hex")
        try:
            int(self.raw_event_sha256, 16)
        except ValueError as exc:
            raise ObservationContractError("raw_event_sha256 must be hexadecimal") from exc
        normalize_payload(self.event_type, self.payload)
        _validate_quality(self.quality)

    def body(self) -> Dict[str, Any]:
        normalized_payload = normalize_payload(self.event_type, self.payload)
        return {
            "schema_version": self.schema_version,
            "observation_id": self.observation_id,
            "instrument": self.instrument.to_wire(),
            "event_type": self.event_type,
            "source": {
                "provider": self.provider,
                "venue": self.venue,
                "provider_symbol": self.provider_symbol,
                "channel": self.channel,
                "sequence": self.sequence,
                "sequence_scope": self.sequence_scope,
                "stream_id": self.stream_id,
            },
            "timing": {
                "source_event_at_ns": int(self.source_event_at_ns),
                "received_at_ns": int(self.received_at_ns),
                "known_at_ns": int(self.known_at_ns),
            },
            "payload": normalized_payload,
            "quality": _validate_quality(self.quality),
            "raw_evidence": {
                "sha256": self.raw_event_sha256.lower(),
                "ref": self.raw_ref,
            },
        }

    def normalized_payload_hash(self) -> str:
        """Cross-adapter semantic-shape fingerprint; never an event dedupe key."""
        return canonical_hash(
            {
                "instrument": self.instrument.to_wire(),
                "event_type": self.event_type,
                "source_event_at_ns": int(self.source_event_at_ns),
                "payload": normalize_payload(self.event_type, self.payload),
            }
        )

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        body = self.body()
        body["integrity"] = {
            "algorithm": "sha256",
            "content_hash": self.content_hash(),
            "normalized_payload_hash": self.normalized_payload_hash(),
        }
        return body

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "CanonicalObservation":
        instrument = value.get("instrument")
        source = value.get("source")
        timing = value.get("timing")
        raw = value.get("raw_evidence")
        if not isinstance(instrument, Mapping) or not isinstance(source, Mapping) or not isinstance(timing, Mapping) or not isinstance(raw, Mapping):
            raise ObservationContractError("canonical observation envelope is malformed")
        item = cls(
            schema_version=str(value.get("schema_version", "")),
            observation_id=str(value.get("observation_id", "")),
            instrument=CanonicalInstrument(
                canonical_id=str(instrument.get("canonical_id", "")),
                asset_class=str(instrument.get("asset_class", "")),
                market_type=str(instrument.get("market_type", "")),
                base_asset=str(instrument.get("base_asset", "")),
                quote_asset=str(instrument.get("quote_asset", "")),
                settlement_asset=instrument.get("settlement_asset"),
                expiry=instrument.get("expiry"),
            ),
            event_type=str(value.get("event_type", "")),
            provider=str(source.get("provider", "")),
            venue=str(source.get("venue", "")),
            provider_symbol=str(source.get("provider_symbol", "")),
            channel=str(source.get("channel", "")),
            source_event_at_ns=int(timing.get("source_event_at_ns", -1)),
            received_at_ns=int(timing.get("received_at_ns", -1)),
            known_at_ns=int(timing.get("known_at_ns", -1)),
            sequence=None if source.get("sequence") is None else str(source.get("sequence")),
            sequence_scope=str(source.get("sequence_scope", "")),
            stream_id=None if source.get("stream_id") is None else str(source.get("stream_id")),
            payload=value.get("payload") if isinstance(value.get("payload"), Mapping) else {},
            quality=value.get("quality") if isinstance(value.get("quality"), Mapping) else {},
            raw_event_sha256=str(raw.get("sha256", "")),
            raw_ref=None if raw.get("ref") is None else str(raw.get("ref")),
        )
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping):
            raise ObservationContractError("canonical observation integrity is missing")
        if integrity.get("content_hash") != item.content_hash():
            raise ObservationContractError("canonical observation content hash mismatch")
        if integrity.get("normalized_payload_hash") != item.normalized_payload_hash():
            raise ObservationContractError("canonical normalized payload hash mismatch")
        return item
