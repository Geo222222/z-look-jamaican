"""Deterministic swing, range, level, and gap geometry."""

from typing import Any, Mapping

from ..core import MarketObjectRef, build_object


def _swings(rows: list[Mapping[str, Any]], radius: int = 2) -> list[dict[str, Any]]:
    values = []
    for index in range(radius, len(rows) - radius):
        high = float(rows[index]["high"])
        low = float(rows[index]["low"])
        neighbors = rows[index - radius : index] + rows[index + 1 : index + radius + 1]
        if all(high > float(row["high"]) for row in neighbors):
            values.append({"kind": "SWING_HIGH", "timestamp": rows[index]["timestamp"], "price": rows[index]["high"], "confirmation_lag_bars": radius})
        if all(low < float(row["low"]) for row in neighbors):
            values.append({"kind": "SWING_LOW", "timestamp": rows[index]["timestamp"], "price": rows[index]["low"], "confirmation_lag_bars": radius})
    return values


def price_structure(*, object_id: str, price_series: Mapping[str, Any], created_at: str, range_lookback: int = 120) -> Mapping[str, Any]:
    rows = price_series.get("payload", {}).get("rows", [])
    if price_series.get("object_type") != "NORMALIZED_MEASUREMENT" or len(rows) < max(30, range_lookback + 1):
        raise ValueError("price structure requires sufficient normalized history")
    swings = _swings(rows)
    highs = [item for item in swings if item["kind"] == "SWING_HIGH"][-3:]
    lows = [item for item in swings if item["kind"] == "SWING_LOW"][-3:]
    rising_highs = len(highs) >= 2 and all(float(a["price"]) < float(b["price"]) for a, b in zip(highs, highs[1:]))
    rising_lows = len(lows) >= 2 and all(float(a["price"]) < float(b["price"]) for a, b in zip(lows, lows[1:]))
    falling_highs = len(highs) >= 2 and all(float(a["price"]) > float(b["price"]) for a, b in zip(highs, highs[1:]))
    falling_lows = len(lows) >= 2 and all(float(a["price"]) > float(b["price"]) for a, b in zip(lows, lows[1:]))
    if rising_highs and rising_lows:
        trend = "HIGHER_HIGHS_HIGHER_LOWS"
    elif falling_highs and falling_lows:
        trend = "LOWER_HIGHS_LOWER_LOWS"
    elif rising_lows and not rising_highs:
        trend = "RISING_LOWS_UNDER_RESISTANCE"
    elif falling_highs and not falling_lows:
        trend = "FALLING_HIGHS_ABOVE_SUPPORT"
    else:
        trend = "MIXED_OR_RANGE"
    prior = rows[-range_lookback - 1 : -1]
    lower = min(float(row["low"]) for row in prior)
    upper = max(float(row["high"]) for row in prior)
    close = float(rows[-1]["close"])
    status = "BROKEN_UP" if close > upper else "BROKEN_DOWN" if close < lower else "INSIDE"
    gaps = []
    for previous, current in zip(rows[-60:-1], rows[-59:]):
        if float(current["low"]) > float(previous["high"]):
            gaps.append({"direction": "UP", "start": previous["high"], "end": current["low"], "timestamp": current["timestamp"]})
        elif float(current["high"]) < float(previous["low"]):
            gaps.append({"direction": "DOWN", "start": current["high"], "end": previous["low"], "timestamp": current["timestamp"]})
    support_zones = [[item["price"], item["price"]] for item in lows[-2:]]
    resistance_zones = [[item["price"], item["price"]] for item in highs[-2:]]
    return build_object(
        object_id=object_id, object_type="PRICE_STRUCTURE", truth_class="DETERMINISTIC_CLASSIFICATION",
        subject=price_series["subject"], effective_at=price_series["effective_at"], created_at=created_at,
        source_time_range=price_series["source_time_range"],
        input_refs=[MarketObjectRef.to(price_series["object_id"], "GEOMETRY_FROM", expected_object_type="NORMALIZED_MEASUREMENT")],
        method={"name": "PIVOT_AND_RANGE_GEOMETRY", "version": "1.0.0", "deterministic": True, "parameters": {"pivot_radius": 2, "range_lookback": range_lookback}},
        quality={"status": "VALID", "confirmed_swing_count": len(swings)},
        payload={"measurement_ref": f"market://{price_series['object_id']}", "trend_structure": trend, "swing_points": swings[-12:], "range": {"lower": lower, "upper": upper, "status": status, "lookback_bars": range_lookback}, "support_zones": support_zones, "resistance_zones": resistance_zones, "gaps": gaps, "unfilled_imbalances": [], "limitations": ["Candle geometry cannot establish intrabar order-book imbalance."]},
    )
