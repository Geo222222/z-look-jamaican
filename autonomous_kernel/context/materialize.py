from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from ..operations import canonical_hash
from ..representation.contracts import RepresentationContractError, RepresentationFrame
from ..representation.store import RepresentationStore, validate_representation_store
from .builder import build_market_context
from .contracts import MarketContextFrame
from .store import MarketContextStore


MATERIALIZER_SCHEMA_VERSION = 1
MATERIALIZER_POLICY_ID = "Z9_DURABLE_POINT_IN_TIME_MATERIALIZER_V1"
MATERIALIZER_SELECTION_RULE = "ALL_DURABLE_Z2_INSTRUMENT_STATE_FRAMES_WITH_CUTOFF_AND_KNOWN_AT_LTE_T"


class MarketContextMaterializationError(RuntimeError):
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


def _frame_order(frame: RepresentationFrame) -> Tuple[Any, ...]:
    return (frame.instrument.canonical_id, frame.cutoff_at_ns, frame.known_at_ns, frame.frame_id)


def _load_indexed_frame(root: Path, item: Mapping[str, Any]) -> RepresentationFrame:
    path = (root / str(item.get("path", ""))).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise MarketContextMaterializationError("representation index path escapes repository") from exc
    if not path.is_file():
        raise MarketContextMaterializationError("representation index references missing frame %s" % item.get("frame_id"))
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        frame = RepresentationFrame.from_wire(document.get("frame", {}))
    except (json.JSONDecodeError, RepresentationContractError, ValueError, TypeError) as exc:
        raise MarketContextMaterializationError("invalid durable representation %s: %s" % (item.get("frame_id"), exc)) from exc
    if frame.frame_id != str(item.get("frame_id", "")):
        raise MarketContextMaterializationError("representation index frame identity mismatch")
    if frame.content_hash() != str(item.get("frame_content_hash", "")):
        raise MarketContextMaterializationError("representation index frame hash mismatch for %s" % frame.frame_id)
    if frame.instrument.canonical_id != str(item.get("instrument_id", "")):
        raise MarketContextMaterializationError("representation index instrument mismatch for %s" % frame.frame_id)
    return frame


def select_durable_representation_frames(root: Path, *, cutoff_at_ns: int) -> Tuple[RepresentationFrame, ...]:
    """Return the complete durable Z2 INSTRUMENT_STATE history knowable at cutoff T.

    The discovery index is rebuilt from immutable representation artifacts first so a
    stale mutable index cannot omit an otherwise durable Z2 frame.
    """
    root = root.resolve()
    cutoff = int(cutoff_at_ns)
    if cutoff < 0:
        raise MarketContextMaterializationError("cutoff_at_ns must be non-negative")

    try:
        index = RepresentationStore(root).rebuild_index()
    except Exception as exc:
        raise MarketContextMaterializationError("cannot rebuild durable Z2 representation index: %s" % exc) from exc
    errors = validate_representation_store(root)
    if errors:
        raise MarketContextMaterializationError("durable Z2 representation store is invalid: " + "; ".join(errors))

    items = index.get("items")
    if not isinstance(items, list):
        raise MarketContextMaterializationError("durable Z2 representation index is malformed")

    selected: List[RepresentationFrame] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise MarketContextMaterializationError("durable Z2 representation index contains malformed item")
        if str(item.get("representation_type", "")) != "INSTRUMENT_STATE":
            continue
        if int(item.get("known_at_ns", -1)) > cutoff or int(item.get("cutoff_at_ns", -1)) > cutoff:
            continue
        frame = _load_indexed_frame(root, item)
        if frame.representation_type != "INSTRUMENT_STATE":
            raise MarketContextMaterializationError("selected Z2 frame changed representation type")
        if frame.known_at_ns > cutoff or frame.cutoff_at_ns > cutoff or frame.latest_source_event_at_ns > cutoff:
            raise MarketContextMaterializationError("lookahead rejected in selected durable Z2 frame %s" % frame.frame_id)
        selected.append(frame)

    if not selected:
        raise MarketContextMaterializationError("no durable Z2 INSTRUMENT_STATE frames are knowable at cutoff")
    ordered = tuple(sorted(selected, key=_frame_order))
    if len({frame.frame_id for frame in ordered}) != len(ordered):
        raise MarketContextMaterializationError("durable Z2 selection contains duplicate frame ids")
    return ordered


