from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from autonomous_kernel.book_bridge import ZLJBookSigner
from autonomous_kernel.book_outbox import BookOutbox
from autonomous_kernel.experience import ExperienceTimescale
from autonomous_kernel.questions import (
    AnswerKind,
    OutcomeDefinition,
    QuestionContractError,
    QuestionDefinition,
    QuestionFamily,
    QuestionRegistryEntry,
    QuestionRegistrySnapshot,
    QuestionScope,
    build_question_registry_snapshot,
    default_question_registry_v1,
    material_question_registry_evidence,
    question_catalog_v1,
)


T = 1_788_400_000_000_000_000


class QuestionRegistryTests(unittest.TestCase):
    def test_default_catalog_is_defined_before_models_or_resolvers_claim_competence(self) -> None:
        catalog = question_catalog_v1()
        families = {item.family for item in catalog}
        self.assertEqual(10, len(catalog))
        self.assertIn(QuestionFamily.DIRECTION, families)
        self.assertIn(QuestionFamily.MAGNITUDE, families)
        self.assertIn(QuestionFamily.VOLATILITY, families)
        self.assertIn(QuestionFamily.LIQUIDITY, families)
        self.assertIn(QuestionFamily.FRAGILITY, families)
        self.assertIn(QuestionFamily.BASIS, families)
        self.assertIn(QuestionFamily.REGIME, families)
        self.assertIn(QuestionFamily.PERSISTENCE, families)
        self.assertIn(QuestionFamily.REVERSAL, families)
        self.assertIn(QuestionFamily.RELATIVE_VALUE, families)
        self.assertNotIn(QuestionFamily.EXECUTION_SUITABILITY, families)
        self.assertEqual(len({item.question_ref for item in catalog}), len(catalog))
        for question in catalog:
            self.assertGreater(question.horizon_ns, 0)
            self.assertEqual("KNOWN_AT_OR_BEFORE_QUESTION_CUTOFF", question.evidence_cutoff_policy)
            self.assertTrue(set(question.required_feature_families).issubset(set(question.allowed_feature_families)))
            self.assertFalse(set(question.allowed_feature_families).intersection(question.forbidden_feature_families))

    def test_registry_hash_is_order_independent_and_changes_when_question_semantics_change(self) -> None:
        first = default_question_registry_v1(registered_at_ns=T, effective_at_ns=T + 1)
        reversed_entries = tuple(reversed(first.entries))
        second = build_question_registry_snapshot(
            registry_id=first.registry_id,
            version=first.version,
            entries=reversed_entries,
            known_at_ns=T,
            effective_at_ns=T + 1,
        )
        self.assertEqual(first.content_hash(), second.content_hash())

        original = question_catalog_v1()[0]
        changed = QuestionDefinition(
            question_id=original.question_id,
            version="1.0.1",
            family=original.family,
            scope=original.scope,
            asks=original.asks,
            horizon_ns=original.horizon_ns * 2,
            outcome=original.outcome,
            required_timescales=original.required_timescales,
            required_artifact_types=original.required_artifact_types,
            required_feature_families=original.required_feature_families,
            allowed_feature_families=original.allowed_feature_families,
            forbidden_feature_families=original.forbidden_feature_families,
            parameters=original.parameters,
        )
        changed_entries = (QuestionRegistryEntry(changed, "DEFINED", T, T + 1),) + first.entries[1:]
        changed_registry = build_question_registry_snapshot(
            registry_id=first.registry_id,
            version="1.0.1",
            entries=changed_entries,
            known_at_ns=T,
            effective_at_ns=T + 1,
        )
        self.assertNotEqual(first.content_hash(), changed_registry.content_hash())

    def test_registry_round_trip_recovers_exact_semantics_and_hash(self) -> None:
        registry = default_question_registry_v1(registered_at_ns=T, effective_at_ns=T + 1)
        restored = QuestionRegistrySnapshot.from_wire(registry.to_wire())
        self.assertEqual(registry.to_wire(), restored.to_wire())
        self.assertEqual(registry.content_hash(), restored.content_hash())
        self.assertEqual(
            tuple(entry.definition.question_ref for entry in registry.entries),
            tuple(entry.definition.question_ref for entry in restored.entries),
        )

    def test_nested_question_semantic_tamper_is_rejected_on_recovery(self) -> None:
        registry = default_question_registry_v1(registered_at_ns=T, effective_at_ns=T + 1)
        wire = registry.to_wire()
        wire["entries"][0]["definition"]["horizon_ns"] *= 2
        with self.assertRaisesRegex(QuestionContractError, "content hash mismatch"):
            QuestionRegistrySnapshot.from_wire(wire)

    def test_entry_definition_hash_tamper_is_rejected_on_recovery(self) -> None:
        registry = default_question_registry_v1(registered_at_ns=T, effective_at_ns=T + 1)
        wire = registry.to_wire()
        wire["entries"][0]["definition_hash"] = "0" * 64
        with self.assertRaisesRegex(QuestionContractError, "definition hash mismatch"):
            QuestionRegistrySnapshot.from_wire(wire)

    def test_required_evidence_cannot_be_forbidden_or_outside_allowed_set(self) -> None:
        outcome = OutcomeDefinition(
            metric_id="TEST",
            answer_kind=AnswerKind.BINARY,
            target_expression="1 if x else 0",
            resolver_policy_id="TEST_RESOLVER",
            max_resolution_lag_ns=1,
            resolution_evidence_families=("SPOT_MICROSTRUCTURE",),
        )
        with self.assertRaisesRegex(QuestionContractError, "must be allowed"):
            QuestionDefinition(
                question_id="BAD_REQUIRED",
                version="1",
                family=QuestionFamily.DIRECTION,
                scope=QuestionScope.ECONOMIC_ROOT,
                asks="bad",
                horizon_ns=1,
                outcome=outcome,
                required_timescales=(ExperienceTimescale.MICRO,),
                required_artifact_types=("MARKET_EXPERIENCE",),
                required_feature_families=("DERIVATIVE_POSITIONING",),
                allowed_feature_families=("SPOT_MICROSTRUCTURE",),
                forbidden_feature_families=(),
                parameters={},
            )
        with self.assertRaisesRegex(QuestionContractError, "cannot also be forbidden"):
            QuestionDefinition(
                question_id="BAD_FORBIDDEN",
                version="1",
                family=QuestionFamily.DIRECTION,
                scope=QuestionScope.ECONOMIC_ROOT,
                asks="bad",
                horizon_ns=1,
                outcome=outcome,
                required_timescales=(ExperienceTimescale.MICRO,),
                required_artifact_types=("MARKET_EXPERIENCE",),
                required_feature_families=("SPOT_MICROSTRUCTURE",),
                allowed_feature_families=("SPOT_MICROSTRUCTURE",),
                forbidden_feature_families=("SPOT_MICROSTRUCTURE",),
                parameters={},
            )

    def test_lifecycle_cannot_claim_qualification_without_resolver_and_evidence(self) -> None:
        definition = question_catalog_v1()[0]
        with self.assertRaisesRegex(QuestionContractError, "QUALIFIED"):
            QuestionRegistryEntry(
                definition=definition,
                lifecycle_state="QUALIFIED",
                registered_at_ns=T,
                effective_at_ns=T,
            )
        ready = QuestionRegistryEntry(
            definition=definition,
            lifecycle_state="RESOLVER_READY",
            registered_at_ns=T,
            effective_at_ns=T,
            resolver_implementation_ref="autonomous_kernel.evaluation.direction_v1",
        )
        self.assertEqual("RESOLVER_READY", ready.lifecycle_state)
        qualified = QuestionRegistryEntry(
            definition=definition,
            lifecycle_state="QUALIFIED",
            registered_at_ns=T,
            effective_at_ns=T,
            resolver_implementation_ref="autonomous_kernel.evaluation.direction_v1",
            qualification_evidence_refs=("EVAL-QUESTION-001",),
        )
        self.assertEqual("QUALIFIED", qualified.lifecycle_state)

    def test_registry_cannot_effectively_exist_before_it_is_known(self) -> None:
        definition = question_catalog_v1()[0]
        with self.assertRaisesRegex(QuestionContractError, "timing"):
            QuestionRegistryEntry(
                definition=definition,
                lifecycle_state="DEFINED",
                registered_at_ns=T,
                effective_at_ns=T - 1,
            )

    def test_question_registry_activation_is_book_bound(self) -> None:
        registry = default_question_registry_v1(registered_at_ns=T, effective_at_ns=T + 1)
        intent = material_question_registry_evidence(
            registry,
            payload_ref="zlj://question-registry/ZLJ-MARKET-QUESTIONS/1.0.0",
        )
        signer = ZLJBookSigner(key_id="question-registry-test", private_key=Ed25519PrivateKey.generate())
        produced_at = datetime.fromtimestamp((T + 2) / 1_000_000_000, tz=timezone.utc)
        envelope = intent.sign(
            signer=signer,
            receipt_id="ZLJ-QUESTION-REGISTRY-1",
            produced_at=produced_at,
            visibility_scope=("INSTITUTION", "BENJAMIN"),
        )
        self.assertEqual("ZLJ.QUESTION_REGISTRY", envelope["event_type"])
        self.assertEqual("ZLJ-MARKET-QUESTIONS@1.0.0", envelope["subject_id"])
        self.assertEqual(intent.payload_digest, envelope["payload_digest"])
        with tempfile.TemporaryDirectory() as directory:
            record = BookOutbox(Path(directory)).enqueue(envelope=envelope, payload=intent.payload)
            self.assertEqual("PENDING", record["state"])

    def test_registry_definitions_do_not_grant_model_or_capital_authority(self) -> None:
        registry = default_question_registry_v1(registered_at_ns=T, effective_at_ns=T + 1)
        wire = registry.to_wire()
        self.assertFalse(wire["authority"]["selects_model"])
        self.assertFalse(wire["authority"]["capital_decision"])
        self.assertFalse(wire["authority"]["risk_authorization"])
        self.assertFalse(wire["authority"]["external_execution"])
        for entry in wire["entries"]:
            authority = entry["definition"]["authority"]
            self.assertFalse(authority["capital_decision"])
            self.assertFalse(authority["risk_authorization"])
            self.assertFalse(authority["external_execution"])


if __name__ == "__main__":
    unittest.main()
