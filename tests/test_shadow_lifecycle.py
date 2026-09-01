import copy
import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.market_data import build_candle_observation
from autonomous_kernel.operations import validate_execution_receipts
from autonomous_kernel.shadow_lifecycle import ExecutionAssumptions, ShadowLifecycle, TypedDecision


GOVERNOR = {"production_financial_trading": "disabled", "max_single_trade_usd": 0, "max_concurrent_financial_exposure_usd": 0, "max_daily_loss_usd": 0}
CAPABILITY = {"id": "CAP-1", "state": "BACKTEST_SUPPORTED", "evidence_ids": ["E-1"], "live_enabled": False}


def obs():
    return build_candle_observation(
        observation_id="OBS-1", provider="public-provider", instrument="BTC-USD", interval_seconds=300,
        candle_start_at=1000, received_at=1302, observed_at=1305, open_price="100", high_price="102",
        low_price="99", close_price="101", volume="100", max_event_age_seconds=10,
        max_transport_age_seconds=5,
    )


def decision():
    return TypedDecision("DEC-1", "CAP-1", "OBS-1", "2026-09-01T00:00:00Z", "BUY", "2.0", "MARKET", None, "QUALIFICATION_BUY")


def assumptions():
    return ExecutionAssumptions("ASSUME-1", "10", "2", "3", 100, "1", "0.5", "0.1", "0.01", "10", "10")


class ShadowLifecycleTests(unittest.TestCase):
    def run_once(self, root, **kwargs):
        values = {"decision": decision(), "observation": obs(), "capability": CAPABILITY, "governor": GOVERNOR, "assumptions": assumptions(), "processed_at": "2026-09-01T00:00:01Z"}
        values.update(kwargs)
        return ShadowLifecycle(root).run(**values)

    def test_non_hold_intent_traverses_complete_zero_exposure_path(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.run_once(Path(directory))
            self.assertEqual("PARTIALLY_FILLED", receipt["execution_result"]["status"])
            self.assertEqual("MODELED", receipt["execution_result"]["fills"][0]["truth_class"])
            self.assertEqual("OBSERVED", receipt["execution_result"]["facts"]["truth_class"])
            self.assertEqual("CONFIGURED", receipt["execution_result"]["configured_assumptions"]["truth_class"])
            self.assertEqual("MATCHED", receipt["accounting"]["reconciliation_state"])
            self.assertTrue(receipt["accounting"]["comparison_performed"])
            self.assertEqual("0.00", receipt["accounting"]["financial_exposure_usd"])
            self.assertEqual("0.00", receipt["accounting"]["pnl_realized_usd"])
            self.assertFalse(receipt["execution_result"]["capital_moved"])
            self.assertEqual([], validate_execution_receipts(Path(directory)))

    def test_same_inputs_are_deterministic_and_duplicate_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.run_once(root)
            second = self.run_once(root)
            self.assertEqual(first, second)
            self.assertEqual(1, len(list((root / "receipts/execution").glob("*.json"))))

    def test_restart_after_each_partial_stage_finalizes_once(self):
        for stage in ("PREPARED", "AUTHORIZED", "EXECUTED", "ACCOUNTED"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaises(RuntimeError):
                    self.run_once(root, fail_after_stage=stage)
                receipt = self.run_once(root)
                self.assertEqual("MATCHED", receipt["accounting"]["reconciliation_state"])
                self.assertEqual(1, len(list((root / "receipts/execution").glob("*.json"))))

    def test_stale_market_data_is_rejected_before_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            stale = copy.deepcopy(obs())
            stale["quality"]["status"] = "STALE"
            with self.assertRaises(ValueError):
                self.run_once(Path(directory), observation=stale)
            self.assertFalse((Path(directory) / "runtime/shadow_operations").exists())

    def test_unqualified_capability_is_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PermissionError):
                self.run_once(Path(directory), capability={**CAPABILITY, "state": "HYPOTHESIS"})

    def test_governor_snapshot_drift_is_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PermissionError):
                self.run_once(Path(directory), governor={**GOVERNOR, "max_single_trade_usd": 1})

    def test_corrupt_journal_fails_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "runtime/shadow_operations"
            path.mkdir(parents=True)
            (path / "REQ-DEC-1.json").write_text("broken", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                self.run_once(root)


if __name__ == "__main__":
    unittest.main()
