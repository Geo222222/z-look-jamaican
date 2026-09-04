from __future__ import annotations

import copy
import unittest

from autonomous_kernel.experience import ExperienceTimescale
from autonomous_kernel.models import (
    QuestionExpertDefinition,
    QuestionExpertRegistryEntry,
    bind_question,
    build_question_expert_registry_snapshot,
)
from autonomous_kernel.prediction import (
    PredictionArtifactRef,
    QuestionExpertPrediction,
    QuestionExpertPredictionError,
    build_prospective_question_expert_prediction,
)
from autonomous_kernel.questions import (
    build_resolver_ready_registry_v1,
    default_question_registry_v1,
)


T = 1_788_400_000_000_000_000


def _question_registry(*, ready=True):
    base = default_question_registry_v1(
        registered_at_ns=T - 1_000,
        effective_at_ns=T - 900,
    )
    if not ready:
        return base
    return build_resolver_ready_registry_v1(
        base,
        version="1.1.0",
        known_at_ns=T - 800,
        effective_at_ns=T - 700,
    )


def _direction_question(registry):
    return next(
        entry.definition
        for entry in registry.entries
        if entry.definition.question_id == "ECONOMIC_ROOT_DIRECTION_10S"
    )


def _expert(question, **changes):
    values = {
        "expert_id": "DIRECTION-NULL-CONTROL",
        "version": "1.0.0",
        "family": "CONTROL_NULL_PRIOR",
        "implementation_ref": "autonomous_kernel.models.question_controls.direction_null_v1",
        "implementation_version": "1.0.0",
        "question_bindings": (bind_question(question),),
        "required_artifact_types": question.required_artifact_types,
        "required_feature_families": question.required_feature_families,
        "allowed_feature_families": question.required_feature_families,
        "required_timescales": question.required_timescales,
        "feature_schema_id": "ZLJ.QUESTION_FEATURES.DIRECTION.NULL",
        "feature_schema_version": "1.0.0",
        "training_mode": "NONE",
        "training_data_cutoff_ns": None,
        "training_completed_at_ns": None,
        "supported_subject_ids": ("ASSET.BTC",),
        "parameters": {"probability_1": "0.5"},
    }
    values.update(changes)
    return QuestionExpertDefinition(**values)


def _expert_registry(
    expert,
    *,
    lifecycle_state="SHADOW_QUALIFIED",
    registered_at_ns=T - 500,
    effective_at_ns=T - 400,
    known_at_ns=T - 300,
    registry_effective_at_ns=T - 200,
):
    evidence = (
        ()
        if lifecycle_state in {"CANDIDATE", "RETIRED"}
        else ("QUAL-QUESTION-EXPERT-001",)
    )
    return build_question_expert_registry_snapshot(
        registry_id="ZLJ-QUESTION-EXPERTS",
        version="1.0.0",
        entries=(
            QuestionExpertRegistryEntry(
                definition=expert,
                lifecycle_state=lifecycle_state,
                registered_at_ns=registered_at_ns,
                effective_at_ns=effective_at_ns,
                qualification_evidence_refs=evidence,
            ),
        ),
        known_at_ns=known_at_ns,
        effective_at_ns=registry_effective_at_ns,
    )


def _artifact(
    *,
    known_at_ns=T - 10,
    status="QUALIFIED",
    timescales=(ExperienceTimescale.MICRO,),
    feature_families=("SPOT_MICROSTRUCTURE",),
    subject_ids=("ASSET.BTC",),
):
    return PredictionArtifactRef(
        artifact_type="MARKET_EXPERIENCE",
        artifact_id="EXP-BTC-T",
        content_hash="a" * 64,
        known_at_ns=known_at_ns,
        status=status,
        timescales=timescales,
        feature_families=feature_families,
        subject_ids=subject_ids,
    )


def _build(
    *,
    question_registry=None,
    question=None,
    expert=None,
    expert_registry=None,
    expert_definition_ref=None,
    subject_id="ASSET.BTC",
    cutoff_at_ns=T,
    created_at_ns=T + 1,
    artifacts=None,
):
    q_registry = question_registry or _question_registry()
    item = question or _direction_question(q_registry)
    model = expert or _expert(item)
    model_registry = expert_registry or _expert_registry(model)
    return build_prospective_question_expert_prediction(
        question_registry=q_registry,
        question=item,
        expert_registry=model_registry,
        expert_definition_ref=expert_definition_ref or model.definition_ref,
        subject_id=subject_id,
        cutoff_at_ns=cutoff_at_ns,
        created_at_ns=created_at_ns,
        answer={"value": 1, "probability_1": "0.5"},
        artifact_refs=(_artifact(),) if artifacts is None else artifacts,
    )


