from __future__ import annotations

import hashlib
from collections import Counter
from decimal import Decimal
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..observation.contracts import CanonicalObservation, normalize_payload
from .contracts import RepresentationFrame


DERIVATIVE_BUILDER_VERSION = "derivative-state-v1"
DERIVATIVE_EVENT_TYPES = {"FUNDING", "OPEN_INTEREST", "LIQUIDATION", "INDEX_PRICE", "MARK_PRICE"}
DERIVATIVE_MARKET_TYPES = {"PERPETUAL", "FUTURE"}


class DerivativeStateError(ValueError):
    pass


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _text(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else format(value, "f")


def _observation_order(item: CanonicalObservation) -> Tuple[Any, ...]:
    return (
        item.known_at_ns,
        item.source_event_at_ns,
        item.provider,
        item.venue,
        item.sequence or "",
        item.observation_id,
    )


def _frame_id(instrument_id: str, cutoff_at_ns: int, source_hashes: Sequence[str], builder_version: str) -> str:
    material = "|".join([instrument_id, str(cutoff_at_ns), builder_version] + list(source_hashes))
    return "DER-%s" % hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _latest(items: Sequence[CanonicalObservation], event_type: str) -> Optional[CanonicalObservation]:
    matches = [item for item in items if item.event_type == event_type]
    if not matches:
        return None
    return max(matches, key=_observation_order)


def _scalar_family(item: Optional[CanonicalObservation], field: str) -> Mapping[str, Any]:
    if item is None:
        return {"status": "UNAVAILABLE"}
    payload = normalize_payload(item.event_type, item.payload)
    return {
        "status": "QUALIFIED",
        "value": str(payload[field]),
        "source_observation_id": item.observation_id,
        "known_at_ns": item.known_at_ns,
        "provider": item.provider,
        "venue": item.venue,
        "unit_semantics": "PROVIDER_NATIVE_UNSPECIFIED" if item.event_type == "OPEN_INTEREST" else "DIMENSIONLESS_RATE" if item.event_type == "FUNDING" else "QUOTE_PRICE",
    }


def _liquidation_groups(items: Sequence[CanonicalObservation]) -> Mapping[str, Mapping[str, Any]]:
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for item in items:
        payload = normalize_payload(item.event_type, item.payload)
        key = (item.provider, item.venue)
        group = grouped.setdefault(
            key,
            {
                "provider": item.provider,
                "venue": item.venue,
                "event_count": 0,
                "reported_buy_size": Decimal("0"),
                "reported_sell_size": Decimal("0"),
                "reported_unknown_size": Decimal("0"),
                "reported_price_times_size": Decimal("0"),
            },
        )
        size = _decimal(payload["size"])
        price = _decimal(payload["price"])
        side = str(payload["side"])
        group["event_count"] += 1
        if side == "BUY":
            group["reported_buy_size"] += size
        elif side == "SELL":
            group["reported_sell_size"] += size
        else:
            group["reported_unknown_size"] += size
        # This is deliberately named price_times_size, not notional. Until a
        # contract rule proves what `size` means, the product is not promoted to
        # an economic quote-notional claim.
        group["reported_price_times_size"] += price * size

    output: Dict[str, Mapping[str, Any]] = {}
    for (provider, venue), group in sorted(grouped.items()):
        group_id = f"{provider}:{venue}"
        output[group_id] = {
            "provider": provider,
            "venue": venue,
            "event_count": int(group["event_count"]),
            "reported_buy_size": _text(group["reported_buy_size"]),
            "reported_sell_size": _text(group["reported_sell_size"]),
            "reported_unknown_size": _text(group["reported_unknown_size"]),
            "reported_price_times_size": _text(group["reported_price_times_size"]),
            "size_unit_semantics": "PROVIDER_NATIVE_UNSPECIFIED",
            "normalized_economic_unit": None,
            "cross_provider_comparable": False,
        }
    return output


def build_derivative_state(
    observations: Sequence[CanonicalObservation],
    *,
    cutoff_at_ns: Optional[int] = None,
    builder_version: str = DERIVATIVE_BUILDER_VERSION,
) -> RepresentationFrame:
    """Build one causal derivative-structure representation.

    Funding, open interest, liquidation, mark, and index facts are preserved as
    separate evidence families. The builder does not infer missing positioning
    from volume/price, and it does not compare provider-native open-interest or
    liquidation units across venues without a later explicit unit-normalization
    contract.
    """
    relevant = [item for item in observations if item.event_type in DERIVATIVE_EVENT_TYPES]
    if not relevant:
        raise DerivativeStateError("derivative state requires derivative observations")
    if len({item.observation_id for item in relevant}) != len(relevant):
        raise DerivativeStateError("duplicate derivative source observation id")
    instruments = {item.instrument for item in relevant}
    if len(instruments) != 1:
        raise DerivativeStateError("derivative state cannot mix canonical instruments")
    instrument = next(iter(instruments))
    if instrument.market_type not in DERIVATIVE_MARKET_TYPES:
        raise DerivativeStateError("derivative state requires PERPETUAL or FUTURE instrument")

    max_known = max(item.known_at_ns for item in relevant)
    cutoff = max_known if cutoff_at_ns is None else int(cutoff_at_ns)
    if cutoff < 0:
        raise DerivativeStateError("cutoff_at_ns must be non-negative")
    future = [item.observation_id for item in relevant if item.known_at_ns > cutoff]
    if future:
        raise DerivativeStateError("lookahead rejected: derivative observations known after cutoff: %s" % ", ".join(sorted(future)))

    ordered = sorted(relevant, key=_observation_order)
    valid = [item for item in ordered if item.quality.get("status") == "VALID"]
    status_counts = Counter(str(item.quality.get("status", "UNAVAILABLE")) for item in ordered)

    funding = _latest(valid, "FUNDING")
    open_interest = _latest(valid, "OPEN_INTEREST")
    mark = _latest(valid, "MARK_PRICE")
    index = _latest(valid, "INDEX_PRICE")
    liquidations = [item for item in valid if item.event_type == "LIQUIDATION"]

    if open_interest is not None:
        normalized_oi = normalize_payload(open_interest.event_type, open_interest.payload)
        if _decimal(normalized_oi["open_interest"]) < 0:
            raise DerivativeStateError("open interest cannot be negative")

    mark_value = None if mark is None else _decimal(normalize_payload(mark.event_type, mark.payload)["price"])
    index_value = None if index is None else _decimal(normalize_payload(index.event_type, index.payload)["price"])
    mark_index_divergence_bps: Optional[Decimal] = None
    if mark_value is not None and index_value is not None:
        if index_value <= 0 or mark_value <= 0:
            raise DerivativeStateError("mark/index prices must be positive")
        mark_index_divergence_bps = (mark_value - index_value) / index_value * Decimal("10000")

    liquidation_groups = _liquidation_groups(liquidations)
    liquidation_status = "QUALIFIED" if liquidations else "UNAVAILABLE"
    cross_provider_aggregate_status = "UNAVAILABLE" if len(liquidation_groups) > 1 else "NOT_REQUIRED"
    mark_index_status = "QUALIFIED" if mark is not None and index is not None else "DEGRADED" if mark is not None or index is not None else "UNAVAILABLE"
    feature_family_status = {
        "FUNDING": "QUALIFIED" if funding is not None else "UNAVAILABLE",
        "OPEN_INTEREST": "QUALIFIED" if open_interest is not None else "UNAVAILABLE",
        "MARK_INDEX": mark_index_status,
        "LIQUIDATIONS": liquidation_status,
    }

    if not valid:
        frame_status = "UNAVAILABLE"
    elif any(state != "VALID" for state in status_counts):
        frame_status = "DEGRADED"
    else:
        frame_status = "QUALIFIED"

    source_ids = tuple(item.observation_id for item in ordered)
    source_hashes = tuple(item.content_hash() for item in ordered)
    state: Dict[str, Any] = {
        "contract": {
            "market_type": instrument.market_type,
            "settlement_asset": instrument.settlement_asset,
            "expiry": instrument.expiry,
        },
        "feature_family_status": feature_family_status,
        "funding": _scalar_family(funding, "rate"),
        "open_interest": _scalar_family(open_interest, "open_interest"),
        "mark_index": {
            "status": mark_index_status,
            "mark_price": _text(mark_value),
            "index_price": _text(index_value),
            "mark_index_divergence_bps": _text(mark_index_divergence_bps),
            "mark_source_observation_id": None if mark is None else mark.observation_id,
            "index_source_observation_id": None if index is None else index.observation_id,
        },
        "liquidations": {
            "status": liquidation_status,
            "truth_class": "PROVIDER_REPORTED_SIDE_UNINTERPRETED",
            "event_count": len(liquidations),
            "provider_venue_groups": liquidation_groups,
            "cross_provider_aggregate_status": cross_provider_aggregate_status,
            "cross_provider_aggregate": None,
            "size_unit_semantics": "PROVIDER_NATIVE_UNSPECIFIED",
            "cross_provider_comparable": False,
        },
        "comparability": {
            "open_interest_cross_venue_comparable": False,
            "liquidation_size_cross_venue_comparable": False,
            "spot_derivative_amount_comparable": False,
            "reason": "CANONICAL_UNIT_NORMALIZATION_NOT_YET_QUALIFIED",
        },
        "input_quality": {
            "status_counts": dict(sorted(status_counts.items())),
        },
    }
    return RepresentationFrame(
        frame_id=_frame_id(instrument.canonical_id, cutoff, source_hashes, builder_version),
        representation_type="DERIVATIVE_STATE",
        instrument=instrument,
        window_start_ns=min(item.known_at_ns for item in ordered),
        cutoff_at_ns=cutoff,
        known_at_ns=max_known,
        latest_source_event_at_ns=max(item.source_event_at_ns for item in ordered),
        status=frame_status,
        builder_version=builder_version,
        parameters={
            "lookahead_policy": "HARD_REJECT_IF_SOURCE_KNOWN_AFTER_CUTOFF",
            "missing_family_policy": "UNAVAILABLE_NOT_SYNTHESIZED",
            "open_interest_unit_policy": "PROVIDER_NATIVE_UNSPECIFIED_NO_CROSS_VENUE_COMPARISON",
            "liquidation_unit_policy": "PARTITION_BY_PROVIDER_VENUE_NO_CROSS_PROVIDER_SUM",
            "liquidation_side_policy": "PROVIDER_REPORTED_SIDE_UNINTERPRETED",
        },
        state=state,
        source_observation_ids=source_ids,
        source_content_hashes=source_hashes,
        source_providers=tuple(sorted({item.provider for item in ordered})),
        source_venues=tuple(sorted({item.venue for item in ordered})),
    )
