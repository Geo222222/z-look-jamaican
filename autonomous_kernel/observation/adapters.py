from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from ..market_data_quality import classify_market_data
from ..operations import canonical_hash
from .contracts import CanonicalObservation
from .instruments import InstrumentRegistry, default_instrument_registry


COINBASE_PROVIDER = "coinbase_advanced_trade_public_websocket"
KRAKEN_PROVIDER = "kraken_websocket_v2"


class ProviderAdapterError(ValueError):
    pass


def _iso_to_ns(value: str) -> int:
    text = str(value).strip()
    if not text:
        raise ProviderAdapterError("provider timestamp is required")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    head, separator, tail = normalized.partition(".")
    if not separator:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ProviderAdapterError("provider timestamp must be timezone-aware")
        return int(parsed.timestamp()) * 1_000_000_000
    plus = tail.rfind("+")
    minus = tail.rfind("-")
    offset_index = max(plus, minus)
    if offset_index < 0:
        raise ProviderAdapterError("provider timestamp must be timezone-aware")
    fraction = tail[:offset_index]
    offset = tail[offset_index:]
    if not fraction.isdigit():
        raise ProviderAdapterError("provider timestamp fractional seconds are invalid")
    parsed = datetime.fromisoformat(head + offset)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderAdapterError("provider timestamp must be timezone-aware")
    whole_ns = int(parsed.timestamp()) * 1_000_000_000
    fractional_ns = int((fraction + "000000000")[:9])
    return whole_ns + fractional_ns


def _initial_quality(provider: str, source_event_at_ns: int, received_at_ns: int) -> Mapping[str, Any]:
    source_seconds = source_event_at_ns // 1_000_000_000
    received_seconds = received_at_ns // 1_000_000_000
    return classify_market_data(
        provider=provider,
        source_event_at=source_seconds,
        received_at=received_seconds,
        observed_at=received_seconds,
        max_event_age_seconds=30,
        max_transport_age_seconds=30,
        max_clock_skew_seconds=1,
    ).to_dict()


def _stable_id(prefix: str, material: str) -> str:
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return "%s-%s" % (prefix, digest)


def _decimal_text(value: Any) -> str:
    return format(Decimal(str(value)), "f")


@dataclass(frozen=True)
class ProviderRecord:
    provider: str
    stream_id: str
    received_at_ns: int
    message: Mapping[str, Any]
    message_hash: str
    raw_ref: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.provider or not self.stream_id:
            raise ProviderAdapterError("provider and stream_id are required")
        if self.received_at_ns < 0:
            raise ProviderAdapterError("received_at_ns must be non-negative")
        if canonical_hash(self.message) != self.message_hash:
            raise ProviderAdapterError("provider record message hash mismatch")


def _coinbase_symbol(event: Mapping[str, Any], item: Optional[Mapping[str, Any]], default_symbol: Optional[str]) -> str:
    for candidate in (
        None if item is None else item.get("product_id"),
        event.get("product_id"),
        default_symbol,
    ):
        if candidate:
            return str(candidate)
    raise ProviderAdapterError("Coinbase event lacks product identity")


def adapt_coinbase_advanced_trade(
    record: ProviderRecord,
    *,
    registry: Optional[InstrumentRegistry] = None,
    default_symbol: Optional[str] = None,
    quality: Optional[Mapping[str, Any]] = None,
) -> Tuple[CanonicalObservation, ...]:
    if record.provider != COINBASE_PROVIDER:
        raise ProviderAdapterError("Coinbase adapter received the wrong provider")
    registry = registry or default_instrument_registry()
    message = record.message
    channel = str(message.get("channel", ""))
    if not channel:
        raise ProviderAdapterError("Coinbase message lacks channel")
    timestamp = str(message.get("timestamp", ""))
    source_event_at_ns = _iso_to_ns(timestamp)
    sequence_num = message.get("sequence_num")
    if not isinstance(sequence_num, int):
        raise ProviderAdapterError("Coinbase message lacks integer sequence_num")
    initial_quality = dict(quality or _initial_quality(record.provider, source_event_at_ns, record.received_at_ns))
    output: List[CanonicalObservation] = []

    if channel in {"level2", "l2_data"}:
        for event_index, event in enumerate(message.get("events", [])):
            event_type = str(event.get("type", ""))
            if event_type not in {"snapshot", "update"}:
                continue
            symbol = _coinbase_symbol(event, None, default_symbol)
            instrument = registry.resolve(record.provider, symbol)
            updates = []
            for update in event.get("updates", []):
                provider_side = str(update.get("side", ""))
                side = "BID" if provider_side == "bid" else "ASK" if provider_side == "offer" else ""
                updates.append(
                    {
                        "side": side,
                        "price": _decimal_text(update.get("price_level")),
                        "size": _decimal_text(update.get("new_quantity")),
                    }
                )
            material = "%s|%s|%s|%s" % (record.stream_id, sequence_num, event_index, event_type)
            output.append(
                CanonicalObservation(
                    observation_id=_stable_id("CAN-CB", material),
                    instrument=instrument,
                    event_type="BOOK_SNAPSHOT" if event_type == "snapshot" else "BOOK_DELTA",
                    provider=record.provider,
                    venue="COINBASE",
                    provider_symbol=symbol,
                    channel="level2",
                    source_event_at_ns=source_event_at_ns,
                    received_at_ns=record.received_at_ns,
                    known_at_ns=record.received_at_ns,
                    sequence="%s:%s" % (sequence_num, event_index),
                    sequence_scope="CONNECTION_GLOBAL",
                    stream_id=record.stream_id,
                    payload={"updates": updates},
                    quality=initial_quality,
                    raw_event_sha256=record.message_hash,
                    raw_ref=record.raw_ref,
                )
            )

    elif channel == "market_trades":
        for event_index, event in enumerate(message.get("events", [])):
            for trade_index, trade in enumerate(event.get("trades", [])):
                symbol = _coinbase_symbol(event, trade, default_symbol)
                instrument = registry.resolve(record.provider, symbol)
                material = "%s|%s|%s|%s|trade" % (
                    record.stream_id,
                    sequence_num,
                    event_index,
                    trade.get("trade_id", trade_index),
                )
                output.append(
                    CanonicalObservation(
                        observation_id=_stable_id("CAN-CB", material),
                        instrument=instrument,
                        event_type="TRADE",
                        provider=record.provider,
                        venue="COINBASE",
                        provider_symbol=symbol,
                        channel="market_trades",
                        source_event_at_ns=source_event_at_ns,
                        received_at_ns=record.received_at_ns,
                        known_at_ns=record.received_at_ns,
                        sequence="%s:%s:%s" % (sequence_num, event_index, trade_index),
                        sequence_scope="CONNECTION_GLOBAL",
                        stream_id=record.stream_id,
                        payload={
                            "trade_id": str(trade.get("trade_id", trade_index)),
                            "price": trade.get("price"),
                            "size": trade.get("size"),
                            "side": str(trade.get("side", "UNKNOWN")).upper(),
                        },
                        quality=initial_quality,
                        raw_event_sha256=record.message_hash,
                        raw_ref=record.raw_ref,
                    )
                )
    return tuple(output)


