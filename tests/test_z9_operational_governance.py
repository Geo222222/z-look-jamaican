from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.assembly import (
    CertifiedContextualAssemblyError,
    ModelContextProfile,
    ModelContextProfileRegistry,
    ModelContextProfileRegistryError,
    contextual_assemble_and_record,
    validate_model_context_profile_registry,
)
from autonomous_kernel.context import (
    MATERIALIZER_POLICY_ID,
    MarketContextMaterializationError,
    MarketContextStore,
    build_market_context,
    materialize_market_context,
    verify_materialized_context,
)
from autonomous_kernel.models import ModelRegistry
from autonomous_kernel.representation import RepresentationStore
from tests.test_adaptive_assembly import current_pair, definition, register_shadow
from tests.test_market_context import BTC, ETH, frame as context_frame, histories


def persist_frame(store: RepresentationStore, source) -> None:
    store.persist(
        source,
        source_batches=(
            {
                "batch_id": "BATCH-%s" % source.frame_id,
                "manifest_ref": "TEST",
                "manifest_content_hash": "a" * 64,
            },
        ),
    )


class Z9MaterializerTests(unittest.TestCase):
    def test_canonical_materializer_rebuilds_z2_index_selects_only_knowable_history_and_receipts_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RepresentationStore(root)
            btc, eth = histories()
            for source in tuple(btc + eth):
                persist_frame(store, source)
            future = context_frame(BTC, 6, "104", known_at_ns=6_000)
            persist_frame(store, future)

            # Prove the materializer does not trust a stale mutable discovery index.
            (root / "state/representations.json").write_text(
                json.dumps({"schema_version": 1, "representation_contract_version": "1.0", "authority": "stale-test", "items": []}) + "\n",
                encoding="utf-8",
            )

            context, receipt = materialize_market_context(root, cutoff_at_ns=5_000)
            self.assertEqual(MATERIALIZER_POLICY_ID, receipt["policy_id"])
            self.assertEqual(5_000, context.cutoff_at_ns)
            self.assertEqual(10, len(context.source_frame_ids))
            self.assertNotIn(future.frame_id, context.source_frame_ids)
            self.assertEqual(context.source_set_hash(), receipt["context"]["source_set_hash"])
            self.assertEqual(context.content_hash(), receipt["context"]["context_content_hash"])
            self.assertEqual(receipt, verify_materialized_context(root, context.context_id))

    def test_manual_context_persistence_is_not_accepted_as_canonical_materialization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RepresentationStore(root)
            btc, eth = histories()
            sources = tuple(source for source in btc + eth if source.known_at_ns <= 4_000)
            for source in sources:
                persist_frame(store, source)
            context = build_market_context(sources, cutoff_at_ns=4_000)
            MarketContextStore(root).persist(context, source_frames=sources)
            with self.assertRaisesRegex(MarketContextMaterializationError, "lacks canonical materialization receipt"):
                verify_materialized_context(root, context.context_id)


