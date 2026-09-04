import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.market_data import build_candle_observation
from autonomous_kernel.replay import ReplayEngine
from autonomous_kernel.shadow_lifecycle import ExecutionAssumptions, TypedDecision


GOVERNOR = {"production_financial_trading": "disabled", "max_single_trade_usd": 0, "max_concurrent_financial_exposure_usd": 0, "max_daily_loss_usd": 0}
CAPABILITY = {"id": "CAP-1", "state": "BACKTEST_SUPPORTED", "evidence_ids": ["E-1"], "live_enabled": False}
ASSUMPTIONS = ExecutionAssumptions("A-1", "10", "2", "3", 10, "1", "1", "0.1", "0.01", "1", "10")


def observation(number, start):
    return build_candle_observation(observation_id=f"OBS-{number}", provider="p", instrument="BTC-USD", interval_seconds=300, candle_start_at=start, received_at=start + 302, observed_at=start + 305, open_price="100", high_price="102", low_price="99", close_price="101", volume="10", max_event_age_seconds=10, max_transport_age_seconds=5)


def decision(number):
    return TypedDecision(f"DEC-{number}", "CAP-1", f"OBS-{number}", "2026-09-01T00:00:00Z", "BUY", "1", "MARKET", None, "REPLAY")


class ReplayTests(unittest.TestCase):
    def test_replay_orders_timestamps_and_reports_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            observations = [observation(3, 1900), observation(1, 1000), observation(2, 1300)]
            decisions = {f"OBS-{i}": decision(i) for i in (1, 2, 3)}
            result = ReplayEngine(Path(directory)).run(replay_id="R-1", observations=observations, decisions=decisions, capability=CAPABILITY, governor=GOVERNOR, assumptions=ASSUMPTIONS, processed_at="2026-09-01T00:00:01Z")
            self.assertEqual(["OBS-1", "OBS-2", "OBS-3"], result["ordered_observation_ids"])
            self.assertEqual(1, len(result["gaps"]))
            self.assertEqual(1, result["gaps"][0]["missing_intervals"])
            self.assertEqual("NONE", result["randomness"])

    def test_duplicate_observation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                ReplayEngine(Path(directory)).run(replay_id="R-1", observations=[observation(1, 1000), observation(1, 1000)], decisions={}, capability=CAPABILITY, governor=GOVERNOR, assumptions=ASSUMPTIONS, processed_at="2026-09-01T00:00:01Z")

    def test_interrupted_replay_resumes_without_duplicate_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observations = [observation(1, 1000), observation(2, 1300)]
            decisions = {f"OBS-{i}": decision(i) for i in (1, 2)}
            engine = ReplayEngine(root)
            with self.assertRaises(RuntimeError):
                engine.run(replay_id="R-1", observations=observations, decisions=decisions, capability=CAPABILITY, governor=GOVERNOR, assumptions=ASSUMPTIONS, processed_at="2026-09-01T00:00:01Z", fail_after_receipts=1)
            result = engine.run(replay_id="R-1", observations=observations, decisions=decisions, capability=CAPABILITY, governor=GOVERNOR, assumptions=ASSUMPTIONS, processed_at="2026-09-01T00:00:01Z")
            self.assertEqual("COMPLETE", result["status"])
            self.assertEqual(2, len(list((root / "receipts/execution").glob("*.json"))))


if __name__ == "__main__":
    unittest.main()
