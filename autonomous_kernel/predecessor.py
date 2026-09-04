"""Read-only verification for untrusted predecessor evidence manifests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


class PredecessorVerificationError(ValueError):
    """Raised when predecessor evidence cannot be verified exactly."""


def verify_manifest(manifest_path: Path, source_root: Path) -> Mapping[str, Any]:
    """Verify declared public/non-secret files without importing or executing them."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise PredecessorVerificationError("unsupported predecessor manifest schema")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise PredecessorVerificationError("manifest files must be a non-empty list")

    verified = []
    resolved_root = source_root.resolve()
    for entry in files:
        relative = Path(str(entry.get("path", "")))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise PredecessorVerificationError(f"unsafe manifest path: {relative}")
        candidate = (resolved_root / relative).resolve()
        try:
            candidate.relative_to(resolved_root)
        except ValueError as exc:
            raise PredecessorVerificationError(f"path escapes source root: {relative}") from exc
        if not candidate.is_file():
            raise PredecessorVerificationError(f"missing predecessor file: {relative.as_posix()}")
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        expected = str(entry.get("sha256", "")).lower()
        if digest != expected:
            raise PredecessorVerificationError(
                f"hash mismatch for {relative.as_posix()}: expected {expected}, observed {digest}"
            )
        verified.append({"path": relative.as_posix(), "sha256": digest, "bytes": candidate.stat().st_size})

    return {
        "status": "verified",
        "manifest_id": manifest.get("manifest_id"),
        "authority": "historical_evidence_only",
        "source_root": str(resolved_root),
        "files": verified,
        "writes_performed": False,
        "code_executed_from_predecessor": False,
    }
