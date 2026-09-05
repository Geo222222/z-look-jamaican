"""Point-in-time MARKET_WIDE_EXPERIENCE materialization from durable Z9 context.

This bridge never fabricates missing universe members, never reads post-cutoff
context, and never grants capital or execution authority. A single-instrument
history is legal and remains DEGRADED.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

from ..context.contracts import MarketContextFrame
from ..context.store import MarketContextStore
from .contracts import ExperienceTimescale
from .market_wide import MarketWideExperienceError, MarketWideExperienceState, build_market_wide_experience
from .market_wide_store import MarketWideExperienceStore


SHORT_LOOKBACK_NS = 30_000_000_000


class MarketWideExperienceBridgeError(RuntimeError):
    pass


def contexts_in_window(
    root: Path,
    *,
    window_start_ns: int,
    cutoff_at_ns: int,
) -> Tuple[MarketContextFrame, ...]:
    store = MarketContextStore(root)
    if not store.directory.is_dir():
        return ()
    selected = []
    for path in sorted(store.directory.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        context = MarketContextFrame.from_wire(document["context"])
        if context.cutoff_at_ns > cutoff_at_ns or context.known_at_ns > cutoff_at_ns:
            continue
        if context.cutoff_at_ns < window_start_ns or context.known_at_ns < window_start_ns:
            continue
        selected.append(context)
    selected.sort(key=lambda item: (item.cutoff_at_ns, item.known_at_ns, item.context_id))
    return tuple(selected)


def materialize_market_wide_experience(
    root: Path,
    *,
    cutoff_at_ns: int,
    window_start_ns: int | None = None,
    timescale: ExperienceTimescale = ExperienceTimescale.SHORT,
    persist: bool = True,
) -> MarketWideExperienceState:
    """Build MARKET_WIDE_EXPERIENCE strictly from contexts knowable inside [start, T]."""
    cutoff = int(cutoff_at_ns)
    start = int(window_start_ns) if window_start_ns is not None else cutoff - SHORT_LOOKBACK_NS
    if start < 0:
        raise MarketWideExperienceBridgeError("market-wide experience window begins before epoch")
    if cutoff < start:
        raise MarketWideExperienceBridgeError("market-wide experience window is invalid")
    contexts = contexts_in_window(Path(root).resolve(), window_start_ns=start, cutoff_at_ns=cutoff)
    if not contexts:
        raise MarketWideExperienceBridgeError("no Market Context history is knowable in the Magnitude window")
    try:
        experience = build_market_wide_experience(
            contexts,
            timescale=timescale,
            window_start_ns=start,
            cutoff_at_ns=cutoff,
        )
    except MarketWideExperienceError as exc:
        raise MarketWideExperienceBridgeError(str(exc)) from exc
    if persist:
        MarketWideExperienceStore(root).persist(experience)
    return experience
