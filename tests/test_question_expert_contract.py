from __future__ import annotations

import copy
import unittest

from autonomous_kernel.models import (
    BookImbalanceLinearModel,
    ModelDefinition,
    NullPriorModel,
    QuestionExpertDefinition,
    QuestionExpertError,
    QuestionExpertRegistryEntry,
    QuestionExpertRegistrySnapshot,
    bind_question,
    build_question_expert_registry_snapshot,
    material_question_expert_registry_evidence,
    validate_expert_question_compatibility,
)
from autonomous_kernel.questions import default_question_registry_v1
from autonomous_kernel.questions.evolution import reversal_question_v1_1


T = 1_788_400_000_000_000_000


def _direction_question():
    registry = default_question_registry_v1(registered_at_ns=T - 10, effective_at_ns=T - 9)
    return next(
        entry.definition
        for entry in registry.entries
        if entry.definition.question_id == "ECONOMIC_ROOT_DIRECTION_10S"
    )


def _expert(question=None, **changes):
    item = question or _direction_question()
    values = {
        "expert_id": "DIRECTION-NULL-CONTROL",
        "version": "1.0.0",
        "family": "CONTROL_NULL_PRIOR",
        "implementation_ref": "autonomous_kernel.models.question_controls.direction_null_v1",
        "implementation_version": "1.0.0",
        "question_bindings": (bind_question(item),),
        "required_artifact_types": item.required_artifact_types,
        "required_feature_families": item.required_feature_families,
        "allowed_feature_families": item.required_feature_families,
        "required_timescales": item.required_timescales,
        "feature_schema_id": "ZLJ.QUESTION_FEATURES.DIRECTION.NULL",
        "feature_schema_version": "1.0.0",
        "training_mode": "NONE",
        "training_data_cutoff_ns": None,
        "training_completed_at_ns": None,
        "supported_subject_ids": (),
        "parameters": {"probability_1": "0.5"},
    }
    values.update(changes)
    return QuestionExpertDefinition(**values)


