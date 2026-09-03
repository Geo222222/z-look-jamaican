from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.assembly import ModelContextProfile, ModelContextProfileRegistry, ModelContextProfileRegistryError
from autonomous_kernel.assembly.contextual_service import contextual_assemble_and_record
from autonomous_kernel.models.contracts import ModelDefinition
from autonomous_kernel.models.registry import ModelRegistry


TARGET = "ZLJ_AGGREGATE_MIDPOINT_RETURN_BPS_V1"
HORIZON = 10_000_000_000


def register_model(root: Path) -> str:
    definition = ModelDefinition(
        model_id="MODEL-A",
        version="1.0.0",
        family="TEST",
        lifecycle_state="CANDIDATE",
        required_representation_type="INSTRUMENT_STATE",
        target_metric=TARGET,
        supported_horizons_ns=(HORIZON,),
        parameters={},
    )
    ModelRegistry(root).register(
        definition,
        artifact_hash="a" * 64,
        code_ref="TEST",
        occurred_at_ns=1,
    )
    return definition.model_ref


class ContextProfileAuthorityBoundaryTests(unittest.TestCase):
    def test_canonical_contextual_service_does_not_accept_caller_profiles(self):
        parameters = inspect.signature(contextual_assemble_and_record).parameters
        self.assertNotIn("profiles", parameters)
        self.assertIn("context", parameters)
        self.assertIn("assembly_at_ns", parameters)

    def test_activation_is_not_authoritative_before_its_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_ref = register_model(root)
            registry = ModelContextProfileRegistry(root)
            profile = ModelContextProfile(
                model_ref,
                ("LIQUIDITY",),
                {"structure": ("ORDERLY",)},
                {},
                "BOOK",
                "1.0",
            )
            record = registry.register(
                profile,
                evidence_refs=("PROFILE-DESIGN",),
                occurred_at_ns=2,
            )
            registry.activate(
                record["profile_id"],
                evidence_refs=("PROFILE-ACTIVATION",),
                occurred_at_ns=10,
            )

            with self.assertRaises(ModelContextProfileRegistryError):
                registry.active_profile(model_ref, as_of_ns=9)
            self.assertEqual(
                profile.to_wire(),
                registry.active_profile(model_ref, as_of_ns=10).to_wire(),
            )

            # Canonical assembly resolves authority at assembly_at_ns - 1. Thus
            # an assembly stamped 10 cannot use a profile first activated at 10;
            # an assembly stamped 11 can.
            with self.assertRaises(ModelContextProfileRegistryError):
                registry.active_profiles((model_ref,), as_of_ns=10 - 1)
            self.assertEqual(
                profile.to_wire(),
                registry.active_profiles((model_ref,), as_of_ns=11 - 1)[0].to_wire(),
            )


if __name__ == "__main__":
    unittest.main()
