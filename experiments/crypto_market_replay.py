"""Deterministic public-data crypto replay for EXP-MKT-001.

This module never authenticates, signs, submits orders, or moves capital. Signals
formed at candle close are first executable at the following candle open.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence


API = "https://api.exchange.coinbase.com/products/{product}/candles"
GRANULARITY_SECONDS = 300
WINDOW_CANDLES = 300


@dataclass(frozen=True)
class Candle:
    timestamp: int
    low: float
    high: float
    open: float
    close: float
    volume: float


def fetch_candles(product: str, start: int, end: int) -> List[Candle]:
    """Fetch non-overlapping public candles and return ascending deduplicated data."""
    rows: Dict[int, Candle] = {}
    cursor = start
    span = GRANULARITY_SECONDS * WINDOW_CANDLES
    while cursor < end:
        window_end = min(end, cursor + span)
        query = urllib.parse.urlencode(
            {"start": _iso(cursor), "end": _iso(window_end), "granularity": GRANULARITY_SECONDS}
        )
        request = urllib.request.Request(
            API.format(product=product) + "?" + query,
            headers={"User-Agent": "z-look-jamaican-research/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        if not isinstance(payload, list):
            raise RuntimeError(f"unexpected {product} response: {payload!r}")
        for item in payload:
            candle = Candle(int(item[0]), *(float(value) for value in item[1:6]))
            if start <= candle.timestamp < end:
                rows[candle.timestamp] = candle
        cursor = window_end
        time.sleep(0.08)
    return [rows[key] for key in sorted(rows)]


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def write_candles(path: Path, candles: Sequence[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "low", "high", "open", "close", "volume"))
        for row in candles:
            writer.writerow((row.timestamp, row.low, row.high, row.open, row.close, row.volume))


def read_candles(path: Path) -> List[Candle]:
    with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
        return [
            Candle(int(row["timestamp"]), *(float(row[key]) for key in ("low", "high", "open", "close", "volume")))
            for row in csv.DictReader(handle)
        ]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def desired_positions(candles: Sequence[Candle], family: str) -> List[int]:
    close = [item.close for item in candles]
    volume = [item.volume * item.close for item in candles]
    output = [0] * len(candles)
    holding = 0
    for index in range(48, len(candles)):
        if family == "trend":
            holding = int(_mean(close[index - 12:index]) > _mean(close[index - 48:index]))
        elif family == "mean_reversion":
            window = close[index - 48:index]
            deviation = statistics.pstdev(window)
            z_score = (close[index] - _mean(window)) / deviation if deviation else 0.0
            if not holding and z_score < -1.5:
                holding = 1
            elif holding and z_score >= 0:
                holding = 0
        elif family == "breakout":
            if not holding and close[index] > max(close[index - 48:index]) and volume[index] > statistics.median(volume[index - 48:index]):
                holding = 1
            elif holding and close[index] < min(close[index - 24:index]):
                holding = 0
        else:
            raise ValueError(f"unknown strategy family: {family}")
        output[index] = holding
    return output


def _deterministic_fill(product: str, family: str, timestamp: int, probability: float) -> bool:
    digest = hashlib.sha256(f"{product}|{family}|{timestamp}".encode()).digest()
    draw = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
    return draw < probability


def replay(
    product: str,
    candles: Sequence[Candle],
    family: str,
    per_side_cost_bps: float,
    fill_probability: float = 0.995,
    failure_cost_bps: float = 1.0,
) -> Mapping[str, object]:
    """Replay the out-of-sample 40% using one-candle latency and cost stress."""
    if len(candles) < 500:
        raise ValueError("at least 500 candles are required")
    desired = desired_positions(candles, family)
    split = int(len(candles) * 0.60)
    position = 0
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    transitions = failures = 0
    gross_return = 0.0
    regime_returns: Dict[str, float] = {"active": 0.0, "quiet": 0.0, "weekend": 0.0, "weekday": 0.0}
    quote_volume = [item.volume * item.close for item in candles]
    train_volume_threshold = statistics.median(quote_volume[288:split])
    abs_returns = [0.0] + [abs(math.log(candles[i].close / candles[i - 1].close)) for i in range(1, len(candles))]
    train_vol_threshold = statistics.median(abs_returns[288:split])

    for index in range(split, len(candles) - 1):
        # Signal[index-1] becomes actionable at open[index].
        target = desired[index - 1]
        if target != position:
            if _deterministic_fill(product, family, candles[index].timestamp, fill_probability):
                equity *= 1.0 - per_side_cost_bps / 10000.0
                transitions += 1
                position = target
            else:
                equity *= 1.0 - failure_cost_bps / 10000.0
                failures += 1
        bar_return = position * (candles[index + 1].open / candles[index].open - 1.0)
        gross_return += bar_return
        equity *= 1.0 + bar_return
        active = quote_volume[index - 1] >= train_volume_threshold or abs_returns[index - 1] >= train_vol_threshold
        weekend = datetime.fromtimestamp(candles[index].timestamp, timezone.utc).weekday() >= 5
        regime_returns["active" if active else "quiet"] += bar_return
        regime_returns["weekend" if weekend else "weekday"] += bar_return
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1.0 - equity / peak)

    if position:
        equity *= 1.0 - per_side_cost_bps / 10000.0
        transitions += 1
    return {
        "product": product,
        "family": family,
        "test_candles": len(candles) - split,
        "test_start": _iso(candles[split].timestamp),
        "test_end": _iso(candles[-1].timestamp),
        "per_side_cost_bps": per_side_cost_bps,
        "fill_probability": fill_probability,
        "latency_candles": 1,
        "funding_cost_bps": 0.0,
        "transitions": transitions,
        "failed_transitions": failures,
        "gross_additive_return": round(gross_return, 8),
        "net_compounded_return": round(equity - 1.0, 8),
        "max_drawdown": round(max_drawdown, 8),
        "regime_gross_additive_returns": {key: round(value, 8) for key, value in regime_returns.items()},
    }


def evaluate(dataset_paths: Mapping[str, Path]) -> Mapping[str, object]:
    results = []
    datasets = {}
    for product, path in sorted(dataset_paths.items()):
        candles = read_candles(path)
        datasets[product] = {
            "path": path.as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "candles": len(candles),
            "start": _iso(candles[0].timestamp),
            "end": _iso(candles[-1].timestamp),
        }
        for family in ("trend", "mean_reversion", "breakout"):
            for cost in (5.0, 10.0, 20.0, 40.0):
                results.append(replay(product, candles, family, cost))
    return {
        "schema_version": 1,
        "experiment_id": "EXP-MKT-001",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "capital_used_usd": "0.00",
        "execution_mode": "offline_public_data_replay",
        "datasets": datasets,
        "method": {
            "train_fraction": 0.60,
            "out_of_sample_fraction": 0.40,
            "signal_latency_candles": 1,
            "cost_scenarios_per_side_bps": [5.0, 10.0, 20.0, 40.0],
            "primary_per_side_cost_bps": 20.0,
            "cost_components": "scenario bound covering fees, half-spread and slippage",
            "fill_probability": 0.995,
            "failed_transition_cost_bps": 1.0,
            "funding": "zero: unlevered spot long/flat only",
            "activity_proxy": "prior candle quote volume or absolute return above fixed training median",
        },
        "results": results,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect = subparsers.add_parser("collect")
    collect.add_argument("--days", type=int, default=60)
    collect.add_argument("--output", type=Path, required=True)
    run = subparsers.add_parser("evaluate")
    run.add_argument("--data-dir", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "collect":
        end = int(datetime.now(timezone.utc).timestamp()) // GRANULARITY_SECONDS * GRANULARITY_SECONDS
        start = end - args.days * 86400
        for product in ("BTC-USD", "ETH-USD"):
            write_candles(args.output / f"{product}-5m.csv.gz", fetch_candles(product, start, end))
        return 0
    paths = {product: args.data_dir / f"{product}-5m.csv.gz" for product in ("BTC-USD", "ETH-USD")}
    result = evaluate(paths)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
