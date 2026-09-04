from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomous_kernel.operator import (
    OperatorCommandError,
    append_operator_receipt,
    command_catalog,
    execute_operator_command,
    operator_snapshot,
    validate_operator_journal,
)
from autonomous_kernel.operator.journal import receipt_for_request_id


ROOT = Path(__file__).resolve().parents[1]


class OperatorConsoleContractTests(unittest.TestCase):
    def test_catalog_exposes_governed_and_locked_authority(self):
        catalog = command_catalog()
        commands = {item["command_id"]: item for item in catalog["commands"]}
        self.assertEqual("AVAILABLE", commands["VALIDATE_KERNEL"]["state"])
        self.assertEqual("MUTATING", commands["MATERIALIZE_CONTEXT"]["control_class"])
        for command_id in ("LIVE_EXECUTION", "CAPITAL_AUTHORIZATION", "ORDER_PLACEMENT"):
            self.assertEqual("LOCKED", commands[command_id]["state"])
            self.assertEqual("CONSTITUTIONALLY_LOCKED", commands[command_id]["control_class"])

    def test_operator_snapshot_has_exact_z1_through_z9_story_and_claim_ceiling(self):
        snapshot = operator_snapshot(ROOT)
        self.assertEqual("zlj-operator-console", snapshot["contract"]["name"])
        self.assertEqual("1.2", snapshot["contract"]["schema_version"])
        self.assertEqual(["Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7", "Z8", "Z9"], [stage["id"] for stage in snapshot["stages"]])
        self.assertEqual("NONE", snapshot["system"]["capital_authority"])
        self.assertEqual("LOCKED_FALSE", snapshot["system"]["live_execution"])
        self.assertEqual("NOT_EARNED", snapshot["certification"]["z8_historical"]["decision"])
        self.assertEqual("CERTIFIED", snapshot["certification"]["z9"]["construction"])
        self.assertEqual("DATA_BLOCKED", snapshot["certification"]["z9"]["contextual_performance"])

    def test_operator_snapshot_exposes_pretraining_and_governed_model_qualification_state(self):
        snapshot = operator_snapshot(ROOT)
        research = snapshot["research_qualification"]
        self.assertEqual("PRE_TRAINING_INFRASTRUCTURE", research["status"])
        self.assertEqual("NOT_RUN", research["training"])
        self.assertEqual("BUILT", research["construction"]["point_in_time_feature_plane"])
        self.assertEqual("BUILT", research["construction"]["walk_forward_evaluation_plan"])
        self.assertEqual("NOT_EARNED", research["construction"]["trained_expert_population"])
        self.assertFalse(research["authority"]["trains_models"])
        self.assertFalse(research["authority"]["promotes_models"])
        qualification = snapshot["model_qualification"]
        self.assertEqual("VALID", qualification["status"])
        self.assertIn("ModelRegistry remains lifecycle authority", qualification["authority"])

    def test_operator_snapshot_exposes_frozen_question_registry_without_granting_model_or_capital_authority(self):
        snapshot = operator_snapshot(ROOT)
        registry = snapshot["question_registry"]
        self.assertEqual("QUESTION_REGISTRY_V1_QUALIFIED", registry["status"])
        self.assertEqual("ZLJ-MARKET-QUESTIONS", registry["registry"]["registry_id"])
        self.assertEqual("1.3.0-question-registry-v1-qualified", registry["registry"]["version"])
        self.assertEqual(10, registry["summary"]["active_resolver_ready"])
        self.assertEqual(["EXECUTION_SUITABILITY"], registry["summary"]["deferred_families"])
        self.assertFalse(registry["authority"]["selects_model"])
        self.assertFalse(registry["authority"]["claims_model_competence"])
        self.assertFalse(registry["authority"]["capital_decision"])
        self.assertFalse(registry["authority"]["risk_authorization"])
        self.assertFalse(registry["authority"]["external_execution"])
        states = {item["question_ref"]: item["lifecycle_state"] for item in registry["questions"]}
        self.assertEqual("DEFINED", states["ECONOMIC_ROOT_REVERSAL_60S@1.0.0"])
        self.assertEqual("RETIRED", states["ECONOMIC_ROOT_REVERSAL_60S@1.1.0"])
        self.assertEqual("RESOLVER_READY", states["ECONOMIC_ROOT_REVERSAL_60S@1.2.0"])
        self.assertTrue(registry["guarantees"]["material_reversal_thresholds_preregistered"])
        self.assertEqual(
            registry["certificate"]["integrity"]["content_hash"],
            snapshot["certification"]["question_registry"]["certificate_hash"],
        )

    def test_locked_and_unavailable_controls_fail_before_domain_execution(self):
        with self.assertRaises(OperatorCommandError):
            execute_operator_command(ROOT, {"command_id": "LIVE_EXECUTION", "confirm": True, "request_id": "REQ-LOCKED"})
        with self.assertRaises(OperatorCommandError):
            execute_operator_command(ROOT, {"command_id": "CODE_CHANGE", "confirm": True, "request_id": "REQ-CODE"})

    def test_mutating_control_requires_external_server_gate(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZLOOK_OPERATOR_MUTATIONS_ENABLED", None)
            with self.assertRaisesRegex(OperatorCommandError, "mutations are disabled"):
                execute_operator_command(ROOT, {"command_id": "RECOVER_PENDING", "confirm": True, "request_id": "REQ-GATED"})

    def test_operator_receipts_are_hash_chained_idempotency_addressable_and_tamper_evident(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = {
                "receipt_version": "1.0",
                "request_id": "REQ-001",
                "command_id": "TEST_MUTATION",
                "control_class": "MUTATING",
                "started_at_ns": 10,
                "completed_at_ns": 11,
                "parameters": {"x": 1},
                "result": {"ok": True},
                "capital_effect": "NONE",
                "execution_effect": "NONE",
            }
            entry = append_operator_receipt(root, receipt)
            self.assertEqual([], validate_operator_journal(root))
            self.assertEqual(entry["entry_hash"], receipt_for_request_id(root, "REQ-001")["entry_hash"])
            with self.assertRaises(Exception):
                append_operator_receipt(root, receipt)
            path = root / "memory/operator_commands.jsonl"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["receipt"]["parameters"]["x"] = 2
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            self.assertTrue(validate_operator_journal(root))

    def test_read_only_validation_does_not_write_operator_receipt(self):
        path = ROOT / "memory/operator_commands.jsonl"
        before = path.read_text(encoding="utf-8") if path.is_file() else None
        result = execute_operator_command(ROOT, {"command_id": "VALIDATE_KERNEL"})
        after = path.read_text(encoding="utf-8") if path.is_file() else None
        self.assertEqual("READ_ONLY_QUERY_NOT_JOURNALED", result["durability"])
        self.assertEqual(before, after)
        self.assertIn("operator_journal", result["receipt"]["result"]["checks"])


if __name__ == "__main__":
    unittest.main()
