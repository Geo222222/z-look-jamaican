from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from math import sqrt
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..operations import canonical_hash
from ..representation.contracts import RepresentationFrame
from .economic_graph import EconomicInstrumentGraph, EconomicRelationshipType, InstrumentRole


RELATIONSHIP_STATE_SCHEMA_VERSION = "1.0"
RELATIONSHIP_STATE_STATUSES = {"QUALIFIED", "DEGRADED", "UNAVAILABLE"}


class RelationshipStateError(ValueError):
    pass


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _text(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else format(value, "f")


def _mean(values: Sequence[Decimal]) -> Optional[Decimal]:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


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
    midpoint = _decimal(value)
    return midpoint if midpoint > 0 else None


def _spread_bps(frame: RepresentationFrame) -> Optional[Decimal]:
    aggregate = frame.state.get("aggregate")
    if not isinstance(aggregate, Mapping):
        return None
    value = aggregate.get("cross_venue_spread_bps")
    return None if value is None else _decimal(value)


def _depth_quote_notional(frame: RepresentationFrame, band_bps: int = 10) -> Decimal:
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


def _returns(frames: Sequence[RepresentationFrame]) -> Tuple[Tuple[int, Decimal], ...]:
    ordered = sorted(frames, key=lambda frame: (frame.cutoff_at_ns, frame.known_at_ns, frame.frame_id))
    output = []
    prior: Optional[Decimal] = None
    for frame in ordered:
        midpoint = _midpoint(frame)
        if midpoint is None:
            continue
        if prior is not None and prior > 0:
            output.append((frame.cutoff_at_ns, (midpoint / prior - Decimal("1")) * Decimal("10000")))
        prior = midpoint
    return tuple(output)


def _aligned(
    left: Sequence[Tuple[int, Decimal]],
    right: Sequence[Tuple[int, Decimal]],
    tolerance_ns: int,
) -> Tuple[Tuple[int, Decimal, Decimal], ...]:
    pairs = []
    used = set()
    for left_time, left_value in left:
        candidates = [
            (abs(left_time - right_time), right_time, index, right_value)
            for index, (right_time, right_value) in enumerate(right)
            if index not in used and abs(left_time - right_time) <= tolerance_ns
        ]
        if candidates:
            _, right_time, index, right_value = min(candidates)
            used.add(index)
            pairs.append((max(left_time, right_time), left_value, right_value))
    return tuple(pairs)


def _lagged_association(
    spot_returns: Sequence[Tuple[int, Decimal]],
    derivative_returns: Sequence[Tuple[int, Decimal]],
    *,
    tolerance_ns: int,
    minimum_pairs: int,
    margin: Decimal,
) -> Dict[str, Any]:
    pairs = _aligned(spot_returns, derivative_returns, tolerance_ns)
    if len(pairs) < minimum_pairs:
        return {
            "status": "UNAVAILABLE",
            "truth_class": "CAUSAL_CUTOFF_LAGGED_ASSOCIATION_NOT_CAUSALITY",
            "aligned_pair_count": len(pairs),
            "association": "UNAVAILABLE",
            "spot_precedes_derivative_correlation": None,
            "derivative_precedes_spot_correlation": None,
        }
    spot_values = [spot for _, spot, _ in pairs]
    derivative_values = [derivative for _, _, derivative in pairs]
    spot_precedes = _pearson(spot_values[:-1], derivative_values[1:])
    derivative_precedes = _pearson(derivative_values[:-1], spot_values[1:])
    if spot_precedes is None or derivative_precedes is None:
        association = "INCONCLUSIVE"
    elif derivative_precedes > spot_precedes + margin:
        association = "DERIVATIVE_PRECEDES_SPOT"
    elif spot_precedes > derivative_precedes + margin:
        association = "SPOT_PRECEDES_DERIVATIVE"
    else:
        association = "INCONCLUSIVE"
    return {
        "status": "QUALIFIED_ASSOCIATION",
        "truth_class": "CAUSAL_CUTOFF_LAGGED_ASSOCIATION_NOT_CAUSALITY",
        "aligned_pair_count": len(pairs),
        "association": association,
        "spot_precedes_derivative_correlation": _text(spot_precedes),
        "derivative_precedes_spot_correlation": _text(derivative_precedes),
    }


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
    end_ns = _expiry_ns(expiry)
    if end_ns is None or end_ns <= cutoff_ns:
        return None
    seconds = Decimal(end_ns - cutoff_ns) / Decimal("1000000000")
    return basis_bps * Decimal("31557600") / seconds


def _latest_derivative_family(frame: Optional[RepresentationFrame], family: str) -> Mapping[str, Any]:
    if frame is None:
        return {"status": "UNAVAILABLE"}
    value = frame.state.get(family)
    if not isinstance(value, Mapping):
        return {"status": "UNAVAILABLE"}
    return dict(value)


def _oi_change(
    derivative_states: Sequence[RepresentationFrame],
) -> Mapping[str, Any]:
    valid = []
    for frame in sorted(derivative_states, key=lambda item: (item.known_at_ns, item.frame_id)):
        if frame.representation_type != "DERIVATIVE_STATE":
            continue
        oi = frame.state.get("open_interest")
        if not isinstance(oi, Mapping) or oi.get("status") != "QUALIFIED" or oi.get("value") is None:
            continue
        provider = str(oi.get("provider", ""))
        venue = str(oi.get("venue", ""))
        unit = str(oi.get("unit_semantics", ""))
        if not provider or not venue or not unit:
            continue
        valid.append((frame, _decimal(oi["value"]), provider, venue, unit))
    if len(valid) < 2:
        return {"status": "UNAVAILABLE", "reason": "INSUFFICIENT_COMPATIBLE_HISTORY"}
    prior, current = valid[-2], valid[-1]
    _, prior_value, prior_provider, prior_venue, prior_unit = prior
    _, current_value, provider, venue, unit = current
    if (prior_provider, prior_venue, prior_unit) != (provider, venue, unit):
        return {"status": "UNAVAILABLE", "reason": "UNIT_OR_SOURCE_SEMANTICS_CHANGED"}
    if prior_value <= 0:
        return {"status": "UNAVAILABLE", "reason": "NON_POSITIVE_PRIOR_OPEN_INTEREST"}
    change = (current_value / prior_value - Decimal("1")) * Decimal("10000")
    return {
        "status": "QUALIFIED",
        "change_bps": _text(change),
        "provider": provider,
        "venue": venue,
        "unit_semantics": unit,
        "prior_state_id": prior[0].frame_id,
        "current_state_id": current[0].frame_id,
        "cross_venue_comparable": False,
    }


@dataclass(frozen=True)
class EconomicRelationshipState:
    relationship_state_id: str
    relationship_id: str
    relationship_type: str
    economic_root_id: str
    cutoff_at_ns: int
    known_at_ns: int
    status: str
    graph_id: str
    graph_version: str
    graph_hash: str
    source_node_id: str
    target_node_id: str
    source_frame_ids: Tuple[str, ...]
    source_frame_hashes: Tuple[str, ...]
    state: Mapping[str, Any]
    builder_version: str = "economic-relationship-state-v1"
    schema_version: str = RELATIONSHIP_STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RELATIONSHIP_STATE_SCHEMA_VERSION:
            raise RelationshipStateError("unsupported relationship-state schema")
        if not self.relationship_state_id or not self.relationship_id or not self.economic_root_id:
            raise RelationshipStateError("relationship-state identity is required")
        if self.status not in RELATIONSHIP_STATE_STATUSES:
            raise RelationshipStateError("relationship-state status is invalid")
        if self.known_at_ns > self.cutoff_at_ns or self.cutoff_at_ns < 0:
            raise RelationshipStateError("relationship-state timing is invalid")
        if len(self.source_frame_ids) != len(self.source_frame_hashes):
            raise RelationshipStateError("relationship-state source ids/hashes must align")
        if len(set(self.source_frame_ids)) != len(self.source_frame_ids):
            raise RelationshipStateError("relationship-state source frame ids must be unique")

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "relationship_state_id": self.relationship_state_id,
            "relationship_id": self.relationship_id,
            "relationship_type": self.relationship_type,
            "economic_root_id": self.economic_root_id,
            "cutoff_at_ns": self.cutoff_at_ns,
            "known_at_ns": self.known_at_ns,
            "status": self.status,
            "builder_version": self.builder_version,
            "economic_graph": {
                "graph_id": self.graph_id,
                "graph_version": self.graph_version,
                "content_hash": self.graph_hash,
            },
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "source_frames": [
                {"frame_id": frame_id, "content_hash": frame_hash}
                for frame_id, frame_hash in zip(self.source_frame_ids, self.source_frame_hashes)
            ],
            "state": dict(self.state),
            "authority": {
                "capital_decision": False,
                "risk_authorization": False,
                "external_execution": False,
            },
        }

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        value = self.body()
        value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}
        return value


