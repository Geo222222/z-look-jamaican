from __future__ import annotations

import copy
import unittest

from autonomous_kernel.experts import (
    ExpertContractError,
    build_expert_claim,
    build_expert_contract,
    validate_expert_claim,
    validate_expert_contract,
)


HASH = "a" * 64
SNAPSHOT_HASH = "b" * 64


class ExpertContractTests(unittest.TestCase):
    def direction_contract(self):
        return build_expert_contract(
            expert_id="NAIVE_DIRECTION_BASELINE",
            version="1.0.0",
            species="NAIVE_PERSISTENCE",
            implementation_ref="autonomous_kernel.experts.direction.naive_persistence_v1",
            implementation_hash=HASH,
            model_refs=(),
            question_refs=("ECONOMIC_ROOT_DIRECTION_10S@1.0.0",),
            required_artifact_types=("MARKET_EXPERIENCE",),
            allowed_feature_families=("SPOT_MICROSTRUCTURE",),
            parameters={"rule": "TRAILING_SIGN_PERSISTENCE"},
        )

    def test_contract_binds_active_question_and_has_no_authority(self):
        contract = self.direction_contract()
        self.assertEqual("CANDIDATE", contract["lifecycle_state"])
        self.assertEqual(["ECONOMIC_ROOT_DIRECTION_10S@1.0.0"], contract["question_refs"])
        self.assertFalse(any(contract["authority"].values()))
        validate_expert_contract(contract)

    def test_historical_reversal_cannot_become_active_expert_question(self):
        with self.assertRaisesRegex(ExpertContractError, "non-active"):
            build_expert_contract(
                expert_id="BAD_REVERSAL",
                version="1.0.0",
                species="TEST",
                implementation_ref="tests.bad",
                implementation_hash=HASH,
                model_refs=(),
                question_refs=("ECONOMIC_ROOT_REVERSAL_60S@1.1.0",),
                required_artifact_types=("MARKET_EXPERIENCE", "MARKET_WIDE_EXPERIENCE", "ECONOMIC_ROOT_PATH"),
                allowed_feature_families=("SPOT_MICROSTRUCTURE", "MARKET_WIDE_CONTEXT", "ECONOMIC_ROOT_PATH"),
            )

    def test_execution_suitability_cannot_be_smuggled_into_expert_school(self):
        with self.assertRaisesRegex(ExpertContractError, "non-active"):
            build_expert_contract(
                expert_id="EXECUTION_ORACLE",
                version="1.0.0",
                species="TEST",
                implementation_ref="tests.execution",
                implementation_hash=HASH,
                model_refs=(),
                question_refs=("EXECUTION_SUITABILITY@1.0.0",),
                required_artifact_types=("HAND_EXECUTION_RESULT",),
                allowed_feature_families=("HAND_EXECUTION_RESULT",),
            )

    def test_contract_must_include_question_required_evidence(self):
        with self.assertRaisesRegex(ExpertContractError, "omits required artifacts"):
            build_expert_contract(
                expert_id="UNDER_SPECIFIED_DIRECTION",
                version="1.0.0",
                species="TEST",
                implementation_ref="tests.under_specified",
                implementation_hash=HASH,
                model_refs=(),
                question_refs=("ECONOMIC_ROOT_DIRECTION_10S@1.0.0",),
                required_artifact_types=("MARKET_WIDE_EXPERIENCE",),
                allowed_feature_families=("SPOT_MICROSTRUCTURE",),
            )

    def test_contract_cannot_allow_future_or_downstream_authority_features(self):
        with self.assertRaisesRegex(ExpertContractError, "forbidden families"):
            build_expert_contract(
                expert_id="LEAKY_DIRECTION",
                version="1.0.0",
                species="TEST",
                implementation_ref="tests.leaky",
                implementation_hash=HASH,
                model_refs=(),
                question_refs=("ECONOMIC_ROOT_DIRECTION_10S@1.0.0",),
                required_artifact_types=("MARKET_EXPERIENCE",),
                allowed_feature_families=("SPOT_MICROSTRUCTURE", "FUTURE_OUTCOME"),
            )

    def test_contract_tampering_breaks_integrity(self):
        contract = copy.deepcopy(self.direction_contract())
        contract["parameters"]["rule"] = "HINDSIGHT"
        with self.assertRaisesRegex(ExpertContractError, "content hash mismatch"):
            validate_expert_contract(contract)

    def test_binary_question_emits_probability_claim(self):
        contract = self.direction_contract()
        claim = build_expert_claim(
            contract,
            question_ref="ECONOMIC_ROOT_DIRECTION_10S@1.0.0",
            cutoff_ns=100,
            answer=0.73,
            evidence_refs=("OBS-1",),
            experience_refs=("EXP-1",),
            input_snapshot_hash=SNAPSHOT_HASH,
        )
        self.assertEqual("PROBABILITY", claim["claim_kind"])
        self.assertEqual(0.73, claim["answer"])
        self.assertEqual(10_000_000_000, claim["horizon_ns"])
        self.assertFalse(any(claim["authority"].values()))
        validate_expert_claim(contract, claim)

    def test_probability_must_be_bounded(self):
        contract = self.direction_contract()
        with self.assertRaisesRegex(ExpertContractError, "\[0,1\]"):
            build_expert_claim(
                contract,
                question_ref="ECONOMIC_ROOT_DIRECTION_10S@1.0.0",
                cutoff_ns=100,
                answer=1.2,
                evidence_refs=("OBS-1",),
                experience_refs=("EXP-1",),
                input_snapshot_hash=SNAPSHOT_HASH,
            )

    def test_claim_cannot_import_realized_outcome_or_competence(self):
        contract = self.direction_contract()
        claim = dict(build_expert_claim(
            contract,
            question_ref="ECONOMIC_ROOT_DIRECTION_10S@1.0.0",
            cutoff_ns=100,
            answer=0.4,
            evidence_refs=("OBS-1",),
            experience_refs=("EXP-1",),
            input_snapshot_hash=SNAPSHOT_HASH,
        ))
        claim["realized_outcome"] = 1
        with self.assertRaisesRegex(ExpertContractError, "realized_outcome"):
            validate_expert_claim(contract, claim)

    def test_continuous_question_requires_point_estimate(self):
        contract = build_expert_contract(
            expert_id="MEAN_MAGNITUDE_BASELINE",
            version="1.0.0",
            species="HISTORICAL_CONDITIONAL_MEAN",
            implementation_ref="autonomous_kernel.experts.magnitude.conditional_mean_v1",
            implementation_hash=HASH,
            model_refs=(),
            question_refs=("ECONOMIC_ROOT_MAGNITUDE_30S@1.0.0",),
            required_artifact_types=("MARKET_EXPERIENCE", "MARKET_WIDE_EXPERIENCE"),
            allowed_feature_families=("SPOT_MICROSTRUCTURE", "MARKET_WIDE_CONTEXT"),
        )
        claim = build_expert_claim(
            contract,
            question_ref="ECONOMIC_ROOT_MAGNITUDE_30S@1.0.0",
            cutoff_ns=100,
            answer=-4.25,
            evidence_refs=("OBS-1",),
            experience_refs=("EXP-1",),
            input_snapshot_hash=SNAPSHOT_HASH,
        )
        self.assertEqual("POINT_ESTIMATE", claim["claim_kind"])


if __name__ == "__main__":
    unittest.main()
