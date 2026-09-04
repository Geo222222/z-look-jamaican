"""Classic technical calculations. Values remain calculations, not states."""

import math
import statistics
from typing import Any, Mapping, Optional, Sequence

from ..core import MarketObjectRef, build_object


def _ema(values: Sequence[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1)
    current = statistics.fmean(values[:period])
    for value in values[period:]:
        current = alpha * value + (1 - alpha) * current
    return current


def _rsi(values: Sequence[float], period: int = 14) -> Optional[float]:
    if len(values) <= period:
        return None
    changes = [current - previous for previous, current in zip(values, values[1:])]
    gain = statistics.fmean(max(value, 0.0) for value in changes[:period])
    loss = statistics.fmean(max(-value, 0.0) for value in changes[:period])
    for change in changes[period:]:
        gain = (gain * (period - 1) + max(change, 0.0)) / period
        loss = (loss * (period - 1) + max(-change, 0.0)) / period
    if loss == 0:
        return 100.0
    relative_strength = gain / loss
    return 100 - 100 / (1 + relative_strength)


def _atr(rows: Sequence[Mapping[str, Any]], period: int = 14) -> Optional[float]:
    if len(rows) <= period:
        return None
    ranges = []
    for previous, current in zip(rows, rows[1:]):
        high, low, prior_close = float(current["high"]), float(current["low"]), float(previous["close"])
        ranges.append(max(high - low, abs(high - prior_close), abs(low - prior_close)))
    value = statistics.fmean(ranges[:period])
    for current in ranges[period:]:
        value = (value * (period - 1) + current) / period
    return value


def _adx(rows: Sequence[Mapping[str, Any]], period: int = 14) -> Optional[float]:
    if len(rows) < period * 2 + 1:
        return None
    true_ranges, plus_dm, minus_dm = [], [], []
    for previous, current in zip(rows, rows[1:]):
        high, low = float(current["high"]), float(current["low"])
        prior_high, prior_low, prior_close = float(previous["high"]), float(previous["low"]), float(previous["close"])
        up, down = high - prior_high, prior_low - low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        true_ranges.append(max(high - low, abs(high - prior_close), abs(low - prior_close)))
    tr, plus, minus = map(sum, (true_ranges[:period], plus_dm[:period], minus_dm[:period]))
    dx = []
    for index in range(period, len(true_ranges)):
        tr = tr - tr / period + true_ranges[index]
        plus = plus - plus / period + plus_dm[index]
        minus = minus - minus / period + minus_dm[index]
        plus_di, minus_di = (100 * plus / tr if tr else 0), (100 * minus / tr if tr else 0)
        denominator = plus_di + minus_di
        dx.append(100 * abs(plus_di - minus_di) / denominator if denominator else 0.0)
    if len(dx) < period:
        return None
    adx = statistics.fmean(dx[:period])
    for value in dx[period:]:
        adx = (adx * (period - 1) + value) / period
    return adx


def technical_calculation(*, object_id: str, price_series: Mapping[str, Any], created_at: str) -> Mapping[str, Any]:
    if price_series.get("object_type") != "NORMALIZED_MEASUREMENT":
        raise ValueError("technical calculations require a normalized measurement series")
    rows = price_series["payload"].get("rows", [])
    if len(rows) < 20:
        raise ValueError("technical calculations require at least 20 rows")
    closes = [float(row["close"]) for row in rows]
    ema20, ema50, ema200 = _ema(closes, 20), _ema(closes, 50), _ema(closes, 200)
    atr14, rsi14, adx14 = _atr(rows), _rsi(closes), _adx(rows)
    recent = closes[-20:]
    middle = statistics.fmean(recent)
    deviation = statistics.pstdev(recent)
    bandwidth = 4 * deviation / middle if middle else 0.0
    values = {
        "rsi_14": rsi14, "ema_20": ema20, "ema_50": ema50, "ema_200": ema200,
        "atr_14": atr14, "adx_14": adx14, "bollinger_bandwidth_20_2": bandwidth,
    }
    values = {key: round(value, 10) if value is not None and math.isfinite(value) else None for key, value in values.items()}
    return build_object(
        object_id=object_id, object_type="TECHNICAL_CALCULATION", truth_class="DETERMINISTIC_CALCULATION",
        subject=price_series["subject"], effective_at=price_series["effective_at"], created_at=created_at,
        source_time_range=price_series["source_time_range"],
        input_refs=[MarketObjectRef.to(price_series["object_id"], "CALCULATED_FROM", expected_object_type="NORMALIZED_MEASUREMENT")],
        method={"name": "WILDER_AND_EMA_TECHNICALS", "version": "1.0.0", "deterministic": True, "parameters": {"rsi": 14, "atr": 14, "adx": 14, "ema": [20, 50, 200], "bollinger": [20, 2]}},
        quality={"status": "VALID", "input_rows": len(rows), "unavailable_values": [key for key, value in values.items() if value is None]},
        payload={"measurement_ref": f"market://{price_series['object_id']}", "window": price_series["subject"].get("interval"), "values": values},
    )