def build_spot_derivative_relationship_state(
    *,
    graph: EconomicInstrumentGraph,
    relationship_id: str,
    spot_frames: Sequence[RepresentationFrame],
    derivative_frames: Sequence[RepresentationFrame],
    derivative_states: Sequence[RepresentationFrame] = (),
    cutoff_at_ns: int,
    alignment_tolerance_ns: int = 5_000_000_000,
    minimum_lag_pairs: int = 4,
    lag_margin: Any = "0.10",
    liquidity_depth_band_bps: int = 10,
    builder_version: str = "economic-relationship-state-v1",
) -> EconomicRelationshipState:
    relationships = {item.relationship_id: item for item in graph.relationships}
    relationship = relationships.get(relationship_id)
    if relationship is None:
        raise RelationshipStateError("relationship_id is absent from Economic Instrument Graph")
    if relationship.relationship_type is not EconomicRelationshipType.SPOT_DERIVATIVE:
        raise RelationshipStateError("relationship is not SPOT_DERIVATIVE")
    node_map = {node.node_id: node for node in graph.nodes}
    source_node = node_map[relationship.source_node_id]
    target_node = node_map[relationship.target_node_id]
    if source_node.role is InstrumentRole.SPOT:
        spot_node, derivative_node = source_node, target_node
    elif target_node.role is InstrumentRole.SPOT:
        spot_node, derivative_node = target_node, source_node
    else:
        raise RelationshipStateError("SPOT_DERIVATIVE relationship does not include a spot node")
    if derivative_node.role not in {InstrumentRole.PERPETUAL, InstrumentRole.DATED_FUTURE}:
        raise RelationshipStateError("SPOT_DERIVATIVE relationship derivative node is invalid")
    if graph.known_at_ns > cutoff_at_ns:
        raise RelationshipStateError("lookahead graph rejected")

    spot = tuple(frame for frame in spot_frames if frame.representation_type == "INSTRUMENT_STATE")
    derivatives = tuple(frame for frame in derivative_frames if frame.representation_type == "INSTRUMENT_STATE")
    structures = tuple(frame for frame in derivative_states if frame.representation_type == "DERIVATIVE_STATE")
    if any(frame.instrument.canonical_id != spot_node.instrument.canonical_id for frame in spot):
        raise RelationshipStateError("spot history contains the wrong instrument")
    if any(frame.instrument.canonical_id != derivative_node.instrument.canonical_id for frame in derivatives + structures):
        raise RelationshipStateError("derivative history contains the wrong instrument")
    all_frames = spot + derivatives + structures
    if any(frame.cutoff_at_ns > cutoff_at_ns or frame.known_at_ns > cutoff_at_ns for frame in all_frames):
        raise RelationshipStateError("lookahead relationship source frame rejected")
    if not spot or not derivatives:
        raise RelationshipStateError("spot-derivative relationship requires spot and derivative price histories")

    spot_latest = max(spot, key=lambda frame: (frame.cutoff_at_ns, frame.known_at_ns, frame.frame_id))
    derivative_latest = max(derivatives, key=lambda frame: (frame.cutoff_at_ns, frame.known_at_ns, frame.frame_id))
    structure_latest = None if not structures else max(structures, key=lambda frame: (frame.known_at_ns, frame.frame_id))
    spot_mid = _midpoint(spot_latest)
    derivative_mid = _midpoint(derivative_latest)
    basis_bps = None
    if spot_mid is not None and derivative_mid is not None and spot_mid > 0:
        basis_bps = (derivative_mid / spot_mid - Decimal("1")) * Decimal("10000")
    annualized_basis = None
    if basis_bps is not None and derivative_node.role is InstrumentRole.DATED_FUTURE:
        annualized_basis = _annualized_basis(basis_bps, derivative_node.instrument.expiry, cutoff_at_ns)

    spot_returns = _returns(spot)
    derivative_returns = _returns(derivatives)
    lagged = _lagged_association(
        spot_returns,
        derivative_returns,
        tolerance_ns=alignment_tolerance_ns,
        minimum_pairs=minimum_lag_pairs,
        margin=_decimal(lag_margin),
    )
    spot_latest_return = None if not spot_returns else spot_returns[-1][1]
    derivative_latest_return = None if not derivative_returns else derivative_returns[-1][1]
    if spot_latest_return is None or derivative_latest_return is None:
        confirmation = "UNAVAILABLE"
    elif spot_latest_return == 0 and derivative_latest_return == 0:
        confirmation = "FLAT_AGREEMENT"
    elif (spot_latest_return > 0 and derivative_latest_return > 0) or (
        spot_latest_return < 0 and derivative_latest_return < 0
    ):
        confirmation = "DIRECTIONALLY_CONFIRMED"
    else:
        confirmation = "DIVERGENT"

    spot_spread = _spread_bps(spot_latest)
    derivative_spread = _spread_bps(derivative_latest)
    spot_depth = _depth_quote_notional(spot_latest, liquidity_depth_band_bps)
    derivative_depth = _depth_quote_notional(derivative_latest, liquidity_depth_band_bps)
    depth_ratio = None if spot_depth <= 0 else derivative_depth / spot_depth
    spread_ratio = None if spot_spread is None or spot_spread <= 0 or derivative_spread is None else derivative_spread / spot_spread

    state = {
        "basis": {
            "status": "QUALIFIED" if basis_bps is not None else "UNAVAILABLE",
            "basis_bps": _text(basis_bps),
            "annualized_basis_bps": _text(annualized_basis),
            "annualized_status": "QUALIFIED" if annualized_basis is not None else "UNAVAILABLE",
        },
        "latest_returns": {
            "spot_return_bps": _text(spot_latest_return),
            "derivative_return_bps": _text(derivative_latest_return),
            "confirmation": confirmation,
        },
        "lagged_association": lagged,
        "relative_liquidity": {
            "spot_spread_bps": _text(spot_spread),
            "derivative_spread_bps": _text(derivative_spread),
            "derivative_to_spot_spread_ratio": _text(spread_ratio),
            "spot_depth_quote_notional": _text(spot_depth),
            "derivative_depth_quote_notional": _text(derivative_depth),
            "derivative_to_spot_depth_ratio": _text(depth_ratio),
        },
        "derivative_structure": {
            "funding": _latest_derivative_family(structure_latest, "funding"),
            "open_interest": _latest_derivative_family(structure_latest, "open_interest"),
            "open_interest_change": _oi_change(structures),
            "mark_index": _latest_derivative_family(structure_latest, "mark_index"),
            "liquidations": _latest_derivative_family(structure_latest, "liquidations"),
        },
        "truth_boundaries": {
            "lagged_association_is_causality": False,
            "open_interest_cross_venue_comparable": False,
            "liquidation_size_cross_venue_comparable": False,
            "structural_graph_is_empirical_leadership_claim": False,
        },
    }

    family_statuses = [
        "QUALIFIED" if basis_bps is not None else "UNAVAILABLE",
        str(lagged.get("status", "UNAVAILABLE")).replace("QUALIFIED_ASSOCIATION", "QUALIFIED"),
        "QUALIFIED" if confirmation != "UNAVAILABLE" else "UNAVAILABLE",
    ]
    if any(frame.status == "DEGRADED" for frame in all_frames):
        status = "DEGRADED"
    elif all(frame.status == "QUALIFIED" for frame in spot + derivatives) and any(
        item == "QUALIFIED" for item in family_statuses
    ):
        status = "QUALIFIED"
    else:
        status = "DEGRADED" if any(frame.status in {"QUALIFIED", "DEGRADED"} for frame in all_frames) else "UNAVAILABLE"

    ordered_frames = tuple(sorted(all_frames, key=lambda frame: (frame.known_at_ns, frame.frame_id)))
    source_ids = tuple(frame.frame_id for frame in ordered_frames)
    source_hashes = tuple(frame.content_hash() for frame in ordered_frames)
    known_at_ns = max([graph.known_at_ns] + [frame.known_at_ns for frame in ordered_frames])
    identity_body = {
        "relationship_id": relationship_id,
        "cutoff_at_ns": cutoff_at_ns,
        "graph_hash": graph.content_hash(),
        "source_hashes": list(source_hashes),
        "builder_version": builder_version,
        "alignment_tolerance_ns": alignment_tolerance_ns,
        "minimum_lag_pairs": minimum_lag_pairs,
        "lag_margin": str(lag_margin),
        "liquidity_depth_band_bps": liquidity_depth_band_bps,
    }
    relationship_state_id = "RELSTATE-%s" % canonical_hash(identity_body)[:32]
    return EconomicRelationshipState(
        relationship_state_id=relationship_state_id,
        relationship_id=relationship_id,
        relationship_type=relationship.relationship_type.value,
        economic_root_id=spot_node.economic_root_id,
        cutoff_at_ns=cutoff_at_ns,
        known_at_ns=known_at_ns,
        status=status,
        graph_id=graph.graph_id,
        graph_version=graph.graph_version,
        graph_hash=graph.content_hash(),
        source_node_id=relationship.source_node_id,
        target_node_id=relationship.target_node_id,
        source_frame_ids=source_ids,
        source_frame_hashes=source_hashes,
        state=state,
        builder_version=builder_version,
    )
