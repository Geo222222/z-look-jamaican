from __future__ import annotations

import copy
import unittest

from autonomous_kernel.research.falsification import (
    FALSIFICATION_AUTHORITY,
    build_falsification_policy,
    evaluate_falsification,
)
from autonomous_kernel.research import ResearchContractError


class ResearchFalsificationTests(unittest.TestCase):
    def _policy(self):
        return build_falsification_policy(
            policy_id="FALSIFICATION-V1",
            cost_scenarios_bps=(5, 10, 20, 40),
            primary_cost_bps=20,
            multiplicity_count=14,
            minimum_observations=12,
            minimum_folds=3,
            minimum_positive_fold_fraction=0.66,
            maximum_drawdown=0.15,
            maximum_best_fold_profit_share=0.6,
            maximum_adjusted_p_value=0.05,
        )

    def test_policy_is_hash_bound_and_has_no_promotion_authority(self):
        policy = self._policy()
        self.assertFalse(policy["authority"]["qualifies_model"])
        self.assertFalse(policy["authority"]["promotes_model"])
        self.assertTrue(policy["authority"]["may_falsify_candidate"])
        self.assertEqual(policy["authority"], FALSIFICATION_AUTHORITY)

    def test_strong_cross_fold_candidate_can_only_survive_falsification(self):
        observations = []
        for fold in ("F1", "F2", "F3"):
            for _ in range(5):
                observations.append({"fold_id": fold, "gross_return": 0.02, "trading_sides": 2})
        result = evaluate_falsification(
            candidate_ref="MODEL-X@1",
            question_ref="DIRECTION@1",
            policy=self._policy(),
            observations=observations,
            evaluated_at_ns=100,
            evidence_refs=("walkforward:1",),
        )
        self.assertEqual(result["decision"], "SURVIVED_FALSIFICATION")
        self.assertFalse(result["authority"]["qualifies_model"])
        self.assertFalse(result["authority"]["promotes_model"])
        self.assertEqual(result["gate_failures"], [])

    def test_costs_can_falsify_candidate_that_looks_positive_gross(self):
        observations = []
        for fold in ("F1", "F2", "F3"):
            for _ in range(5):
                observations.append({"fold_id": fold, "gross_return": 0.002, "trading_sides": 2})
        result = evaluate_falsification(
            candidate_ref="MODEL-X@1",
            question_ref="DIRECTION@1",
            policy=self._policy(),
            observations=observations,
            evaluated_at_ns=100,
            evidence_refs=("walkforward:2",),
        )
        self.assertEqual(result["decision"], "FALSIFIED")
        self.assertIn("minimum_mean_net_return", result["gate_failures"])

    def test_thin_samples_and_fold_concentration_fail_closed(self):
        observations = [
            {"fold_id": "F1", "gross_return": 0.04, "trading_sides": 2},
            {"fold_id": "F1", "gross_return": 0.04, "trading_sides": 2},
            {"fold_id": "F2", "gross_return": -0.001, "trading_sides": 2},
        ]
        result = evaluate_falsification(
            candidate_ref="MODEL-X@1",
            question_ref="DIRECTION@1",
            policy=self._policy(),
            observations=observations,
            evaluated_at_ns=100,
            evidence_refs=("walkforward:3",),
        )
        self.assertEqual(result["decision"], "FALSIFIED")
        self.assertIn("minimum_observations", result["gate_failures"])
        self.assertIn("minimum_folds", result["gate_failures"])

    def test_policy_tampering_is_rejected(self):
        policy = copy.deepcopy(self._policy())
        policy["maximum_drawdown"] = 1.0
        with self.assertRaisesRegex(ResearchContractError, "integrity"):
            evaluate_falsification(
                candidate_ref="MODEL-X@1",
                question_ref="DIRECTION@1",
                policy=policy,
                observations=({"fold_id": "F1", "gross_return": 0.01, "trading_sides": 2},),
                evaluated_at_ns=100,
                evidence_refs=("walkforward:4",),
            )

    def test_bad_observations_and_duplicate_evidence_refs_are_rejected(self):
        policy = self._policy()
        with self.assertRaises(ResearchContractError):
            evaluate_falsification(
                candidate_ref="MODEL-X@1",
                question_ref="DIRECTION@1",
                policy=policy,
                observations=({"fold_id": "F1", "gross_return": float("nan"), "trading_sides": 2},),
                evaluated_at_ns=100,
                evidence_refs=("same", "same"),
            )


if __name__ == "__main__":
    unittest.main()
