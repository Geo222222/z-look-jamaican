"""Compare compatible Apify Store snapshots without rewriting source evidence."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .apify_store_snapshot import COLLECTOR_REVISION


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("experiment_id") != "EXP-OPP-001":
        raise ValueError(f"not an EXP-OPP-001 snapshot: {path}")
    return value


def _query_index(snapshot: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {str(query["term"]): query for query in snapshot.get("queries", [])}


def compare_snapshots(first: Mapping[str, Any], last: Mapping[str, Any]) -> Dict[str, Any]:
    if first.get("collector_revision") != COLLECTOR_REVISION or last.get("collector_revision") != COLLECTOR_REVISION:
        raise ValueError("snapshots must use the current collector revision")
    first_queries = _query_index(first)
    last_queries = _query_index(last)
    if set(first_queries) != set(last_queries):
        raise ValueError("snapshot search terms differ")
    comparisons = []
    for term in sorted(first_queries):
        older = first_queries[term]
        newer = last_queries[term]
        older_items = {str(item["id"]): item for item in older.get("items", [])}
        newer_items = {str(item["id"]): item for item in newer.get("items", [])}
        comparisons.append(
            {
                "term": term,
                "aggregate_users_30_days_delta": newer["summary"]["aggregate_users_30_days"] - older["summary"]["aggregate_users_30_days"],
                "aggregate_runs_30_days_delta": sum(item.get("runs_30_days", 0) for item in newer_items.values()) - sum(item.get("runs_30_days", 0) for item in older_items.values()),
                "aggregate_reviews_delta": sum(item.get("review_count", 0) for item in newer_items.values()) - sum(item.get("review_count", 0) for item in older_items.values()),
                "new_result_ids": sorted(set(newer_items) - set(older_items)),
                "departed_result_ids": sorted(set(older_items) - set(newer_items)),
            }
        )
    return {
        "schema_version": 1,
        "experiment_id": "EXP-OPP-001",
        "collector_revision": COLLECTOR_REVISION,
        "first_captured_at": first["captured_at"],
        "last_captured_at": last["captured_at"],
        "comparisons": comparisons,
        "interpretation_warning": "Deltas describe the returned semantic result sets. Manual relevance curation and a multi-day window are required before promotion.",
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compare compatible Apify Store snapshots")
    parser.add_argument("--directory", type=Path, default=Path("artifacts/evidence/apify_store"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        paths = sorted(args.directory.glob("snapshot-*.json"))
        compatible = [(path, _load(path)) for path in paths]
        compatible = [(path, doc) for path, doc in compatible if doc.get("collector_revision") == COLLECTOR_REVISION]
        if len(compatible) < 2:
            print(json.dumps({"status": "insufficient_data", "compatible_snapshots": len(compatible), "required": 2}, indent=2))
            return 3
        result = compare_snapshots(compatible[0][1], compatible[-1][1])
        result["source_paths"] = [str(compatible[0][0]), str(compatible[-1][0])]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = args.output or args.directory / f"comparison-{timestamp}.json"
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "success", "output": str(output.resolve()), **result}, indent=2))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
