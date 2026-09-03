import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.observation import CanonicalBatchStore, CanonicalObservation, default_instrument_registry
from autonomous_kernel.representation import (
    RepresentationContractError,
    RepresentationError,
    RepresentationFrame,
    RepresentationStore,
    build_instrument_state,
    validate_representation_store,
)
from autonomous_kernel.representation.materialize import materialize_instrument_state


VALID = {"status": "VALID", "action_permitted": True}
DEGRADED = {"status": "DEGRADED", "action_permitted": False, "reasons": ["test"]}


def observation(
    observation_id,
    *,
    venue="COINBASE",
    provider="coinbase_advanced_trade_public_websocket",
    symbol="BTC-USD",
    event_type="TRADE",
    source_ns=100,
    known_ns=110,
    sequence="1",
    payload=None,
    quality=VALID,
):
    instrument = default_instrument_registry().resolve(provider, symbol)
    return CanonicalObservation(
        observation_id=observation_id,
        instrument=instrument,
        event_type=event_type,
        provider=provider,
        venue=venue,
        provider_symbol=symbol,
        channel="level2" if event_type.startswith("BOOK_") else "trades",
        source_event_at_ns=source_ns,
        received_at_ns=known_ns,
        known_at_ns=known_ns,
        sequence=sequence,
        sequence_scope="PROVIDER_EVENT",
        stream_id="STREAM-%s" % venue,
        payload=payload or {"price": "100", "size": "1", "side": "BUY", "trade_id": observation_id},
        quality=quality,
        raw_event_sha256=("a" if venue == "COINBASE" else "b") * 64,
        raw_ref="raw/%s" % observation_id,
    )


def snapshot(observation_id="OBS-SNAP", *, venue="COINBASE", provider="coinbase_advanced_trade_public_websocket", symbol="BTC-USD", bid="99", ask="101", known_ns=110, sequence="1", quality=VALID):
    return observation(
        observation_id,
        venue=venue,
        provider=provider,
        symbol=symbol,
        event_type="BOOK_SNAPSHOT",
        known_ns=known_ns,
        source_ns=known_ns - 10,
        sequence=sequence,
        quality=quality,
        payload={
            "updates": [
                {"side": "BID", "price": bid, "size": "2"},
                {"side": "ASK", "price": ask, "size": "1"},
            ]
        },
    )


