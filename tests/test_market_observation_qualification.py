import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.market_data import MarketDataStore, build_candle_observation
from autonomous_kernel.market_data_quality import classify_market_data
from autonomous_kernel.market_observation_qualification import (
    BLOCKED,
    LEGACY_UNJOINED,
    NOT_APPLICABLE,
    NOT_EARNED,
    QUALIFIED,
    bind_shadow_decision,
    qualification_snapshot,
    qualify_observation,
    sequence_integrity,
)
from autonomous_kernel.operations import canonical_hash


def candle(observation_id="OBS-CANDLE-1"):
    return build_candle_observation(
        observation_id=observation_id,
        provider="provider-a",
        instrument="BTC-USD",
        interval_seconds=300,
        candle_start_at=1000,
        received_at=1302,
        observed_at=1305,
        open_price="100",
        high_price="102",
        low_price="99",
        close_price="101",
        volume="4.5",
        max_event_age_seconds=30,
        max_transport_age_seconds=30,
    )


def decision():
    return {
        "id": "SHADOW-BTC-USD-1000",
        "product": "BTC-USD",
        "signal_candle_timestamp": 1000,
        "observed_at": 1305,
        "actionable_at": 1500,
        "target_position": 1,
        "status": "pending",
    }


def stream_observation(*, gaps=None, out_of_order=None):
    raw = {
        "provider": "provider-stream",
        "instrument": "BTC-USD",
        "channel": "microstructure_stream",
        "source_event_at": 1000,
        "received_at": 1000,
    }
    normalized = {
        "schema_version": 1,
        "type": "microstructure_stream_summary",
        "instrument": "BTC-USD",
        "raw_observation_id": "OBS-STREAM-1",
        "stream_id": "STREAM-1",
        "summary": {
            "schema_version": 1,
            "record_count": 10,
            "unique_message_count": 10,
            "duplicate_count": 0,
            "sequence_scope": "CONNECTION_GLOBAL",
            "gaps": [] if gaps is None else gaps,
            "out_of_order": [] if out_of_order is None else out_of_order,
            "level2_snapshot_count": 1,
            "level2_update_count": 8,
            "final_book_hash": "book-hash",
        },
        "truth_class": "OBSERVED_PUBLIC_MARKET_DATA",
    }
    quality = classify_market_data(
        provider=raw["provider"],
        source_event_at=1000,
        received_at=1000,
        observed_at=1000,
        max_event_age_seconds=30,
        max_transport_age_seconds=30,
    ).to_dict()
    content = {"raw": raw, "normalized": normalized, "quality": quality}
    return {
        "schema_version": 1,
        "observation_id": "OBS-STREAM-1",
        "observed_at": 1000,
        "raw": raw,
        "normalized": normalized,
        "quality": quality,
        "integrity": {"algorithm": "sha256", "content_hash": canonical_hash(content)},
    }


class MarketObservationQualificationTests(unittest.TestCase):
    def test_fresh_candle_is_qualified_and_sequence_is_not_applicable(self):
        item = candle()
        result = qualify_observation(item, consumed_at=1305)
        self.assertEqual(QUALIFIED, result["state"])
        self.assertEqual(NOT_APPLICABLE, result["sequence_integrity"]["state"])

    def test_freshness_is_re_evaluated_at_consumption_time(self):
        result = qualify_observation(candle(), consumed_at=1400)
        self.assertEqual(BLOCKED, result["state"])
        self.assertIn("observation_not_fresh_at_consumption", result["reasons"])

    def test_valid_connection_global_microstream_sequence_is_qualified(self):
        result = sequence_integrity(stream_observation())
        self.assertEqual(QUALIFIED, result["state"])
        self.assertTrue(result["required"])
        self.assertEqual(0, result["gap_count"])
        self.assertEqual(0, result["out_of_order_count"])

    def test_microstream_gap_blocks_qualification(self):
        item = stream_observation(gaps=[{"after": 4, "before": 6, "missing": 1}])
        self.assertEqual(BLOCKED, sequence_integrity(item)["state"])
        qualified = qualify_observation(item, consumed_at=1005)
        self.assertEqual(BLOCKED, qualified["state"])
        self.assertIn("sequence_integrity_not_qualified", qualified["reasons"])

    def test_shadow_decision_binds_exact_fresh_evidence(self):
        bound = bind_shadow_decision(decision(), [candle()])
        self.assertEqual("OBS-CANDLE-1", bound["market_evidence"][0]["observation_id"])
        self.assertEqual("VALID", bound["market_evidence"][0]["quality_state"])
        self.assertTrue(bound["market_evidence_bond"]["content_hash"])

    def test_wrong_signal_candle_cannot_be_bound(self):
        wrong = candle()
        wrong["normalized"]["start_at"] = 700
        content = {"raw": wrong["raw"], "normalized": wrong["normalized"], "quality": wrong["quality"]}
        wrong["integrity"]["content_hash"] = canonical_hash(content)
        with self.assertRaisesRegex(ValueError, "signal candle"):
            bind_shadow_decision(decision(), [wrong])

    def test_legacy_shadow_history_is_not_retroactively_upgraded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            MarketDataStore(root).persist(candle())
            snapshot = qualification_snapshot(root, {"decisions": [decision()]})
            self.assertEqual(1, snapshot["shadow_evidence"]["legacy_unjoined_count"])
            self.assertEqual(NOT_EARNED, snapshot["shadow_evidence"]["certification_state"])
            self.assertEqual(LEGACY_UNJOINED, snapshot["shadow_evidence"]["decisions"][0]["state"])

    def test_legacy_bound_join_is_audited_but_cannot_earn_successor_certification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = candle()
            MarketDataStore(root).persist(item)
            bound = bind_shadow_decision(decision(), [item])
            snapshot = qualification_snapshot(root, {"decisions": [bound]})
            self.assertEqual(NOT_EARNED, snapshot["shadow_evidence"]["certification_state"])
            self.assertEqual(1, snapshot["shadow_evidence"]["qualified_joined_count"])
            self.assertEqual(0, snapshot["shadow_evidence"]["successor_joined_count"])
            self.assertEqual(QUALIFIED, snapshot["shadow_evidence"]["decisions"][0]["state"])

            tampered = dict(bound)
            tampered["market_evidence"] = [dict(bound["market_evidence"][0])]
            tampered["market_evidence"][0]["content_hash"] = "tampered"
            tampered_snapshot = qualification_snapshot(root, {"decisions": [tampered]})
            self.assertEqual(NOT_EARNED, tampered_snapshot["shadow_evidence"]["certification_state"])
            self.assertEqual(BLOCKED, tampered_snapshot["shadow_evidence"]["decisions"][0]["state"])
            reasons = tampered_snapshot["shadow_evidence"]["decisions"][0]["reasons"]
            self.assertIn("market_evidence_bond_hash_mismatch", reasons)
            self.assertTrue(any(reason.startswith("bound_observation_hash_mismatch") for reason in reasons))


if __name__ == "__main__":
    unittest.main()
