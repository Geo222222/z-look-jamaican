"""Storage circuit breaker and safe raw-journal compaction for market observation."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .market_data import _atomic_json


def _load_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _observer_files(root: Path) -> list[Path]:
    patterns = (
        (root / "runtime/market_stream", "COINBASE-BTC-USD-OBS-*"),
        (root / "artifacts/market_data/streams", "COINBASE-BTC-USD-OBS-*"),
        (root / "artifacts/market_data/observations", "OBS-COINBASE-BTC-USD-OBS-*"),
        (root / "artifacts/evidence/market/observer", "**/*"),
        (root / "evidence/audits/market_observer", "**/*"),
    )
    files: set[Path] = set()
    for directory, pattern in patterns:
        if not directory.exists():
            continue
        for path in directory.glob(pattern):
            if path.is_file():
                files.add(path.resolve())
    return sorted(files)


def observer_storage_status(root: Path) -> Mapping[str, Any]:
    root = root.resolve()
    config = _load_json(root / "config/market_observer.json")
    maximum_bytes = int(config["maximum_observer_storage_bytes"])
    minimum_free_bytes = int(config["minimum_free_disk_bytes"])
    files = _observer_files(root)
    used_bytes = sum(path.stat().st_size for path in files)
    free_bytes = shutil.disk_usage(root).free
    allowed = used_bytes < maximum_bytes and free_bytes >= minimum_free_bytes
    reasons = []
    if used_bytes >= maximum_bytes:
        reasons.append("observer_storage_limit_reached")
    if free_bytes < minimum_free_bytes:
        reasons.append("minimum_free_disk_reserve_breached")
    return {
        "schema_version": 1,
        "allowed": allowed,
        "used_bytes": used_bytes,
        "maximum_observer_storage_bytes": maximum_bytes,
        "free_bytes": free_bytes,
        "minimum_free_disk_bytes": minimum_free_bytes,
        "file_count": len(files),
        "reasons": reasons,
    }


def persist_storage_block(root: Path, status: Mapping[str, Any]) -> None:
    path = root / "state/market_observer.json"
    state = dict(_load_json(path))
    state["status"] = "DEGRADED"
    state["updated_at"] = _iso_now()
    state["storage_guard"] = dict(status)
    _atomic_json(path, state)


def compact_successful_raw_journal(root: Path, stream_id: str) -> Mapping[str, Any]:
    """Delete redundant raw journal only after compressed evidence and audit verify."""
    root = root.resolve()
    raw_path = root / "runtime/market_stream" / (stream_id + ".jsonl")
    manifest_path = root / "artifacts/market_data/streams" / (stream_id + ".manifest.json")
    bundle_path = root / "artifacts/market_data/streams" / (stream_id + ".jsonl.gz")
    state = _load_json(root / "state/market_observer.json")
    windows = [item for item in state.get("windows", []) if item.get("stream_id") == stream_id]
    if not windows:
        return {"status": "SKIPPED", "reason": "successful observer window not found"}
    audit_path = root / str(windows[-1].get("audit_path", ""))
    if not raw_path.is_file():
        return {"status": "ALREADY_COMPACTED", "stream_id": stream_id}
    if not manifest_path.is_file() or not bundle_path.is_file() or not audit_path.is_file():
        return {"status": "SKIPPED", "reason": "required manifest, bundle, or audit missing"}

    manifest = _load_json(manifest_path)
    audit = _load_json(audit_path)
    if audit.get("outcome") != "VALID_PUBLIC_OBSERVATION_WINDOW":
        return {"status": "SKIPPED", "reason": "observer audit is not a valid window"}
    compressed = bundle_path.read_bytes()
    if hashlib.sha256(compressed).hexdigest() != manifest.get("compressed_sha256"):
        return {"status": "SKIPPED", "reason": "compressed bundle hash mismatch"}
    try:
        decompressed = gzip.decompress(compressed)
    except gzip.BadGzipFile:
        return {"status": "SKIPPED", "reason": "compressed bundle is unreadable"}
    if hashlib.sha256(decompressed).hexdigest() != manifest.get("journal_sha256"):
        return {"status": "SKIPPED", "reason": "decompressed journal hash mismatch"}
    if hashlib.sha256(raw_path.read_bytes()).hexdigest() != manifest.get("journal_sha256"):
        return {"status": "SKIPPED", "reason": "runtime raw journal hash mismatch"}

    raw_path.unlink()
    return {
        "status": "COMPACTED",
        "stream_id": stream_id,
        "retained_manifest": manifest_path.relative_to(root).as_posix(),
        "retained_compressed_bundle": bundle_path.relative_to(root).as_posix(),
        "retained_audit": audit_path.relative_to(root).as_posix(),
    }
