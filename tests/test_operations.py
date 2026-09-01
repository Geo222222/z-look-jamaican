import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.operations import (
    ExecutionRequest, ReceiptStore, authorize_execution, build_execution_receipt,
    promote_capability, validate_capability_registry,
    validate_execution_receipts,
    capability_non_success, evidence_bound_promotion,
)


GOVERNOR = {"production_financial_trading": "disabled", "max_single_trade_usd": 0, "max_concurrent_financial_exposure_usd": 0, "max_daily_loss_usd": 0}
CAPABILITY = {"id": "CAP-1", "state": "PREREGISTERED", "evidence_ids": ["E-1"], "live_enabled": False}


def request(mode="SHADOW", request_id="REQ-1", capital_effect="NONE"):
    return ExecutionRequest(request_id, "IDEM-1", "CAP-1", "DEC-1", "OBS-1", mode, "BTC-USD", "BUY", "LIMIT", "0.01", "50000.00", "2026-09-01T00:00:00Z", capital_effect)


class OperationsTests(unittest.TestCase):
    def test_shadow_request_gets_zero_exposure_authorization_and_receipt(self):
        receipt = build_execution_receipt(request(), CAPABILITY, GOVERNOR, "2026-09-01T00:00:01Z")
        self.assertTrue(receipt["risk_authorization"]["allowed"])
        self.assertEqual("0.00", receipt["accounting"]["financial_exposure_usd"])
        self.assertFalse(receipt["execution_result"]["capital_moved"])
        self.assertEqual([], receipt["execution_result"]["fills"])

    def test_live_is_always_denied_under_current_governor(self):
        auth = authorize_execution(request("LIVE"), CAPABILITY, GOVERNOR, "2026-09-01T00:00:01Z")
        self.assertFalse(auth["allowed"])
        self.assertIn("live_execution_disabled", auth["reasons"])

    def test_pre_live_capital_effect_fails_validation(self):
        with self.assertRaises(ValueError):
            request(capital_effect="NONZERO").validate()

    def test_capability_cannot_skip_promotion_state(self):
        with self.assertRaises(ValueError):
            promote_capability(CAPABILITY, "SHADOW_QUALIFIED", ["E-2"])

    def test_registry_forces_live_disabled(self):
        errors = validate_capability_registry({"schema_version": 1, "items": [{**CAPABILITY, "live_enabled": True}]})
        self.assertTrue(any("live_enabled" in item for item in errors))

    def test_receipt_retry_is_idempotent_and_conflict_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReceiptStore(Path(directory))
            receipt = build_execution_receipt(request(), CAPABILITY, GOVERNOR, "2026-09-01T00:00:01Z")
            first = store.persist(receipt)
            second = store.persist(receipt)
            self.assertEqual(first, second)
            changed = json.loads(json.dumps(receipt))
            changed["request_hash"] = "different"
            with self.assertRaises(RuntimeError):
                store.persist(changed)

    def test_corrupt_existing_receipt_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts" / "execution"
            path.mkdir(parents=True)
            (path / "REQ-1.json").write_text("not json", encoding="utf-8")
            receipt = build_execution_receipt(request(), CAPABILITY, GOVERNOR, "2026-09-01T00:00:01Z")
            with self.assertRaises(json.JSONDecodeError):
                ReceiptStore(Path(directory)).persist(receipt)

    def test_orphan_temporary_file_does_not_block_safe_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts" / "execution"
            path.mkdir(parents=True)
            (path / ".orphan.tmp").write_text("partial", encoding="utf-8")
            receipt = build_execution_receipt(request(), CAPABILITY, GOVERNOR, "2026-09-01T00:00:01Z")
            persisted = ReceiptStore(Path(directory)).persist(receipt)
            self.assertEqual("RECEIPT-REQ-1", persisted["receipt_id"])

    def test_receipt_integrity_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = build_execution_receipt(request(), CAPABILITY, GOVERNOR, "2026-09-01T00:00:01Z")
            ReceiptStore(root).persist(receipt)
            path = root / "receipts/execution/REQ-1.json"
            changed = json.loads(path.read_text(encoding="utf-8"))
            changed["accounting"]["pnl_realized_usd"] = "99.00"
            path.write_text(json.dumps(changed), encoding="utf-8")
            errors = validate_execution_receipts(root)
            self.assertTrue(any("content hash mismatch" in item for item in errors))
            self.assertTrue(any("realized economics" in item for item in errors))

    def test_capital_and_live_promotion_are_not_available_to_model_path(self):
        for state in ("CAPITAL_ELIGIBLE", "LIVE"):
            with self.assertRaises(PermissionError):
                evidence_bound_promotion(CAPABILITY, state, [{"path": "x"}], "RULE", "2026-09-01T00:00:00Z", Path("."))

    def test_non_success_outcome_preserves_earned_state_and_lineage(self):
        updated, transition = capability_non_success(CAPABILITY, "SUSPENDED", "data unavailable", ["E-2"], "2026-09-01T00:00:00Z")
        self.assertEqual(CAPABILITY["state"], updated["state"])
        self.assertEqual("SUSPENDED", updated["operational_status"])
        self.assertEqual("SUSPENDED", transition["outcome"])


if __name__ == "__main__":
    unittest.main()
