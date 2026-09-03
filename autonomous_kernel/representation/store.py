from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from ..operations import canonical_hash
from .contracts import RepresentationContractError, RepresentationFrame


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(value), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _source_batch(value: Mapping[str, Any]) -> Dict[str, str]:
    required = ("batch_id", "manifest_ref", "manifest_content_hash")
    result = {key: str(value.get(key, "")) for key in required}
    if any(not result[key] for key in required):
        raise ValueError("source batch requires batch_id, manifest_ref, and manifest_content_hash")
    digest = result["manifest_content_hash"]
    if len(digest) != 64:
        raise ValueError("source batch manifest_content_hash must be SHA-256 hex")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ValueError("source batch manifest_content_hash must be hexadecimal") from exc
    return result


class RepresentationStore:
    """Immutable Z2 state frames plus a rebuildable discovery index."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.directory = self.root / "artifacts/market_data/representations"
        self.index_path = self.root / "state/representations.json"

    def persist(
        self,
        frame: RepresentationFrame,
        *,
        source_batches: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        batches = tuple(_source_batch(value) for value in source_batches)
        if not batches:
            raise ValueError("persisted representation requires source batch lineage")
        if len({item["batch_id"] for item in batches}) != len(batches):
            raise ValueError("source batch ids must be unique")
        wire = frame.to_wire()
        artifact_body: Dict[str, Any] = {
            "schema_version": 1,
            "frame": wire,
            "source_batches": list(batches),
        }
        artifact = dict(artifact_body)
        artifact["integrity"] = {
            "algorithm": "sha256",
            "content_hash": canonical_hash(artifact_body),
        }
        path = self.directory / (frame.frame_id + ".json")
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != artifact:
                raise RuntimeError("representation frame identity conflict")
            self.rebuild_index()
            return existing
        _atomic_json(path, artifact)
        self.rebuild_index()
        return artifact

    def rebuild_index(self) -> Mapping[str, Any]:
        items: List[Mapping[str, Any]] = []
        if self.directory.is_dir():
            for path in sorted(self.directory.glob("*.json")):
                document = json.loads(path.read_text(encoding="utf-8"))
                errors = _validate_artifact(document)
                if errors:
                    raise RuntimeError("%s: %s" % (path.name, "; ".join(errors)))
                frame = RepresentationFrame.from_wire(document["frame"])
                items.append(
                    {
                        "frame_id": frame.frame_id,
                        "path": path.relative_to(self.root).as_posix(),
                        "representation_type": frame.representation_type,
                        "instrument_id": frame.instrument.canonical_id,
                        "status": frame.status,
                        "known_at_ns": frame.known_at_ns,
                        "cutoff_at_ns": frame.cutoff_at_ns,
                        "builder_version": frame.builder_version,
                        "frame_content_hash": frame.content_hash(),
                        "source_set_hash": frame.source_set_hash(),
                        "source_batch_ids": [item["batch_id"] for item in document["source_batches"]],
                        "artifact_content_hash": document["integrity"]["content_hash"],
                    }
                )
        index = {
            "schema_version": 1,
            "representation_contract_version": "1.0",
            "authority": "rebuildable derived state; Z1 canonical observations remain source truth",
            "items": items,
        }
        _atomic_json(self.index_path, index)
        return index


def _validate_artifact(document: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    body = {key: value for key, value in document.items() if key != "integrity"}
    if document.get("schema_version") != 1:
        errors.append("representation artifact schema invalid")
    if canonical_hash(body) != document.get("integrity", {}).get("content_hash"):
        errors.append("representation artifact content hash mismatch")
        return errors
    try:
        RepresentationFrame.from_wire(document.get("frame", {}))
    except (RepresentationContractError, ValueError, TypeError) as exc:
        errors.append("representation frame invalid: %s" % exc)
    batches = document.get("source_batches")
    if not isinstance(batches, list) or not batches:
        errors.append("representation source_batches missing")
    else:
        try:
            normalized = [_source_batch(item) for item in batches]
            if len({item["batch_id"] for item in normalized}) != len(normalized):
                errors.append("representation source batch ids are duplicated")
        except (ValueError, TypeError) as exc:
            errors.append("representation source batch invalid: %s" % exc)
    return errors


def validate_representation_store(root: Path) -> List[str]:
    errors: List[str] = []
    index_path = root / "state/representations.json"
    full_kernel = (root / "state/current_state.json").is_file()
    if not index_path.is_file():
        return ["missing required state file: state/representations.json"] if full_kernel else []
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ["state/representations.json: unreadable JSON: %s" % exc]
    if index.get("schema_version") != 1 or index.get("representation_contract_version") != "1.0" or not isinstance(index.get("items"), list):
        return ["state/representations.json: invalid schema"]
    for item in index["items"]:
        path = (root / str(item.get("path", ""))).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            errors.append("representation index path escapes repository")
            continue
        if not path.is_file():
            errors.append("representation index references missing frame %s" % item.get("frame_id"))
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append("%s: unreadable representation: %s" % (item.get("path"), exc))
            continue
        errors.extend("%s: %s" % (item.get("path"), error) for error in _validate_artifact(document))
        if document.get("integrity", {}).get("content_hash") != item.get("artifact_content_hash"):
            errors.append("representation index artifact hash mismatch for %s" % item.get("frame_id"))
        try:
            frame = RepresentationFrame.from_wire(document["frame"])
        except (KeyError, RepresentationContractError, ValueError, TypeError):
            continue
        if frame.content_hash() != item.get("frame_content_hash"):
            errors.append("representation index frame hash mismatch for %s" % item.get("frame_id"))
    return errors
