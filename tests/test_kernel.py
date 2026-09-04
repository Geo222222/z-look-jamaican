import json
import shutil
import tempfile
import unittest
import hashlib
from pathlib import Path

from autonomous_kernel.store import (
    StateValidationError,
    load_json,
    next_work,
    repository_root,
    recover_pending,
    transition,
    update_task,
    validate,
)


class KernelStateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        source = repository_root()
        for relative in (
            "state",
            "memory",
            "opportunities",
            "metrics",
            "accounting",
            "evidence",
            "artifacts",
            "receipts",
            "config",
        ):
            shutil.copytree(source / relative, self.root / relative)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_json(self, relative, value):
        path = self.root / relative
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def test_repository_state_validates(self):
        checks = validate(self.root)
        self.assertIn("governor_zero_exposure", checks)
        self.assertIn("treasury_registry_integrity", checks)
        self.assertIn("resume_checkpoint", checks)

    def test_governor_drift_fails_closed(self):
        current = load_json(self.root / "state/current_state.json")
        current["governor"]["max_single_trade_usd"] = 1
        self._write_json("state/current_state.json", current)
        with self.assertRaises(StateValidationError) as context:
            validate(self.root)
        self.assertTrue(any("max_single_trade_usd" in error for error in context.exception.errors))

    def test_treasury_registry_drift_fails_closed(self):
        registry = self.root / "config/treasury_destinations.yaml"
        registry.write_text(registry.read_text(encoding="utf-8") + "\n# owner change\n", encoding="utf-8")
        with self.assertRaises(StateValidationError) as context:
            validate(self.root)
        self.assertTrue(any("treasury destination registry differs" in error for error in context.exception.errors))

    def test_treasury_registry_and_mutable_hash_cannot_drift_together(self):
        registry = self.root / "config/treasury_destinations.yaml"
        registry.write_text(registry.read_text(encoding="utf-8") + "\n# unauthorized change\n", encoding="utf-8")
        changed_hash = hashlib.sha256(registry.read_bytes()).hexdigest()
        current = load_json(self.root / "state/current_state.json")
        current["treasury_registry"]["sha256_at_inspection"] = changed_hash
        self._write_json("state/current_state.json", current)
        with self.assertRaises(StateValidationError) as context:
            validate(self.root)
        self.assertTrue(any("owner-controlled version-1 anchor" in error for error in context.exception.errors))

    def test_secret_bearing_field_is_rejected(self):
        wallets = load_json(self.root / "state/operational_wallets.json")
        wallets["items"].append({"id": "unsafe", "private_key": "never-store-this"})
        self._write_json("state/operational_wallets.json", wallets)
        with self.assertRaises(StateValidationError) as context:
            validate(self.root)
        self.assertTrue(any("forbidden secret-bearing field" in error for error in context.exception.errors))

    def test_evidence_checksum_detects_tampering(self):
        artifact = self.root / "artifacts/evidence/apify_store/snapshot-20260821T195023Z.json"
        artifact.write_text(artifact.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(StateValidationError) as context:
            validate(self.root)
        self.assertTrue(any("artifact checksum mismatch" in error for error in context.exception.errors))

    def test_opportunity_unknown_evidence_is_rejected(self):
        opportunities = load_json(self.root / "opportunities/register.json")
        opportunities["items"][0]["evidence_ids"].append("EVIDENCE-MISSING")
        self._write_json("opportunities/register.json", opportunities)
        with self.assertRaises(StateValidationError) as context:
            validate(self.root)
        self.assertTrue(any("references unknown evidence" in error for error in context.exception.errors))

    def test_next_work_obeys_dependencies_and_score(self):
        backlog = load_json(self.root / "state/backlog.json")
        for item in backlog["items"]:
            if item["id"] in {"TASK-BOOT-001", "TASK-BOOT-002", "TASK-BOOT-003"}:
                item["status"] = "in_progress"
            elif item["id"] == "TASK-BOOT-004":
                item["status"] = "ready"
            else:
                item["status"] = "completed"
        self._write_json("state/backlog.json", backlog)
        resume = load_json(self.root / "state/resume.json")
        resume["active_task_ids"] = sorted(
            item["id"] for item in backlog["items"] if item["status"] == "in_progress"
        )
        self._write_json("state/resume.json", resume)
        self.assertIsNone(next_work(self.root))
        for task_id in ("TASK-BOOT-001", "TASK-BOOT-002", "TASK-BOOT-003"):
            update_task(task_id, "completed", self.root)
        candidate = next_work(self.root)
        self.assertEqual("TASK-BOOT-004", candidate["id"])

    def test_valid_transition_is_recorded_and_invalid_one_is_rejected(self):
        current = load_json(self.root / "state/current_state.json")
        resume = load_json(self.root / "state/resume.json")
        current["root_state"] = "BOOTSTRAP"
        resume["root_state"] = "BOOTSTRAP"
        self._write_json("state/current_state.json", current)
        self._write_json("state/resume.json", resume)
        record = transition(
            "DISCOVERY",
            "kernel acceptance checks passed",
            "DEC-BOOT-001",
            ["tests/test_kernel.py"],
            self.root,
        )
        self.assertEqual("BOOTSTRAP", record["previous_state"])
        self.assertEqual("DISCOVERY", load_json(self.root / "state/current_state.json")["root_state"])
        with self.assertRaises(ValueError):
            transition(
                "DEPLOY",
                "skip unsafe stages",
                "DEC-INVALID",
                ["none"],
                self.root,
            )

    def test_pending_transaction_recovers_idempotently(self):
        current = load_json(self.root / "state/current_state.json")
        resume = load_json(self.root / "state/resume.json")
        current["root_state"] = "DISCOVERY"
        resume["root_state"] = "DISCOVERY"
        record = {
            "id": "TRANSITION-RECOVERY-TEST",
            "created_at": "2026-08-21T00:00:00Z",
            "created_by": "test",
            "type": "state_transition",
            "previous_state": "BOOTSTRAP",
            "new_state": "DISCOVERY",
            "trigger": "failure injection",
            "evidence": ["tests/test_kernel.py"],
            "decision_id": "DEC-BOOT-001",
            "rollback_or_demotion_condition": "validation failure",
        }
        journal = {
            "schema_version": 1,
            "id": "TXN-RECOVERY-TEST",
            "created_at": "2026-08-21T00:00:00Z",
            "status": "prepared",
            "writes": [
                {"path": "state/current_state.json", "document": current},
                {"path": "state/resume.json", "document": resume},
            ],
            "appends": [{"path": "state/transitions.jsonl", "record": record}],
        }
        self._write_json("state/pending_transaction.json", journal)
        result = recover_pending(self.root)
        self.assertEqual("recovered", result["status"])
        self.assertEqual("DISCOVERY", load_json(self.root / "state/current_state.json")["root_state"])
        self.assertFalse((self.root / "state/pending_transaction.json").exists())
        self.assertEqual("clean", recover_pending(self.root)["status"])


if __name__ == "__main__":
    unittest.main()
