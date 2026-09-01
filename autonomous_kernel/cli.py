"""Command-line interface for deterministic kernel state operations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from .store import (
    StateValidationError,
    next_work,
    recover_pending,
    repository_root,
    status_summary,
    transition,
    update_task,
    validate,
)
from .monitor import monitor_snapshot
from .predecessor import PredecessorVerificationError, verify_manifest


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autokernel", description="Operate validated durable autonomous state")
    parser.add_argument("--root", type=Path, default=repository_root(), help="repository root (defaults to package root)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate schemas, references, and Governor snapshots")
    subparsers.add_parser("status", help="show a machine-readable resume summary")
    subparsers.add_parser("next-work", help="select the highest-scored ready task whose dependencies are complete")
    subparsers.add_parser("recover", help="idempotently roll a prepared state transaction forward")
    monitor_parser = subparsers.add_parser("monitor_snapshot", help="emit the authoritative read-only observer snapshot")
    monitor_parser.add_argument("--json", action="store_true", help="emit JSON (the only supported representation)")
    predecessor_parser = subparsers.add_parser(
        "predecessor_verify", help="verify hashed predecessor evidence without importing or executing it"
    )
    predecessor_parser.add_argument("--manifest", type=Path, required=True)
    predecessor_parser.add_argument("--source-root", type=Path, required=True)

    transition_parser = subparsers.add_parser("transition", help="record an allowed root-state transition")
    transition_parser.add_argument("--to", required=True, dest="new_state")
    transition_parser.add_argument("--trigger", required=True)
    transition_parser.add_argument("--decision-id", required=True)
    transition_parser.add_argument("--evidence", action="append", required=True)

    task_parser = subparsers.add_parser("task-status", help="update a backlog task and refresh the resume pointer")
    task_parser.add_argument("--task-id", required=True)
    task_parser.add_argument("--status", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "validate":
            checks = validate(root)
            _print({"status": "ok", "root": str(root), "checks": checks})
        elif args.command == "status":
            _print(status_summary(root))
        elif args.command == "next-work":
            _print({"status": "ok", "next_work": next_work(root)})
        elif args.command == "recover":
            _print(recover_pending(root))
        elif args.command == "monitor_snapshot":
            _print(monitor_snapshot(root))
        elif args.command == "predecessor_verify":
            _print(verify_manifest(args.manifest.resolve(), args.source_root.resolve()))
        elif args.command == "transition":
            record = transition(args.new_state, args.trigger, args.decision_id, args.evidence, root)
            _print({"status": "ok", "transition": record})
        elif args.command == "task-status":
            task, candidate = update_task(args.task_id, args.status, root)
            _print({"status": "ok", "task": task, "next_work": candidate})
        else:
            parser.error(f"unknown command: {args.command}")
    except StateValidationError as exc:
        _print({"status": "invalid", "errors": exc.errors})
        return 2
    except (KeyError, RuntimeError, ValueError, PredecessorVerificationError) as exc:
        _print({"status": "error", "error": str(exc)})
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
