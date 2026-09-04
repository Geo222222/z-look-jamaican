"""Rolling distribution and dependence calculations."""

import math
import statistics
from typing import Any, Mapping, Sequence

from ..core import MarketObjectRef, build_object


def _percentile_rank(values: Sequence[float], current: float) -> float:
    return sum(value <= current for value in values) / len(values) if values else 0.0


def statistical_calculation(*, object_id: str, price_series: Mapping[str, Any], created_at: str) -> Mapping[str, Any]:
    rows = price_series.get("payload", {}).get("rows", [])
    if price_series.get("object_type") != "NORMALIZED_MEASUREMENT" or len(rows) < 30:
        raise ValueError("statistical calculations require at least 30 normalized rows")
    closes = [float(row["close"]) for row in rows]
    volumes = [float(row["quote_volume"]) for row in rows]
    returns = [math.log(current / previous) for previous, current in zip(closes, closes[1:])]
    mean, variance = statistics.fmean(returns), statistics.pvariance(returns)
    deviation = math.sqrt(variance)
    centered = [value - mean for value in returns]
    skew = statistics.fmean(value**3 for value in centered) / deviation**3 if deviation else 0.0
    kurtosis = statistics.fmean(value**4 for value in centered) / deviation**4 - 3 if deviation else 0.0
    autocorrelation = 0.0
    if len(returns) > 2 and variance:
        autocorrelation = sum((a - mean) * (b - mean) for a, b in zip(returns, returns[1:])) / ((len(returns) - 1) * variance)
    volume_mean, volume_std = statistics.fmean(volumes[-30:]), statistics.pstdev(volumes[-30:])
    return_mean, return_std = statistics.fmean(returns[-30:]), statistics.pstdev(returns[-30:])
    current_return = returns[-1]
    values = {
        "mean_log_return": mean,
        "rolling_variance": variance,
        "rolling_volatility": deviation,
        "skewness": skew,
        "excess_kurtosis": kurtosis,
        "lag1_autocorrelation": autocorrelation,
        "volume_zscore_30": (volumes[-1] - volume_mean) / volume_std if volume_std else 0.0,
        "return_zscore_30": (current_return - return_mean) / return_std if return_std else 0.0,
        "absolute_return_percentile": _percentile_rank([abs(value) for value in returns], abs(current_return)),
    }
    return build_object(
        object_id=object_id, object_type="STATISTICAL_CALCULATION", truth_class="STATISTICAL_ESTIMATE",
        subject=price_series["subject"], effective_at=price_series["effective_at"], created_at=created_at,
        source_time_range=price_series["source_time_range"],
        input_refs=[MarketObjectRef.to(price_series["object_id"], "ESTIMATED_FROM", expected_object_type="NORMALIZED_MEASUREMENT")],
        method={"name": "ROLLING_DISTRIBUTION_STATISTICS", "version": "1.0.0", "deterministic": True, "parameters": {"zscore_window": 30, "autocorrelation_lag": 1}},
        quality={"status": "VALID", "input_rows": len(rows), "stationarity_claimed": False},
        payload={"measurement_ref": f"market://{price_series['object_id']}", "values": {key: round(value, 10) for key, value in values.items()}},
    )
