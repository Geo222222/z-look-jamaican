import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from autonomous_kernel.evaluation import OutcomeJournal, build_competence_profiles, resolve_prediction
from autonomous_kernel.observation import default_instrument_registry
from autonomous_kernel.prediction import PredictionJournal, create_prediction
from autonomous_kernel.representation import RepresentationFrame


PROVIDER = "coinbase_advanced_trade_public_websocket"
INSTRUMENT = default_instrument_registry().resolve(PROVIDER, "BTC-USD")


def frame(frame_id, known_at_ns, midpoint, hash_char):
    mid = Decimal(str(midpoint))
    return RepresentationFrame(
        frame_id=frame_id,
        representation_type="INSTRUMENT_STATE",
        instrument=INSTRUMENT,
        window_start_ns=max(0, known_at_ns - 10),
        cutoff_at_ns=known_at_ns,
        known_at_ns=known_at_ns,
        latest_source_event_at_ns=max(0, known_at_ns - 1),
        status="QUALIFIED",
        builder_version="test-v1",
        parameters={"test": True},
        state={
            "venue_states": {},
            "aggregate": {
                "cross_venue_book_state": "NORMAL",
                "cross_venue_best_bid": format(mid - Decimal("1"), "f"),
                "cross_venue_best_ask": format(mid + Decimal("1"), "f"),
                "mean_venue_midpoint": format(mid, "f"),
            },
            "input_quality": {"status_counts": {"VALID": 1}, "degraded_reasons": []},
        },
        source_observation_ids=("OBS-%s" % frame_id,),
        source_content_hashes=(hash_char * 64,),
        source_providers=(PROVIDER,),
        source_venues=("COINBASE",),
    )


def add_resolved_case(root, pred_id, prediction_at_ns, expected, probability, realized, hash_char):
    source = frame("REP-SRC-%s" % pred_id, prediction_at_ns, "100", hash_char)
    prediction = create_prediction(
        source,
        mode="PROSPECTIVE_SHADOW",
        prediction_at_ns=prediction_at_ns,
        created_at_ns=prediction_at_ns,
        horizon_ns=100,
        expected_move_bps=expected,
        probability_positive=probability,
        interval_low_bps="0" if Decimal(str(expected)) > 0 else "-100",
        interval_high_bps="200" if Decimal(str(expected)) > 0 else "0",
        model_refs=("MODEL-A@1.0.0",),
        prediction_id=pred_id,
    )
    PredictionJournal(root).append(prediction, journaled_at_ns=prediction_at_ns + 1)
    target_midpoint = Decimal("100") * (Decimal("1") + Decimal(str(realized)) / Decimal("10000"))
    resolution = frame(
        "REP-RES-%s" % pred_id,
        prediction.resolves_at_ns + 1,
        target_midpoint,
        "f" if hash_char != "f" else "e",
    )
    outcome = resolve_prediction(root, pred_id, (resolution,), now_at_ns=prediction.resolves_at_ns + 2)
    OutcomeJournal(root).append(outcome)
    return prediction, outcome


class CompetenceTests(unittest.TestCase):
    def test_metrics_are_segmented_and_calibrated_from_resolved_evidence_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            add_resolved_case(root, "PRED-A", 100, "100", "0.8", "120", "a")
            add_resolved_case(root, "PRED-B", 300, "-50", "0.2", "-30", "b")

            pending_source = frame("REP-PENDING", 500, "100", "c")
            pending = create_prediction(
                pending_source,
                mode="PROSPECTIVE_SHADOW",
                prediction_at_ns=500,
                created_at_ns=500,
                horizon_ns=100,
                expected_move_bps="0",
                probability_positive="0.5",
                interval_low_bps="-20",
                interval_high_bps="20",
                model_refs=("MODEL-A@1.0.0",),
                prediction_id="PRED-PENDING",
            )
            PredictionJournal(root).append(pending, journaled_at_ns=501)

            profiles = build_competence_profiles(root, as_of_ns=1000)
            self.assertEqual(1, len(profiles))
            profile = profiles[0]
            self.assertEqual("MODEL-A@1.0.0", profile.model_ref)
            self.assertEqual("FORWARD_EVALUABLE", profile.evidence_class)
            self.assertEqual(3, profile.prediction_count)
            self.assertEqual(2, profile.resolved_count)
            self.assertEqual(0, profile.unresolvable_count)
            self.assertEqual(1, profile.pending_count)
            self.assertEqual(Decimal("20"), Decimal(profile.metrics["mean_absolute_error_bps"]))
            self.assertEqual(Decimal("20"), Decimal(profile.metrics["root_mean_squared_error_bps"]))
            self.assertEqual(Decimal("20"), Decimal(profile.metrics["mean_forecast_bias_bps"]))
            self.assertEqual(Decimal("1"), Decimal(profile.metrics["directional_accuracy"]))
            self.assertEqual(Decimal("0.04"), Decimal(profile.metrics["brier_score"]))
            self.assertEqual(Decimal("0.5"), Decimal(profile.metrics["mean_probability_positive"]))
            self.assertEqual(Decimal("0.5"), Decimal(profile.metrics["actual_positive_rate"]))
            self.assertEqual(Decimal("0"), Decimal(profile.metrics["calibration_gap"]))
            self.assertEqual(Decimal("1"), Decimal(profile.metrics["interval_coverage"]))
            self.assertEqual(Decimal(2) / Decimal(52), Decimal(profile.metrics["sample_strength"]))
            self.assertEqual(3, len(profile.prediction_hashes))
            self.assertEqual(2, len(profile.outcome_hashes))

    def test_research_and_forward_evidence_never_share_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            add_resolved_case(root, "PRED-FWD", 100, "10", "0.6", "20", "a")

            source = frame("REP-HIST", 10, "100", "b")
            historical = create_prediction(
                source,
                mode="HISTORICAL_REPLAY",
                prediction_at_ns=10,
                created_at_ns=500,
                horizon_ns=100,
                expected_move_bps="10",
                probability_positive="0.6",
                interval_low_bps="-20",
                interval_high_bps="40",
                model_refs=("MODEL-A@1.0.0",),
                prediction_id="PRED-HIST",
            )
            PredictionJournal(root).append(historical, journaled_at_ns=500)
            profiles = build_competence_profiles(root, as_of_ns=1000)
            self.assertEqual({"FORWARD_EVALUABLE", "RESEARCH_ONLY"}, {item.evidence_class for item in profiles})
            for profile in profiles:
                self.assertEqual(1, profile.prediction_count)

    def test_as_of_cutoff_prevents_future_outcome_from_leaking_into_competence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            add_resolved_case(root, "PRED-A", 100, "100", "0.8", "120", "a")
            add_resolved_case(root, "PRED-B", 300, "-50", "0.2", "-30", "b")
            early = build_competence_profiles(root, as_of_ns=350)[0]
            late = build_competence_profiles(root, as_of_ns=1000)[0]
            self.assertEqual(2, early.prediction_count)
            self.assertEqual(1, early.resolved_count)
            self.assertEqual(1, early.pending_count)
            self.assertEqual(2, late.resolved_count)
            self.assertNotEqual(early.content_hash(), late.content_hash())


if __name__ == "__main__":
    unittest.main()
