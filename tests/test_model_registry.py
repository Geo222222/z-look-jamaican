import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.models import ModelDefinition, ModelRegistry, ModelRegistryError, validate_model_registry


HORIZON_NS = 60_000_000_000


def definition(model_id="TEST-MODEL", version="1.0.0"):
    return ModelDefinition(
        model_id=model_id,
        version=version,
        family="TEST",
        lifecycle_state="CANDIDATE",
        required_representation_type="INSTRUMENT_STATE",
        target_metric="AGGREGATE_MIDPOINT_RETURN_BPS",
        supported_horizons_ns=(HORIZON_NS,),
        parameters={"alpha": "1"},
    )


def register(registry, item=None, *, artifact="a", occurred_at_ns=100):
    item = item or definition()
    return registry.register(
        item,
        artifact_hash=artifact * 64,
        code_ref="git:TEST-COMMIT:autonomous_kernel/models/test.py",
        training_data_refs=("DATASET:TEST-V1",),
        occurred_at_ns=occurred_at_ns,
    )


def qualify(registry, model_ref="TEST-MODEL@1.0.0", start=200):
    steps = (
        ("REPLAY_QUALIFIED", "REPLAY_EVALUATION", "EVIDENCE:REPLAY"),
        ("WALK_FORWARD_QUALIFIED", "WALK_FORWARD_EVALUATION", "EVIDENCE:WALK"),
        ("SHADOW", "SHADOW_EVALUATION", "EVIDENCE:SHADOW"),
        ("QUALIFIED", "QUALIFICATION_DECISION", "EVIDENCE:QUALIFY"),
    )
    for index, (target, kind, evidence) in enumerate(steps):
        registry.transition(
            model_ref,
            target,
            evidence_kind=kind,
            evidence_refs=(evidence,),
            occurred_at_ns=start + index,
        )


