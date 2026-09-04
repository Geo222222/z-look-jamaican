from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.context.contracts import MarketContextFrame
from autonomous_kernel.observation.instruments import CanonicalInstrument
from autonomous_kernel.perception_graph import (
    GraphRef,
    PerceptionGraphError,
    build_graph_node,
    context_to_graph_node,
    persist_graph_nodes,
    representation_to_graph_node,
    validate_graph_node,
    validate_perception_graph_store,
)
from autonomous_kernel.representation.contracts import RepresentationFrame


SECOND = 1_000_000_000


def _instrument():
    return CanonicalInstrument(
        canonical_id="CRYPTO.SPOT.BTC-USD",
        asset_class="CRYPTO",
        market_type="SPOT",
        base_asset="BTC",
        quote_asset="USD",
        settlement_asset="USD",
        expiry=None,
    )


def _frame(cutoff=10 * SECOND):
    return RepresentationFrame(
        frame_id="REP-BTC-001",
        representation_type="INSTRUMENT_STATE",
        instrument=_instrument(),
        window_start_ns=cutoff - SECOND,
        cutoff_at_ns=cutoff,
        known_at_ns=cutoff - 1,
        latest_source_event_at_ns=cutoff - 2,
        status="QUALIFIED",
        builder_version="instrument-state-v1",
        parameters={},
        state={
            "venue_states": {},
            "aggregate": {"qualified_book_venue_count": 1, "venue_count": 1},
        },
        source_observation_ids=("OBS-001",),
        source_content_hashes=("a" * 64,),
        source_providers=("coinbase",),
        source_venues=("coinbase",),
    )


def _context(frame, cutoff=10 * SECOND):
    return MarketContextFrame(
        context_id="CTX-BTC-001",
        context_type="MARKET_CONTEXT",
        cutoff_at_ns=cutoff,
        known_at_ns=cutoff - 1,
        status="QUALIFIED",
        builder_version="market-context-v1",
        parameters={},
        state={
            "members": {
                frame.instrument.canonical_id: {
                    "frame_id": frame.frame_id,
                    "frame_content_hash": frame.content_hash(),
                }
            },
            "market": {"breadth_positive": "1"},
            "regimes": {"direction": "RISK_ON"},
        },
        source_frame_ids=(frame.frame_id,),
        source_frame_hashes=(frame.content_hash(),),
        source_instrument_ids=(frame.instrument.canonical_id,),
    )


class PerceptionGraphTests(unittest.TestCase):
    def test_z2_and_z9_adapters_preserve_exact_artifact_identity(self):
        frame = _frame()
        rep = representation_to_graph_node(frame)
        context = _context(frame)
        ctx = context_to_graph_node(context, {rep["node_id"]: rep})
        self.assertEqual(rep["payload"]["representation_content_hash"], frame.content_hash())
        self.assertEqual(ctx["payload"]["market_context_content_hash"], context.content_hash())
        self.assertEqual(ctx["input_refs"][0]["node_id"], rep["node_id"])
        self.assertFalse(ctx["authority"]["economic_decision"])
        self.assertFalse(ctx["authority"]["external_execution"])

    def test_graph_forbids_strategy_and_opportunity_authority(self):
        with self.assertRaisesRegex(PerceptionGraphError, "outside ZLJ perception authority"):
            build_graph_node(
                node_id="STRATEGY-1",
                node_type="STRATEGY_APPLICABILITY",
                truth_class="APPLICABILITY_ASSESSMENT",
                subject_id="CRYPTO.SPOT.BTC-USD",
                cutoff_at_ns=10,
                known_at_ns=10,
                source_refs=("source:a",),
                input_refs=(),
                method={"name": "old", "version": "1"},
                quality={"status": "VALID"},
                payload={},
            )

    def test_graph_forbids_same_layer_and_upward_dependencies(self):
        base = build_graph_node(
            node_id="DERIVED-A",
            node_type="DETERMINISTIC_DERIVATION",
            truth_class="DETERMINISTIC_CALCULATION",
            subject_id="CRYPTO.SPOT.BTC-USD",
            cutoff_at_ns=10,
            known_at_ns=9,
            source_refs=("source:a",),
            input_refs=(),
            method={"name": "calc", "version": "1"},
            quality={"status": "VALID"},
            payload={},
        )
        peer = build_graph_node(
            node_id="DERIVED-B",
            node_type="DETERMINISTIC_DERIVATION",
            truth_class="DETERMINISTIC_CALCULATION",
            subject_id="CRYPTO.SPOT.BTC-USD",
            cutoff_at_ns=10,
            known_at_ns=9,
            source_refs=("source:b",),
            input_refs=(GraphRef(node_id=base["node_id"], relationship="PEER"),),
            method={"name": "calc", "version": "1"},
            quality={"status": "VALID"},
            payload={},
        )
        with self.assertRaisesRegex(PerceptionGraphError, "same-layer or upward"):
            validate_graph_node(peer, resolver={base["node_id"]: base}.get)

    def test_graph_forbids_point_in_time_lookahead(self):
        parent = build_graph_node(
            node_id="REP-FUTURE",
            node_type="REPRESENTATION",
            truth_class="NORMALIZED_MEASUREMENT",
            subject_id="CRYPTO.SPOT.BTC-USD",
            cutoff_at_ns=20,
            known_at_ns=19,
            source_refs=("observation:future",),
            input_refs=(),
            method={"name": "z2", "version": "1"},
            quality={"status": "QUALIFIED"},
            payload={},
        )
        child = build_graph_node(
            node_id="CTX-PAST",
            node_type="MARKET_CONTEXT",
            truth_class="DERIVED_CONTEXT",
            subject_id="MARKET.WIDE",
            cutoff_at_ns=10,
            known_at_ns=10,
            source_refs=("representation:future",),
            input_refs=(GraphRef(node_id=parent["node_id"], relationship="MEMBER"),),
            method={"name": "z9", "version": "1"},
            quality={"status": "QUALIFIED"},
            payload={},
        )
        with self.assertRaisesRegex(PerceptionGraphError, "point-in-time cutoff"):
            validate_graph_node(child, resolver={parent["node_id"]: parent}.get)

    def test_store_is_immutable_rebuildable_and_tamper_evident(self):
        frame = _frame()
        rep = representation_to_graph_node(frame)
        ctx = context_to_graph_node(_context(frame), {rep["node_id"]: rep})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_graph_nodes(root, (rep, ctx))
            self.assertEqual([], validate_perception_graph_store(root, require_state=False))
            persist_graph_nodes(root, (rep, ctx))
            path = root / "artifacts/market_data/perception_graph/context" / (ctx["node_id"] + ".json")
            import json
            value = json.loads(path.read_text(encoding="utf-8"))
            value["payload"]["state"]["regimes"]["direction"] = "RISK_OFF"
            path.write_text(json.dumps(value), encoding="utf-8")
            errors = validate_perception_graph_store(root, require_state=False)
            self.assertTrue(any("integrity mismatch" in error for error in errors))

    def test_authority_tampering_is_rejected(self):
        node = representation_to_graph_node(_frame())
        tampered = copy.deepcopy(node)
        tampered["authority"]["economic_decision"] = True
        with self.assertRaisesRegex(PerceptionGraphError, "authority boundary"):
            validate_graph_node(tampered)


if __name__ == "__main__":
    unittest.main()