class ModelContextProfileRegistryTests(unittest.TestCase):
    def test_profiles_are_immutable_model_bound_and_resolved_point_in_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            models = ModelRegistry(root)
            model = definition("MODEL-A")
            models.register(
                model,
                artifact_hash="b" * 64,
                code_ref="tests/test_z9_operational_governance.py",
                training_data_refs=("DATASET:A",),
                occurred_at_ns=100,
            )
            registry = ModelContextProfileRegistry(root)
            v1 = ModelContextProfile(model.model_ref, ("LIQUIDITY", "CORRELATION"), {"structure": ("ORDERLY",)}, {"volatility": ("HIGH",)}, "FLOW", profile_version="1.0")
            v2 = ModelContextProfile(model.model_ref, ("LIQUIDITY", "CORRELATION", "DERIVATIVES"), {"structure": ("ORDERLY",)}, {"volatility": ("HIGH",)}, "FLOW", profile_version="1.1")
            first = registry.register(v1, registered_at_ns=110, evidence_refs=("EVIDENCE:PROFILE-A-V1",))
            registry.register(v2, registered_at_ns=120, evidence_refs=("EVIDENCE:PROFILE-A-V2",))

            with self.assertRaises(ModelContextProfileRegistryError):
                registry.resolve((model.model_ref,), as_of_ns=109)
            self.assertEqual("1.0", registry.resolve((model.model_ref,), as_of_ns=119)[0].profile_version)
            self.assertEqual("1.1", registry.resolve((model.model_ref,), as_of_ns=120)[0].profile_version)
            self.assertEqual(model.content_hash(), first["model_binding"]["definition_hash"])
            self.assertEqual("b" * 64, first["model_binding"]["artifact_hash"])

            changed_same_version = ModelContextProfile(model.model_ref, ("CORE_MARKET",), {}, {}, "OTHER", profile_version="1.0")
            with self.assertRaisesRegex(ModelContextProfileRegistryError, "immutable"):
                registry.register(changed_same_version, registered_at_ns=111, evidence_refs=("EVIDENCE:ILLEGAL-REWRITE",))
            self.assertEqual([], validate_model_context_profile_registry(root))

    def test_profile_artifact_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            models = ModelRegistry(root)
            model = definition("MODEL-A")
            models.register(model, artifact_hash="c" * 64, code_ref="test", training_data_refs=(), occurred_at_ns=1)
            registry = ModelContextProfileRegistry(root)
            profile = ModelContextProfile(model.model_ref, ("CORE_MARKET",), {}, {}, "CORE")
            artifact = registry.register(profile, registered_at_ns=2, evidence_refs=("EVIDENCE:PROFILE",))
            path = root / "artifacts/model_context_profiles" / (artifact["profile_id"] + ".json")
            value = json.loads(path.read_text(encoding="utf-8"))
            value["profile"]["diversity_group"] = "TAMPERED"
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            self.assertTrue(validate_model_context_profile_registry(root))


class GovernedContextualAssemblyServiceTests(unittest.TestCase):
    def _setup(self, root: Path):
        registry = ModelRegistry(root)
        model_a = definition("MODEL-A")
        model_b = definition("MODEL-B")
        register_shadow(registry, model_a, start_ns=1)
        register_shadow(registry, model_b, start_ns=10)
        source, first, second = current_pair(root, model_a, model_b, created_at_ns=10_001)

        z2 = RepresentationStore(root)
        btc, eth = histories()
        for item in tuple(btc + eth) + (source,):
            persist_frame(z2, item)
        context, _ = materialize_market_context(root, cutoff_at_ns=source.known_at_ns)
        return registry, model_a, model_b, source, first, second, context

    def test_canonical_service_resolves_registered_profiles_instead_of_accepting_caller_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry, model_a, model_b, source, first, second, context = self._setup(root)
            profiles = ModelContextProfileRegistry(root)
            profile_a = ModelContextProfile(model_a.model_ref, ("CORE_MARKET",), {"direction": ("RISK_ON",)}, {}, "FLOW")
            profile_b = ModelContextProfile(model_b.model_ref, ("CORRELATION",), {}, {"direction": ("RISK_ON",)}, "BOOK")
            profiles.register(profile_a, registered_at_ns=10_001, evidence_refs=("EVIDENCE:A-POLICY",))
            profiles.register(profile_b, registered_at_ns=10_001, evidence_refs=("EVIDENCE:B-POLICY",))

            final, receipt = contextual_assemble_and_record(root, source, (second, first), registry, context, assembly_at_ns=10_002)
            hashes = {item["model_ref"]: item["context_profile_hash"] for item in receipt.contributors}
            self.assertEqual(profile_a.content_hash(), hashes[model_a.model_ref])
            self.assertEqual(profile_b.content_hash(), hashes[model_b.model_ref])
            self.assertEqual(receipt.final_prediction_hash, final.content_hash())

    def test_canonical_service_fails_before_contextual_weighting_if_any_governed_profile_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry, model_a, model_b, source, first, second, context = self._setup(root)
            ModelContextProfileRegistry(root).register(
                ModelContextProfile(model_a.model_ref, ("CORE_MARKET",), {}, {}, "FLOW"),
                registered_at_ns=10_001,
                evidence_refs=("EVIDENCE:A-POLICY",),
            )
            with self.assertRaisesRegex(CertifiedContextualAssemblyError, "no governed ModelContextProfile"):
                contextual_assemble_and_record(root, source, (first, second), registry, context, assembly_at_ns=10_002)


if __name__ == "__main__":
    unittest.main()
