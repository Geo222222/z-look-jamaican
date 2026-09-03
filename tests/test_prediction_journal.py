import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.observation import CanonicalObservation, default_instrument_registry
from autonomous_kernel.prediction import (
    PredictionContractError,
    PredictionJournal,
    PredictionJournalError,
    create_prediction,
    validate_prediction_journal,
)
from autonomous_kernel.representation import build_instrument_state
from autonomous_kernel.store import repository_root


VALID = {"status": "VALID", "action_permitted": True}
DEGRADED = {"status": "DEGRADED", "action_permitted": False, "reasons": ["test"]}


def source(observation_id="OBS-SNAP", *, event_type="BOOK_SNAPSHOT", known_ns=110, quality=VALID, payload=None):
    instrument = default_instrument_registry().resolve(
        "coinbase_advanced_trade_public_websocket", "BTC-USD"
    )
    return CanonicalObservation(
        observation_id=observation_id,
        instrument=instrument,
        event_type=event_type,
        provider="coinbase_advanced_trade_public_websocket",
        venue="COINBASE",
        provider_symbol="BTC-USD",
        channel="level2" if event_type.startswith("BOOK_") else "market_trades",
        source_event_at_ns=known_ns - 10,
        received_at_ns=known_ns,
        known_at_ns=known_ns,
        sequence="1",
        sequence_scope="PROVIDER_EVENT",
        stream_id="PRED-STREAM",
        payload=payload or {
            "updates": [
                {"side": "BID", "price": "99", "size": "2"},
                {"side": "ASK", "price": "101", "size": "2"},
            ]
        },
        quality=quality,
        raw_event_sha256="a" * 64,
        raw_ref="raw/%s" % observation_id,
    )


def frame():
    return build_instrument_state((source(),), cutoff_at_ns=110)


def prediction(*, mode="PROSPECTIVE_SHADOW", prediction_id=None, expected="8.5", created_at_ns=125):
    return create_prediction(
        frame(),
        mode=mode,
        prediction_at_ns=120,
        created_at_ns=created_at_ns,
        horizon_ns=100,
        expected_move_bps=expected,
        probability_positive="0.67",
        interval_low_bps="-4",
        interval_high_bps="18",
        model_refs=("MODEL-TEST@1",),
        prediction_id=prediction_id,
    )


class PredictionJournalTests(unittest.TestCase):
    def test_prediction_binds_exact_representation_and_target(self):
        current = frame()
        item = create_prediction(
            current,
            mode="PROSPECTIVE_SHADOW",
            prediction_at_ns=120,
            created_at_ns=125,
            horizon_ns=100,
            expected_move_bps="8.5",
            probability_positive="0.67",
            model_refs=("MODEL-TEST@1",),
        )
        self.assertEqual(current.frame_id, item.representation_frame_id)
        self.assertEqual(current.content_hash(), item.representation_content_hash)
        self.assertEqual("100", item.reference_price)
        self.assertEqual("CROSS_VENUE_BBO_MIDPOINT_V1", item.reference_price_source)
        self.assertEqual(220, item.resolves_at_ns)
        self.assertEqual("FORWARD_EVALUABLE", item.evidence_class)

    def test_prediction_cannot_precede_representation_knowledge(self):
        with self.assertRaisesRegex(ValueError, "cannot precede representation"):
            create_prediction(
                frame(),
                mode="PROSPECTIVE_SHADOW",
                prediction_at_ns=100,
                created_at_ns=125,
                horizon_ns=100,
                expected_move_bps="1",
                probability_positive="0.5",
                model_refs=("MODEL-TEST@1",),
            )

    def test_prospective_claim_cannot_be_created_after_resolution(self):
        with self.assertRaises(PredictionContractError):
            prediction(created_at_ns=221)

    def test_historical_replay_is_explicitly_research_only_even_if_created_late(self):
        item = prediction(mode="HISTORICAL_REPLAY", created_at_ns=1000)
        self.assertEqual("RESEARCH_ONLY", item.evidence_class)
        self.assertGreater(item.created_at_ns, item.resolves_at_ns)

    def test_degraded_representation_cannot_be_forward_evidence(self):
        degraded_trade = source(
            "OBS-BAD",
            event_type="TRADE",
            known_ns=115,
            quality=DEGRADED,
            payload={"price": "100", "size": "1", "side": "BUY", "trade_id": "BAD"},
        )
        degraded_frame = build_instrument_state((source(), degraded_trade), cutoff_at_ns=115)
        self.assertEqual("DEGRADED", degraded_frame.status)
        with self.assertRaises(PredictionContractError):
            create_prediction(
                degraded_frame,
                mode="PROSPECTIVE_SHADOW",
                prediction_at_ns=120,
                created_at_ns=125,
                horizon_ns=100,
                expected_move_bps="1",
                probability_positive="0.5",
                model_refs=("MODEL-TEST@1",),
            )
        replay = create_prediction(
            degraded_frame,
            mode="HISTORICAL_REPLAY",
            prediction_at_ns=120,
            created_at_ns=1000,
            horizon_ns=100,
            expected_move_bps="1",
            probability_positive="0.5",
            model_refs=("MODEL-TEST@1",),
        )
        self.assertEqual("RESEARCH_ONLY", replay.evidence_class)

    def test_prospective_prediction_must_be_journaled_before_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = PredictionJournal(Path(directory))
            item = prediction()
            with self.assertRaisesRegex(PredictionJournalError, "before its resolution"):
                journal.append(item, journaled_at_ns=item.resolves_at_ns)

    def test_append_is_idempotent_and_conflicting_prediction_id_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = PredictionJournal(root)
            first = prediction(prediction_id="PRED-FIXED")
            accepted = journal.append(first, journaled_at_ns=130)
            retried = journal.append(first, journaled_at_ns=140)
            self.assertEqual(accepted, retried)
            conflict = prediction(prediction_id="PRED-FIXED", expected="9.5")
            with self.assertRaisesRegex(PredictionJournalError, "different content"):
                journal.append(conflict, journaled_at_ns=140)
            self.assertEqual([], validate_prediction_journal(root))

    def test_journal_chain_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = PredictionJournal(root)
            journal.append(prediction(prediction_id="PRED-A"), journaled_at_ns=130)
            journal.append(prediction(prediction_id="PRED-B", expected="4"), journaled_at_ns=131)
            lines = journal.path.read_text(encoding="utf-8").splitlines()
            first = json.loads(lines[0])
            first["prediction"]["forecast"]["expected_move_bps"] = "999"
            lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
            journal.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            errors = validate_prediction_journal(root)
            self.assertTrue(any("entry hash mismatch" in error for error in errors))

    def test_repository_empty_prediction_journal_state_is_valid(self):
        self.assertEqual([], validate_prediction_journal(repository_root()))


if __name__ == "__main__":
    unittest.main()
