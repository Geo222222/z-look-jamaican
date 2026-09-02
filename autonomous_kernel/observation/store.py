from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from ..operations import canonical_hash
from .contracts import CanonicalObservation, ObservationContractError


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = (json.dumps(dict(value), indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_bytes(path, encoded)


def _safe_id(value: str) -> str:
    if not value or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in value):
        raise ValueError("batch_id must be non-empty and file-safe")
    return value


def _sha256_hex(value: str) -> str:
    if len(value) != 64:
        raise ValueError("source_sha256 must be a SHA-256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError("source_sha256 must be hexadecimal") from exc
    return value.lower()


class CanonicalBatchStore:
    """Durable, deterministic canonical-observation batches.

    Canonical batches are derived data.  Their manifest always binds them back
    to immutable source evidence, so they can be rebuilt without claiming to be
    the provider's original bytes.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.directory = self.root / "artifacts/market_data/canonical"
        self.index_path = self.root / "state/canonical_market_data.json"

    def persist_batch(
        self,
        *,
        batch_id: str,
        observations: Sequence[CanonicalObservation],
        source_ref: str,
        source_sha256: str,
    ) -> Mapping[str, Any]:
        batch_id = _safe_id(batch_id)
        source_sha256 = _sha256_hex(source_sha256)
        if not source_ref:
            raise ValueError("source_ref is required")
        if not observations:
            raise ValueError("canonical batch cannot be empty")

        observation_ids = [item.observation_id for item in observations]
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("canonical batch contains duplicate observation ids")

        wires = [item.to_wire() for item in observations]
        lines = [
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            for value in wires
        ]
        raw = ("\n".join(lines) + "\n").encode("utf-8")
        compressed = gzip.compress(raw, mtime=0)
        data_path = self.directory / (batch_id + ".jsonl.gz")
        manifest_path = self.directory / (batch_id + ".manifest.json")

        content_hashes = [str(value["integrity"]["content_hash"]) for value in wires]
        event_counts = Counter(item.event_type for item in observations)
        known_times = [item.known_at_ns for item in observations]
        manifest_body: Dict[str, Any] = {
            "schema_version": 1,
            "contract_version": "1.0",
            "batch_id": batch_id,
            "source_ref": source_ref,
            "source_sha256": source_sha256,
            "path": data_path.relative_to(self.root).as_posix(),
            "canonical_jsonl_sha256": hashlib.sha256(raw).hexdigest(),
            "compressed_sha256": hashlib.sha256(compressed).hexdigest(),
            "observation_set_hash": canonical_hash(content_hashes),
            "record_count": len(observations),
            "event_counts": dict(sorted(event_counts.items())),
            "instrument_ids": sorted({item.instrument.canonical_id for item in observations}),
            "providers": sorted({item.provider for item in observations}),
            "venues": sorted({item.venue for item in observations}),
            "first_known_at_ns": min(known_times),
            "last_known_at_ns": max(known_times),
        }
        manifest = dict(manifest_body)
        manifest["integrity"] = {
            "algorithm": "sha256",
            "content_hash": canonical_hash(manifest_body),
        }

        if manifest_path.exists() or data_path.exists():
            if not (manifest_path.exists() and data_path.exists()):
                raise RuntimeError("partial canonical batch already exists")
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing != manifest or data_path.read_bytes() != compressed:
                raise RuntimeError("canonical batch identity conflict")
            self.rebuild_index()
            return existing

        _atomic_bytes(data_path, compressed)
        _atomic_json(manifest_path, manifest)
        self.rebuild_index()
        return manifest

    def rebuild_index(self) -> Mapping[str, Any]:
        items: List[Mapping[str, Any]] = []
        if self.directory.is_dir():
            for path in sorted(self.directory.glob("*.manifest.json")):
                manifest = json.loads(path.read_text(encoding="utf-8"))
                errors = _validate_manifest_and_batch(self.root, path, manifest)
                if errors:
                    raise RuntimeError("%s: %s" % (path.name, "; ".join(errors)))
                items.append(
                    {
                        "batch_id": manifest["batch_id"],
                        "manifest_path": path.relative_to(self.root).as_posix(),
                        "path": manifest["path"],
                        "source_ref": manifest["source_ref"],
                        "source_sha256": manifest["source_sha256"],
                        "record_count": manifest["record_count"],
                        "event_counts": manifest["event_counts"],
                        "instrument_ids": manifest["instrument_ids"],
                        "providers": manifest["providers"],
                        "venues": manifest["venues"],
                        "first_known_at_ns": manifest["first_known_at_ns"],
                        "last_known_at_ns": manifest["last_known_at_ns"],
                        "observation_set_hash": manifest["observation_set_hash"],
                        "manifest_content_hash": manifest["integrity"]["content_hash"],
                    }
                )
        index = {
            "schema_version": 1,
            "contract_version": "1.0",
            "authority": "rebuildable canonical observations derived from immutable provider evidence",
            "items": items,
        }
        _atomic_json(self.index_path, index)
        return index


def _validate_manifest_and_batch(root: Path, manifest_path: Path, manifest: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    body = {key: value for key, value in manifest.items() if key != "integrity"}
    if manifest.get("schema_version") != 1 or manifest.get("contract_version") != "1.0":
        errors.append("unsupported canonical batch schema")
    if canonical_hash(body) != manifest.get("integrity", {}).get("content_hash"):
        errors.append("manifest content hash mismatch")
        return errors
    data_path = (root / str(manifest.get("path", ""))).resolve()
    try:
        data_path.relative_to(root.resolve())
    except ValueError:
        errors.append("canonical batch path escapes repository")
        return errors
    if not data_path.is_file():
        errors.append("canonical batch data is missing")
        return errors
    try:
        compressed = data_path.read_bytes()
        if hashlib.sha256(compressed).hexdigest() != manifest.get("compressed_sha256"):
            errors.append("compressed canonical batch hash mismatch")
            return errors
        raw = gzip.decompress(compressed)
    except (OSError, gzip.BadGzipFile) as exc:
        errors.append("canonical batch cannot be decompressed: %s" % exc)
        return errors
    if hashlib.sha256(raw).hexdigest() != manifest.get("canonical_jsonl_sha256"):
        errors.append("canonical JSONL hash mismatch")
        return errors

    content_hashes: List[str] = []
    event_counts: Counter[str] = Counter()
    instrument_ids = set()
    providers = set()
    venues = set()
    known_times: List[int] = []
    record_count = 0
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            item = CanonicalObservation.from_wire(value)
        except (json.JSONDecodeError, ObservationContractError, ValueError, TypeError) as exc:
            errors.append("canonical record %d invalid: %s" % (line_number, exc))
            continue
        record_count += 1
        content_hashes.append(item.content_hash())
        event_counts[item.event_type] += 1
        instrument_ids.add(item.instrument.canonical_id)
        providers.add(item.provider)
        venues.add(item.venue)
        known_times.append(item.known_at_ns)

    if record_count != manifest.get("record_count"):
        errors.append("canonical batch record_count mismatch")
    if canonical_hash(content_hashes) != manifest.get("observation_set_hash"):
        errors.append("canonical observation_set_hash mismatch")
    if dict(sorted(event_counts.items())) != manifest.get("event_counts"):
        errors.append("canonical event_counts mismatch")
    if sorted(instrument_ids) != manifest.get("instrument_ids"):
        errors.append("canonical instrument_ids mismatch")
    if sorted(providers) != manifest.get("providers"):
        errors.append("canonical providers mismatch")
    if sorted(venues) != manifest.get("venues"):
        errors.append("canonical venues mismatch")
    if known_times:
        if min(known_times) != manifest.get("first_known_at_ns") or max(known_times) != manifest.get("last_known_at_ns"):
            errors.append("canonical known-time bounds mismatch")
    return errors


def validate_canonical_market_data_store(root: Path) -> List[str]:
    errors: List[str] = []
    index_path = root / "state/canonical_market_data.json"
    full_kernel = (root / "state/current_state.json").is_file()
    if not index_path.is_file():
        return ["missing required state file: state/canonical_market_data.json"] if full_kernel else []
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ["state/canonical_market_data.json: unreadable JSON: %s" % exc]
    if index.get("schema_version") != 1 or index.get("contract_version") != "1.0" or not isinstance(index.get("items"), list):
        return ["state/canonical_market_data.json: invalid schema"]
    for item in index["items"]:
        manifest_path = (root / str(item.get("manifest_path", ""))).resolve()
        try:
            manifest_path.relative_to(root.resolve())
        except ValueError:
            errors.append("canonical market index manifest path escapes repository")
            continue
        if not manifest_path.is_file():
            errors.append("canonical market index references missing manifest")
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append("%s: unreadable manifest: %s" % (item.get("manifest_path"), exc))
            continue
        errors.extend(
            "%s: %s" % (item.get("manifest_path"), error)
            for error in _validate_manifest_and_batch(root, manifest_path, manifest)
        )
        if manifest.get("integrity", {}).get("content_hash") != item.get("manifest_content_hash"):
            errors.append("canonical market index manifest hash mismatch for %s" % item.get("batch_id"))
    return errors
