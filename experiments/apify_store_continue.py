"""Cold-start preflight for the recurring EXP-OPP-001 observation loop.

This module makes no external requests and performs no writes. It derives the
next safe action from validated repository state, compatible evidence artifacts,
and an explicit clock so a scheduled run does not depend on chat history.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from autonomous_kernel.store import StateValidationError, load_json, repository_root, validate
from experiments.apify_store_snapshot import COLLECTOR_REVISION, DEFAULT_TERMS


EXPERIMENT_ID = "EXP-OPP-001"
TASK_ID = "TASK-EXP-001"
AUTOMATION_ID = "AUTO-EXP-OPP-001"
AUTOMATION_EXTERNAL_ID = "continue-exp-opp-001-daily"
DEFAULT_MINIMUM_HOURS = 20.0
DEFAULT_TARGET_DAYS = 7


class ContinuationError(ValueError):
    """Raised when durable continuation state is incomplete or inconsistent."""


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ContinuationError(f"timestamp must include a timezone: {value}")
    return parsed.astimezone(timezone.utc)


def _compatible_snapshot(path: Path) -> Optional[Dict[str, Any]]:
    document = load_json(path)
    if document.get("experiment_id") != EXPERIMENT_ID:
        return None
    if document.get("collector_revision") != COLLECTOR_REVISION:
        return None
    if document.get("schema_version") != 1:
        raise ContinuationError(f"{path}: compatible snapshot has an invalid schema version")
    if document.get("method") != "unauthenticated HTTP GET only":
        raise ContinuationError(f"{path}: compatible snapshot has an unexpected collection method")
    if document.get("terms") != list(DEFAULT_TERMS):
        raise ContinuationError(f"{path}: compatible snapshot terms differ from the experiment contract")
    queries = document.get("queries")
    if not isinstance(queries, list) or len(queries) != len(DEFAULT_TERMS):
        raise ContinuationError(f"{path}: compatible snapshot query count is invalid")
    if any(query.get("http_status") != 200 for query in queries if isinstance(query, Mapping)):
        raise ContinuationError(f"{path}: compatible snapshot contains an unsuccessful query")
    if any(not isinstance(query, Mapping) for query in queries):
        raise ContinuationError(f"{path}: compatible snapshot contains a malformed query")
    captured_at = document.get("captured_at")
    if not isinstance(captured_at, str):
        raise ContinuationError(f"{path}: compatible snapshot lacks captured_at")
    return {
        "path": path.as_posix(),
        "captured_at": parse_timestamp(captured_at),
    }


def inspect_snapshots(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    compatible = []
    for path in sorted(paths):
        candidate = _compatible_snapshot(path)
        if candidate is not None:
            compatible.append(candidate)
    compatible.sort(key=lambda item: item["captured_at"])
    return compatible


def decide(
    snapshots: Sequence[Mapping[str, Any]],
    now: datetime,
    resume_not_before: datetime,
    minimum_hours: float = DEFAULT_MINIMUM_HOURS,
    target_days: int = DEFAULT_TARGET_DAYS,
) -> Dict[str, Any]:
    if now.tzinfo is None:
        raise ContinuationError("decision clock must include a timezone")
    if minimum_hours <= 0 or target_days <= 0:
        raise ContinuationError("minimum hours and target days must be positive")
    if not snapshots:
        raise ContinuationError("no compatible collector-revision-2 snapshot exists")

    latest = max(item["captured_at"] for item in snapshots)
    distinct_dates = sorted({item["captured_at"].date().isoformat() for item in snapshots})
    cadence_not_before = latest + timedelta(hours=minimum_hours)
    next_not_before = max(cadence_not_before, resume_not_before)

    if len(distinct_dates) >= target_days:
        action = "finalize"
        reason = "target_date_distinct_snapshots_reached"
    elif now.astimezone(timezone.utc) >= next_not_before:
        action = "capture"
        reason = "cadence_elapsed_and_target_not_reached"
    else:
        action = "wait"
        reason = "cadence_not_elapsed"

    return {
        "action": action,
        "reason": reason,
        "compatible_snapshot_count": len(snapshots),
        "date_distinct_snapshot_count": len(distinct_dates),
        "target_date_distinct_snapshots": target_days,
        "latest_captured_at": latest.isoformat().replace("+00:00", "Z"),
        "next_capture_not_before": next_not_before.isoformat().replace("+00:00", "Z"),
        "observed_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def preflight(
    root: Path,
    now: Optional[datetime] = None,
    minimum_hours: float = DEFAULT_MINIMUM_HOURS,
    target_days: int = DEFAULT_TARGET_DAYS,
) -> Dict[str, Any]:
    root = root.resolve()
    checks = validate(root)
    resume = load_json(root / "state/resume.json")
    backlog = load_json(root / "state/backlog.json")
    deployments = load_json(root / "state/deployments.json")

    tasks = {item.get("id"): item for item in backlog.get("items", [])}
    task = tasks.get(TASK_ID)
    if not task:
        raise ContinuationError(f"{TASK_ID} is missing from the durable backlog")

    automations = {item.get("id"): item for item in deployments.get("items", [])}
    automation = automations.get(AUTOMATION_ID)
    if not automation or automation.get("external_id") != AUTOMATION_EXTERNAL_ID:
        raise ContinuationError(f"{AUTOMATION_ID} is not registered with the expected external id")

    not_before_value = resume.get("next_observation_not_before")
    snapshots = inspect_snapshots((root / "artifacts/evidence/apify_store").glob("snapshot-*.json"))
    observed_at = now or datetime.now(timezone.utc)
    task_status = task.get("status")
    automation_status = str(automation.get("status", ""))

    if task_status == "completed":
        if not automation_status.startswith("paused"):
            raise ContinuationError(f"{AUTOMATION_ID} must be paused after experiment completion")
        if AUTOMATION_ID in resume.get("active_automation_ids", []):
            raise ContinuationError(f"{AUTOMATION_ID} is paused but remains active in the resume checkpoint")
        if not snapshots:
            raise ContinuationError("completed experiment has no compatible snapshot")
        latest = max(item["captured_at"] for item in snapshots)
        distinct_dates = sorted({item["captured_at"].date().isoformat() for item in snapshots})
        decision = {
            "action": "closed",
            "reason": "experiment_completed_and_automation_paused",
            "compatible_snapshot_count": len(snapshots),
            "date_distinct_snapshot_count": len(distinct_dates),
            "target_date_distinct_snapshots": target_days,
            "latest_captured_at": latest.isoformat().replace("+00:00", "Z"),
            "next_capture_not_before": None,
            "observed_at": observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    else:
        if task_status != "in_progress" or TASK_ID not in resume.get("active_task_ids", []):
            raise ContinuationError(f"{TASK_ID} is neither active nor cleanly completed")
        if not automation_status.startswith("active"):
            raise ContinuationError(f"{AUTOMATION_ID} is not active in durable state")
        if AUTOMATION_ID not in resume.get("active_automation_ids", []):
            raise ContinuationError(f"{AUTOMATION_ID} is missing from the resume checkpoint")
        if not isinstance(not_before_value, str):
            raise ContinuationError("resume checkpoint lacks next_observation_not_before")
        decision = decide(
            snapshots,
            observed_at,
            parse_timestamp(not_before_value),
            minimum_hours,
            target_days,
        )
    decision.update(
        {
            "status": "ok",
            "experiment_id": EXPERIMENT_ID,
            "task_id": TASK_ID,
            "automation_id": AUTOMATION_ID,
            "automation_external_id": AUTOMATION_EXTERNAL_ID,
            "root": str(root),
            "validation_checks": checks,
            "snapshot_paths": [item["path"] for item in snapshots],
            "capture_command": "python -m experiments.apify_store_snapshot --limit 25",
            "compare_command": "python -m experiments.apify_store_compare",
        }
    )
    return decision


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Derive the next EXP-OPP-001 action from durable state")
    parser.add_argument("--root", type=Path, default=repository_root())
    parser.add_argument("--at", help="inject an ISO-8601 decision time for replay/testing")
    parser.add_argument("--minimum-hours", type=float, default=DEFAULT_MINIMUM_HOURS)
    parser.add_argument("--target-days", type=int, default=DEFAULT_TARGET_DAYS)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = preflight(
            args.root,
            parse_timestamp(args.at) if args.at else None,
            args.minimum_hours,
            args.target_days,
        )
    except (ContinuationError, StateValidationError, OSError, ValueError) as exc:
        errors = exc.errors if isinstance(exc, StateValidationError) else [str(exc)]
        print(json.dumps({"status": "invalid", "errors": errors}, indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
