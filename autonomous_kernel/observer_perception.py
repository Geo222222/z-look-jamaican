"""Propagate one qualified public observer window into durable Z1/Z2/Z9 state.

This is perception materialization only. It does not create capital decisions,
Watchman authorization, or Hand execution.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .context.service import ContextMaterializationError, materialize_market_context
from .observation.materialize import CanonicalMaterializationError, materialize_coinbase_stream
from .representation.contracts import RepresentationFrame
from .representation.materialize import RepresentationMaterializationError, materialize_instrument_state


BTC_SPOT_ID = "CRYPTO.SPOT.BTC-USD"
DEFAULT_SYMBOL = "BTC-USD"


def propagate_captured_window(root: Path, window: Mapping[str, Any]) -> dict[str, Any]:
    """Materialize canonical observations, instrument state, and truthful Z9 context."""
    root = root.resolve()
    stream_id = str(window.get("stream_id") or "")
    if not stream_id:
        raise ValueError("captured window is missing stream_id")

    try:
        canonical = materialize_coinbase_stream(root, stream_id, default_symbol=DEFAULT_SYMBOL)
    except CanonicalMaterializationError as exc:
        return {
            "status": "Z1_BLOCKED",
            "stream_id": stream_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    batch_id = str(canonical["batch_id"])
    try:
        representation = materialize_instrument_state(
            root,
            batch_ids=(batch_id,),
            instrument_id=BTC_SPOT_ID,
        )
    except RepresentationMaterializationError as exc:
        return {
            "status": "Z2_BLOCKED",
            "stream_id": stream_id,
            "canonical_batch_id": batch_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    frame = RepresentationFrame.from_wire(representation["frame"])
    z9 = _materialize_context(root, cutoff_at_ns=int(frame.cutoff_at_ns))
    return {
        "status": "PROPAGATED",
        "stream_id": stream_id,
        "canonical_batch_id": batch_id,
        "canonical_record_count": canonical.get("record_count"),
        "representation_frame_id": frame.frame_id,
        "representation_status": frame.status,
        "representation_cutoff_at_ns": frame.cutoff_at_ns,
        "representation_known_at_ns": frame.known_at_ns,
        "source_observation_count": len(frame.source_observation_ids),
        "z9": z9,
        "authority": {
            "capital_allocation": False,
            "economic_decision": False,
            "external_execution": False,
        },
    }


def _materialize_context(root: Path, *, cutoff_at_ns: int) -> dict[str, Any]:
    try:
        result = materialize_market_context(root, cutoff_at_ns=cutoff_at_ns)
    except (ContextMaterializationError, ValueError) as exc:
        return {
            "status": "Z9_BLOCKED",
            "cutoff_at_ns": cutoff_at_ns,
            "blocking_reason": str(exc),
        }
    context = result.context
    reasons = list(((context.state.get("input_quality") or {}).get("degraded_reasons")) or [])
    return {
        "status": context.status,
        "context_id": context.context_id,
        "cutoff_at_ns": context.cutoff_at_ns,
        "known_at_ns": context.known_at_ns,
        "member_universe": sorted(set(result.selected_instrument_ids)),
        "source_frame_ids": list(result.selected_frame_ids),
        "source_frame_hashes": list(context.source_frame_hashes),
        "degraded_reasons": reasons,
        "regimes": (context.state.get("regimes") or {}),
        "feature_quality": (context.state.get("feature_quality") or {}),
    }
