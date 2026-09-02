"""Operational entrypoint for the deterministic public market observer."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from autonomous_kernel.market_observer import run_observer_daemon, run_observer_once


def main() -> int:
    parser = argparse.ArgumentParser(description="Run zero-capital public microstructure observation")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--mode", choices=("once", "daemon"), default="once")
    parser.add_argument("--max-cycles", type=int, default=None)
    args = parser.parse_args()
    root = args.root.resolve()

    if args.mode == "once":
        result = asyncio.run(run_observer_once(root))
    else:
        result = asyncio.run(run_observer_daemon(root, max_cycles=args.max_cycles))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
