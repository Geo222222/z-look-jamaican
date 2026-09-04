"""Immutable on-disk market-object store with a rebuildable index."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .core import LAYERS, canonical_hash, validate_market_object


def _atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(document), handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class MarketObjectStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.directory = self.root / "artifacts/market_objects"
        self.index_path = self.root / "state/market_objects.json"

    def _paths(self) -> List[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(path for path in self.directory.glob("*/*.json") if path.is_file())

    def _load_catalog(self) -> Dict[str, Mapping[str, Any]]:
        catalog: Dict[str, Mapping[str, Any]] = {}
        for path in self._paths():
            document = json.loads(path.read_text(encoding="utf-8"))
            object_id = str(document.get("object_id", ""))
            if object_id in catalog:
                raise RuntimeError(f"duplicate market object ID: {object_id}")
            catalog[object_id] = document
        return catalog

    def get(self, object_id: str) -> Optional[Mapping[str, Any]]:
        return self._load_catalog().get(object_id)

    def persist(self, document: Mapping[str, Any]) -> Mapping[str, Any]:
        catalog = self._load_catalog()
        errors = validate_market_object(document, resolver=catalog.get)
        if errors:
            raise ValueError("; ".join(errors))
        object_id = str(document["object_id"])
        layer = str(document["layer"])
        target = self.directory / layer.lower() / f"{object_id}.json"
        existing = catalog.get(object_id)
        if existing is not None:
            if existing.get("integrity", {}).get("content_hash") != document.get("integrity", {}).get("content_hash"):
                raise RuntimeError(f"immutable market object ID conflict: {object_id}")
            return existing
        _atomic_json(target, document)
        self.rebuild_index()
        return document

    def persist_many(self, documents: List[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
        catalog = self._load_catalog()
        persisted = []
        for document in documents:
            errors = validate_market_object(document, resolver=catalog.get)
            if errors:
                raise ValueError("; ".join(errors))
            object_id = str(document["object_id"])
            existing = catalog.get(object_id)
            if existing is not None:
                if existing.get("integrity", {}).get("content_hash") != document.get("integrity", {}).get("content_hash"):
                    raise RuntimeError(f"immutable market object ID conflict: {object_id}")
                persisted.append(existing)
                continue
            target = self.directory / str(document["layer"]).lower() / f"{object_id}.json"
            _atomic_json(target, document)
            catalog[object_id] = document
            persisted.append(document)
        self.rebuild_index()
        return persisted

    def rebuild_index(self) -> Mapping[str, Any]:
        catalog = self._load_catalog()
        errors: List[str] = []
        for document in catalog.values():
            errors.extend(validate_market_object(document, resolver=catalog.get))
        if errors:
            raise RuntimeError("; ".join(errors))
        items = []
        for path in self._paths():
            document = json.loads(path.read_text(encoding="utf-8"))
            items.append(
                {
                    "object_id": document["object_id"],
                    "object_type": document["object_type"],
                    "layer": document["layer"],
                    "path": path.relative_to(self.root).as_posix(),
                    "instrument": document["subject"]["instrument"],
                    "exchange": document["subject"]["exchange"],
                    "effective_at": document["effective_at"],
                    "quality_status": document["quality"]["status"],
                    "content_hash": document["integrity"]["content_hash"],
                }
            )
        items.sort(key=lambda item: (LAYERS.index(item["layer"]), item["object_id"]))
        index = {
            "schema_version": 1,
            "authority": "rebuildable index over immutable composable market objects",
            "object_count": len(items),
            "layer_counts": {layer: sum(item["layer"] == layer for item in items) for layer in LAYERS},
            "items": items,
        }
        _atomic_json(self.index_path, index)
        return index


def validate_market_object_store(root: Path) -> List[str]:
    errors: List[str] = []
    store = MarketObjectStore(root)
    if not store.index_path.is_file():
        return ["missing required state file: state/market_objects.json"]
    try:
        index = json.loads(store.index_path.read_text(encoding="utf-8"))
        catalog = store._load_catalog()
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        return [f"market object store unreadable: {exc}"]
    if index.get("schema_version") != 1 or not isinstance(index.get("items"), list):
        errors.append("state/market_objects.json: invalid schema")
        return errors
    indexed_ids = set()
    for item in index["items"]:
        object_id = str(item.get("object_id", ""))
        indexed_ids.add(object_id)
        document = catalog.get(object_id)
        if document is None:
            errors.append(f"state/market_objects.json: missing object {object_id}")
            continue
        errors.extend(validate_market_object(document, resolver=catalog.get))
        if document.get("integrity", {}).get("content_hash") != item.get("content_hash"):
            errors.append(f"state/market_objects.json: hash mismatch for {object_id}")
        expected_path = root / str(item.get("path", ""))
        if not expected_path.is_file():
            errors.append(f"state/market_objects.json: path missing for {object_id}")
    if indexed_ids != set(catalog):
        errors.append("state/market_objects.json: index/catalog object IDs differ")
    expected_counts = {layer: sum(document.get("layer") == layer for document in catalog.values()) for layer in LAYERS}
    if index.get("layer_counts") != expected_counts or index.get("object_count") != len(catalog):
        errors.append("state/market_objects.json: count summary mismatch")
    return errors
