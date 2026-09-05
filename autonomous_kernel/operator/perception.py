"""Read-only perception projection for the operator console."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from ..context.status import market_context_status


LIVE_MAX_AGE_NS = 90_000_000_000


def _json(root: Path, relative: str) -> Mapping[str, Any]:
    path = root / relative
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _iso_to_ns(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


def _latest_item(items: list, key: str) -> Optional[Mapping[str, Any]]:
    eligible = [item for item in items if isinstance(item, Mapping)]
    if not eligible:
        return None
    return max(eligible, key=lambda item: (int(item.get(key) or -1), str(item.get("frame_id") or item.get("context_id") or "")))


def _feed_status(
    *,
    observer_status: str,
    last_success_ns: Optional[int],
    now_ns: int,
    quality: Optional[str],
) -> str:
    if observer_status in {"DEGRADED", "PAUSED"}:
        return "DEGRADED"
    if last_success_ns is None:
        return "NO CURRENT EVIDENCE"
    if quality and quality not in {"VALID", "VALID_PUBLIC_OBSERVATION_WINDOW"}:
        return "DEGRADED"
    age = now_ns - last_success_ns
    if age < 0:
        return "DEGRADED"
    if age <= LIVE_MAX_AGE_NS:
        return "LIVE"
    return "STALE"


def _z2_status(frame_count: int, latest: Optional[Mapping[str, Any]]) -> str:
    if frame_count <= 0 or latest is None:
        return "NO CURRENT FRAME"
    status = str(latest.get("status") or "")
    if status == "DEGRADED":
        return "DEGRADED"
    if status == "UNAVAILABLE":
        return "NO CURRENT FRAME"
    return "READY / %s FRAMES" % frame_count


def _z9_status(latest: Optional[Mapping[str, Any]], frame_count: int, z2_count: int) -> str:
    if latest is None or frame_count <= 0:
        if z2_count > 0:
            return "BLOCKED"
        return "NO CURRENT FRAME"
    status = str(latest.get("status") or "")
    if status in {"QUALIFIED", "DEGRADED", "UNAVAILABLE"}:
        return status
    return "BLOCKED"


def _book_summary(root: Path, latest: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if latest is None:
        return None
    path = root / str(latest.get("path") or "")
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        frame = document.get("frame") or {}
        state = frame.get("state") or {}
        aggregate = state.get("aggregate") or {}
        venues = state.get("venue_states") or {}
        first_venue = next(iter(venues.values()), {}) if isinstance(venues, Mapping) else {}
        book = first_venue.get("book") if isinstance(first_venue, Mapping) else {}
        book = book if isinstance(book, Mapping) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return {
        "frame_id": latest.get("frame_id"),
        "instrument_id": latest.get("instrument_id"),
        "status": latest.get("status") or frame.get("status"),
        "cutoff_at_ns": latest.get("cutoff_at_ns"),
        "known_at_ns": latest.get("known_at_ns"),
        "frame_content_hash": latest.get("frame_content_hash"),
        "source_observation_count": len(frame.get("source_observation_ids") or []),
        "source_providers": list(frame.get("source_providers") or []),
        "best_bid": book.get("best_bid") or aggregate.get("cross_venue_best_bid"),
        "best_ask": book.get("best_ask") or aggregate.get("cross_venue_best_ask"),
        "midpoint": book.get("midpoint") or aggregate.get("mean_venue_midpoint"),
        "spread_bps": book.get("spread_bps") or aggregate.get("cross_venue_spread_bps"),
        "bid_size": book.get("best_bid_size"),
        "ask_size": book.get("best_ask_size"),
        "depth_bands_bps": book.get("depth_bands_bps"),
        "trade_flow": aggregate.get("trade_flow"),
        "data_quality": (state.get("input_quality") or {}),
        "cross_venue_book_state": aggregate.get("cross_venue_book_state"),
    }


def _context_summary(root: Path, latest: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if latest is None:
        return None
    path = root / str(latest.get("path") or "")
    if not path.is_file():
        return {
            "context_id": latest.get("context_id"),
            "status": latest.get("status"),
            "cutoff_at_ns": latest.get("cutoff_at_ns"),
            "known_at_ns": latest.get("known_at_ns"),
        }
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        context = document.get("context") or document.get("frame") or {}
        state = context.get("state") or {}
    except (OSError, json.JSONDecodeError, TypeError):
        context, state = {}, {}
    return {
        "context_id": latest.get("context_id") or context.get("context_id"),
        "status": latest.get("status") or context.get("status"),
        "cutoff_at_ns": latest.get("cutoff_at_ns") or context.get("cutoff_at_ns"),
        "known_at_ns": latest.get("known_at_ns") or context.get("known_at_ns"),
        "context_content_hash": latest.get("context_content_hash") or latest.get("artifact_content_hash"),
        "source_frame_ids": list(context.get("source_frame_ids") or []),
        "source_frame_hashes": list(context.get("source_frame_hashes") or []),
        "member_universe": list(context.get("source_instrument_ids") or []),
        "regimes": state.get("regimes") or {},
        "degraded_reasons": list(((state.get("input_quality") or {}).get("degraded_reasons")) or []),
        "feature_quality": state.get("feature_quality") or {},
    }


def perception_projection(root: Path, *, now_ns: Optional[int] = None) -> Dict[str, Any]:
    root = root.resolve()
    current_ns = int(now_ns if now_ns is not None else datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    observer = _json(root, "state/market_observer.json")
    windows = list(observer.get("windows") or [])
    latest_window = windows[-1] if windows else None
    last_success_ns = _iso_to_ns(observer.get("last_success_at"))
    quality = None
    if isinstance(latest_window, Mapping):
        quality = str(latest_window.get("quality") or "")
        if last_success_ns is None:
            last_success_ns = _iso_to_ns(str(latest_window.get("completed_at") or ""))

    representations = list((_json(root, "state/representations.json").get("items") or []))
    latest_frame = _latest_item([item for item in representations if isinstance(item, Mapping)], "known_at_ns")
    contexts = list((_json(root, "state/market_context.json").get("items") or []))
    latest_context = _latest_item([item for item in contexts if isinstance(item, Mapping)], "known_at_ns")

    observer_status = str(observer.get("status") or "IDLE")
    feed_status = _feed_status(
        observer_status=observer_status,
        last_success_ns=last_success_ns,
        now_ns=current_ns,
        quality=quality,
    )
    z2_status = _z2_status(len(representations), latest_frame)
    z9_status = _z9_status(latest_context, len(contexts), len(representations))
    context_store = market_context_status(root)

    sequence = {}
    if isinstance(latest_window, Mapping):
        features = latest_window.get("public_features") if isinstance(latest_window.get("public_features"), Mapping) else {}
        sequence = {
            "window_id": latest_window.get("window_id"),
            "stream_id": latest_window.get("stream_id"),
            "observation_id": latest_window.get("observation_id"),
            "quality": latest_window.get("quality"),
            "message_rate_per_second": features.get("message_rate_per_second"),
        }

    return {
        "feed_status": feed_status,
        "z2_status": z2_status,
        "z9_status": z9_status,
        "now_ns": current_ns,
        "observer": {
            "observer_id": observer.get("observer_id"),
            "status": observer_status,
            "provider": "coinbase_advanced_trade_public_websocket",
            "instrument": "BTC-USD",
            "last_success_at": observer.get("last_success_at"),
            "last_attempt_at": observer.get("last_attempt_at"),
            "next_eligible_at": observer.get("next_eligible_at"),
            "consecutive_failures": int(observer.get("consecutive_failures") or 0),
            "window_count": len(windows),
            "latest_window": sequence or None,
            "age_ns": None if last_success_ns is None else max(0, current_ns - last_success_ns),
        },
        "latest_instrument_state": _book_summary(root, latest_frame),
        "latest_context": _context_summary(root, latest_context),
        "context_store": {
            "status": context_store.get("status"),
            "context_count": context_store.get("context_count"),
            "status_counts": context_store.get("status_counts"),
        },
        "authority": {
            "economic_decision": False,
            "capital_allocation": False,
            "external_execution": False,
            "display_only": True,
        },
    }
