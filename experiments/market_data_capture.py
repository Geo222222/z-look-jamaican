"""Bounded public-data capture for the provider-neutral market-data plane."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from autonomous_kernel.market_data import MarketDataStore, build_candle_observation
from experiments.crypto_market_replay import fetch_candles


def capture(root: Path, now: int | None = None) -> list[dict]:
    observed_at = int(now if now is not None else datetime.now(timezone.utc).timestamp())
    outputs = []
    store = MarketDataStore(root)
    for instrument in ("BTC-USD", "ETH-USD"):
        candles = [item for item in fetch_candles(instrument, observed_at - 3600, observed_at) if item.timestamp + 300 <= observed_at]
        if not candles:
            raise RuntimeError(f"no completed public candle for {instrument}")
        candle = candles[-1]
        received_at = int(datetime.now(timezone.utc).timestamp()) if now is None else observed_at
        document = build_candle_observation(
            observation_id=f"OBS-COINBASE-{instrument}-{candle.timestamp}",
            provider="coinbase_exchange_public", instrument=instrument, interval_seconds=300,
            candle_start_at=candle.timestamp, received_at=received_at, observed_at=received_at,
            open_price=str(candle.open), high_price=str(candle.high), low_price=str(candle.low),
            close_price=str(candle.close), volume=str(candle.volume),
            max_event_age_seconds=900, max_transport_age_seconds=900,
        )
        outputs.append(dict(store.persist(document)))
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    observations = capture(args.root.resolve())
    print(json.dumps({"captured": [item["observation_id"] for item in observations], "writes": len(observations)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
