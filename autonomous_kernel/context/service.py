"""Authoritative operational materialization for Z9 market context.

Z2 remains the source of point-in-time instrument state. This module owns the
single supported policy for turning durable Z2 history into one reproducible
Z9 MarketContextFrame at a requested cutoff.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from ..representation.contracts import RepresentationFrame
from ..representation.store import validate_representation_store
from .builder import build_market_context
from .contracts import MarketContextFrame
from .store import MarketContextStore, validate_market_context_store


class ContextMaterializationError(ValueError):
    """Raised when durable Z2 history cannot produce an authoritative Z9 context."""


@dataclass(frozen=True)
class ContextMaterializationResult:
    context: MarketContextFrame
    selected_frame_ids: Tuple[str, ...]
    selected_instrument_ids: Tuple[str, ...]


def _load_durable_frames(root: Path) -> Tuple[RepresentationFrame, ...]:
    errors = validate_representation_store(root)
    if errors:
        raise ContextMaterializationError("invalid Z2 representation store: " + "; ".join(errors))

    index_path = root / "state/representations.json"
    if not index_path.is_file():
        raise ContextMaterializationError("missing Z2 representation index")
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContextMaterializationError("unreadable Z2 representation index: %s" % exc) from exc

    frames = []
    for item in index.get("items", []):
        if not isinstance(item, Mapping):
            raise ContextMaterializationError("Z2 representation index item is malformed")
        path = (root / str(item.get("path", ""))).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise ContextMaterializationError("Z2 representation path escapes repository") from exc
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            frame = RepresentationFrame.from_wire(document["frame"])
        except (OSError, KeyError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ContextMaterializationError("unable to load durable Z2 frame %s: %s" % (item.get("frame_id"), exc)) from exc
        if frame.frame_id != str(item.get("frame_id", "")):
            raise ContextMaterializationError("Z2 representation index frame id mismatch")
        if frame.content_hash() != str(item.get("frame_content_hash", "")):
            raise ContextMaterializationError("Z2 representation index frame hash mismatch")
        frames.append(frame)

    return tuple(frames)


def select_point_in_time_history(
    frames: Tuple[RepresentationFrame, ...],
    *,
    cutoff_at_ns: int,
) -> Tuple[RepresentationFrame, ...]:
    """Select every durable Z2 frame causally knowable at the requested cutoff."""
    cutoff = int(cutoff_at_ns)
    if cutoff < 0:
        raise ContextMaterializationError("cutoff_at_ns must be non-negative")

    eligible = tuple(
        sorted(
            (
                frame
                for frame in frames
                if frame.representation_type == "INSTRUMENT_STATE"
                and frame.known_at_ns <= cutoff
                and frame.cutoff_at_ns <= cutoff
            ),
            key=lambda frame: (
                frame.instrument.canonical_id,
                frame.cutoff_at_ns,
                frame.known_at_ns,
                frame.frame_id,
            ),
        )
    )
    if not eligible:
        raise ContextMaterializationError(
            "no durable Z2 instrument history is knowable at requested cutoff"
        )
    if len({frame.frame_id for frame in eligible}) != len(eligible):
        raise ContextMaterializationError("duplicate durable Z2 frame identity")
    return eligible


def materialize_market_context(
    root: Path,
    *,
    cutoff_at_ns: int,
    builder_options: Optional[Mapping[str, Any]] = None,
) -> ContextMaterializationResult:
    """Build, verify, persist and re-read the canonical Z9 context for cutoff ``T``."""
    root = Path(root).resolve()
    durable = _load_durable_frames(root)
    selected = select_point_in_time_history(durable, cutoff_at_ns=cutoff_at_ns)

    options = dict(builder_options or {})
    if "cutoff_at_ns" in options:
        raise ContextMaterializationError("builder_options may not override cutoff_at_ns")
    context = build_market_context(selected, cutoff_at_ns=int(cutoff_at_ns), **options)

    expected_ids = tuple(frame.frame_id for frame in selected)
    expected_hashes = tuple(frame.content_hash() for frame in selected)
    expected_instruments = tuple(frame.instrument.canonical_id for frame in selected)
    if context.source_frame_ids != expected_ids:
        raise ContextMaterializationError("Z9 builder changed selected source frame order or identity")
    if context.source_frame_hashes != expected_hashes:
        raise ContextMaterializationError("Z9 builder changed selected source frame hashes")
    if context.source_instrument_ids != expected_instruments:
        raise ContextMaterializationError("Z9 builder changed selected source instrument lineage")

    store = MarketContextStore(root)
    store.persist(context, source_frames=selected)
    persisted = store.load(context.context_id)
    if persisted.to_wire() != context.to_wire():
        raise ContextMaterializationError("persisted Z9 context differs from constructed context")
    context_errors = validate_market_context_store(root)
    if context_errors:
        raise ContextMaterializationError("invalid Z9 context store after persist: " + "; ".join(context_errors))

    return ContextMaterializationResult(
        context=persisted,
        selected_frame_ids=expected_ids,
        selected_instrument_ids=tuple(sorted(set(expected_instruments))),
    )
