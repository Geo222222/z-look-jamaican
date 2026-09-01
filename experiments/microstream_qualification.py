"""Run the preregistered bounded public microstructure stream capture."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import websockets

from autonomous_kernel.microstream import StreamJournal


ENDPOINT = "wss://advanced-trade-ws.coinbase.com"
STREAM_ID = "COINBASE-BTC-USD-MICROSTREAM-001"
CAPTURE_SECONDS = 60
MAX_MESSAGES = 100_000
MAX_BYTES = 67_108_864


async def capture(root: Path) -> dict:
    journal = StreamJournal(root, STREAM_ID)
    accepted = len(journal.records())
    total_bytes = sum(len(json.dumps(record, separators=(",", ":"))) for record in journal.records())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CAPTURE_SECONDS
    async with websockets.connect(ENDPOINT, open_timeout=20, max_size=MAX_BYTES, ping_interval=20, ping_timeout=20) as socket:
        for channel in ("level2", "market_trades", "heartbeats"):
            await socket.send(json.dumps({"type": "subscribe", "product_ids": ["BTC-USD"], "channel": channel}, separators=(",", ":")))
        while loop.time() < deadline and accepted < MAX_MESSAGES and total_bytes < MAX_BYTES:
            remaining = deadline - loop.time()
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=min(10, max(0.1, remaining)))
            except asyncio.TimeoutError as exc:
                if loop.time() < deadline:
                    raise RuntimeError("public stream idle timeout") from exc
                break
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            total_bytes += len(raw.encode("utf-8"))
            if total_bytes > MAX_BYTES:
                raise RuntimeError("stream exceeded preregistered byte bound")
            message = json.loads(raw)
            if journal.ingest(message, time.time_ns()):
                accepted += 1
    finalized = journal.finalize([50, 90, 99, 100])
    summary = finalized["manifest"]["summary"]
    if not set(("level2", "market_trades", "heartbeats")).issubset(summary["channels"]):
        raise RuntimeError("required public channel missing")
    if summary["level2_snapshot_count"] < 1 or summary["level2_update_count"] < 1 or summary["market_trade_message_count"] < 1 or summary["heartbeat_message_count"] < 1:
        raise RuntimeError("required snapshot/update/trade/heartbeat evidence missing")
    return {"experiment_id": "EXP-MICROSTREAM-001", "stream_id": STREAM_ID, "accepted_messages": accepted, "uncompressed_bytes": total_bytes, "summary": {key: value for key, value in summary.items() if key != "final_book"}, "observation_id": finalized["observation"]["observation_id"], "quality": finalized["observation"]["quality"]["status"], "authentication_used": False, "capital_used_usd": "0.00"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(json.dumps(asyncio.run(capture(args.root.resolve())), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
