from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.assembly import ModelContextProfile, ModelContextProfileRegistry, ModelContextProfileRegistryError, profile_set_hash, validate_context_profile_registry
from autonomous_kernel.models.contracts import ModelDefinition
from autonomous_kernel.models.registry import ModelRegistry


TARGET = "ZLJ_AGGREGATE_MIDPOINT_RETURN_BPS_V1"
HORIZON = 10_000_000_000


def register_model(root: Path, model_id: str) -> str:
    definition = ModelDefinition(
        model_id=model_id,
        version="1.0.0",
        family="TEST",
        lifecycle_state="CANDIDATE",
        required_representation_type="INSTRUMENT_STATE",
        target_metric=TARGET,
        supported_horizons_ns=(HORIZON,),
        parameters={},
    )
    ModelRegistry(root).register(definition, artifact_hash=("a" if model_id == "MODEL-A" else "b") * 64, code_ref="TEST", occurred_at_ns=1)
    return definition.model_ref


class ContextProfileRegistryTests(unittest.TestCase):
    def test_registration_is_immutable_and_binds_exact_z5_model_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); model_ref = register_model(root, "MODEL-A"); registry = ModelContextProfileRegistry(root)
            profile = ModelContextProfile(model_ref, ("LIQUIDITY", "CORRELATION"), {"structure": ("ORDERLY",)}, {"volatility": ("HIGH",)}, "BOOK", "1.0")
            record = registry.register(profile, evidence_refs=("DESIGN-Z9-001",), occurred_at_ns=2)
            same = registry.register(profile, evidence_refs=("DIFFERENT-RETRY-EVIDENCE",), occurred_at_ns=99)
            self.assertEqual(record["profile_id"], same["profile_id"])
            self.assertEqual(profile.content_hash(), record["profile_hash"])
            drifted = ModelContextProfile(model_ref, ("LIQUIDITY",), {}, {}, "BOOK", "1.0")
            with self.assertRaises(ModelContextProfileRegistryError):
                registry.register(drifted, evidence_refs=("DESIGN-Z9-002",), occurred_at_ns=3)
            unregistered = ModelContextProfile("UNKNOWN@1", (), {}, {}, "NONE", "1.0")
            with self.assertRaises(ModelContextProfileRegistryError):
                registry.register(unregistered, evidence_refs=("DESIGN-Z9-003",), occurred_at_ns=3)
            self.assertEqual([], validate_context_profile_registry(root))

    def test_activation_history_resolves_exact_profile_as_of_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); model_ref = register_model(root, "MODEL-A"); registry = ModelContextProfileRegistry(root)
            first = ModelContextProfile(model_ref, ("LIQUIDITY",), {"structure": ("ORDERLY",)}, {}, "BOOK", "1.0")
            second = ModelContextProfile(model_ref, ("CORRELATION",), {"correlation": ("COHERENT",)}, {"structure": ("DISLOCATED",)}, "BOOK", "2.0")
            first_record = registry.register(first, evidence_refs=("PROFILE-1",), occurred_at_ns=2)
            second_record = registry.register(second, evidence_refs=("PROFILE-2",), occurred_at_ns=3)
            registry.activate(first_record["profile_id"], evidence_refs=("ACTIVATE-1",), occurred_at_ns=10)
            registry.activate(second_record["profile_id"], evidence_refs=("ACTIVATE-2",), occurred_at_ns=20)
            self.assertEqual(first.to_wire(), registry.active_profile(model_ref, as_of_ns=15).to_wire())
            self.assertEqual(second.to_wire(), registry.active_profile(model_ref, as_of_ns=20).to_wire())
            with self.assertRaises(ModelContextProfileRegistryError):
                registry.activate(first_record["profile_id"], evidence_refs=("ROLLBACK",), occurred_at_ns=19)
            self.assertEqual([], validate_context_profile_registry(root))

    def test_exact_active_profile_set_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); first_ref = register_model(root, "MODEL-A"); second_ref = register_model(root, "MODEL-B"); registry = ModelContextProfileRegistry(root)
            first = ModelContextProfile(first_ref, ("CORE_MARKET",), {}, {}, "FLOW", "1.0")
            second = ModelContextProfile(second_ref, ("CORRELATION",), {}, {}, "BOOK", "1.0")
            first_record = registry.register(first, evidence_refs=("A",), occurred_at_ns=2)
            second_record = registry.register(second, evidence_refs=("B",), occurred_at_ns=2)
            registry.activate(first_record["profile_id"], evidence_refs=("ACT-A",), occurred_at_ns=3)
            registry.activate(second_record["profile_id"], evidence_refs=("ACT-B",), occurred_at_ns=3)
            profiles = registry.active_profiles((second_ref, first_ref), as_of_ns=4)
            self.assertEqual((first_ref, second_ref), tuple(profile.model_ref for profile in profiles))
            self.assertEqual(profile_set_hash((second, first)), profile_set_hash(profiles))
            with self.assertRaises(ModelContextProfileRegistryError):
                registry.active_profiles((first_ref, first_ref), as_of_ns=4)

    def test_artifact_and_event_tampering_fail_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); model_ref = register_model(root, "MODEL-A"); registry = ModelContextProfileRegistry(root)
            profile = ModelContextProfile(model_ref, ("LIQUIDITY",), {}, {}, "BOOK", "1.0")
            record = registry.register(profile, evidence_refs=("REGISTER",), occurred_at_ns=2)
            registry.activate(record["profile_id"], evidence_refs=("ACTIVATE",), occurred_at_ns=3)
            artifact = root / record["artifact_path"]
            document = json.loads(artifact.read_text(encoding="utf-8")); document["profile"]["diversity_group"] = "TAMPERED"; artifact.write_text(json.dumps(document) + "\n", encoding="utf-8")
            self.assertTrue(validate_context_profile_registry(root))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); model_ref = register_model(root, "MODEL-A"); registry = ModelContextProfileRegistry(root)
            profile = ModelContextProfile(model_ref, (), {}, {}, "BOOK", "1.0")
            record = registry.register(profile, evidence_refs=("REGISTER",), occurred_at_ns=2); registry.activate(record["profile_id"], evidence_refs=("ACTIVATE",), occurred_at_ns=3)
            lines = (root / "memory/model_context_profile_events.jsonl").read_text(encoding="utf-8").splitlines(); event = json.loads(lines[-1]); event["payload"]["profile_hash"] = "f" * 64; lines[-1] = json.dumps(event); (root / "memory/model_context_profile_events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertTrue(validate_context_profile_registry(root))


if __name__ == "__main__": unittest.main()
