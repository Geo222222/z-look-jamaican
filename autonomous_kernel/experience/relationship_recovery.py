from __future__ import annotations

from typing import Any, Mapping, Sequence

from .relationships import EconomicRelationshipState, RelationshipStateError


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RelationshipStateError("%s is malformed" % field)
    return value


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RelationshipStateError("%s must be an array" % field)
    return value


def _validate_semantic_air_gap(item: EconomicRelationshipState) -> None:
    state = item.state
    basis = _mapping(state.get("basis"), "relationship basis")
    unit_air_gap = _mapping(state.get("unit_air_gap"), "relationship unit_air_gap")
    truth = _mapping(state.get("truth_boundaries"), "relationship truth_boundaries")

    spot_quote = str(basis.get("spot_quote_unit", ""))
    derivative_quote = str(basis.get("derivative_quote_unit", ""))
    price_comparable = unit_air_gap.get("price_basis_directly_comparable")
    basis_status = str(basis.get("status", ""))
    basis_value = basis.get("basis_bps")

    if spot_quote and derivative_quote and spot_quote == derivative_quote:
        if price_comparable is not True:
            raise RelationshipStateError("matching quote units must preserve direct price-basis comparability")
    else:
        if price_comparable is not False:
            raise RelationshipStateError("mismatched quote units cannot claim direct price-basis comparability")
        if basis_status == "QUALIFIED" or basis_value is not None:
            raise RelationshipStateError("quote-unit mismatch cannot carry qualified direct basis")

    if basis_status == "QUALIFIED" and basis_value is None:
        raise RelationshipStateError("qualified basis requires basis_bps")
    if basis_status != "QUALIFIED" and basis_value is not None:
        raise RelationshipStateError("unqualified basis cannot carry basis_bps")

    required_false = (
        "spot_derivative_amounts_directly_comparable",
        "cross_venue_open_interest_directly_comparable",
        "cross_venue_liquidation_size_directly_comparable",
    )
    for key in required_false:
        if unit_air_gap.get(key) is not False:
            raise RelationshipStateError("relationship unit air-gap cannot be weakened: %s" % key)
    if unit_air_gap.get("rule") != "NUMERIC_EQUALITY_NEVER_IMPLIES_ECONOMIC_UNIT_COMPATIBILITY":
        raise RelationshipStateError("relationship unit air-gap rule is invalid")

    truth_false = (
        "lagged_association_is_causality",
        "open_interest_cross_venue_comparable",
        "liquidation_size_cross_venue_comparable",
        "structural_graph_is_empirical_leadership_claim",
    )
    for key in truth_false:
        if truth.get(key) is not False:
            raise RelationshipStateError("relationship truth boundary cannot be weakened: %s" % key)


def recover_economic_relationship_state(value: Mapping[str, Any]) -> EconomicRelationshipState:
    """Strictly reconstruct and verify one stored EconomicRelationshipState.

    Recovery verifies content-addressed integrity, authority boundaries, exact
    source-frame lineage, and the unit/comparability air-gap. This is the
    canonical recovery path until the relationship-state schema itself is next
    versioned; callers must not trust raw JSON as an executable fact.
    """
    graph = _mapping(value.get("economic_graph"), "relationship economic_graph")
    source_frames = _sequence(value.get("source_frames"), "relationship source_frames")
    state = _mapping(value.get("state"), "relationship state")
    parsed_frames = []
    for raw in source_frames:
        frame = _mapping(raw, "relationship source frame")
        frame_id = str(frame.get("frame_id", ""))
        content_hash = str(frame.get("content_hash", ""))
        if not frame_id or len(content_hash) != 64:
            raise RelationshipStateError("relationship source-frame identity/hash is invalid")
        try:
            int(content_hash, 16)
        except ValueError as exc:
            raise RelationshipStateError("relationship source-frame hash must be hexadecimal") from exc
        parsed_frames.append((frame_id, content_hash))

    item = EconomicRelationshipState(
        schema_version=str(value.get("schema_version", "")),
        relationship_state_id=str(value.get("relationship_state_id", "")),
        relationship_id=str(value.get("relationship_id", "")),
        relationship_type=str(value.get("relationship_type", "")),
        economic_root_id=str(value.get("economic_root_id", "")),
        cutoff_at_ns=int(value.get("cutoff_at_ns", -1)),
        known_at_ns=int(value.get("known_at_ns", -1)),
        status=str(value.get("status", "")),
        graph_id=str(graph.get("graph_id", "")),
        graph_version=str(graph.get("graph_version", "")),
        graph_hash=str(graph.get("content_hash", "")),
        source_node_id=str(value.get("source_node_id", "")),
        target_node_id=str(value.get("target_node_id", "")),
        source_frame_ids=tuple(frame_id for frame_id, _ in parsed_frames),
        source_frame_hashes=tuple(content_hash for _, content_hash in parsed_frames),
        state=dict(state),
        builder_version=str(value.get("builder_version", "")),
    )

    if len(item.graph_hash) != 64:
        raise RelationshipStateError("relationship graph hash must be SHA-256 hex")
    try:
        int(item.graph_hash, 16)
    except ValueError as exc:
        raise RelationshipStateError("relationship graph hash must be hexadecimal") from exc

    authority = _mapping(value.get("authority"), "relationship authority")
    for key in ("capital_decision", "risk_authorization", "external_execution"):
        if authority.get(key) is not False:
            raise RelationshipStateError("relationship authority boundary is invalid")

    integrity = _mapping(value.get("integrity"), "relationship integrity")
    if integrity.get("algorithm") != "sha256" or integrity.get("content_hash") != item.content_hash():
        raise RelationshipStateError("relationship-state content hash mismatch")

    _validate_semantic_air_gap(item)
    return item