class ModelRegistryTests(unittest.TestCase):
    def test_registration_is_idempotent_and_binds_artifact_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ModelRegistry(Path(directory))
            first = register(registry)
            second = register(registry)
            self.assertEqual(first, second)
            self.assertEqual(1, len(registry.events()))
            self.assertEqual("CANDIDATE", first["state"])
            with self.assertRaisesRegex(ModelRegistryError, "different artifact identity"):
                register(registry, artifact="b")
            self.assertEqual([], validate_model_registry(Path(directory), require_state=False))

    def test_illegal_skip_to_qualified_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ModelRegistry(Path(directory))
            register(registry)
            with self.assertRaisesRegex(ModelRegistryError, "illegal model transition"):
                registry.transition(
                    "TEST-MODEL@1.0.0",
                    "QUALIFIED",
                    evidence_kind="QUALIFICATION_DECISION",
                    evidence_refs=("EVIDENCE:QUALIFY",),
                    occurred_at_ns=200,
                )
            self.assertFalse(registry.eligible("TEST-MODEL@1.0.0", "QUALIFIED_SERVING"))

    def test_full_qualification_path_requires_exact_evidence_kinds(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ModelRegistry(Path(directory))
            register(registry)
            with self.assertRaisesRegex(ModelRegistryError, "requires evidence kind REPLAY_EVALUATION"):
                registry.transition(
                    "TEST-MODEL@1.0.0",
                    "REPLAY_QUALIFIED",
                    evidence_kind="SHADOW_EVALUATION",
                    evidence_refs=("EVIDENCE:WRONG",),
                    occurred_at_ns=200,
                )
            self.assertFalse(registry.eligible("TEST-MODEL@1.0.0", "SHADOW_EVALUATION"))
            qualify(registry)
            self.assertTrue(registry.eligible("TEST-MODEL@1.0.0", "QUALIFIED_SERVING"))
            self.assertTrue(registry.eligible("TEST-MODEL@1.0.0", "SHADOW_EVALUATION"))
            self.assertEqual("QUALIFIED", registry.state()["models"]["TEST-MODEL@1.0.0"]["state"])
            self.assertEqual([], validate_model_registry(Path(directory), require_state=False))

    def test_same_state_retry_is_idempotent_only_for_same_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ModelRegistry(Path(directory))
            register(registry)
            first = registry.transition(
                "TEST-MODEL@1.0.0",
                "REPLAY_QUALIFIED",
                evidence_kind="REPLAY_EVALUATION",
                evidence_refs=("EVIDENCE:REPLAY",),
                occurred_at_ns=200,
            )
            event_count = len(registry.events())
            second = registry.transition(
                "TEST-MODEL@1.0.0",
                "REPLAY_QUALIFIED",
                evidence_kind="REPLAY_EVALUATION",
                evidence_refs=("EVIDENCE:REPLAY",),
                occurred_at_ns=201,
            )
            self.assertEqual(first, second)
            self.assertEqual(event_count, len(registry.events()))
            with self.assertRaisesRegex(ModelRegistryError, "same-state transition"):
                registry.transition(
                    "TEST-MODEL@1.0.0",
                    "REPLAY_QUALIFIED",
                    evidence_kind="REPLAY_EVALUATION",
                    evidence_refs=("EVIDENCE:DIFFERENT",),
                    occurred_at_ns=202,
                )

    def test_degraded_model_loses_serving_eligibility_until_requalified(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ModelRegistry(Path(directory))
            register(registry)
            qualify(registry)
            registry.transition(
                "TEST-MODEL@1.0.0",
                "DEGRADED",
                evidence_kind="MONITORING_EVIDENCE",
                evidence_refs=("EVIDENCE:DRIFT",),
                occurred_at_ns=300,
            )
            self.assertFalse(registry.eligible("TEST-MODEL@1.0.0", "QUALIFIED_SERVING"))
            self.assertFalse(registry.eligible("TEST-MODEL@1.0.0", "SHADOW_EVALUATION"))
            registry.transition(
                "TEST-MODEL@1.0.0",
                "SHADOW",
                evidence_kind="SHADOW_EVALUATION",
                evidence_refs=("EVIDENCE:RESHADOW",),
                occurred_at_ns=301,
            )
            self.assertTrue(registry.eligible("TEST-MODEL@1.0.0", "SHADOW_EVALUATION"))
            self.assertFalse(registry.eligible("TEST-MODEL@1.0.0", "QUALIFIED_SERVING"))

    def test_quarantined_and_superseded_models_are_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ModelRegistry(root)
            register(registry, definition("QUARANTINE-MODEL"))
            registry.transition(
                "QUARANTINE-MODEL@1.0.0",
                "QUARANTINED",
                evidence_kind="INTEGRITY_EVIDENCE",
                evidence_refs=("EVIDENCE:INTEGRITY",),
                occurred_at_ns=200,
            )
            self.assertFalse(registry.eligible("QUARANTINE-MODEL@1.0.0", "HISTORICAL_RESEARCH"))
            with self.assertRaisesRegex(ModelRegistryError, "illegal model transition"):
                registry.transition(
                    "QUARANTINE-MODEL@1.0.0",
                    "REPLAY_QUALIFIED",
                    evidence_kind="REPLAY_EVALUATION",
                    evidence_refs=("EVIDENCE:REPLAY",),
                    occurred_at_ns=201,
                )

            register(registry, definition("OLD-MODEL"), artifact="b", occurred_at_ns=300)
            qualify(registry, "OLD-MODEL@1.0.0", start=400)
            registry.transition(
                "OLD-MODEL@1.0.0",
                "SUPERSEDED",
                evidence_kind="SUCCESSION_EVIDENCE",
                evidence_refs=("EVIDENCE:SUCCESSOR",),
                occurred_at_ns=500,
            )
            self.assertFalse(registry.eligible("OLD-MODEL@1.0.0", "HISTORICAL_RESEARCH"))
            with self.assertRaisesRegex(ModelRegistryError, "illegal model transition"):
                registry.transition(
                    "OLD-MODEL@1.0.0",
                    "DEGRADED",
                    evidence_kind="MONITORING_EVIDENCE",
                    evidence_refs=("EVIDENCE:LATE",),
                    occurred_at_ns=501,
                )

    def test_transition_time_cannot_move_backwards(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ModelRegistry(Path(directory))
            register(registry, occurred_at_ns=200)
            with self.assertRaisesRegex(ModelRegistryError, "time cannot move backwards"):
                registry.transition(
                    "TEST-MODEL@1.0.0",
                    "REPLAY_QUALIFIED",
                    evidence_kind="REPLAY_EVALUATION",
                    evidence_refs=("EVIDENCE:REPLAY",),
                    occurred_at_ns=199,
                )

    def test_duplicate_or_empty_evidence_refs_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ModelRegistry(Path(directory))
            register(registry)
            for refs in ((), ("EVIDENCE:X", "EVIDENCE:X"), ("",)):
                with self.assertRaises(ModelRegistryError):
                    registry.transition(
                        "TEST-MODEL@1.0.0",
                        "REPLAY_QUALIFIED",
                        evidence_kind="REPLAY_EVALUATION",
                        evidence_refs=refs,
                        occurred_at_ns=200,
                    )

    def test_tampered_transition_chain_fails_validation_and_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ModelRegistry(root)
            register(registry)
            registry.transition(
                "TEST-MODEL@1.0.0",
                "REPLAY_QUALIFIED",
                evidence_kind="REPLAY_EVALUATION",
                evidence_refs=("EVIDENCE:REPLAY",),
                occurred_at_ns=200,
            )
            lines = registry.events_path.read_text(encoding="utf-8").splitlines()
            first = json.loads(lines[0])
            first["payload"]["artifact_hash"] = "b" * 64
            lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
            registry.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            errors = validate_model_registry(root, require_state=False)
            self.assertTrue(any("event_hash mismatch" in error for error in errors))
            with self.assertRaisesRegex(ModelRegistryError, "journal invalid"):
                registry.eligible("TEST-MODEL@1.0.0", "HISTORICAL_RESEARCH")

    def test_projection_drift_is_detected_and_rebuildable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ModelRegistry(root)
            register(registry)
            registry.transition(
                "TEST-MODEL@1.0.0",
                "REPLAY_QUALIFIED",
                evidence_kind="REPLAY_EVALUATION",
                evidence_refs=("EVIDENCE:REPLAY",),
                occurred_at_ns=200,
            )
            state = registry.state()
            state["models"]["TEST-MODEL@1.0.0"]["state"] = "QUALIFIED"
            registry.state_path.write_text(json.dumps(state), encoding="utf-8")
            self.assertTrue(any("projection differs" in error for error in validate_model_registry(root, require_state=False)))
            rebuilt = registry.rebuild_state()
            self.assertEqual("REPLAY_QUALIFIED", rebuilt["models"]["TEST-MODEL@1.0.0"]["state"])
            self.assertEqual([], validate_model_registry(root, require_state=False))

    def test_idempotent_registration_repairs_stale_projection_from_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ModelRegistry(root)
            expected = register(registry)
            registry.state_path.unlink()
            recovered = register(registry)
            self.assertEqual(expected, recovered)
            self.assertTrue(registry.state_path.is_file())
            self.assertEqual(1, len(registry.events()))
            self.assertEqual([], validate_model_registry(root, require_state=False))


if __name__ == "__main__":
    unittest.main()
