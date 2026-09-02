"""Operational entrypoint for the deterministic public market observer."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Mapping, Optional

from autonomous_kernel.joined_shadow_observer import join_observer_window
from autonomous_kernel.market_observer import ObserverConfig, run_observer_once
from autonomous_kernel.observer_storage import (
    compact_successful_raw_journal,
    observer_storage_status,
    persist_storage_block,
)


async def _guarded_tick(root: Path) -> Mapping[str, Any]:
    storage = observer_storage_status(root)
    if not storage["allowed"]:
        persist_storage_block(root, storage)
        return {
            "status": "BLOCKED_STORAGE",
            "observer_id": ObserverConfig.load(root).observer_id,
            "storage": storage,
        }

    result = dict(await run_observer_once(root))
    result["storage_before"] = storage
    if result.get("status") == "CAPTURED":
        try:
            result["joined_shadow_handoff"] = join_observer_window(root, result["window"])
        except Exception as exc:
            # A handoff defect must never fabricate a successor decision or
            # rewrite a successful public capture as market-data success/failure.
            # The explicit error remains visible to supervision and monitoring.
            result["joined_shadow_handoff"] = {
                "status": "ERROR",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        stream_id = str(result["window"]["stream_id"])
        result["raw_journal_cleanup"] = compact_successful_raw_journal(root, stream_id)
    return result


async def _guarded_daemon(root: Path, max_cycles: Optional[int]) -> list[Mapping[str, Any]]:
    config = ObserverConfig.load(root)
    results: list[Mapping[str, Any]] = []
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        results.append(await _guarded_tick(root))
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        await asyncio.sleep(config.cadence_seconds)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run zero-capital public microstructure observation")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--mode", choices=("once", "daemon"), default="once")
    parser.add_argument("--max-cycles", type=int, default=None)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.max_cycles is not None and args.max_cycles <= 0:
        parser.error("--max-cycles must be positive")

    if args.mode == "once":
        result = asyncio.run(_guarded_tick(root))
    else:
        result = asyncio.run(_guarded_daemon(root, args.max_cycles))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
