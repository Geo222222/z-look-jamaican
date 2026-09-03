from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..observation.contracts import CanonicalObservation
from .contracts import RepresentationFrame


BUILDER_VERSION = "instrument-state-v1"
RELEVANT_EVENT_TYPES = {"BOOK_SNAPSHOT", "BOOK_DELTA", "TRADE"}


class RepresentationError(ValueError):
    pass


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _text(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else format(value, "f")


def _mean(values: Sequence[Decimal]) -> Optional[Decimal]:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _sequence_key(value: Optional[str]) -> Tuple[Tuple[int, Any], ...]:
    if value is None:
        return ((2, ""),)
    parts = str(value).split(":")
    output: List[Tuple[int, Any]] = []
    for part in parts:
        try:
            output.append((0, int(part)))
        except ValueError:
            output.append((1, part))
    return tuple(output)


def _observation_order(item: CanonicalObservation) -> Tuple[Any, ...]:
    return (
        item.known_at_ns,
        item.source_event_at_ns,
        item.provider,
        item.venue,
        item.stream_id or "",
        _sequence_key(item.sequence),
        item.observation_id,
    )


def _book_metrics(
    bids: Mapping[Decimal, Decimal],
    asks: Mapping[Decimal, Decimal],
    depth_bands_bps: Sequence[int],
) -> Mapping[str, Any]:
    if not bids or not asks:
        return {"status": "UNAVAILABLE_NO_BOOK"}
    best_bid = max(bids)
    best_ask = min(asks)
    if best_bid >= best_ask:
        return {
            "status": "UNAVAILABLE_CROSSED_OR_LOCKED",
            "best_bid": _text(best_bid),
            "best_ask": _text(best_ask),
        }
    midpoint = (best_bid + best_ask) / Decimal("2")
    spread_bps = (best_ask - best_bid) / midpoint * Decimal("10000")
    bands: Dict[str, Any] = {}
    for raw_band in depth_bands_bps:
        band = int(raw_band)
        fraction = Decimal(band) / Decimal("10000")
        bid_floor = midpoint * (Decimal("1") - fraction)
        ask_ceiling = midpoint * (Decimal("1") + fraction)
        bid_base = sum((size for price, size in bids.items() if price >= bid_floor), Decimal("0"))
        ask_base = sum((size for price, size in asks.items() if price <= ask_ceiling), Decimal("0"))
        bid_quote = sum((price * size for price, size in bids.items() if price >= bid_floor), Decimal("0"))
        ask_quote = sum((price * size for price, size in asks.items() if price <= ask_ceiling), Decimal("0"))
        total_quote = bid_quote + ask_quote
        imbalance = None if total_quote == 0 else (bid_quote - ask_quote) / total_quote
        bands[str(band)] = {
            "bid_base": _text(bid_base),
            "ask_base": _text(ask_base),
            "bid_quote_notional": _text(bid_quote),
            "ask_quote_notional": _text(ask_quote),
            "quote_notional_imbalance": _text(imbalance),
        }
    return {
        "status": "QUALIFIED",
        "best_bid": _text(best_bid),
        "best_ask": _text(best_ask),
        "midpoint": _text(midpoint),
        "spread_bps": _text(spread_bps),
        "best_bid_size": _text(bids[best_bid]),
        "best_ask_size": _text(asks[best_ask]),
        "depth_bands_bps": bands,
        "bid_level_count": len(bids),
        "ask_level_count": len(asks),
    }


def _empty_flow() -> Dict[str, Decimal]:
    return {
        "buy_base": Decimal("0"),
        "sell_base": Decimal("0"),
        "unknown_base": Decimal("0"),
        "buy_quote": Decimal("0"),
        "sell_quote": Decimal("0"),
        "unknown_quote": Decimal("0"),
    }


def _add_trade(flow: Dict[str, Decimal], payload: Mapping[str, Any]) -> None:
    price = _decimal(payload["price"])
    size = _decimal(payload["size"])
    side = str(payload.get("side", "UNKNOWN")).upper()
    prefix = "buy" if side == "BUY" else "sell" if side == "SELL" else "unknown"
    flow[prefix + "_base"] += size
    flow[prefix + "_quote"] += price * size


def _flow_wire(flow: Mapping[str, Decimal], count: int) -> Mapping[str, Any]:
    return {
        "truth_class": "PROVIDER_REPORTED_SIDE_NOT_AGGRESSOR_INFERENCE",
        "trade_count": int(count),
        "reported_buy_base": _text(flow["buy_base"]),
        "reported_sell_base": _text(flow["sell_base"]),
        "reported_unknown_base": _text(flow["unknown_base"]),
        "reported_buy_quote_notional": _text(flow["buy_quote"]),
        "reported_sell_quote_notional": _text(flow["sell_quote"]),
        "reported_unknown_quote_notional": _text(flow["unknown_quote"]),
        "net_reported_quote_notional": _text(flow["buy_quote"] - flow["sell_quote"]),
    }


def _frame_id(instrument_id: str, cutoff_at_ns: int, source_hashes: Sequence[str], builder_version: str) -> str:
    material = "|".join([instrument_id, str(cutoff_at_ns), builder_version] + list(source_hashes))
    return "REP-%s" % hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def build_instrument_state(
    observations: Sequence[CanonicalObservation],
    *,
    cutoff_at_ns: Optional[int] = None,
    depth_bands_bps: Sequence[int] = (1, 5, 10),
    builder_version: str = BUILDER_VERSION,
) -> RepresentationFrame:
    """Build one deterministic point-in-time instrument representation.

    The function never silently filters future-known observations. Supplying one
    is a lookahead error, so callers must explicitly materialize the correct
    point-in-time source set.
    """
    relevant = [item for item in observations if item.event_type in RELEVANT_EVENT_TYPES]
    if not relevant:
        raise RepresentationError("instrument representation requires book or trade observations")
    if len({item.observation_id for item in relevant}) != len(relevant):
        raise RepresentationError("duplicate source observation id")
    instruments = {item.instrument for item in relevant}
    if len(instruments) != 1:
        raise RepresentationError("instrument representation cannot mix canonical instruments")
    instrument = next(iter(instruments))
    bands = tuple(int(value) for value in depth_bands_bps)
    if not bands or any(value <= 0 or value > 10000 for value in bands) or len(set(bands)) != len(bands):
        raise RepresentationError("depth_bands_bps must be unique values in 1..10000")

    max_known = max(item.known_at_ns for item in relevant)
    cutoff = max_known if cutoff_at_ns is None else int(cutoff_at_ns)
    if cutoff < 0:
        raise RepresentationError("cutoff_at_ns must be non-negative")
    future = [item.observation_id for item in relevant if item.known_at_ns > cutoff]
    if future:
        raise RepresentationError(
            "lookahead rejected: observations known after cutoff: %s" % ", ".join(sorted(future))
        )

    ordered = sorted(relevant, key=_observation_order)
    valid = [item for item in ordered if item.quality.get("status") == "VALID"]
    status_counts = Counter(str(item.quality.get("status", "UNAVAILABLE")) for item in ordered)

    venue_books: Dict[str, Dict[str, Any]] = {}
    venue_flows: Dict[str, Dict[str, Decimal]] = defaultdict(_empty_flow)
    venue_trade_counts: Counter[str] = Counter()
    venue_issues: Dict[str, List[str]] = defaultdict(list)

    for item in valid:
        venue = item.venue
        if item.event_type == "TRADE":
            _add_trade(venue_flows[venue], item.payload)
            venue_trade_counts[venue] += 1
            continue

        book = venue_books.setdefault(
            venue,
            {
                "bids": {},
                "asks": {},
                "snapshot_seen": False,
                "last_book_observation_id": None,
            },
        )
        if item.event_type == "BOOK_SNAPSHOT":
            book["bids"] = {}
            book["asks"] = {}
            book["snapshot_seen"] = True
        elif not book["snapshot_seen"]:
            if "DELTA_BEFORE_SNAPSHOT" not in venue_issues[venue]:
                venue_issues[venue].append("DELTA_BEFORE_SNAPSHOT")
            continue

        for update in item.payload.get("updates", []):
            side = str(update["side"])
            price = _decimal(update["price"])
            size = _decimal(update["size"])
            target = book["bids"] if side == "BID" else book["asks"]
            if size == 0:
                target.pop(price, None)
            else:
                target[price] = size
        book["last_book_observation_id"] = item.observation_id

    venues = sorted({item.venue for item in ordered})
    venue_states: Dict[str, Any] = {}
    qualified_midpoints: List[Decimal] = []
    qualified_best_bids: List[Decimal] = []
    qualified_best_asks: List[Decimal] = []
    aggregate_flow = _empty_flow()
    aggregate_trade_count = 0
    qualified_book_venues = 0

    for venue in venues:
        book = venue_books.get(venue)
        if book is None or not book.get("snapshot_seen"):
            book_state: Mapping[str, Any] = {"status": "UNAVAILABLE_NO_SNAPSHOT"}
            if venue_issues.get(venue):
                book_state = {
                    "status": "UNAVAILABLE_NO_SNAPSHOT",
                    "issues": list(venue_issues[venue]),
                }
        else:
            book_state = _book_metrics(book["bids"], book["asks"], bands)
            if book.get("last_book_observation_id") is not None:
                book_state = dict(book_state)
                book_state["last_book_observation_id"] = book["last_book_observation_id"]
            if venue_issues.get(venue):
                book_state = dict(book_state)
                book_state["issues"] = list(venue_issues[venue])
            if book_state.get("status") == "QUALIFIED":
                qualified_book_venues += 1
                qualified_midpoints.append(_decimal(book_state["midpoint"]))
                qualified_best_bids.append(_decimal(book_state["best_bid"]))
                qualified_best_asks.append(_decimal(book_state["best_ask"]))

        flow = venue_flows.get(venue, _empty_flow())
        for key in aggregate_flow:
            aggregate_flow[key] += flow[key]
        aggregate_trade_count += int(venue_trade_counts[venue])
        venue_states[venue] = {
            "book": book_state,
            "trade_flow": _flow_wire(flow, int(venue_trade_counts[venue])),
            "source_providers": sorted({item.provider for item in ordered if item.venue == venue}),
        }

    cross_bid = max(qualified_best_bids) if qualified_best_bids else None
    cross_ask = min(qualified_best_asks) if qualified_best_asks else None
    mean_midpoint = _mean(qualified_midpoints)
    dispersion_bps: Optional[Decimal] = None
    if mean_midpoint is not None and len(qualified_midpoints) > 1 and mean_midpoint != 0:
        dispersion_bps = (max(qualified_midpoints) - min(qualified_midpoints)) / mean_midpoint * Decimal("10000")
    cross_state = "UNAVAILABLE"
    cross_spread: Optional[Decimal] = None
    if cross_bid is not None and cross_ask is not None:
        if cross_bid < cross_ask:
            cross_state = "NORMAL"
            midpoint = (cross_bid + cross_ask) / Decimal("2")
            cross_spread = (cross_ask - cross_bid) / midpoint * Decimal("10000")
        else:
            cross_state = "CROSSED_OR_DISLOCATED"

    degraded_reasons: List[str] = []
    if any(state != "VALID" for state in status_counts):
        degraded_reasons.append("NON_VALID_SOURCE_OBSERVATION_PRESENT")
    for venue in venues:
        if venue_states[venue]["book"].get("status") != "QUALIFIED":
            degraded_reasons.append("BOOK_UNAVAILABLE_%s" % venue)
        if venue_states[venue]["book"].get("issues"):
            degraded_reasons.append("BOOK_LINEAGE_ISSUE_%s" % venue)
    if cross_state == "CROSSED_OR_DISLOCATED":
        degraded_reasons.append("CROSS_VENUE_DISLOCATION")

    if qualified_book_venues == 0:
        frame_status = "UNAVAILABLE"
    elif degraded_reasons:
        frame_status = "DEGRADED"
    else:
        frame_status = "QUALIFIED"

    source_ids = tuple(item.observation_id for item in ordered)
    source_hashes = tuple(item.content_hash() for item in ordered)
    parameters = {
        "depth_bands_bps": list(bands),
        "book_merge_policy": "VENUE_ISOLATED_WITH_SEPARATE_CROSS_VENUE_SUMMARY",
        "trade_side_semantics": "PROVIDER_REPORTED_SIDE_NOT_AGGRESSOR_INFERENCE",
        "quality_policy": "VALID_SOURCES_BUILD_STATE_NON_VALID_SOURCES_DEGRADE_FRAME",
        "lookahead_policy": "HARD_REJECT_IF_SOURCE_KNOWN_AFTER_CUTOFF",
    }
    state = {
        "venue_states": venue_states,
        "aggregate": {
            "venue_count": len(venues),
            "qualified_book_venue_count": qualified_book_venues,
            "cross_venue_book_state": cross_state,
            "cross_venue_best_bid": _text(cross_bid),
            "cross_venue_best_ask": _text(cross_ask),
            "cross_venue_spread_bps": _text(cross_spread),
            "mean_venue_midpoint": _text(mean_midpoint),
            "venue_midpoint_dispersion_bps": _text(dispersion_bps),
            "trade_flow": _flow_wire(aggregate_flow, aggregate_trade_count),
        },
        "input_quality": {
            "status_counts": dict(sorted(status_counts.items())),
            "degraded_reasons": sorted(set(degraded_reasons)),
        },
    }
    return RepresentationFrame(
        frame_id=_frame_id(instrument.canonical_id, cutoff, source_hashes, builder_version),
        representation_type="INSTRUMENT_STATE",
        instrument=instrument,
        window_start_ns=min(item.known_at_ns for item in ordered),
        cutoff_at_ns=cutoff,
        known_at_ns=max_known,
        latest_source_event_at_ns=max(item.source_event_at_ns for item in ordered),
        status=frame_status,
        builder_version=builder_version,
        parameters=parameters,
        state=state,
        source_observation_ids=source_ids,
        source_content_hashes=source_hashes,
        source_providers=tuple(sorted({item.provider for item in ordered})),
        source_venues=tuple(venues),
    )
