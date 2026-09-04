"""Cross-market relative calculations."""

import math
import statistics
from typing import Any, Mapping

from ..core import MarketObjectRef, build_object


def relative_calculation(
    *, object_id: str, subject_series: Mapping[str, Any], benchmark_series: Mapping[str, Any], created_at: str,
) -> Mapping[str, Any]:
    subject_rows = subject_series.get("payload", {}).get("rows", [])
    benchmark_rows = benchmark_series.get("payload", {}).get("rows", [])
    if subject_series.get("object_type") != "NORMALIZED_MEASUREMENT" or benchmark_series.get("object_type") != "NORMALIZED_MEASUREMENT":
        raise ValueError("relative calculation requires two normalized series")
    subject_by_time = {row["timestamp"]: float(row["close"]) for row in subject_rows}
    benchmark_by_time = {row["timestamp"]: float(row["close"]) for row in benchmark_rows}
    timestamps = sorted(set(subject_by_time) & set(benchmark_by_time))
    if len(timestamps) < 21:
        raise ValueError("relative calculation requires at least 21 aligned rows")
    subject_returns = [math.log(subject_by_time[b] / subject_by_time[a]) for a, b in zip(timestamps, timestamps[1:])]
    benchmark_returns = [math.log(benchmark_by_time[b] / benchmark_by_time[a]) for a, b in zip(timestamps, timestamps[1:])]
    subject_mean, benchmark_mean = statistics.fmean(subject_returns), statistics.fmean(benchmark_returns)
    covariance = statistics.fmean((a - subject_mean) * (b - benchmark_mean) for a, b in zip(subject_returns, benchmark_returns))
    subject_std, benchmark_std = statistics.pstdev(subject_returns), statistics.pstdev(benchmark_returns)
    correlation = covariance / (subject_std * benchmark_std) if subject_std and benchmark_std else 0.0
    benchmark_variance = benchmark_std**2
    beta = covariance / benchmark_variance if benchmark_variance else 0.0
    window = min(120, len(timestamps) - 1)
    relative_return = subject_by_time[timestamps[-1]] / subject_by_time[timestamps[-1 - window]] - benchmark_by_time[timestamps[-1]] / benchmark_by_time[timestamps[-1 - window]]
    return build_object(
        object_id=object_id, object_type="RELATIVE_CALCULATION", truth_class="STATISTICAL_ESTIMATE",
        subject={**subject_series["subject"], "benchmark_instrument": benchmark_series["subject"]["instrument"]},
        effective_at=timestamps[-1], created_at=created_at, source_time_range={"start": timestamps[0], "end": timestamps[-1], "aligned_rows": len(timestamps)},
        input_refs=[MarketObjectRef.to(subject_series["object_id"], "SUBJECT_SERIES", expected_object_type="NORMALIZED_MEASUREMENT"), MarketObjectRef.to(benchmark_series["object_id"], "BENCHMARK_SERIES", expected_object_type="NORMALIZED_MEASUREMENT")],
        method={"name": "ALIGNED_RETURN_RELATIONSHIPS", "version": "1.0.0", "deterministic": True, "parameters": {"relative_return_window": window}},
        quality={"status": "VALID", "aligned_rows": len(timestamps)},
        payload={"subject_ref": f"market://{subject_series['object_id']}", "benchmark_ref": f"market://{benchmark_series['object_id']}", "values": {"relative_return": round(relative_return, 10), "correlation": round(correlation, 10), "beta": round(beta, 10), "tracking_error": round(statistics.pstdev([a - b for a, b in zip(subject_returns, benchmark_returns)]), 10)}},
    )
