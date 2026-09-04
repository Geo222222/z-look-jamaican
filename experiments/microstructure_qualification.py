"""Capture the frozen public BTC-USD microstructure qualification bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

from autonomous_kernel.market_data import MarketDataStore, build_microstructure_observation
from autonomous_kernel.execution_realism import evaluate_public_assumptions


FROZEN_AT_EPOCH = 1788274500
BASE = "https://api.exchange.coinbase.com/products/BTC-USD"
SURFACES = {
    "product_rules": BASE,
    "level2_book": f"{BASE}/book?level=2",
    "ticker": f"{BASE}/ticker",
    "recent_trades": f"{BASE}/trades?limit=100",
}
MAX_TOTAL_RAW_BYTES = 25_165_824


def fetch_surface(url: str, timeout: int = 20) -> tuple[Any, str, int, int, int, int]:
    started_ns = time.time_ns()
    started_at = started_ns // 1_000_000_000
    request = urllib.request.Request(url, headers={"User-Agent": "z-look-jamaican-public-research/1.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    received_at = time.time_ns() // 1_000_000_000
    duration_ms = max(0, (time.time_ns() - started_ns) // 1_000_000)
    return json.loads(raw), hashlib.sha256(raw).hexdigest(), started_at, received_at, duration_ms, len(raw)


def capture(root: Path) -> dict:
    payloads, hashes, started, received, durations = {}, {}, {}, {}, {}
    total_bytes = 0
    for name, url in SURFACES.items():
        payload, digest, started_at, received_at, duration_ms, size = fetch_surface(url)
        total_bytes += size
        if total_bytes > MAX_TOTAL_RAW_BYTES:
            raise RuntimeError("public payloads exceed preregistered size bound")
        payloads[name], hashes[name], started[name], received[name] = payload, digest, started_at, received_at
        durations[name] = duration_ms
    observed_at = int(time.time())
    sequence = int(payloads["level2_book"]["sequence"])
    observation_id = f"OBS-COINBASE-BTC-USD-MICRO-{sequence}"
    document = build_microstructure_observation(
        observation_id=observation_id, provider="coinbase_exchange_public", instrument="BTC-USD",
        payloads=payloads, payload_hashes=hashes, request_started_at=started, received_at=received, request_duration_ms=durations,
        observed_at=observed_at, max_event_age_seconds=30, max_transport_age_seconds=30,
        test_quantities=["0.001", "0.01"], depth_bands_bps=["5", "10"],
    )
    if document["raw"]["source_event_at"] <= FROZEN_AT_EPOCH:
        raise RuntimeError("microstructure snapshot is not prospective to preregistration")
    if document["quality"]["status"] != "VALID":
        raise RuntimeError(f"microstructure quality is {document['quality']['status']}")
    product = document["normalized"]["product_rules"]
    if product.get("status") != "online" or product.get("trading_disabled") is True:
        raise RuntimeError("product is not publicly reported as trade-enabled")
    persisted = MarketDataStore(root).persist(document)
    evaluation = evaluate_public_assumptions(persisted, {"half_spread_bps": "2", "slippage_bps": "3", "quantity_step": "0.0001", "price_step": "0.01", "minimum_notional_usd": "10", "available_capacity_base": "0.01", "fee_bps": "20", "latency_ms": "150", "fill_ratio": "0.5"})
    return {"experiment_id": "EXP-MICROSTRUCTURE-001", "observation_id": persisted["observation_id"], "quality": persisted["quality"]["status"], "total_raw_bytes": total_bytes, "normalized": persisted["normalized"], "assumption_evaluation": evaluation}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(json.dumps(capture(args.root.resolve()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
