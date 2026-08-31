"""Prospective, zero-capital shadow observer for EXP-MKT-002."""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping

from experiments.crypto_market_replay import Candle, desired_positions, fetch_candles


THRESHOLDS = {
    "BTC-USD": {"quote_volume": 794705.2274048326, "absolute_log_return": 0.0004311985809890824},
    "ETH-USD": {"quote_volume": 303456.35020704503, "absolute_log_return": 0.0005949453936603113},
}
PER_SIDE_COST_BPS = 20.0


def make_decision(product: str, candles: List[Candle], observed_at: int) -> Mapping[str, object]:
    """Create one decision whose actionable time is strictly after observation."""
    complete = [row for row in candles if row.timestamp + 300 <= observed_at]
    if len(complete) < 50:
        raise ValueError("insufficient completed candle history")
    signal = complete[-1]
    baseline_target = desired_positions(complete, "breakout")[-1]
    absolute_return = abs(math.log(signal.close / complete[-2].close))
    active = signal.volume * signal.close >= THRESHOLDS[product]["quote_volume"] or absolute_return >= THRESHOLDS[product]["absolute_log_return"]
    weekday = datetime.fromtimestamp(signal.timestamp, timezone.utc).weekday() < 5
    target = int(bool(baseline_target and active and weekday))
    actionable_at = max(signal.timestamp + 600, (observed_at // 300 + 1) * 300)
    return {
        "id": f"SHADOW-{product}-{signal.timestamp}",
        "product": product,
        "observed_at": observed_at,
        "signal_candle_timestamp": signal.timestamp,
        "actionable_at": actionable_at,
        "target_position": target,
        "baseline_breakout_target": baseline_target,
        "active": active,
        "weekday": weekday,
        "quote_volume": signal.volume * signal.close,
        "absolute_log_return": absolute_return,
        "status": "pending",
    }


def reconcile(decisions: List[Dict[str, object]], candles_by_product: Mapping[str, List[Candle]]) -> None:
    for product in THRESHOLDS:
        product_decisions = sorted((item for item in decisions if item["product"] == product), key=lambda item: int(item["actionable_at"]))
        opens = {row.timestamp: row.open for row in candles_by_product[product]}
        for index in range(len(product_decisions) - 1):
            current = product_decisions[index]
            following = product_decisions[index + 1]
            if current["status"] != "pending":
                continue
            start = int(current["actionable_at"])
            end = int(following["actionable_at"])
            if start not in opens or end not in opens:
                continue
            previous_target = int(product_decisions[index - 1]["target_position"]) if index else 0
            target = int(current["target_position"])
            gross = target * (opens[end] / opens[start] - 1.0)
            transition_cost = abs(target - previous_target) * PER_SIDE_COST_BPS / 10000.0
            current.update({
                "status": "resolved",
                "resolved_at": int(datetime.now(timezone.utc).timestamp()),
                "evaluation_end": end,
                "gross_return": gross,
                "transition_cost": transition_cost,
                "net_return": gross - transition_cost,
            })


def _write_atomic(path: Path, document: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def observe(state_path: Path, now: int | None = None) -> Mapping[str, object]:
    observed_at = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    start = observed_at - 3 * 86400
    candles = {product: fetch_candles(product, start, observed_at) for product in THRESHOLDS}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {"schema_version": 1, "experiment_id": "EXP-MKT-002", "mode": "zero_capital_shadow", "decisions": []}
    reconcile(state["decisions"], candles)
    existing = {item["id"] for item in state["decisions"]}
    for product in THRESHOLDS:
        decision = dict(make_decision(product, candles[product], observed_at))
        if decision["id"] not in existing:
            state["decisions"].append(decision)
    state["updated_at"] = datetime.fromtimestamp(observed_at, timezone.utc).isoformat().replace("+00:00", "Z")
    state["summary"] = {
        "total": len(state["decisions"]),
        "resolved": sum(item["status"] == "resolved" for item in state["decisions"]),
        "eligible_long": sum(int(item["target_position"]) for item in state["decisions"]),
        "net_return_sum": sum(float(item.get("net_return", 0.0)) for item in state["decisions"]),
    }
    _write_atomic(state_path, state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=Path("state/market_shadow.json"))
    args = parser.parse_args()
    state = observe(args.state)
    print(json.dumps(state["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