def _receipt_directory(root: Path) -> Path:
    return root / "artifacts/market_data/context_materializations"


def materialization_receipt_path(root: Path, context_id: str) -> Path:
    return _receipt_directory(root.resolve()) / (str(context_id) + ".json")


def _context_artifact_hash(root: Path, context: MarketContextFrame) -> str:
    path = root / "artifacts/market_data/contexts" / (context.context_id + ".json")
    if not path.is_file():
        raise MarketContextMaterializationError("persisted Z9 context artifact is missing")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MarketContextMaterializationError("persisted Z9 context artifact is invalid JSON") from exc
    digest = str(document.get("integrity", {}).get("content_hash", ""))
    if len(digest) != 64:
        raise MarketContextMaterializationError("persisted Z9 context artifact lacks integrity hash")
    return digest


def _receipt_body(context: MarketContextFrame, *, context_artifact_hash: str) -> Dict[str, Any]:
    return {
        "schema_version": MATERIALIZER_SCHEMA_VERSION,
        "policy_id": MATERIALIZER_POLICY_ID,
        "selection_rule": MATERIALIZER_SELECTION_RULE,
        "cutoff_at_ns": int(context.cutoff_at_ns),
        "context": {
            "context_id": context.context_id,
            "context_content_hash": context.content_hash(),
            "context_artifact_hash": context_artifact_hash,
            "source_set_hash": context.source_set_hash(),
            "builder_version": context.builder_version,
        },
        "selection": {
            "representation_type": "INSTRUMENT_STATE",
            "selected_frame_count": len(context.source_frame_ids),
            "selected_frame_ids": list(context.source_frame_ids),
            "selected_frame_hashes": list(context.source_frame_hashes),
            "selected_instrument_ids": list(context.source_instrument_ids),
            "maximum_known_at_ns": int(context.known_at_ns),
        },
        "authority": {
            "source_truth": False,
            "capital_decision": False,
            "risk_authorization": False,
            "external_execution": False,
            "purpose": "canonical durable Z2 to Z9 point-in-time materialization proof",
        },
    }


