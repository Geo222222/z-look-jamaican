from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from ..operations import canonical_hash
from ..representation.contracts import RepresentationContractError, RepresentationFrame
from .contracts import MarketContextContractError, MarketContextFrame


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


def _source_entries(frames: Sequence[RepresentationFrame]) -> Tuple[Mapping[str, Any], ...]:
    ordered = tuple(sorted(frames, key=lambda frame: (frame.instrument.canonical_id, frame.cutoff_at_ns, frame.known_at_ns, frame.frame_id)))
    return tuple({"frame_id": frame.frame_id, "frame_content_hash": frame.content_hash(), "instrument_id": frame.instrument.canonical_id} for frame in ordered)


class MarketContextStore:
    """Immutable Z9 artifacts plus a rebuildable discovery index."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.directory = self.root / "artifacts/market_data/contexts"
        self.index_path = self.root / "state/market_context.json"

    def persist(self, context: MarketContextFrame, *, source_frames: Sequence[RepresentationFrame]) -> Mapping[str, Any]:
        entries = _source_entries(source_frames)
        expected = tuple(zip(context.source_frame_ids, context.source_frame_hashes, context.source_instrument_ids))
        provided = tuple((str(item["frame_id"]), str(item["frame_content_hash"]), str(item["instrument_id"])) for item in entries)
        if provided != expected:
            raise ValueError("source_frames do not match exact Z9 lineage")
        for item in entries:
            path = self.root / "artifacts/market_data/representations" / (str(item["frame_id"]) + ".json")
            if not path.is_file():
                raise ValueError("Z9 context requires durable source representation: %s" % item["frame_id"])
            document = json.loads(path.read_text(encoding="utf-8"))
            source = RepresentationFrame.from_wire(document.get("frame", {}))
            if source.content_hash() != item["frame_content_hash"]:
                raise ValueError("durable source representation hash mismatch")
        body: Dict[str, Any] = {"schema_version": 1, "context": context.to_wire(), "source_frames": list(entries)}
        artifact = dict(body)
        artifact["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
        path = self.directory / (context.context_id + ".json")
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != artifact:
                raise RuntimeError("market-context identity conflict")
            self.rebuild_index()
            return existing
        _atomic_json(path, artifact)
        self.rebuild_index()
        return artifact

    def load(self, context_id: str) -> MarketContextFrame:
        path = self.directory / (str(context_id) + ".json")
        if not path.is_file():
            raise FileNotFoundError("market context not found: %s" % context_id)
        document = json.loads(path.read_text(encoding="utf-8"))
        errors = _validate_artifact(self.root, document)
        if errors:
            raise RuntimeError("invalid market context: " + "; ".join(errors))
        return MarketContextFrame.from_wire(document["context"])

    def rebuild_index(self) -> Mapping[str, Any]:
        items: List[Mapping[str, Any]] = []
        if self.directory.is_dir():
            for path in sorted(self.directory.glob("*.json")):
                document = json.loads(path.read_text(encoding="utf-8"))
                errors = _validate_artifact(self.root, document)
                if errors:
                    raise RuntimeError("%s: %s" % (path.name, "; ".join(errors)))
                context = MarketContextFrame.from_wire(document["context"])
                items.append({"context_id": context.context_id, "path": path.relative_to(self.root).as_posix(), "status": context.status, "cutoff_at_ns": context.cutoff_at_ns, "known_at_ns": context.known_at_ns, "builder_version": context.builder_version, "context_content_hash": context.content_hash(), "source_set_hash": context.source_set_hash(), "source_frame_count": len(context.source_frame_ids), "latest_regimes": context.state.get("regimes", {}), "artifact_content_hash": document["integrity"]["content_hash"]})
        index = {"schema_version": 1, "context_contract_version": "1.0", "authority": "rebuildable Z9 derived context; never capital or execution authority", "items": items}
        _atomic_json(self.index_path, index)
        return index


def _validate_artifact(root: Path, document: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    body = {key: value for key, value in document.items() if key != "integrity"}
    if document.get("schema_version") != 1:
        errors.append("market-context artifact schema invalid")
    if canonical_hash(body) != document.get("integrity", {}).get("content_hash"):
        return errors + ["market-context artifact content hash mismatch"]
    try:
        context = MarketContextFrame.from_wire(document.get("context", {}))
    except (MarketContextContractError, ValueError, TypeError) as exc:
        return errors + ["market-context frame invalid: %s" % exc]
    source_frames = document.get("source_frames")
    if not isinstance(source_frames, list) or not source_frames:
        return errors + ["market-context source frame lineage missing"]
    normalized = tuple((str(item.get("frame_id", "")), str(item.get("frame_content_hash", "")), str(item.get("instrument_id", ""))) for item in source_frames if isinstance(item, Mapping))
    expected = tuple(zip(context.source_frame_ids, context.source_frame_hashes, context.source_instrument_ids))
    if normalized != expected:
        return errors + ["market-context artifact source lineage mismatch"]
    for frame_id, frame_hash, instrument_id in normalized:
        path = root / "artifacts/market_data/representations" / (frame_id + ".json")
        if not path.is_file():
            errors.append("market-context source representation missing: %s" % frame_id)
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            frame = RepresentationFrame.from_wire(value.get("frame", {}))
        except (json.JSONDecodeError, RepresentationContractError, ValueError, TypeError) as exc:
            errors.append("market-context source representation invalid: %s: %s" % (frame_id, exc))
            continue
        if frame.content_hash() != frame_hash:
            errors.append("market-context source representation hash mismatch: %s" % frame_id)
        if frame.instrument.canonical_id != instrument_id:
            errors.append("market-context source instrument mismatch: %s" % frame_id)
    return errors


def validate_market_context_store(root: Path) -> List[str]:
    root = root.resolve()
    index_path = root / "state/market_context.json"
    full_kernel = (root / "state/current_state.json").is_file()
    if not index_path.is_file():
        return ["missing required state file: state/market_context.json"] if full_kernel else []
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ["state/market_context.json: unreadable JSON: %s" % exc]
    if index.get("schema_version") != 1 or index.get("context_contract_version") != "1.0" or not isinstance(index.get("items"), list):
        return ["state/market_context.json: invalid schema"]
    errors: List[str] = []
    seen = set()
    for item in index["items"]:
        context_id = str(item.get("context_id", ""))
        if not context_id or context_id in seen:
            errors.append("state/market_context.json: context ids must be unique and non-empty")
            continue
        seen.add(context_id)
        path = (root / str(item.get("path", ""))).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            errors.append("market-context index path escapes repository")
            continue
        if not path.is_file():
            errors.append("market-context index references missing context %s" % context_id)
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append("%s: unreadable market context: %s" % (item.get("path"), exc))
            continue
        errors.extend("%s: %s" % (item.get("path"), error) for error in _validate_artifact(root, document))
        if document.get("integrity", {}).get("content_hash") != item.get("artifact_content_hash"):
            errors.append("market-context index artifact hash mismatch for %s" % context_id)
        try:
            context = MarketContextFrame.from_wire(document["context"])
        except (KeyError, MarketContextContractError, ValueError, TypeError):
            continue
        if context.content_hash() != item.get("context_content_hash"):
            errors.append("market-context index context hash mismatch for %s" % context_id)
    return errors
