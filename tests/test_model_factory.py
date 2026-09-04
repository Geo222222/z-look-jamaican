import unittest

from autonomous_kernel.models import (
    BaselineModelError,
    BookImbalanceLinearModel,
    NullPriorModel,
    ReportedFlowLinearModel,
    baseline_model_set,
    run_baseline_models,
)
from autonomous_kernel.observation import CanonicalObservation, default_instrument_registry
from autonomous_kernel.representation import build_instrument_state


VALID = {"status": "VALID", "action_permitted": True}
HORIZON = 10_000_000_000


def observation(observation_id, event_type, payload, known_ns, sequence):
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
        source_event_at_ns=known_ns - 1,
        received_at_ns=known_ns,
        known_at_ns=known_ns,
        sequence=str(sequence),
        sequence_scope="PROVIDER_EVENT",
        stream_id="MODEL-STREAM",
        payload=payload,
        quality=VALID,
        raw_event_sha256="a" * 64,
        raw_ref="raw/%s" % observation_id,
    )


def model_frame():
    sources = (
        observation(
            "OBS-SNAP",
            "BOOK_SNAPSHOT",
            {
                "updates": [
                    {"side": "BID", "price": "99.99", "size": "4"},
                    {"side": "ASK", "price": "100.01", "size": "1"},
                ]
            },
            100,
            1,
        ),
        observation(
            "OBS-BUY",
            "TRADE",
            {"trade_id": "B", "price": "100", "size": "1", "side": "BUY"},
            110,
            2,
        ),
        observation(
            "OBS-SELL",
            "TRADE",
            {"trade_id": "S", "price": "100", "size": "4", "side": "SELL"},
            120,
            3,
        ),
    )
    return build_instrument_state(sources, cutoff_at_ns=120)


class ModelFactoryTests(unittest.TestCase):
    def test_all_baselines_are_versioned_candidates(self):
        models = baseline_model_set()
        self.assertEqual(3, len(models))
        self.assertEqual(3, len({model.definition.model_ref for model in models}))
        self.assertTrue(all(model.definition.lifecycle_state == "CANDIDATE" for model in models))
        self.assertTrue(all(len(model.definition.content_hash()) == 64 for model in models))

    def test_models_emit_comparable_but_different_predictions(self):
        frame = model_frame()
        predictions = run_baseline_models(
            frame,
            mode="PROSPECTIVE_SHADOW",
            prediction_at_ns=130,
            created_at_ns=140,
            horizon_ns=HORIZON,
        )
        self.assertEqual(3, len(predictions))
        self.assertEqual(1, len({item.representation_content_hash for item in predictions}))
        self.assertEqual(1, len({item.target_metric for item in predictions}))
        self.assertEqual(1, len({item.horizon_ns for item in predictions}))
        by_model = {item.model_refs[0]: item for item in predictions}
        self.assertEqual("0", by_model["NULL-PRIOR@1.0.0"].expected_move_bps)
        self.assertEqual("0.5", by_model["NULL-PRIOR@1.0.0"].probability_positive)
        self.assertGreater(float(by_model["BOOK-IMBALANCE-LINEAR@1.0.0"].expected_move_bps), 0)
        self.assertLess(float(by_model["REPORTED-FLOW-LINEAR@1.0.0"].expected_move_bps), 0)
        self.assertEqual(3, len({item.prediction_id for item in predictions}))

    def test_same_frame_produces_same_model_claims(self):
        frame = model_frame()
        kwargs = dict(
            mode="PROSPECTIVE_SHADOW",
            prediction_at_ns=130,
            created_at_ns=140,
            horizon_ns=HORIZON,
        )
        first = run_baseline_models(frame, **kwargs)
        second = run_baseline_models(frame, **kwargs)
        self.assertEqual([item.to_wire() for item in first], [item.to_wire() for item in second])

    def test_unsupported_horizon_fails_closed(self):
        with self.assertRaisesRegex(BaselineModelError, "unsupported model horizon"):
            NullPriorModel().predict(
                model_frame(),
                mode="PROSPECTIVE_SHADOW",
                prediction_at_ns=130,
                created_at_ns=140,
                horizon_ns=1,
            )

    def test_flow_and_book_models_are_distinct_hypotheses(self):
        frame = model_frame()
        book = BookImbalanceLinearModel().forecast(frame, HORIZON)
        flow = ReportedFlowLinearModel().forecast(frame, HORIZON)
        self.assertGreater(book[0], 0)
        self.assertLess(flow[0], 0)
        self.assertNotEqual(book, flow)


if __name__ == "__main__":
    unittest.main()