def _write_receipt(root: Path, context: MarketContextFrame, *, context_artifact_hash: str) -> Mapping[str, Any]:
    body = _receipt_body(context, context_artifact_hash=context_artifact_hash)
    document = dict(body)
    document["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
    path = materialization_receipt_path(root, context.context_id)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MarketContextMaterializationError("existing materialization receipt is unreadable") from exc
        if existing != document:
            raise MarketContextMaterializationError("market-context materialization identity conflict")
        return existing
    _atomic_json(path, document)
    return document


def verify_materialized_context(root: Path, context_id: str) -> Mapping[str, Any]:
    """Verify that a durable context is bound to the canonical materializer policy."""
    root = root.resolve()
    path = materialization_receipt_path(root, context_id)
    if not path.is_file():
        raise MarketContextMaterializationError("Z9 context lacks canonical materialization receipt: %s" % context_id)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MarketContextMaterializationError("Z9 materialization receipt is invalid JSON") from exc
    body = {key: value for key, value in document.items() if key != "integrity"}
    if document.get("schema_version") != MATERIALIZER_SCHEMA_VERSION:
        raise MarketContextMaterializationError("Z9 materialization receipt schema is invalid")
    if document.get("policy_id") != MATERIALIZER_POLICY_ID or document.get("selection_rule") != MATERIALIZER_SELECTION_RULE:
        raise MarketContextMaterializationError("Z9 materialization policy is not canonical")
    if canonical_hash(body) != document.get("integrity", {}).get("content_hash"):
        raise MarketContextMaterializationError("Z9 materialization receipt content hash mismatch")

    context = MarketContextStore(root).load(str(context_id))
    context_meta = document.get("context")
    selection = document.get("selection")
    if not isinstance(context_meta, Mapping) or not isinstance(selection, Mapping):
        raise MarketContextMaterializationError("Z9 materialization receipt envelope is malformed")
    if int(document.get("cutoff_at_ns", -1)) != context.cutoff_at_ns:
        raise MarketContextMaterializationError("Z9 materialization cutoff does not match context")
    if context_meta.get("context_id") != context.context_id or context_meta.get("context_content_hash") != context.content_hash() or context_meta.get("source_set_hash") != context.source_set_hash() or context_meta.get("builder_version") != context.builder_version:
        raise MarketContextMaterializationError("Z9 materialization context binding mismatch")
    if context_meta.get("context_artifact_hash") != _context_artifact_hash(root, context):
        raise MarketContextMaterializationError("Z9 materialization context artifact hash mismatch")

    ids = list(context.source_frame_ids)
    hashes = list(context.source_frame_hashes)
    instruments = list(context.source_instrument_ids)
    if selection.get("representation_type") != "INSTRUMENT_STATE" or selection.get("selected_frame_count") != len(ids) or selection.get("selected_frame_ids") != ids or selection.get("selected_frame_hashes") != hashes or selection.get("selected_instrument_ids") != instruments or int(selection.get("maximum_known_at_ns", -1)) != context.known_at_ns:
        raise MarketContextMaterializationError("Z9 materialization selected-source binding mismatch")

    for frame_id, frame_hash, instrument_id in zip(ids, hashes, instruments):
        source_path = root / "artifacts/market_data/representations" / (frame_id + ".json")
        if not source_path.is_file():
            raise MarketContextMaterializationError("canonical materialization source frame is no longer durable: %s" % frame_id)
        try:
            source_document = json.loads(source_path.read_text(encoding="utf-8"))
            frame = RepresentationFrame.from_wire(source_document.get("frame", {}))
        except (json.JSONDecodeError, RepresentationContractError, ValueError, TypeError) as exc:
            raise MarketContextMaterializationError("canonical materialization source frame is invalid: %s" % frame_id) from exc
        if frame.content_hash() != frame_hash or frame.instrument.canonical_id != instrument_id:
            raise MarketContextMaterializationError("canonical materialization source lineage mismatch: %s" % frame_id)
        if frame.known_at_ns > context.cutoff_at_ns or frame.cutoff_at_ns > context.cutoff_at_ns or frame.latest_source_event_at_ns > context.cutoff_at_ns:
            raise MarketContextMaterializationError("canonical materialization source contains lookahead: %s" % frame_id)
    return document


def materialize_market_context(root: Path, *, cutoff_at_ns: int) -> Tuple[MarketContextFrame, Mapping[str, Any]]:
    """Canonical operational Z9 entrypoint: durable Z2 -> point-in-time Z9 -> persist."""
    root = root.resolve()
    cutoff = int(cutoff_at_ns)
    frames = select_durable_representation_frames(root, cutoff_at_ns=cutoff)
    try:
        context = build_market_context(frames, cutoff_at_ns=cutoff)
    except Exception as exc:
        raise MarketContextMaterializationError("Z9 builder rejected durable point-in-time selection: %s" % exc) from exc
    if context.cutoff_at_ns != cutoff or context.known_at_ns > cutoff:
        raise MarketContextMaterializationError("Z9 builder violated materializer cutoff boundary")
    expected = tuple((frame.frame_id, frame.content_hash(), frame.instrument.canonical_id) for frame in frames)
    actual = tuple(zip(context.source_frame_ids, context.source_frame_hashes, context.source_instrument_ids))
    if actual != expected:
        raise MarketContextMaterializationError("Z9 builder lineage differs from canonical durable selection")

    try:
        MarketContextStore(root).persist(context, source_frames=frames)
        durable = MarketContextStore(root).load(context.context_id)
    except Exception as exc:
        raise MarketContextMaterializationError("cannot persist/reload canonical Z9 context: %s" % exc) from exc
    if durable.to_wire() != context.to_wire():
        raise MarketContextMaterializationError("reloaded canonical Z9 context differs from materialized frame")
    receipt = _write_receipt(root, context, context_artifact_hash=_context_artifact_hash(root, context))
    verify_materialized_context(root, context.context_id)
    return context, receipt


def validate_market_context_materializations(root: Path) -> List[str]:
    root = root.resolve()
    directory = _receipt_directory(root)
    if not directory.is_dir():
        return []
    errors: List[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            context_id = str(document.get("context", {}).get("context_id", ""))
            if not context_id or path.name != context_id + ".json":
                raise MarketContextMaterializationError("materialization receipt filename/context identity mismatch")
            verify_materialized_context(root, context_id)
        except (json.JSONDecodeError, MarketContextMaterializationError, RuntimeError, ValueError, TypeError) as exc:
            errors.append("%s: %s" % (path.relative_to(root).as_posix(), exc))
    return errors