def adapt_kraken_v2(
    record: ProviderRecord,
    *,
    registry: Optional[InstrumentRegistry] = None,
    quality: Optional[Mapping[str, Any]] = None,
) -> Tuple[CanonicalObservation, ...]:
    if record.provider != KRAKEN_PROVIDER:
        raise ProviderAdapterError("Kraken adapter received the wrong provider")
    registry = registry or default_instrument_registry()
    message = record.message
    channel = str(message.get("channel", ""))
    message_type = str(message.get("type", "update"))
    output: List[CanonicalObservation] = []

    if channel == "trade":
        for index, trade in enumerate(message.get("data", [])):
            symbol = str(trade.get("symbol", ""))
            if not symbol:
                raise ProviderAdapterError("Kraken trade lacks symbol")
            source_event_at_ns = _iso_to_ns(str(trade.get("timestamp", "")))
            item_quality = dict(quality or _initial_quality(record.provider, source_event_at_ns, record.received_at_ns))
            trade_id = str(trade.get("trade_id", index))
            sequence = trade_id if trade.get("trade_id") is not None else None
            output.append(
                CanonicalObservation(
                    observation_id=_stable_id("CAN-KR", "%s|trade|%s" % (record.stream_id, trade_id)),
                    instrument=registry.resolve(record.provider, symbol),
                    event_type="TRADE",
                    provider=record.provider,
                    venue="KRAKEN",
                    provider_symbol=symbol,
                    channel="trade",
                    source_event_at_ns=source_event_at_ns,
                    received_at_ns=record.received_at_ns,
                    known_at_ns=record.received_at_ns,
                    sequence=sequence,
                    sequence_scope="PROVIDER_EVENT" if sequence is not None else "NONE",
                    stream_id=record.stream_id,
                    payload={
                        "trade_id": trade_id,
                        "price": trade.get("price"),
                        "size": trade.get("qty", trade.get("size")),
                        "side": str(trade.get("side", "UNKNOWN")).upper(),
                    },
                    quality=item_quality,
                    raw_event_sha256=record.message_hash,
                    raw_ref=record.raw_ref,
                )
            )

    elif channel == "book":
        event_type = "BOOK_SNAPSHOT" if message_type == "snapshot" else "BOOK_DELTA"
        for index, book in enumerate(message.get("data", [])):
            symbol = str(book.get("symbol", ""))
            if not symbol:
                raise ProviderAdapterError("Kraken book event lacks symbol")
            source_event_at_ns = _iso_to_ns(str(book.get("timestamp", message.get("timestamp", ""))))
            item_quality = dict(quality or _initial_quality(record.provider, source_event_at_ns, record.received_at_ns))
            updates = []
            for side_name, canonical_side in (("bids", "BID"), ("asks", "ASK")):
                for level in book.get(side_name, []):
                    if isinstance(level, Mapping):
                        price = level.get("price")
                        size = level.get("qty", level.get("size"))
                    elif isinstance(level, Sequence) and len(level) >= 2:
                        price, size = level[0], level[1]
                    else:
                        raise ProviderAdapterError("Kraken book level is malformed")
                    updates.append({"side": canonical_side, "price": price, "size": size})
            sequence_value = book.get("checksum")
            sequence = None if sequence_value is None else str(sequence_value)
            output.append(
                CanonicalObservation(
                    observation_id=_stable_id(
                        "CAN-KR",
                        "%s|book|%s|%s|%s" % (record.stream_id, message_type, index, sequence or "none"),
                    ),
                    instrument=registry.resolve(record.provider, symbol),
                    event_type=event_type,
                    provider=record.provider,
                    venue="KRAKEN",
                    provider_symbol=symbol,
                    channel="book",
                    source_event_at_ns=source_event_at_ns,
                    received_at_ns=record.received_at_ns,
                    known_at_ns=record.received_at_ns,
                    sequence=sequence,
                    sequence_scope="PROVIDER_EVENT" if sequence is not None else "NONE",
                    stream_id=record.stream_id,
                    payload={"updates": updates, "checksum": sequence},
                    quality=item_quality,
                    raw_event_sha256=record.message_hash,
                    raw_ref=record.raw_ref,
                )
            )
    return tuple(output)
