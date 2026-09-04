from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..store import writer_lock
from .contracts import GRAPH_LAYERS, PerceptionGraphError, validate_graph_node

INDEX_SCHEMA_VERSION = 1


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


def _paths(root: Path) -> List[Path]:
    directory = root / "artifacts/market_data/perception_graph"
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*/*.json") if path.is_file())


def load_graph_catalog(root: Path) -> Dict[str, Mapping[str, Any]]:
    catalog: Dict[str, Mapping[str, Any]] = {}
    for path in _paths(root.resolve()):
        value = json.loads(path.read_text(encoding="utf-8"))
        node_id = str(value.get("node_id", ""))
        if not node_id or node_id in catalog:
            raise PerceptionGraphError("perception graph contains duplicate or missing node id")
        catalog[node_id] = value
    return catalog


def persist_graph_nodes(root: Path, nodes: Sequence[Mapping[str, Any]]) -> Sequence[Mapping[str, Any]]:
    root = root.resolve()
    with writer_lock(root):
        catalog = load_graph_catalog(root)
        persisted = []
        for node in nodes:
            validate_graph_node(node, resolver=catalog.get)
            node_id = str(node["node_id"])
            existing = catalog.get(node_id)
            if existing is not None:
                if existing != dict(node):
                    raise PerceptionGraphError("immutable perception graph node identity conflict")
                persisted.append(existing)
                continue
            target = root / "artifacts/market_data/perception_graph" / str(node["layer"]).lower() / (node_id + ".json")
            _atomic_json(target, node)
            catalog[node_id] = dict(node)
            persisted.append(dict(node))
        rebuild_graph_index(root, catalog=catalog)
        return tuple(persisted)


def rebuild_graph_index(root: Path, *, catalog: Optional[Mapping[str, Mapping[str, Any]]] = None) -> Mapping[str, Any]:
    root = root.resolve()
    current = dict(catalog or load_graph_catalog(root))
    for node in current.values():
        validate_graph_node(node, resolver=current.get)
    items = []
    for node_id, node in current.items():
        items.append({
            "node_id": node_id,
            "node_type": node["node_type"],
            "layer": node["layer"],
            "subject_id": node["subject_id"],
            "cutoff_at_ns": node["cutoff_at_ns"],
            "known_at_ns": node["known_at_ns"],
            "quality_status": node["quality"]["status"],
            "content_hash": node["integrity"]["content_hash"],
            "path": "artifacts/market_data/perception_graph/%s/%s.json" % (str(node["layer"]).lower(), node_id),
        })
    items.sort(key=lambda item: (GRAPH_LAYERS.index(str(item["layer"])), int(item["cutoff_at_ns"]), str(item["node_id"])))
    index = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "authority": "rebuildable index over immutable perception-only market relationship nodes",
        "node_count": len(items),
        "layer_counts": {layer: sum(1 for item in items if item["layer"] == layer) for layer in GRAPH_LAYERS},
        "items": items,
    }
    _atomic_json(root / "state/perception_graph.json", index)
    return index


def validate_perception_graph_store(root: Path, *, require_state: bool = True) -> List[str]:
    root = root.resolve()
    errors: List[str] = []
    try:
        catalog = load_graph_catalog(root)
    except (OSError, json.JSONDecodeError, PerceptionGraphError) as exc:
        return ["perception graph unreadable: %s" % exc]
    for node in catalog.values():
        try:
            validate_graph_node(node, resolver=catalog.get)
        except PerceptionGraphError as exc:
            errors.append(str(exc))
    index_path = root / "state/perception_graph.json"
    full_kernel = (root / "state/current_state.json").is_file()
    if not index_path.is_file():
        if require_state and full_kernel:
            errors.append("missing required state file: state/perception_graph.json")
        return errors
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return errors + ["state/perception_graph.json unreadable: %s" % exc]
    if index.get("schema_version") != INDEX_SCHEMA_VERSION or not isinstance(index.get("items"), list):
        return errors + ["state/perception_graph.json schema invalid"]
    if int(index.get("node_count", -1)) != len(catalog):
        errors.append("perception graph index count mismatch")
    indexed = {str(item.get("node_id", "")): item for item in index["items"] if isinstance(item, Mapping)}
    if set(indexed) != set(catalog):
        errors.append("perception graph index/catalog identities differ")
    for node_id, node in catalog.items():
        item = indexed.get(node_id)
        if item is not None and item.get("content_hash") != node.get("integrity", {}).get("content_hash"):
            errors.append("perception graph index hash mismatch for %s" % node_id)
    expected_counts = {layer: sum(1 for node in catalog.values() if node.get("layer") == layer) for layer in GRAPH_LAYERS}
    if index.get("layer_counts") != expected_counts:
        errors.append("perception graph index layer counts mismatch")
    return errors
