from __future__ import annotations

from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from autonomous_kernel.book_bridge import ZLJBookSigner
from autonomous_kernel.book_outbox import BookOutbox
from autonomous_kernel.experience import (
    EconomicInstrumentGraph,
    EconomicInstrumentNode,
    EconomicRelationship,
    EconomicRelationshipType,
    InstrumentRole,
    material_graph_evidence,
)
from autonomous_kernel.experience.economic_graph import EconomicGraphError
from autonomous_kernel.observation.instruments import CanonicalInstrument


def _spot() -> EconomicInstrumentNode:
    return EconomicInstrumentNode(
        node_id="BTC-USD-SPOT",
        instrument=CanonicalInstrument(
            canonical_id="CRYPTO.SPOT.BTC-USD",
            asset_class="CRYPTO",
            market_type="SPOT",
            base_asset="BTC",
            quote_asset="USD",
            settlement_asset="USD",
        ),
        role=InstrumentRole.SPOT,
        economic_root_id="ASSET.BTC",
        quote_family_id="QUOTE.USD",
    )


def _perp() -> EconomicInstrumentNode:
    return EconomicInstrumentNode(
        node_id="BTC-USD-PERP",
        instrument=CanonicalInstrument(
            canonical_id="CRYPTO.PERP.BTC-USD",
            asset_class="CRYPTO",
            market_type="PERPETUAL",
            base_asset="BTC",
            quote_asset="USD",
            settlement_asset="USD",
        ),
        role=InstrumentRole.PERPETUAL,
        economic_root_id="ASSET.BTC",
        quote_family_id="QUOTE.USD",
        contract_spec_ref="spec://btc-usd-perp-v1",
    )


def _future() -> EconomicInstrumentNode:
    return EconomicInstrumentNode(
        node_id="BTC-USD-202612",
        instrument=CanonicalInstrument(
            canonical_id="CRYPTO.FUTURE.BTC-USD.202612",
            asset_class="CRYPTO",
            market_type="FUTURE",
            base_asset="BTC",
            quote_asset="USD",
            settlement_asset="USD",
            expiry="2026-12-18",
        ),
        role=InstrumentRole.DATED_FUTURE,
        economic_root_id="ASSET.BTC",
        quote_family_id="QUOTE.USD",
        contract_spec_ref="spec://btc-usd-202612-v1",
    )


def _relations() -> tuple[EconomicRelationship, ...]:
    return (
        EconomicRelationship(
            relationship_id="REL-SPOT-PERP",
            relationship_type=EconomicRelationshipType.SPOT_DERIVATIVE,
            source_node_id="BTC-USD-SPOT",
            target_node_id="BTC-USD-PERP",
            rationale="same BTC economic root expressed through spot and perpetual markets",
        ),
        EconomicRelationship(
            relationship_id="REL-SPOT-FUTURE",
            relationship_type=EconomicRelationshipType.SPOT_DERIVATIVE,
            source_node_id="BTC-USD-SPOT",
            target_node_id="BTC-USD-202612",
            rationale="same BTC economic root expressed through spot and dated future markets",
        ),
        EconomicRelationship(
            relationship_id="REL-PERP-FUTURE",
            relationship_type=EconomicRelationshipType.TERM_STRUCTURE,
            source_node_id="BTC-USD-PERP",
            target_node_id="BTC-USD-202612",
            rationale="BTC derivative curve relationship",
        ),
    )


def _graph(*, reverse: bool = False) -> EconomicInstrumentGraph:
    nodes = (_spot(), _perp(), _future())
    relationships = _relations()
    if reverse:
        nodes = tuple(reversed(nodes))
        relationships = tuple(reversed(relationships))
    return EconomicInstrumentGraph(
        graph_id="CRYPTO-MARKET-GRAPH",
        graph_version="1.0.0",
        effective_at_ns=1_788_400_000_000_000_000,
        known_at_ns=1_788_400_000_100_000_000,
        nodes=nodes,
        relationships=relationships,
    )


def test_graph_identity_is_order_independent_and_round_trips() -> None:
    first = _graph()
    second = _graph(reverse=True)
    assert first.content_hash() == second.content_hash()
    restored = EconomicInstrumentGraph.from_wire(first.to_wire())
    assert restored.content_hash() == first.content_hash()
    assert [node.node_id for node in restored.nodes_for_root("ASSET.BTC")] == [
        "BTC-USD-202612",
        "BTC-USD-PERP",
        "BTC-USD-SPOT",
    ]


def test_spot_derivative_relationship_requires_same_underlying() -> None:
    bad_perp = EconomicInstrumentNode(
        node_id="ETH-USD-PERP",
        instrument=CanonicalInstrument(
            canonical_id="CRYPTO.PERP.ETH-USD",
            asset_class="CRYPTO",
            market_type="PERPETUAL",
            base_asset="ETH",
            quote_asset="USD",
            settlement_asset="USD",
        ),
        role=InstrumentRole.PERPETUAL,
        economic_root_id="ASSET.ETH",
        quote_family_id="QUOTE.USD",
    )
    with pytest.raises(EconomicGraphError, match="same economic_root_id"):
        EconomicInstrumentGraph(
            graph_id="BAD",
            graph_version="1",
            effective_at_ns=1,
            known_at_ns=1,
            nodes=(_spot(), bad_perp),
            relationships=(
                EconomicRelationship(
                    relationship_id="BAD-REL",
                    relationship_type=EconomicRelationshipType.SPOT_DERIVATIVE,
                    source_node_id="BTC-USD-SPOT",
                    target_node_id="ETH-USD-PERP",
                    rationale="invalid cross-underlying derivative relation",
                ),
            ),
        )


def test_graph_tamper_is_detected() -> None:
    wire = _graph().to_wire()
    wire["graph_version"] = "9.9.9"
    with pytest.raises(EconomicGraphError, match="content hash mismatch"):
        EconomicInstrumentGraph.from_wire(wire)


def test_related_nodes_can_filter_relationship_family() -> None:
    graph = _graph()
    related = graph.related_nodes(
        "BTC-USD-PERP",
        relationship_types=(EconomicRelationshipType.TERM_STRUCTURE,),
    )
    assert [node.node_id for node in related] == ["BTC-USD-202612"]


def test_material_graph_version_is_book_bound_without_copying_raw_market_history(tmp_path) -> None:
    graph = _graph()
    intent = material_graph_evidence(graph, payload_ref="zlj://economic-graphs/CRYPTO-MARKET-GRAPH/1.0.0")
    signer = ZLJBookSigner(key_id="zlj-test-1", private_key=Ed25519PrivateKey.generate())
    produced_at = datetime.fromtimestamp(graph.known_at_ns / 1_000_000_000 + 1, tz=timezone.utc)
    envelope = intent.sign(
        signer=signer,
        receipt_id="ZLJ-GRAPH-1",
        produced_at=produced_at,
        visibility_scope=("INSTITUTION", "BENJAMIN"),
    )

    assert envelope["producer"] == "ZLJ"
    assert envelope["event_type"] == "ZLJ.ECONOMIC_INSTRUMENT_GRAPH"
    assert envelope["subject_id"] == "CRYPTO-MARKET-GRAPH@1.0.0"
    assert envelope["payload_digest"] == intent.payload_digest
    assert envelope["known_at"] <= envelope["produced_at"]

    record = BookOutbox(tmp_path).enqueue(envelope=envelope, payload=intent.payload)
    assert record["state"] == "PENDING"
    assert "raw market" not in intent.payload.decode("utf-8").lower()
