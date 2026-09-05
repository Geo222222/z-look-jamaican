from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping

from ..operations import canonical_hash
from .market_wide import MarketWideExperienceError, MarketWideExperienceState


class MarketWideExperienceStoreError(RuntimeError):
    pass


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


class MarketWideExperienceStore:
    """Immutable MARKET_WIDE_EXPERIENCE snapshots plus a rebuildable index."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.directory = self.root / "artifacts" / "market_experience" / "market_wide"
        self.index_path = self.root / "state" / "market_wide_experience.json"

    def persist(self, experience: MarketWideExperienceState) -> Mapping[str, Any]:
        body: Dict[str, Any] = {"schema_version": 1, "experience": experience.to_wire()}
        artifact = dict(body)
        artifact["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
        path = self.directory / (experience.market_wide_experience_id + ".json")
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != artifact:
                raise MarketWideExperienceStoreError("market-wide experience identity conflict")
            self.rebuild_index()
            return existing
        _atomic_json(path, artifact)
        self.rebuild_index()
        return artifact

    def load(self, experience_id: str) -> MarketWideExperienceState:
        path = self.directory / (str(experience_id) + ".json")
        if not path.is_file():
            raise FileNotFoundError("market-wide experience not found: %s" % experience_id)
        document = json.loads(path.read_text(encoding="utf-8"))
        errors = _validate_artifact(document)
        if errors:
            raise MarketWideExperienceStoreError("invalid market-wide experience: " + "; ".join(errors))
        return MarketWideExperienceState.from_wire(document["experience"])

    def rebuild_index(self) -> Mapping[str, Any]:
        items: List[Mapping[str, Any]] = []
        if self.directory.is_dir():
            for path in sorted(self.directory.glob("*.json")):
                document = json.loads(path.read_text(encoding="utf-8"))
                errors = _validate_artifact(document)
                if errors:
                    raise MarketWideExperienceStoreError("%s: %s" % (path.name, "; ".join(errors)))
                experience = MarketWideExperienceState.from_wire(document["experience"])
                items.append(
                    {
                        "market_wide_experience_id": experience.market_wide_experience_id,
                        "path": path.relative_to(self.root).as_posix(),
                        "status": experience.status,
                        "cutoff_at_ns": experience.cutoff_at_ns,
                        "known_at_ns": experience.known_at_ns,
                        "timescale": experience.timescale.value,
                        "content_hash": experience.content_hash(),
                        "artifact_content_hash": document["integrity"]["content_hash"],
                    }
                )
        index = {
            "schema_version": 1,
            "authority": "rebuildable MARKET_WIDE_EXPERIENCE; never capital or execution authority",
            "items": items,
        }
        _atomic_json(self.index_path, index)
        return index


def _validate_artifact(document: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    body = {key: value for key, value in document.items() if key != "integrity"}
    if document.get("schema_version") != 1:
        errors.append("market-wide experience artifact schema invalid")
    if canonical_hash(body) != document.get("integrity", {}).get("content_hash"):
        return errors + ["market-wide experience artifact content hash mismatch"]
    try:
        MarketWideExperienceState.from_wire(document.get("experience", {}))
    except (MarketWideExperienceError, ValueError, TypeError) as exc:
        return errors + ["market-wide experience frame invalid: %s" % exc]
    return errors
