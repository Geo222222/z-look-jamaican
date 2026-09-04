from __future__ import annotations

import unittest

from autonomous_kernel.experience.contracts import ExperienceTimescale
from autonomous_kernel.experts import (
    ExpertAdapterError,
    build_baseline_expert_school,
    implemented_baseline_expert_contracts,
    operational_expert_inventory,
    question_prediction_to_expert_claim,
)
from autonomous_kernel.prediction.question_bound import PredictionArtifactRef, build_question_bound_prediction
from autonomous_kernel.questions.catalog import default_question_registry_v1, question_catalog_v1
from autonomous_kernel.questions.certification import build_question_registry_v1_qualified


class ExpertPredictionAdapterTests(unittest.TestCase):
    def _direction_prediction(self, model_ref):
        base = default_question_registry_v1(registered_at_ns=0, effective_at_ns=0)
        registry = build_question_registry_v1_qualified(base, known_at_ns=0, effective_at_ns=0)
        question = next(item for item in question_catalog_v1() if item.question_id == "ECONOMIC_ROOT_DIRECTION_10S")
        artifact = PredictionArtifactRef(
            artifact_type="MARKET_EXPERIENCE",
            artifact_id="ME-BTC-001",
            content_hash="a" * 64,
            known_at_ns=100,
            status="QUALIFIED",
            timescales=(ExperienceTimescale.MICRO,),
            feature_families=("SPOT_MICROSTRUCTURE",),
            subject_ids=("BTC-USD",),
        )
        return build_question_bound_prediction(
            registry=registry,
            question=question,
            subject_id="BTC-USD",
            mode="PROSPECTIVE_SHADOW",
            evidence_class="FORWARD_EVALUABLE",
            cutoff_at_ns=100,
            created_at_ns=101,
            answer={"value": 1, "probability_1": "0.73"},
            model_refs=(model_ref,),
            artifact_refs=(artifact,),
        )

    def test_existing_baselines_create_six_executable_expert_roles(self):
        contracts = implemented_baseline_expert_contracts()
        self.assertEqual(len(contracts), 6)
        self.assertEqual(sum(1 for item in contracts if "DIRECTION" in item["expert_id"]), 3)
        self.assertEqual(sum(1 for item in contracts if "MAGNITUDE" in item["expert_id"]), 3)
        self.assertTrue(all(item["model_refs"] for item in contracts))
        inventory = operational_expert_inventory()
        self.assertEqual(inventory["implemented_expert_count"], 6)
        self.assertFalse(inventory["earned_competence"])
        self.assertFalse(inventory["capital_authority"])

    def test_question_bound_prediction_becomes_exact_expert_claim(self):
        contract = next(item for item in implemented_baseline_expert_contracts() if "DIRECTION" in item["expert_id"])
        prediction = self._direction_prediction(contract["model_refs"][0])
        claim = question_prediction_to_expert_claim(contract, prediction)
        self.assertEqual(claim["question_ref"], prediction.question_ref)
        self.assertEqual(claim["question_definition_hash"], prediction.question_definition_hash)
        self.assertEqual(claim["horizon_ns"], prediction.horizon_ns)
        self.assertAlmostEqual(claim["answer"], 0.73)
        self.assertTrue(any(value.startswith("question-prediction:") for value in claim["evidence_refs"]))
        self.assertTrue(any(value.startswith("experience:MARKET_EXPERIENCE:") for value in claim["experience_refs"]))
        self.assertFalse(claim["authority"]["capital_decision"])

    def test_model_backed_prediction_cannot_enter_abstract_curriculum_contract(self):
        abstract = next(item for item in build_baseline_expert_school()["experts"] if item["question_refs"] == ["ECONOMIC_ROOT_DIRECTION_10S@1.0.0"])
        operational = next(item for item in implemented_baseline_expert_contracts() if "DIRECTION" in item["expert_id"])
        prediction = self._direction_prediction(operational["model_refs"][0])
        with self.assertRaises(ExpertAdapterError):
            question_prediction_to_expert_claim(abstract, prediction)

    def test_wrong_model_identity_is_rejected(self):
        contract = next(item for item in implemented_baseline_expert_contracts() if "DIRECTION" in item["expert_id"])
        prediction = self._direction_prediction("UNBOUND-MODEL@1.0.0")
        with self.assertRaises(ExpertAdapterError):
            question_prediction_to_expert_claim(contract, prediction)


if __name__ == "__main__":
    unittest.main()
