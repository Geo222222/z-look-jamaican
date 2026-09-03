from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from math import sqrt
from statistics import median
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..operations import canonical_hash
from ..representation.contracts import RepresentationFrame
from .contracts import MarketContextFrame


BUILDER_VERSION = "market-context-v1"
DERIVATIVE_MARKET_TYPES = {"FUTURE", "PERPETUAL", "PERP", "SWAP", "DERIVATIVE"}


class MarketContextBuildError(ValueError):
    pass


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _text(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else format(value, "f")


def _mean(values: Sequence[Decimal]) -> Optional[Decimal]:
    return None if not values else sum(values, Decimal("0")) / Decimal(len(values))


def _stdev(values: Sequence[Decimal]) -> Optional[Decimal]:
    if len(values) < 2:
        return None
    center = _mean(values)
    if center is None:
        return None
    variance = sum((value - center) ** 2 for value in values) / Decimal(len(values))
    return Decimal(str(sqrt(float(variance))))


def _pearson(left: Sequence[Decimal], right: Sequence[Decimal]) -> Optional[Decimal]:
    if len(left) != len(right) or len(left) < 3:
        return None
    left_mean = _mean(left)
    right_mean = _mean(right)
    if left_mean is None or right_mean is None:
        return None
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_ss = sum((a - left_mean) ** 2 for a in left)
    right_ss = sum((b - right_mean) ** 2 for b in right)
    if left_ss == 0 or right_ss == 0:
        return None
    return Decimal(str(float(numerator) / sqrt(float(left_ss * right_ss))))


def _order(frame: RepresentationFrame) -> Tuple[Any, ...]:
    return (frame.instrument.canonical_id, frame.cutoff_at_ns, frame.known_at_ns, frame.frame_id)


def _midpoint(frame: RepresentationFrame) -> Optional[Decimal]:
    aggregate = frame.state.get("aggregate")
    if not isinstance(aggregate, Mapping):
        return None
    bid = aggregate.get("cross_venue_best_bid")
    ask = aggregate.get("cross_venue_best_ask")
    if aggregate.get("cross_venue_book_state") == "NORMAL" and bid is not None and ask is not None:
        value = (_decimal(bid) + _decimal(ask)) / Decimal("2")
        return value if value > 0 else None
    value = aggregate.get("mean_venue_midpoint")
    if value is None:
        return None
    number = _decimal(value)
    return number if number > 0 else None


def _spread(frame: RepresentationFrame) -> Optional[Decimal]:
    aggregate = frame.state.get("aggregate")
    if not isinstance(aggregate, Mapping):
        return None
    value = aggregate.get("cross_venue_spread_bps")
    if value is not None:
        return _decimal(value)
    values: List[Decimal] = []
    venues = frame.state.get("venue_states")
    if isinstance(venues, Mapping):
        for venue_state in venues.values():
            if not isinstance(venue_state, Mapping):
                continue
            book = venue_state.get("book")
            if isinstance(book, Mapping) and book.get("status") == "QUALIFIED" and book.get("spread_bps") is not None:
                values.append(_decimal(book["spread_bps"]))
    return _mean(values)


def _depth(frame: RepresentationFrame, band_bps: int) -> Decimal:
    total = Decimal("0")
    venues = frame.state.get("venue_states")
    if not isinstance(venues, Mapping):
        return total
    for venue_state in venues.values():
        if not isinstance(venue_state, Mapping):
            continue
        book = venue_state.get("book")
        if not isinstance(book, Mapping) or book.get("status") != "QUALIFIED":
            continue
        bands = book.get("depth_bands_bps")
        band = bands.get(str(band_bps)) if isinstance(bands, Mapping) else None
        if isinstance(band, Mapping):
            total += max(Decimal("0"), _decimal(band.get("bid_quote_notional", "0")))
            total += max(Decimal("0"), _decimal(band.get("ask_quote_notional", "0")))
    return total


def _flow_ratio(frame: RepresentationFrame) -> Optional[Decimal]:
    aggregate = frame.state.get("aggregate")
    flow = aggregate.get("trade_flow") if isinstance(aggregate, Mapping) else None
    if not isinstance(flow, Mapping):
        return None
    buy = _decimal(flow.get("reported_buy_quote_notional", "0"))
    sell = _decimal(flow.get("reported_sell_quote_notional", "0"))
    return None if buy + sell <= 0 else (buy - sell) / (buy + sell)


def _returns(sequence: Sequence[RepresentationFrame]) -> Tuple[Tuple[int, Decimal], ...]:
    output: List[Tuple[int, Decimal]] = []
    prior: Optional[Decimal] = None
    for frame in sequence:
        current = _midpoint(frame)
        if current is None:
            continue
        if prior is not None and prior > 0:
            output.append((frame.cutoff_at_ns, (current / prior - Decimal("1")) * Decimal("10000")))
        prior = current
    return tuple(output)


def _aligned(left: Sequence[Tuple[int, Decimal]], right: Sequence[Tuple[int, Decimal]], tolerance_ns: int) -> Tuple[Tuple[Decimal, Decimal], ...]:
    output: List[Tuple[Decimal, Decimal]] = []
    used = set()
    for left_time, left_value in left:
        candidates = [(abs(left_time - right_time), right_time, index, right_value) for index, (right_time, right_value) in enumerate(right) if index not in used and abs(left_time - right_time) <= tolerance_ns]
        if candidates:
            _, _, index, right_value = min(candidates)
            used.add(index)
            output.append((left_value, right_value))
    return tuple(output)


def _pair_correlation(left: Sequence[Tuple[int, Decimal]], right: Sequence[Tuple[int, Decimal]], tolerance_ns: int) -> Tuple[Optional[Decimal], int]:
    pairs = _aligned(left, right, tolerance_ns)
    return (_pearson([a for a, _ in pairs], [b for _, b in pairs]), len(pairs)) if len(pairs) >= 3 else (None, len(pairs))


def _lead_lag(spot: Sequence[Tuple[int, Decimal]], derivative: Sequence[Tuple[int, Decimal]], tolerance_ns: int, minimum_pairs: int, margin: Decimal) -> Mapping[str, Any]:
    pairs = _aligned(spot, derivative, tolerance_ns)
    if len(pairs) < minimum_pairs:
        return {"status": "UNAVAILABLE", "truth_class": "ALIGNED_RETURN_SEQUENCE_LAG_PROXY", "aligned_pair_count": len(pairs), "leader": "UNAVAILABLE", "spot_leads_correlation": None, "derivative_leads_correlation": None}
    spot_values = [a for a, _ in pairs]
    derivative_values = [b for _, b in pairs]
    spot_leads = _pearson(spot_values[:-1], derivative_values[1:])
    derivative_leads = _pearson(derivative_values[:-1], spot_values[1:])
    if spot_leads is None or derivative_leads is None:
        leader = "INCONCLUSIVE"
    elif derivative_leads > spot_leads + margin:
        leader = "DERIVATIVE_LEADING"
    elif spot_leads > derivative_leads + margin:
        leader = "SPOT_LEADING"
    else:
        leader = "INCONCLUSIVE"
    return {"status": "QUALIFIED_PROXY", "truth_class": "ALIGNED_RETURN_SEQUENCE_LAG_PROXY_NOT_CAUSALITY", "aligned_pair_count": len(pairs), "leader": leader, "spot_leads_correlation": _text(spot_leads), "derivative_leads_correlation": _text(derivative_leads)}


def _expiry_ns(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


def _annualized_basis(basis_bps: Decimal, expiry: Optional[str], cutoff_ns: int) -> Optional[Decimal]:
    end = _expiry_ns(expiry)
    if end is None or end <= cutoff_ns:
        return None
    seconds = Decimal(end - cutoff_ns) / Decimal("1000000000")
    return basis_bps * Decimal("31557600") / seconds


def _regimes(market_return: Optional[Decimal], breadth: Optional[Decimal], median_vol: Optional[Decimal], median_spread: Optional[Decimal], median_abs_corr: Optional[Decimal], bases: Sequence[Decimal], structural_dislocation: bool, thresholds: Mapping[str, Decimal]) -> Mapping[str, str]:
    if market_return is None or breadth is None:
        direction = "UNAVAILABLE"
    elif market_return >= thresholds["direction"] and breadth >= thresholds["breadth"]:
        direction = "RISK_ON"
    elif market_return <= -thresholds["direction"] and breadth <= Decimal("1") - thresholds["breadth"]:
        direction = "RISK_OFF"
    elif abs(market_return) < thresholds["direction"]:
        direction = "NEUTRAL"
    else:
        direction = "MIXED"
    volatility = "UNAVAILABLE" if median_vol is None else "HIGH" if median_vol >= thresholds["high_vol"] else "LOW" if median_vol <= thresholds["low_vol"] else "NORMAL"
    liquidity = "UNAVAILABLE" if median_spread is None else "STRESSED" if median_spread >= thresholds["stressed_spread"] else "NORMAL"
    correlation = "UNAVAILABLE" if median_abs_corr is None else "COHERENT" if median_abs_corr >= thresholds["coherent_corr"] else "FRAGMENTED" if median_abs_corr <= thresholds["fragmented_corr"] else "NORMAL"
    if not bases:
        derivatives = "UNAVAILABLE"
    elif all(value >= thresholds["basis"] for value in bases):
        derivatives = "CONTANGO"
    elif all(value <= -thresholds["basis"] for value in bases):
        derivatives = "BACKWARDATION"
    else:
        derivatives = "MIXED"
    return {"direction": direction, "volatility": volatility, "liquidity": liquidity, "correlation": correlation, "derivatives": derivatives, "structure": "DISLOCATED" if structural_dislocation else "ORDERLY"}


def build_market_context(frames: Sequence[RepresentationFrame], *, cutoff_at_ns: Optional[int] = None, instrument_weights: Optional[Mapping[str, Any]] = None, liquidity_depth_band_bps: int = 10, alignment_tolerance_ns: int = 5_000_000_000, maximum_member_age_ns: int = 30_000_000_000, minimum_core_instruments: int = 2, minimum_history_points: int = 3, minimum_lead_lag_pairs: int = 4, direction_threshold_bps: Any = "2", breadth_threshold: Any = "0.60", high_volatility_bps: Any = "15", low_volatility_bps: Any = "3", stressed_spread_bps: Any = "5", coherent_correlation: Any = "0.65", fragmented_correlation: Any = "0.25", basis_regime_threshold_bps: Any = "2", lead_lag_margin: Any = "0.10", builder_version: str = BUILDER_VERSION) -> MarketContextFrame:
    source = tuple(frames)
    if not source:
        raise MarketContextBuildError("Z9 requires representation frames")
    if len({frame.frame_id for frame in source}) != len(source):
        raise MarketContextBuildError("duplicate representation frame id")
    if any(frame.representation_type != "INSTRUMENT_STATE" for frame in source):
        raise MarketContextBuildError("Z9 v1 accepts only INSTRUMENT_STATE source frames")
    cutoff = max(frame.cutoff_at_ns for frame in source) if cutoff_at_ns is None else int(cutoff_at_ns)
    future = [frame.frame_id for frame in source if frame.known_at_ns > cutoff or frame.cutoff_at_ns > cutoff]
    if future:
        raise MarketContextBuildError("lookahead rejected: representation frames exceed context cutoff: %s" % ", ".join(sorted(future)))
    if cutoff < 0 or liquidity_depth_band_bps <= 0 or alignment_tolerance_ns < 0 or maximum_member_age_ns <= 0 or minimum_core_instruments <= 0 or minimum_history_points < 2 or minimum_lead_lag_pairs < 3:
        raise MarketContextBuildError("Z9 parameters are invalid")

    grouped: Dict[str, List[RepresentationFrame]] = {}
    for frame in sorted(source, key=_order):
        grouped.setdefault(frame.instrument.canonical_id, []).append(frame)
    latest = {key: value[-1] for key, value in grouped.items()}
    returns = {key: _returns(value) for key, value in grouped.items()}
    configured_weights = {str(key): _decimal(value) for key, value in (instrument_weights or {}).items()}
    if any(value <= 0 for value in configured_weights.values()):
        raise MarketContextBuildError("instrument weights must be positive")

    members: Dict[str, Mapping[str, Any]] = {}
    current_returns: Dict[str, Decimal] = {}
    depths: Dict[str, Decimal] = {}
    spreads: List[Decimal] = []
    volatilities: List[Decimal] = []
    degraded: List[str] = []
    structural_dislocation = False
    for instrument_id in sorted(latest):
        frame = latest[instrument_id]
        midpoint = _midpoint(frame)
        spread = _spread(frame)
        depth = _depth(frame, liquidity_depth_band_bps)
        history_returns = [value for _, value in returns[instrument_id]]
        latest_return = history_returns[-1] if history_returns else None
        realized_vol = _stdev(history_returns)
        age = max(0, cutoff - frame.known_at_ns)
        freshness = max(Decimal("0"), min(Decimal("1"), Decimal("1") - Decimal(age) / Decimal(maximum_member_age_ns)))
        base_reliability = {"QUALIFIED": Decimal("1"), "DEGRADED": Decimal("0.6"), "UNAVAILABLE": Decimal("0")}.get(frame.status, Decimal("0"))
        reliability = base_reliability * freshness
        if latest_return is not None:
            current_returns[instrument_id] = latest_return
        if depth > 0:
            depths[instrument_id] = depth
        if spread is not None:
            spreads.append(spread)
        if realized_vol is not None:
            volatilities.append(realized_vol)
        if frame.status != "QUALIFIED":
            degraded.append("NON_QUALIFIED_MEMBER_%s" % instrument_id)
        if len(grouped[instrument_id]) < minimum_history_points:
            degraded.append("INSUFFICIENT_HISTORY_%s" % instrument_id)
        aggregate = frame.state.get("aggregate")
        if isinstance(aggregate, Mapping) and aggregate.get("cross_venue_book_state") == "CROSSED_OR_DISLOCATED":
            structural_dislocation = True
        members[instrument_id] = {"frame_id": frame.frame_id, "frame_content_hash": frame.content_hash(), "status": frame.status, "market_type": frame.instrument.market_type, "asset_class": frame.instrument.asset_class, "base_asset": frame.instrument.base_asset, "quote_asset": frame.instrument.quote_asset, "settlement_asset": frame.instrument.settlement_asset, "expiry": frame.instrument.expiry, "cutoff_at_ns": frame.cutoff_at_ns, "known_at_ns": frame.known_at_ns, "age_ns": age, "freshness_factor": _text(freshness), "data_reliability": _text(reliability), "midpoint": _text(midpoint), "spread_bps": _text(spread), "liquidity_quote_notional": _text(depth), "reported_flow_ratio": _text(_flow_ratio(frame)), "latest_return_bps": _text(latest_return), "realized_volatility_bps": _text(realized_vol), "history_frame_count": len(grouped[instrument_id]), "history_return_count": len(history_returns)}

    return_ids = sorted(current_returns)
    raw_weights = {key: configured_weights.get(key, Decimal("1")) for key in return_ids}
    weight_total = sum(raw_weights.values(), Decimal("0"))
    effective_weights = {key: raw_weights[key] / weight_total for key in return_ids} if weight_total > 0 else {}
    market_return = sum((current_returns[key] * effective_weights[key] for key in return_ids), Decimal("0")) if return_ids else None
    breadth = Decimal(sum(1 for value in current_returns.values() if value > 0)) / Decimal(len(current_returns)) if current_returns else None
    dispersion = _stdev(list(current_returns.values()))
    median_spread = Decimal(str(median(spreads))) if spreads else None
    median_vol = Decimal(str(median(volatilities))) if volatilities else None
    liquidity_hhi = None
    if depths:
        total_depth = sum(depths.values(), Decimal("0"))
        if total_depth > 0:
            liquidity_hhi = sum((value / total_depth) ** 2 for value in depths.values())

    pairwise: Dict[str, Mapping[str, Any]] = {}
    absolute_correlations: List[Decimal] = []
    ids = sorted(grouped)
    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1:]:
            correlation, pair_count = _pair_correlation(returns[left_id], returns[right_id], alignment_tolerance_ns)
            pairwise["%s|%s" % (left_id, right_id)] = {"correlation": _text(correlation), "aligned_pair_count": pair_count, "truth_class": "POINT_IN_TIME_RETURN_CORRELATION"}
            if correlation is not None:
                absolute_correlations.append(abs(correlation))
    median_abs_corr = Decimal(str(median(absolute_correlations))) if absolute_correlations else None

    spots = [(key, latest[key]) for key in ids if latest[key].instrument.market_type == "SPOT"]
    derivatives = [(key, latest[key]) for key in ids if latest[key].instrument.market_type in DERIVATIVE_MARKET_TYPES]
    relationships: List[Mapping[str, Any]] = []
    bases: List[Decimal] = []
    for derivative_id, derivative_frame in derivatives:
        derivative_instrument = derivative_frame.instrument
        for spot_id, spot_frame in spots:
            spot_instrument = spot_frame.instrument
            if spot_instrument.asset_class != derivative_instrument.asset_class or spot_instrument.base_asset != derivative_instrument.base_asset or spot_instrument.quote_asset != derivative_instrument.quote_asset:
                continue
            spot_mid = _midpoint(spot_frame)
            derivative_mid = _midpoint(derivative_frame)
            if spot_mid is None or derivative_mid is None or spot_mid <= 0:
                continue
            basis = (derivative_mid / spot_mid - Decimal("1")) * Decimal("10000")
            bases.append(basis)
            relationships.append({"spot_instrument_id": spot_id, "derivative_instrument_id": derivative_id, "derivative_market_type": derivative_instrument.market_type, "spot_frame_id": spot_frame.frame_id, "derivative_frame_id": derivative_frame.frame_id, "basis_bps": _text(basis), "annualized_basis_bps": _text(_annualized_basis(basis, derivative_instrument.expiry, cutoff)), "lead_lag": _lead_lag(returns[spot_id], returns[derivative_id], alignment_tolerance_ns, minimum_lead_lag_pairs, _decimal(lead_lag_margin))})

    qualified_spots = [frame for _, frame in spots if frame.status == "QUALIFIED" and _midpoint(frame) is not None]
    core_status = "QUALIFIED" if len(qualified_spots) >= minimum_core_instruments and len(current_returns) >= minimum_core_instruments else "DEGRADED" if qualified_spots else "UNAVAILABLE"
    feature_quality = {
        "CORE_MARKET": {"status": core_status},
        "CROSS_ASSET": {"status": "QUALIFIED" if len(current_returns) >= minimum_core_instruments else "DEGRADED" if current_returns else "UNAVAILABLE"},
        "LIQUIDITY": {"status": "QUALIFIED" if depths and median_spread is not None else "UNAVAILABLE"},
        "CORRELATION": {"status": "QUALIFIED" if absolute_correlations else "UNAVAILABLE"},
        "DERIVATIVES": {"status": "QUALIFIED" if relationships else "UNAVAILABLE"},
        "LEAD_LAG": {"status": "QUALIFIED" if any(item["lead_lag"]["status"] == "QUALIFIED_PROXY" for item in relationships) else "UNAVAILABLE", "truth_class": "PROXY_ONLY_NOT_CAUSALITY"},
    }
    if core_status == "UNAVAILABLE":
        context_status = "UNAVAILABLE"
    elif core_status != "QUALIFIED" or any(frame.status != "QUALIFIED" for frame in latest.values()):
        context_status = "DEGRADED"
    else:
        context_status = "QUALIFIED"
    if core_status != "QUALIFIED":
        degraded.append("INSUFFICIENT_CORE_MARKET_BREADTH")
    if not absolute_correlations:
        degraded.append("CORRELATION_CONTEXT_UNAVAILABLE")
    if not relationships:
        degraded.append("DERIVATIVE_CONTEXT_UNAVAILABLE")

    thresholds = {"direction": _decimal(direction_threshold_bps), "breadth": _decimal(breadth_threshold), "high_vol": _decimal(high_volatility_bps), "low_vol": _decimal(low_volatility_bps), "stressed_spread": _decimal(stressed_spread_bps), "coherent_corr": _decimal(coherent_correlation), "fragmented_corr": _decimal(fragmented_correlation), "basis": _decimal(basis_regime_threshold_bps)}
    regimes = _regimes(market_return, breadth, median_vol, median_spread, median_abs_corr, bases, structural_dislocation, thresholds)
    ordered = tuple(sorted(source, key=_order))
    parameters = {"liquidity_depth_band_bps": int(liquidity_depth_band_bps), "alignment_tolerance_ns": int(alignment_tolerance_ns), "maximum_member_age_ns": int(maximum_member_age_ns), "minimum_core_instruments": int(minimum_core_instruments), "minimum_history_points": int(minimum_history_points), "minimum_lead_lag_pairs": int(minimum_lead_lag_pairs), "instrument_weights": {key: format(value, "f") for key, value in sorted(configured_weights.items())}, "regime_thresholds": {key: format(value, "f") for key, value in thresholds.items()}, "lead_lag_margin": format(_decimal(lead_lag_margin), "f"), "lookahead_policy": "HARD_REJECT_FRAME_KNOWN_OR_CUTOFF_AFTER_CONTEXT_CUTOFF", "correlation_policy": "ALIGN_RETURNS_BY_NEAREST_TIMESTAMP_WITHOUT_REUSE", "lead_lag_policy": "ALIGNED_SEQUENCE_ONE_STEP_PROXY_NOT_CAUSALITY", "weighting_authority": "REPRESENTATIONAL_ONLY_NO_CAPITAL_AUTHORITY"}
    state = {"members": members, "market": {"member_instrument_count": len(latest), "qualified_spot_count": len(qualified_spots), "return_breadth_count": len(current_returns), "aggregate_return_bps": _text(market_return), "effective_return_weights": {key: format(value, "f") for key, value in sorted(effective_weights.items())}, "breadth_positive": _text(breadth), "cross_sectional_return_dispersion_bps": _text(dispersion), "median_realized_volatility_bps": _text(median_vol), "median_spread_bps": _text(median_spread), "liquidity_concentration_hhi": _text(liquidity_hhi), "median_absolute_pairwise_correlation": _text(median_abs_corr), "pairwise_correlations": pairwise}, "derivatives": {"relationship_count": len(relationships), "relationships": relationships}, "regimes": regimes, "feature_quality": feature_quality, "input_quality": {"degraded_reasons": sorted(set(degraded))}}
    material = {"cutoff_at_ns": cutoff, "builder_version": builder_version, "source": [(frame.frame_id, frame.content_hash()) for frame in ordered], "parameters": parameters, "state": state}
    return MarketContextFrame(context_id="CTX-%s" % canonical_hash(material)[:32], context_type="MARKET_CONTEXT", cutoff_at_ns=cutoff, known_at_ns=max(frame.known_at_ns for frame in ordered), status=context_status, builder_version=builder_version, parameters=parameters, state=state, source_frame_ids=tuple(frame.frame_id for frame in ordered), source_frame_hashes=tuple(frame.content_hash() for frame in ordered), source_instrument_ids=tuple(frame.instrument.canonical_id for frame in ordered))
