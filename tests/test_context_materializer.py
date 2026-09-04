from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from autonomous_kernel.context import ContextMaterializationError, materialize_market_context
from autonomous_kernel.observation import default_instrument_registry
from autonomous_kernel.representation import RepresentationFrame, RepresentationStore


PROVIDER = "coinbase_advanced_trade_public_websocket"
BTC = default_instrument_registry().resolve(PROVIDER, "BTC-USD")
ETH = default_instrument_registry().resolve(PROVIDER, "ETH-USD")


def frame(instrument, index: int, midpoint: object) -> RepresentationFrame:
    known = index * 1_000
    mid = Decimal(str(midpoint))
    digest = hashlib.sha256((instrument.canonical_id + str(index)).encode()).hexdigest()
    return RepresentationFrame(
        frame_id="MAT-%s-%d" % (hashlib.sha256(instrument.canonical_id.encode()).hexdigest()[:8], index),
        representation_type="INSTRUMENT_STATE",
        instrument=instrument,
        window_start_ns=max(0, known - 100),
        cutoff_at_ns=known,
        known_at_ns=known,
        latest_source_event_at_ns=max(0, known - 1),
        status="QUALIFIED",
        builder_version="materializer-test-v1",
        parameters={"depth_bands_bps": [10]},
        state={
            "venue_states": {
                "TEST": {
                    "book": {
                        "status": "QUALIFIED",
                        "spread_bps": "1",
                        "depth_bands_bps": {
                            "10": {"bid_quote_notional": "500", "ask_quote_notional": "500"}
                        },
                    },
                    "trade_flow": {},
                }
            },
            "aggregate": {
                "cross_venue_book_state": "NORMAL",
                "cross_venue_best_bid": format(mid - Decimal("0.01"), "f"),
                "cross_venue_best_ask": format(mid + Decimal("0.01"), "f"),
                "cross_venue_spread_bps": "1",
                "mean_venue_midpoint": format(mid, "f"),
                "trade_flow": {"reported_buy_quote_notional": "60", "reported_sell_quote_notional": "40"},
            },
            "input_quality": {"status_counts": {"VALID": 1}, "degraded_reasons": []},
        },
        source_observation_ids=("OBS-%s-%d" % (instrument.canonical_id.replace(".", "-"), index),),
        source_content_hashes=(digest,),
        source_providers=(PROVIDER,),
        source_venues=("TEST",),
    )


def persist(store: RepresentationStore, source: RepresentationFrame) -> None:
    store.persist(
        source,
        source_batches=(
            {
                "batch_id": "BATCH-%s" % source.frame_id,
                "manifest_ref": "TEST",
                "manifest_content_hash": "a" * 64,
            },
        ),
    )


class ContextMaterializerTests(unittest.TestCase):
    def test_materializer_selects_all_and_only_causal_durable_history_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RepresentationStore(root)
            btc = [frame(BTC, index, value) for index, value in enumerate(("100", "101", "102", "103", "104", "105"), 1)]
            eth = [frame(ETH, index, value) for index, value in enumerate(("200", "202", "204", "206", "208"), 1)]
            for source in tuple(reversed(btc + eth)):
                persist(store, source)

            first = materialize_market_context(root, cutoff_at_ns=5_000)
            second = materialize_market_context(root, cutoff_at_ns=5_000)

            self.assertEqual(first.context.to_wire(), second.context.to_wire())
            self.assertEqual(10, len(first.selected_frame_ids))
            self.assertNotIn(btc[-1].frame_id, first.selected_frame_ids)
            self.assertEqual((BTC.canonical_id, ETH.canonical_id), first.selected_instrument_ids)
            self.assertEqual(tuple(first.selected_frame_ids), first.context.source_frame_ids)
            context_files = list((root / "artifacts/market_data/contexts").glob("*.json"))
            self.assertEqual(1, len(context_files))

    def test_materializer_fails_closed_when_durable_z2_store_is_tampered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RepresentationStore(root)
            sources = [frame(BTC, index, value) for index, value in enumerate(("100", "101", "102"), 1)]
            for source in sources:
                persist(store, source)
            path = root / "artifacts/market_data/representations" / (sources[0].frame_id + ".json")
            document = json.loads(path.read_text(encoding="utf-8"))
            document["frame"]["status"] = "DEGRADED"
            path.write_text(json.dumps(document) + "\n", encoding="utf-8")
            with self.assertRaises(ContextMaterializationError):
                materialize_market_context(root, cutoff_at_ns=3_000)

    def test_materializer_rejects_cutoff_before_any_durable_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RepresentationStore(root)
            persist(store, frame(BTC, 1, "100"))
            with self.assertRaises(ContextMaterializationError):
                materialize_market_context(root, cutoff_at_ns=999)


if __name__ == "__main__":
    unittest.main()
