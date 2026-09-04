import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from autonomous_kernel.evaluation import (
    MAX_RESOLUTION_LAG_NS_V1,
    OutcomeJournal,
    OutcomePendingError,
    OutcomeResolutionError,
    resolve_prediction,
    select_resolution_frame,
    validate_outcome_journal,
)
from autonomous_kernel.observation import default_instrument_registry
from autonomous_kernel.prediction import PredictionJournal, create_prediction
from autonomous_kernel.representation import RepresentationFrame


PROVIDER = "coinbase_advanced_trade_public_websocket"
INSTRUMENT = default_instrument_registry().resolve(PROVIDER, "BTC-USD")


def frame(frame_id, *, known_at_ns, midpoint="100", status="QUALIFIED", source_hash_char="a"):
    mid = Decimal(str(midpoint))
    bid = mid - Decimal("1")
    ask = mid + Decimal("1")
    return RepresentationFrame(
        frame_id=frame_id,
        representation_type="INSTRUMENT_STATE",
        instrument=INSTRUMENT,
        window_start_ns=max(0, int(known_at_ns) - 10),
        cutoff_at_ns=int(known_at_ns),
        known_at_ns=int(known_at_ns),
        latest_source_event_at_ns=max(0, int(known_at_ns) - 1),
        status=status,
        builder_version="test-v1",
        parameters={"test": True},
        state={
            "venue_states": {},
            "aggregate": {
                "cross_venue_book_state": "NORMAL",
                "cross_venue_best_bid": format(bid, "f"),
                "cross_venue_best_ask": format(ask, "f"),
                "mean_venue_midpoint": format(mid, "f"),
            },
            "input_quality": {"status_counts": {"VALID": 1}, "degraded_reasons": []},
        },
        source_observation_ids=("OBS-%s" % frame_id,),
        source_content_hashes=(source_hash_char * 64,),
        source_providers=(PROVIDER,),
        source_venues=("COINBASE",),
    )


def journal_prediction(root, *, expected_move_bps="20"):
    source = frame("REP-PRED", known_at_ns=100, midpoint="100")
    prediction = create_prediction(
        source,
        mode="PROSPECTIVE_SHADOW",
        prediction_at_ns=100,
        created_at_ns=100,
        horizon_ns=100,
        expected_move_bps=expected_move_bps,
        probability_positive="0.6",
        interval_low_bps="-20",
        interval_high_bps="60",
        model_refs=("TEST-MODEL@1.0.0",),
        prediction_id="PRED-TEST",
    )
    PredictionJournal(root).append(prediction, journaled_at_ns=101)
    return prediction


class OutcomeResolverTests(unittest.TestCase):
    def test_first_qualified_frame_after_target_wins_not_best_later_price(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = journal_prediction(root)
            frames = (
                frame("REP-BEFORE", known_at_ns=199, midpoint="90", source_hash_char="b"),
                frame("REP-DEGRADED", known_at_ns=200, midpoint="200", status="DEGRADED", source_hash_char="c"),
                frame("REP-FIRST", known_at_ns=201, midpoint="101", source_hash_char="d"),
                frame("REP-LATER", known_at_ns=202, midpoint="110", source_hash_char="e"),
            )
            selected = select_resolution_frame(prediction, frames)
            self.assertEqual("REP-FIRST", selected.frame_id)
            outcome = resolve_prediction(root, prediction.prediction_id, frames, now_at_ns=500)
            self.assertEqual("RESOLVED", outcome.status)
            self.assertEqual("REP-FIRST", outcome.resolution_frame_id)
            self.assertEqual("101", outcome.realized_price)
            self.assertEqual("100.00", outcome.realized_return_bps)
            self.assertEqual("80.00", outcome.forecast_error_bps)
            self.assertEqual(1, outcome.actual_positive)
            self.assertEqual(201, outcome.decided_at_ns)

    def test_resolution_retry_time_does_not_change_final_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = journal_prediction(root)
            frames = (frame("REP-RESULT", known_at_ns=201, midpoint="99", source_hash_char="f"),)
            first = resolve_prediction(root, prediction.prediction_id, frames, now_at_ns=500)
            second = resolve_prediction(root, prediction.prediction_id, frames, now_at_ns=999999)
            self.assertEqual(first.to_wire(), second.to_wire())
            self.assertEqual(201, first.decided_at_ns)
            self.assertEqual(0, first.actual_positive)

    def test_missing_frame_stays_pending_until_fixed_window_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = journal_prediction(root)
            closes = prediction.resolves_at_ns + MAX_RESOLUTION_LAG_NS_V1
            with self.assertRaises(OutcomePendingError):
                resolve_prediction(root, prediction.prediction_id, (), now_at_ns=closes)
            outcome = resolve_prediction(root, prediction.prediction_id, (), now_at_ns=closes + 999)
            self.assertEqual("UNRESOLVABLE", outcome.status)
            self.assertEqual(closes + 1, outcome.decided_at_ns)
            self.assertIsNone(outcome.realized_price)

    def test_frame_outside_resolution_window_cannot_be_cherry_picked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = journal_prediction(root)
            too_late = frame(
                "REP-TOO-LATE",
                known_at_ns=prediction.resolves_at_ns + MAX_RESOLUTION_LAG_NS_V1 + 1,
                midpoint="150",
                source_hash_char="1",
            )
            self.assertIsNone(select_resolution_frame(prediction, (too_late,)))
            outcome = resolve_prediction(
                root,
                prediction.prediction_id,
                (too_late,),
                now_at_ns=too_late.known_at_ns,
            )
            self.assertEqual("UNRESOLVABLE", outcome.status)

    def test_prediction_must_exist_in_valid_durable_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(OutcomeResolutionError, "not durably journaled"):
                resolve_prediction(root, "PRED-MISSING", (), now_at_ns=1000)

    def test_outcome_journal_is_idempotent_conflict_detecting_and_tamper_evident(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = journal_prediction(root)
            frames = (frame("REP-RESULT", known_at_ns=201, midpoint="101", source_hash_char="2"),)
            outcome = resolve_prediction(root, prediction.prediction_id, frames, now_at_ns=500)
            journal = OutcomeJournal(root)
            first = journal.append(outcome)
            second = journal.append(outcome)
            self.assertEqual(first, second)
            self.assertEqual(1, len(journal.entries()))
            self.assertEqual([], validate_outcome_journal(root))

            records = journal.path.read_text(encoding="utf-8").splitlines()
            changed = json.loads(records[0])
            changed["outcome"]["resolution"]["realized_price"] = "999"
            journal.path.write_text(json.dumps(changed) + "\n", encoding="utf-8")
            self.assertTrue(any("entry hash mismatch" in error for error in validate_outcome_journal(root)))


if __name__ == "__main__":
    unittest.main()
