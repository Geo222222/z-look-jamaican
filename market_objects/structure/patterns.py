"""Deterministic pattern candidates; never canonical price truth."""

from typing import Any, Mapping

from ..core import MarketObjectRef, build_object


def pattern_detection(
    *, object_id: str, price_series: Mapping[str, Any], technical: Mapping[str, Any], created_at: str,
) -> Mapping[str, Any]:
    rows = price_series.get("payload", {}).get("rows", [])
    if price_series.get("object_type") != "NORMALIZED_MEASUREMENT" or technical.get("object_type") != "TECHNICAL_CALCULATION" or len(rows) < 30:
        raise ValueError("pattern detection requires normalized prices and technical calculations")
    recent = rows[-30:]
    highs = [float(row["high"]) for row in recent]
    lows = [float(row["low"]) for row in recent]
    high_band = (max(highs) - min(highs)) / max(highs)
    low_slope = (sum(lows[-5:]) / 5 - sum(lows[:5]) / 5) / (sum(lows[:5]) / 5)
    bandwidth = technical["payload"]["values"].get("bollinger_bandwidth_20_2")
    candidates = []
    if high_band <= 0.04 and low_slope > 0.01:
        candidates.append({"pattern": "ASCENDING_TRIANGLE", "confidence": round(min(0.9, 0.55 + low_slope * 4), 4), "status": "CANDIDATE"})
    if bandwidth is not None and float(bandwidth) < 0.04:
        candidates.append({"pattern": "VOLATILITY_COMPRESSION", "confidence": round(min(0.9, 0.8 - float(bandwidth) * 5), 4), "status": "CANDIDATE"})
    if not candidates:
        candidates.append({"pattern": "NO_QUALIFYING_PATTERN", "confidence": 0.8, "status": "ABSENCE_CLASSIFICATION"})
    return build_object(
        object_id=object_id, object_type="PATTERN_DETECTION", truth_class="PATTERN_CANDIDATE",
        subject=price_series["subject"], effective_at=price_series["effective_at"], created_at=created_at,
        source_time_range=price_series["source_time_range"],
        input_refs=[MarketObjectRef.to(price_series["object_id"], "PATTERN_INPUT", expected_object_type="NORMALIZED_MEASUREMENT"), MarketObjectRef.to(technical["object_id"], "PATTERN_INPUT", expected_object_type="TECHNICAL_CALCULATION")],
        method={"name": "BOUNDED_GEOMETRIC_PATTERN_RULES", "version": "1.0.0", "deterministic": True, "parameters": {"window_bars": 30, "horizontal_high_band": 0.04, "rising_low_threshold": 0.01}},
        quality={"status": "VALID", "candidate_count": sum(item["status"] == "CANDIDATE" for item in candidates)},
        payload={"price_measurement_ref": f"market://{price_series['object_id']}", "technical_ref": f"market://{technical['object_id']}", "possible_patterns": candidates, "canonical_price_truth": False},
    )
