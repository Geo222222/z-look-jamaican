"""Boring, authoritative OHLCV evidence."""

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from ..core import build_object


def _number(value: Any, name: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def price_observation(
    *, object_id: str, instrument: str, exchange: str, asset: str, timestamp: str,
    interval: str, open_price: Any, high_price: Any, low_price: Any, close_price: Any,
    volume: Any, quote_volume: Any, trade_count: int, taker_buy_quote_volume: Any,
    source_record_id: str, source_sha256: str, created_at: str,
) -> Mapping[str, Any]:
    open_value, high_value, low_value, close_value = (
        _number(open_price, "open"), _number(high_price, "high"),
        _number(low_price, "low"), _number(close_price, "close"),
    )
    volume_value = _number(volume, "volume")
    quote_value = _number(quote_volume, "quote_volume")
    taker_value = _number(taker_buy_quote_volume, "taker_buy_quote_volume")
    if min(open_value, high_value, low_value, close_value) <= 0:
        raise ValueError("OHLC prices must be positive")
    if low_value > min(open_value, close_value) or high_value < max(open_value, close_value):
        raise ValueError("OHLC relation is invalid")
    if volume_value < 0 or quote_value < 0 or taker_value < 0 or taker_value > quote_value:
        raise ValueError("volume relation is invalid")
    if int(trade_count) < 0:
        raise ValueError("trade_count must be non-negative")
    return build_object(
        object_id=object_id,
        object_type="MARKET_OBSERVATION",
        truth_class="OBSERVED_EVIDENCE",
        subject={"instrument": instrument, "exchange": exchange, "asset": asset, "interval": interval},
        effective_at=timestamp,
        created_at=created_at,
        source_time_range={"start": timestamp, "end": timestamp, "interval": interval},
        input_refs=[],
        method={"name": "SOURCE_RECORD_PRESERVATION", "version": "1.0.0", "deterministic": True},
        quality={"status": "VALID", "source_record_id": source_record_id, "source_sha256": source_sha256},
        payload={
            "open": str(open_value), "high": str(high_value), "low": str(low_value), "close": str(close_value),
            "volume": str(volume_value), "quote_volume": str(quote_value), "trade_count": int(trade_count),
            "taker_buy_quote_volume": str(taker_value), "source_record_id": source_record_id,
        },
    )