class QuestionExpertPredictionTests(unittest.TestCase):
    def test_shadow_qualified_expert_binds_exact_forward_prediction_lineage(self):
        result = _build()
        self.assertEqual("SHADOW_QUALIFIED", result.expert_lifecycle_state)
        self.assertEqual(
            (result.expert_definition_ref,),
            result.prediction.model_refs,
        )
        self.assertEqual("PROSPECTIVE_SHADOW", result.prediction.mode)
        self.assertEqual("FORWARD_EVALUABLE", result.prediction.evidence_class)
        self.assertFalse(result.to_wire()["authority"]["model_competence"])
        self.assertFalse(result.to_wire()["authority"]["adaptive_weighting"])
        self.assertFalse(result.to_wire()["authority"]["capital_decision"])

        restored = QuestionExpertPrediction.from_wire(result.to_wire())
        self.assertEqual(result.to_wire(), restored.to_wire())
        self.assertEqual(result.content_hash(), restored.content_hash())
        self.assertEqual(result.content_hash(), _build().content_hash())

    def test_candidate_replay_qualified_and_retired_experts_cannot_emit_prospective_claims(self):
        question_registry = _question_registry()
        question = _direction_question(question_registry)
        expert = _expert(question)
        for state in ("CANDIDATE", "REPLAY_QUALIFIED", "RETIRED"):
            registry = _expert_registry(expert, lifecycle_state=state)
            with self.subTest(state=state):
                with self.assertRaisesRegex(
                    QuestionExpertPredictionError, "SHADOW_QUALIFIED"
                ):
                    _build(
                        question_registry=question_registry,
                        question=question,
                        expert=expert,
                        expert_registry=registry,
                    )

    def test_exact_expert_definition_ref_is_required_no_same_id_version_substitution(self):
        question_registry = _question_registry()
        question = _direction_question(question_registry)
        registered = _expert(question)
        registry = _expert_registry(registered)
        other_version = _expert(question, version="1.0.1")
        self.assertNotEqual(registered.definition_ref, other_version.definition_ref)
        with self.assertRaisesRegex(
            QuestionExpertPredictionError, "not uniquely present"
        ):
            _build(
                question_registry=question_registry,
                question=question,
                expert=registered,
                expert_registry=registry,
                expert_definition_ref=other_version.definition_ref,
            )

    def test_expert_registry_and_entry_must_preexist_prospective_cutoff(self):
        question_registry = _question_registry()
        question = _direction_question(question_registry)
        expert = _expert(question)

        late_registry = _expert_registry(
            expert,
            known_at_ns=T + 1,
            registry_effective_at_ns=T + 2,
        )
        with self.assertRaisesRegex(
            QuestionExpertPredictionError, "registry was not knowable"
        ):
            _build(
                question_registry=question_registry,
                question=question,
                expert=expert,
                expert_registry=late_registry,
            )

        late_entry = _expert_registry(
            expert,
            registered_at_ns=T - 20,
            effective_at_ns=T + 1,
            known_at_ns=T - 10,
            registry_effective_at_ns=T - 5,
        )
        with self.assertRaisesRegex(
            QuestionExpertPredictionError, "not registered/effective"
        ):
            _build(
                question_registry=question_registry,
                question=question,
                expert=expert,
                expert_registry=late_entry,
            )

    def test_wrong_question_binding_is_rejected(self):
        question_registry = _question_registry()
        direction = _direction_question(question_registry)
        other = next(
            entry.definition
            for entry in question_registry.entries
            if entry.definition.question_id == "ECONOMIC_ROOT_LIQUIDITY_DETERIORATION_30S"
        )
        expert = _expert(direction)
        registry = _expert_registry(expert)
        with self.assertRaisesRegex(
            QuestionExpertPredictionError,
            "does not bind the exact question version|question hash",
        ):
            _build(
                question_registry=question_registry,
                question=other,
                expert=expert,
                expert_registry=registry,
            )

    def test_subject_support_and_evidence_subject_binding_are_both_fail_closed(self):
        question_registry = _question_registry()
        question = _direction_question(question_registry)
        expert = _expert(question)
        registry = _expert_registry(expert)

        with self.assertRaisesRegex(
            QuestionExpertPredictionError, "does not support prediction subject"
        ):
            _build(
                question_registry=question_registry,
                question=question,
                expert=expert,
                expert_registry=registry,
                subject_id="ASSET.ETH",
                artifacts=(_artifact(subject_ids=("ASSET.ETH",)),),
            )

        with self.assertRaisesRegex(
            QuestionExpertPredictionError, "not bound by expert input evidence"
        ):
            _build(
                question_registry=question_registry,
                question=question,
                expert=expert,
                expert_registry=registry,
                artifacts=(_artifact(subject_ids=("ASSET.ETH",)),),
            )

    def test_expert_required_artifact_feature_and_timescale_cannot_be_omitted(self):
        question_registry = _question_registry()
        question = _direction_question(question_registry)
        expert = _expert(question)
        registry = _expert_registry(expert)

        with self.assertRaisesRegex(
            QuestionExpertPredictionError, "expert-required artifact"
        ):
            _build(
                question_registry=question_registry,
                question=question,
                expert=expert,
                expert_registry=registry,
                artifacts=(),
            )

        with self.assertRaisesRegex(
            QuestionExpertPredictionError, "expert-required feature"
        ):
            _build(
                question_registry=question_registry,
                question=question,
                expert=expert,
                expert_registry=registry,
                artifacts=(_artifact(feature_families=()),),
            )

        with self.assertRaisesRegex(
            QuestionExpertPredictionError, "expert-required timescale"
        ):
            _build(
                question_registry=question_registry,
                question=question,
                expert=expert,
                expert_registry=registry,
                artifacts=(_artifact(timescales=(ExperienceTimescale.SHORT,)),),
            )

    def test_question_allowed_feature_still_cannot_bypass_expert_allowlist(self):
        question_registry = _question_registry()
        question = _direction_question(question_registry)
        self.assertIn("DERIVATIVE_MICROSTRUCTURE", question.allowed_feature_families)
        expert = _expert(question)
        registry = _expert_registry(expert)
        with self.assertRaisesRegex(
            QuestionExpertPredictionError, "outside expert allowlist"
        ):
            _build(
                question_registry=question_registry,
                question=question,
                expert=expert,
                expert_registry=registry,
                artifacts=(
                    _artifact(
                        feature_families=(
                            "SPOT_MICROSTRUCTURE",
                            "DERIVATIVE_MICROSTRUCTURE",
                        )
                    ),
                ),
            )

    def test_training_must_be_completed_and_frozen_before_prediction_and_registration(self):
        question_registry = _question_registry()
        question = _direction_question(question_registry)

        future_training = _expert(
            question,
            training_mode="FROZEN_SUPERVISED",
            training_data_cutoff_ns=T + 1,
            training_completed_at_ns=T + 2,
        )
        future_registry = _expert_registry(
            future_training,
            registered_at_ns=T - 500,
            effective_at_ns=T - 400,
        )
        with self.assertRaisesRegex(
            QuestionExpertPredictionError,
            "training data cutoff",
        ):
            _build(
                question_registry=question_registry,
                question=question,
                expert=future_training,
                expert_registry=future_registry,
            )

        late_completion = _expert(
            question,
            training_mode="FROZEN_SUPERVISED",
            training_data_cutoff_ns=T - 700,
            training_completed_at_ns=T - 450,
        )
        late_completion_registry = _expert_registry(
            late_completion,
            registered_at_ns=T - 500,
            effective_at_ns=T - 400,
        )
        with self.assertRaisesRegex(
            QuestionExpertPredictionError, "registered before training completed"
        ):
            _build(
                question_registry=question_registry,
                question=question,
                expert=late_completion,
                expert_registry=late_completion_registry,
            )

    def test_existing_question_readiness_and_no_lookahead_gates_remain_authoritative(self):
        unready_registry = _question_registry(ready=False)
        unready_question = _direction_question(unready_registry)
        expert = _expert(unready_question)
        expert_registry = _expert_registry(expert)
        with self.assertRaisesRegex(
            QuestionExpertPredictionError, "resolver-ready question"
        ):
            _build(
                question_registry=unready_registry,
                question=unready_question,
                expert=expert,
                expert_registry=expert_registry,
            )

        ready_registry = _question_registry()
        question = _direction_question(ready_registry)
        expert = _expert(question)
        expert_registry = _expert_registry(expert)
        with self.assertRaisesRegex(
            QuestionExpertPredictionError, "post-cutoff artifact"
        ):
            _build(
                question_registry=ready_registry,
                question=question,
                expert=expert,
                expert_registry=expert_registry,
                artifacts=(_artifact(known_at_ns=T + 1),),
            )
        with self.assertRaisesRegex(
            QuestionExpertPredictionError, "qualified artifact"
        ):
            _build(
                question_registry=ready_registry,
                question=question,
                expert=expert,
                expert_registry=expert_registry,
                artifacts=(_artifact(status="DEGRADED"),),
            )

    def test_wire_tamper_and_authority_escalation_are_rejected(self):
        result = _build()

        tampered = copy.deepcopy(result.to_wire())
        tampered["expert_registry"]["content_hash"] = "b" * 64
        with self.assertRaisesRegex(
            QuestionExpertPredictionError, "content hash mismatch"
        ):
            QuestionExpertPrediction.from_wire(tampered)

        escalated = copy.deepcopy(result.to_wire())
        escalated["authority"]["capital_decision"] = True
        with self.assertRaisesRegex(
            QuestionExpertPredictionError, "authority boundary"
        ):
            QuestionExpertPrediction.from_wire(escalated)


if __name__ == "__main__":
    unittest.main()
