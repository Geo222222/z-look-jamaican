from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence


POLICY_REF = "artifacts/evidence/market/z8-certification-policy-v1.json"
INVENTORY_REF = "artifacts/evidence/market/z8-certification-inventory-20260903.json"


class CertificationStatusError(RuntimeError):
    pass


def _load_json(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise CertificationStatusError("required certification evidence missing: %s" % path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CertificationStatusError("expected JSON object: %s" % path)
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _verify_compressed_journal(path: Path, *, compressed_sha256: str, journal_sha256: str) -> None:
    if not path.is_file():
        raise CertificationStatusError("compressed journal missing: %s" % path)
    payload = path.read_bytes()
    if _sha256_bytes(payload) != compressed_sha256:
        raise CertificationStatusError("compressed journal hash mismatch: %s" % path)
    try:
        raw = gzip.decompress(payload)
    except OSError as exc:
        raise CertificationStatusError("compressed journal cannot be decompressed: %s" % path) from exc
    if _sha256_bytes(raw) != journal_sha256:
        raise CertificationStatusError("decompressed journal hash mismatch: %s" % path)


def _verify_manifest(path: Path, expected_content_hash: str) -> None:
    manifest = _load_json(path)
    actual = manifest.get("integrity", {}).get("content_hash")
    if actual != expected_content_hash:
        raise CertificationStatusError("manifest content hash mismatch: %s" % path)
    summary = manifest.get("summary", {})
    if summary.get("gaps") or summary.get("out_of_order") or int(summary.get("duplicate_count", 0)) != 0:
        raise CertificationStatusError("admissible manifest violates sequence integrity: %s" % path)


def _verify_window(root: Path, window: Mapping[str, Any]) -> None:
    window_id = str(window.get("window_id", ""))
    if not window_id:
        raise CertificationStatusError("inventory window lacks identity")
    if window_id == "COINBASE-BTC-USD-MICROSTREAM-004":
        compressed = root / "artifacts/market_data/streams/COINBASE-BTC-USD-MICROSTREAM-004.jsonl.gz"
        manifest = root / "artifacts/market_data/streams/COINBASE-BTC-USD-MICROSTREAM-004.manifest.json"
    elif window_id == "COINBASE-BTC-USD-PROSPECTIVE-V2-1788402667122611620":
        base = root / "artifacts/evidence/market/exp-z8-prospective-002-data"
        compressed = base / (window_id + ".jsonl.gz")
        manifest = base / (window_id + ".manifest.json")
    else:
        raise CertificationStatusError("unrecognized admissible window requires validator support: %s" % window_id)

    _verify_compressed_journal(
        compressed,
        compressed_sha256=str(window["compressed_sha256"]),
        journal_sha256=str(window["raw_journal_sha256"]),
    )
    _verify_manifest(manifest, str(window["manifest_content_hash"]))
    if str(window.get("data_quality")) != "QUALIFIED":
        raise CertificationStatusError("inventory admitted a non-qualified data window: %s" % window_id)
    if int(window.get("resolved_adaptive_predictions", 0)) < 0:
        raise CertificationStatusError("resolved prediction count cannot be negative")


def _compute_status(policy: Mapping[str, Any], inventory: Mapping[str, Any]) -> Mapping[str, Any]:
    windows = inventory.get("admissible_windows", [])
    if not isinstance(windows, list):
        raise CertificationStatusError("admissible_windows must be a list")
    dates = {str(item["utc_date"]) for item in windows}
    buckets = {str(item["utc_six_hour_bucket"]) for item in windows}
    resolved = sum(int(item.get("resolved_adaptive_predictions", 0)) for item in windows)

    broad = policy["broad_historical"]
    minima = {
        "qualified_windows": (len(windows), int(broad["minimum_qualified_windows"])),
        "distinct_utc_dates": (len(dates), int(broad["minimum_distinct_utc_dates"])),
        "distinct_utc_six_hour_buckets": (
            len(buckets), int(broad["minimum_distinct_utc_six_hour_buckets"])
        ),
        "resolved_adaptive_predictions": (
            resolved, int(broad["minimum_total_resolved_adaptive_predictions"])
        ),
    }
    broad_ready = all(actual >= required for actual, required in minima.values())
    broad_status = "READY_FOR_PREREGISTERED_SCORING" if broad_ready else "DATA_BLOCKED"

    walk = policy["walk_forward"]
    required_folds = int(walk["minimum_chronological_evaluation_folds"])
    # Fold construction is intentionally forbidden before the broad evidence
    # minimum is met; doing otherwise would repeatedly score the same tiny sample.
    eligible_folds = required_folds if broad_ready else 0
    walk_status = "READY_FOR_PREREGISTERED_FOLD_SCORING" if broad_ready else "DATA_BLOCKED"

    return {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "inventory_id": inventory["inventory_id"],
        "broad_historical": {
            "status": broad_status,
            "minimums": {
                key: {"actual": actual, "required": required, "met": actual >= required}
                for key, (actual, required) in minima.items()
            },
            "performance_scoring_permitted": broad_ready,
        },
        "walk_forward": {
            "status": walk_status,
            "broad_evidence_prerequisite_met": broad_ready,
            "eligible_chronological_folds": eligible_folds,
            "required_chronological_folds": required_folds,
            "performance_scoring_permitted": broad_ready,
        },
        "prospective": {
            "status": "SINGLE_SESSION_PROSPECTIVE_MECHANISM_SUPPORTED",
            "source_experiment": "EXP-Z8-PROSPECTIVE-002",
        },
        "contextual": {
            "status": "ARCHITECTURALLY_BLOCKED_UNTIL_Z9"
        },
        "capital_effect": "NONE",
        "live_execution": False,
    }


def certification_status(root: Path) -> Mapping[str, Any]:
    root = root.resolve()
    policy = _load_json(root / POLICY_REF)
    inventory = _load_json(root / INVENTORY_REF)
    if policy.get("policy_id") != "Z8-CERTIFICATION-POLICY-V1":
        raise CertificationStatusError("unexpected certification policy identity")
    if policy.get("status") != "FROZEN_BEFORE_BROAD_OR_WALK_FORWARD_RESULT":
        raise CertificationStatusError("certification policy is not frozen")
    if inventory.get("policy_ref") != POLICY_REF:
        raise CertificationStatusError("inventory does not bind the frozen policy")
    for window in inventory.get("admissible_windows", []):
        _verify_window(root, window)

    status = _compute_status(policy, inventory)
    recorded_broad = inventory.get("broad_historical_progress", {})
    computed_broad = status["broad_historical"]
    expected = {
        "status": recorded_broad.get("status"),
        "qualified_windows": recorded_broad.get("qualified_windows"),
        "distinct_utc_dates": recorded_broad.get("distinct_utc_dates"),
        "distinct_utc_six_hour_buckets": recorded_broad.get("distinct_utc_six_hour_buckets"),
        "total_resolved_adaptive_predictions": recorded_broad.get("total_resolved_adaptive_predictions"),
    }
    actual = {
        "status": computed_broad["status"],
        "qualified_windows": computed_broad["minimums"]["qualified_windows"]["actual"],
        "distinct_utc_dates": computed_broad["minimums"]["distinct_utc_dates"]["actual"],
        "distinct_utc_six_hour_buckets": computed_broad["minimums"]["distinct_utc_six_hour_buckets"]["actual"],
        "total_resolved_adaptive_predictions": computed_broad["minimums"]["resolved_adaptive_predictions"]["actual"],
    }
    if actual != expected:
        raise CertificationStatusError("recorded certification inventory drift: expected %s, computed %s" % (expected, actual))
    return status


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Z8 empirical certification evidence and readiness")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = certification_status(args.root)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