class QuestionExpertContractTests(unittest.TestCase):
    def test_definition_is_deterministic_round_trip_and_content_addressed(self):
        first = _expert()
        second = _expert()
        self.assertEqual(first.content_hash(), second.content_hash())
        self.assertEqual(first.definition_ref, second.definition_ref)
        restored = QuestionExpertDefinition.from_wire(first.to_wire())
        self.assertEqual(first.to_wire(), restored.to_wire())
        self.assertIn(first.content_hash(), first.definition_ref)

    def test_material_changes_change_definition_identity(self):
        base = _expert()
        variants = (
            _expert(parameters={"probability_1": "0.6"}),
            _expert(implementation_version="1.0.1"),
            _expert(feature_schema_version="1.1.0"),
            _expert(supported_subject_ids=("ASSET.BTC",)),
        )
        for variant in variants:
            self.assertNotEqual(base.content_hash(), variant.content_hash())
            self.assertNotEqual(base.definition_ref, variant.definition_ref)

        changed_question = reversal_question_v1_1()
        different = _expert(
            question=changed_question,
            expert_id="REVERSAL-NULL-CONTROL",
            question_bindings=(bind_question(changed_question),),
            required_artifact_types=changed_question.required_artifact_types,
            required_feature_families=changed_question.required_feature_families,
            allowed_feature_families=changed_question.required_feature_families,
            required_timescales=changed_question.required_timescales,
        )
        self.assertNotEqual(base.definition_ref, different.definition_ref)

    def test_wire_tamper_and_authority_escalation_are_rejected(self):
        expert = _expert()
        changed = copy.deepcopy(expert.to_wire())
        changed["parameters"]["probability_1"] = "0.9"
        with self.assertRaisesRegex(QuestionExpertError, "content hash mismatch"):
            QuestionExpertDefinition.from_wire(changed)

        escalated = copy.deepcopy(expert.to_wire())
        escalated["authority"]["capital_decision"] = True
        with self.assertRaisesRegex(QuestionExpertError, "authority boundary"):
            QuestionExpertDefinition.from_wire(escalated)

    def test_training_timing_is_explicit_and_fail_closed(self):
        with self.assertRaisesRegex(QuestionExpertError, "untrained expert"):
            _expert(training_mode="NONE", training_data_cutoff_ns=T - 100, training_completed_at_ns=T - 50)
        with self.assertRaisesRegex(QuestionExpertError, "requires training cutoff"):
            _expert(training_mode="FROZEN_SUPERVISED", training_data_cutoff_ns=None, training_completed_at_ns=None)
        with self.assertRaisesRegex(QuestionExpertError, "training timing"):
            _expert(training_mode="FROZEN_SUPERVISED", training_data_cutoff_ns=T, training_completed_at_ns=T - 1)
        trained = _expert(
            training_mode="FROZEN_SUPERVISED",
            training_data_cutoff_ns=T - 100,
            training_completed_at_ns=T - 50,
        )
        self.assertEqual(T - 100, trained.training_data_cutoff_ns)

    def test_exact_question_hash_and_input_contract_are_enforced(self):
        question = _direction_question()
        expert = _expert(question)
        validate_expert_question_compatibility(expert, question)

        wrong_binding = bind_question(question)
        object.__setattr__(wrong_binding, "question_definition_hash", "f" * 64)
        wrong = _expert(question_bindings=(wrong_binding,))
        with self.assertRaisesRegex(QuestionExpertError, "question hash"):
            validate_expert_question_compatibility(wrong, question)

        forbidden = _expert(
            allowed_feature_families=question.required_feature_families + ("FUTURE_OUTCOME",),
        )
        with self.assertRaisesRegex(QuestionExpertError, "outside question allowlist|forbidden"):
            validate_expert_question_compatibility(forbidden, question)

        missing_artifact = _expert(required_artifact_types=("OTHER",))
        with self.assertRaisesRegex(QuestionExpertError, "artifact required"):
            validate_expert_question_compatibility(missing_artifact, question)

    def test_registry_lifecycle_changes_do_not_mutate_expert_definition(self):
        expert = _expert()
        candidate = QuestionExpertRegistryEntry(
            definition=expert,
            lifecycle_state="CANDIDATE",
            registered_at_ns=T,
            effective_at_ns=T + 1,
        )
        first = build_question_expert_registry_snapshot(
            registry_id="ZLJ-QUESTION-EXPERTS",
            version="1.0.0",
            entries=(candidate,),
            known_at_ns=T,
            effective_at_ns=T + 1,
        )
        replay = QuestionExpertRegistryEntry(
            definition=expert,
            lifecycle_state="REPLAY_QUALIFIED",
            registered_at_ns=T,
            effective_at_ns=T + 10,
            qualification_evidence_refs=("EVAL-JOURNAL-COMMITMENT-001",),
        )
        second = build_question_expert_registry_snapshot(
            registry_id="ZLJ-QUESTION-EXPERTS",
            version="1.1.0",
            entries=(replay,),
            known_at_ns=T + 10,
            effective_at_ns=T + 10,
        )
        self.assertEqual(expert.definition_ref, second.entries[0].definition.definition_ref)
        self.assertNotEqual(first.content_hash(), second.content_hash())
        with self.assertRaisesRegex(QuestionExpertError, "candidate expert cannot claim"):
            QuestionExpertRegistryEntry(
                definition=expert,
                lifecycle_state="CANDIDATE",
                registered_at_ns=T,
                effective_at_ns=T,
                qualification_evidence_refs=("FAKE",),
            )
        with self.assertRaisesRegex(QuestionExpertError, "qualified expert requires"):
            QuestionExpertRegistryEntry(
                definition=expert,
                lifecycle_state="SHADOW_QUALIFIED",
                registered_at_ns=T,
                effective_at_ns=T,
            )

    def test_registry_roundtrip_tamper_and_book_event_do_not_claim_competence(self):
        expert = _expert()
        registry = build_question_expert_registry_snapshot(
            registry_id="ZLJ-QUESTION-EXPERTS",
            version="1.0.0",
            entries=(
                QuestionExpertRegistryEntry(
                    definition=expert,
                    lifecycle_state="CANDIDATE",
                    registered_at_ns=T,
                    effective_at_ns=T + 1,
                ),
            ),
            known_at_ns=T,
            effective_at_ns=T + 1,
        )
        restored = QuestionExpertRegistrySnapshot.from_wire(registry.to_wire())
        self.assertEqual(registry.to_wire(), restored.to_wire())
        changed = copy.deepcopy(registry.to_wire())
        changed["entries"][0]["lifecycle_state"] = "SHADOW_QUALIFIED"
        with self.assertRaises(QuestionExpertError):
            QuestionExpertRegistrySnapshot.from_wire(changed)
        evidence = material_question_expert_registry_evidence(registry)
        self.assertEqual("ZLJ.QUESTION_EXPERT_REGISTRY", evidence.event_type)
        self.assertEqual("ANALYTICAL", evidence.evidence_class)
        self.assertIn(b'"model_competence":false', evidence.payload)
        self.assertNotIn(b'"model_competence":true', evidence.payload)

    def test_legacy_z4_contract_and_baseline_imports_remain_unchanged(self):
        legacy = NullPriorModel().definition
        self.assertIsInstance(legacy, ModelDefinition)
        self.assertEqual("CANDIDATE", legacy.lifecycle_state)
        self.assertEqual("ZLJ_AGGREGATE_MIDPOINT_RETURN_BPS_V1", legacy.target_metric)
        self.assertIsInstance(BookImbalanceLinearModel().definition, ModelDefinition)


if __name__ == "__main__":
    unittest.main()