class RepresentationTests(unittest.TestCase):
    def test_replay_builds_deterministic_book_depth_and_reported_flow(self):
        sources = (
            snapshot(),
            observation(
                "OBS-DELTA",
                event_type="BOOK_DELTA",
                source_ns=120,
                known_ns=130,
                sequence="2",
                payload={"updates": [{"side": "BID", "price": "100", "size": "3"}]},
            ),
            observation("OBS-BUY", source_ns=140, known_ns=150, sequence="3", payload={"price": "100.5", "size": "2", "side": "BUY", "trade_id": "T1"}),
            observation("OBS-SELL", source_ns=160, known_ns=170, sequence="4", payload={"price": "100.25", "size": "1", "side": "SELL", "trade_id": "T2"}),
        )
        first = build_instrument_state(sources, cutoff_at_ns=170, depth_bands_bps=(100,))
        second = build_instrument_state(tuple(reversed(sources)), cutoff_at_ns=170, depth_bands_bps=(100,))
        self.assertEqual(first.to_wire(), second.to_wire())
        self.assertEqual("QUALIFIED", first.status)
        venue = first.state["venue_states"]["COINBASE"]
        self.assertEqual("100", venue["book"]["best_bid"])
        self.assertEqual("101", venue["book"]["best_ask"])
        self.assertEqual("50", venue["book"]["spread_bps"])
        self.assertEqual("201.00", venue["trade_flow"]["reported_buy_quote_notional"])
        self.assertEqual("100.25", venue["trade_flow"]["reported_sell_quote_notional"])
        self.assertEqual("100.75", venue["trade_flow"]["net_reported_quote_notional"])
        self.assertEqual(170, first.known_at_ns)
        self.assertEqual(tuple(item.observation_id for item in sorted(sources, key=lambda item: (item.known_at_ns, item.source_event_at_ns, item.provider, item.venue, item.stream_id or "", item.sequence or "", item.observation_id))), first.source_observation_ids)

    def test_future_known_source_is_hard_rejected(self):
        with self.assertRaisesRegex(RepresentationError, "lookahead rejected"):
            build_instrument_state((snapshot(), observation("OBS-FUTURE", known_ns=500)), cutoff_at_ns=200)

    def test_delta_cannot_invent_a_book_without_snapshot(self):
        delta = observation(
            "OBS-DELTA-ONLY",
            event_type="BOOK_DELTA",
            payload={"updates": [{"side": "BID", "price": "100", "size": "2"}]},
        )
        frame = build_instrument_state((delta,), cutoff_at_ns=110)
        self.assertEqual("UNAVAILABLE", frame.status)
        self.assertEqual("UNAVAILABLE_NO_SNAPSHOT", frame.state["venue_states"]["COINBASE"]["book"]["status"])
        self.assertIn("DELTA_BEFORE_SNAPSHOT", frame.state["venue_states"]["COINBASE"]["book"]["issues"])

    def test_venues_remain_separate_while_cross_venue_state_is_derived(self):
        coinbase = snapshot("OBS-CB", bid="99", ask="101", known_ns=110)
        kraken = snapshot(
            "OBS-KR",
            venue="KRAKEN",
            provider="kraken_websocket_v2",
            symbol="BTC/USD",
            bid="99.5",
            ask="100.5",
            known_ns=120,
        )
        frame = build_instrument_state((coinbase, kraken), cutoff_at_ns=120)
        self.assertEqual("QUALIFIED", frame.status)
        self.assertEqual({"COINBASE", "KRAKEN"}, set(frame.state["venue_states"]))
        self.assertEqual("99.5", frame.state["aggregate"]["cross_venue_best_bid"])
        self.assertEqual("100.5", frame.state["aggregate"]["cross_venue_best_ask"])
        self.assertEqual("NORMAL", frame.state["aggregate"]["cross_venue_book_state"])
        self.assertEqual(2, frame.state["aggregate"]["qualified_book_venue_count"])

    def test_non_valid_source_degrades_but_does_not_contaminate_state(self):
        good = snapshot()
        bad_trade = observation("OBS-BAD", known_ns=120, quality=DEGRADED, payload={"price": "100", "size": "99", "side": "BUY", "trade_id": "BAD"})
        frame = build_instrument_state((good, bad_trade), cutoff_at_ns=120)
        self.assertEqual("DEGRADED", frame.status)
        self.assertEqual(0, frame.state["aggregate"]["trade_flow"]["trade_count"])
        self.assertIn("NON_VALID_SOURCE_OBSERVATION_PRESENT", frame.state["input_quality"]["degraded_reasons"])

    def test_representation_wire_is_tamper_evident(self):
        frame = build_instrument_state((snapshot(),), cutoff_at_ns=110)
        wire = frame.to_wire()
        self.assertEqual(frame, RepresentationFrame.from_wire(wire))
        changed = json.loads(json.dumps(wire))
        changed["state"]["aggregate"]["cross_venue_best_bid"] = "999"
        with self.assertRaises(RepresentationContractError):
            RepresentationFrame.from_wire(changed)

    def test_materialization_persists_source_batch_lineage_and_replays_idempotently(self):
        sources = (
            snapshot(),
            observation("OBS-T1", source_ns=120, known_ns=130, sequence="2", payload={"price": "100", "size": "1", "side": "BUY", "trade_id": "T1"}),
            observation("OBS-FUTURE", event_type="BOOK_DELTA", source_ns=300, known_ns=310, sequence="3", payload={"updates": [{"side": "ASK", "price": "102", "size": "1"}]}),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = CanonicalBatchStore(root).persist_batch(
                batch_id="CAN-Z2-INPUT",
                observations=sources,
                source_ref="raw/source.jsonl.gz",
                source_sha256="c" * 64,
            )
            first = materialize_instrument_state(
                root,
                batch_ids=("CAN-Z2-INPUT",),
                instrument_id="CRYPTO.SPOT.BTC-USD",
                cutoff_at_ns=200,
            )
            second = materialize_instrument_state(
                root,
                batch_ids=("CAN-Z2-INPUT",),
                instrument_id="CRYPTO.SPOT.BTC-USD",
                cutoff_at_ns=200,
            )
            self.assertEqual(first, second)
            frame = RepresentationFrame.from_wire(first["frame"])
            self.assertEqual(("OBS-SNAP", "OBS-T1"), frame.source_observation_ids)
            self.assertEqual(130, frame.known_at_ns)
            self.assertEqual("CAN-Z2-INPUT", first["source_batches"][0]["batch_id"])
            self.assertEqual(batch["integrity"]["content_hash"], first["source_batches"][0]["manifest_content_hash"])
            self.assertEqual([], validate_representation_store(root))
            path = root / "artifacts/market_data/representations" / (frame.frame_id + ".json")
            changed = json.loads(path.read_text(encoding="utf-8"))
            changed["frame"]["status"] = "UNAVAILABLE"
            path.write_text(json.dumps(changed), encoding="utf-8")
            self.assertTrue(validate_representation_store(root))


if __name__ == "__main__":
    unittest.main()
