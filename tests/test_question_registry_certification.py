from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import unittest

from autonomous_kernel.evaluation import (
    FIXED_GRID_RESOLVER_IMPLEMENTATION_REF,
    LIQUIDITY_RESOLVER_IMPLEMENTATION_REF,
    MIDPOINT_RESOLVER_IMPLEMENTATION_REF,
    REGIME_ENDPOINT_IMPLEMENTATION_REF,
    REGIME_PERSISTENCE_IMPLEMENTATION_REF,
    RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF,
    REVERSAL_ROOT_PATH_RESOLVER_IMPLEMENTATION_REF,
)
from autonomous_kernel.operations import canonical_hash
from autonomous_kernel.questions import (
    DEFERRED_QUESTION_FAMILIES_V1,
    QUESTION_REGISTRY_V1_QUALIFIED,
    QUESTION_REGISTRY_V1_QUALIFIED_VERSION,
    REVERSAL_QUESTION_V1_1_REF,
    REVERSAL_QUESTION_V1_REF,
    QuestionContractError,
    QuestionFamily,
    QuestionRegistryEntry,
    build_question_registry_v1_qualified,
    certify_question_registry_v1,
    default_question_registry_v1,
    material_question_registry_evidence,
    resolver_ready_refs_v1_qualified,
    validate_question_registry_v1_certificate,
    verify_question_registry_v1_certificate,
)


T = 1_788_500_000_000_000_000


EXPECTED_READY = {
    "ECONOMIC_ROOT_DIRECTION_10S@1.0.0": MIDPOINT_RESOLVER_IMPLEMENTATION_REF,
    "ECONOMIC_ROOT_MAGNITUDE_30S@1.0.0": MIDPOINT_RESOLVER_IMPLEMENTATION_REF,
    "ECONOMIC_ROOT_VOLATILITY_60S@1.0.0": FIXED_GRID_RESOLVER_IMPLEMENTATION_REF,
    "ECONOMIC_ROOT_FRAGILITY_MAE_60S@1.0.0": FIXED_GRID_RESOLVER_IMPLEMENTATION_REF,
    "ECONOMIC_ROOT_LIQUIDITY_DETERIORATION_30S@1.0.0": LIQUIDITY_RESOLVER_IMPLEMENTATION_REF,
    "SPOT_DERIVATIVE_BASIS_CHANGE_5M@1.0.0": RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF,
    "SPOT_DERIVATIVE_RELATIVE_VALUE_CONVERGENCE_5M@1.0.0": RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF,
    "MARKET_DIRECTION_REGIME_15M@1.0.0": REGIME_ENDPOINT_IMPLEMENTATION_REF,
    "MARKET_REGIME_PERSISTENCE_5M@1.0.0": REGIME_PERSISTENCE_IMPLEMENTATION_REF,
    REVERSAL_QUESTION_V1_1_REF: REVERSAL_ROOT_PATH_RESOLVER_IMPLEMENTATION_REF,
}


class QuestionRegistryCertificationTests(unittest.TestCase):
    def _snapshot(self):
        base = default_question_registry_v1(
            registered_at_ns=T,
            effective_at_ns=T + 1,
        )
        return build_question_registry_v1_qualified(
            base,
            known_at_ns=T + 2,
            effective_at_ns=T + 3,
        )

    def test_exact_resolver_exports_form_the_frozen_examination_surface(self):
        self.assertEqual(EXPECTED_READY, resolver_ready_refs_v1_qualified())
        self.assertEqual((QuestionFamily.EXECUTION_SUITABILITY.value,), DEFERRED_QUESTION_FAMILIES_V1)

    def test_complete_registry_has_ten_ready_questions_and_historical_reversal_v1(self):
        snapshot = self._snapshot()
        self.assertEqual(QUESTION_REGISTRY_V1_QUALIFIED_VERSION, snapshot.version)
        by_ref = {entry.definition.question_ref: entry for entry in snapshot.entries}
        ready = {
            ref: entry.resolver_implementation_ref
            for ref, entry in by_ref.items()
            if entry.lifecycle_state == "RESOLVER_READY"
        }
        self.assertEqual(EXPECTED_READY, ready)
        self.assertEqual(11, len(snapshot.entries))

        historical = by_ref[REVERSAL_QUESTION_V1_REF]
        self.assertEqual("DEFINED", historical.lifecycle_state)
        self.assertIsNone(historical.resolver_implementation_ref)
        self.assertEqual((), historical.qualification_evidence_refs)

        self.assertFalse(any(
            entry.definition.family is QuestionFamily.EXECUTION_SUITABILITY
            for entry in snapshot.entries
        ))

    def test_certificate_is_deterministic_tamper_evident_and_replayable(self):
        snapshot = self._snapshot()
        first = certify_question_registry_v1(snapshot)
        second = certify_question_registry_v1(snapshot)
        self.assertEqual(first, second)
        self.assertEqual(QUESTION_REGISTRY_V1_QUALIFIED, first["certification_id"])
        self.assertEqual(snapshot.content_hash(), first["registry"]["content_hash"])
        self.assertEqual(10, len(first["resolver_ready_questions"]))
        validate_question_registry_v1_certificate(first)
        verify_question_registry_v1_certificate(snapshot, first)

        tampered = deepcopy(first)
        tampered["resolver_ready_questions"][0]["horizon_ns"] += 1
        with self.assertRaisesRegex(QuestionContractError, "content hash mismatch"):
            validate_question_registry_v1_certificate(tampered)

        tampered["integrity"]["content_hash"] = canonical_hash({
            key: item for key, item in tampered.items() if key != "integrity"
        })
        validate_question_registry_v1_certificate(tampered)
        with self.assertRaisesRegex(QuestionContractError, "does not match exact registry"):
            verify_question_registry_v1_certificate(snapshot, tampered)

    def test_every_ready_question_binds_identity_horizon_outcome_and_resolver(self):
        certificate = certify_question_registry_v1(self._snapshot())
        for item in certificate["resolver_ready_questions"]:
            self.assertTrue(item["question_ref"])
            self.assertEqual(64, len(item["definition_hash"]))
            self.assertGreater(item["horizon_ns"], 0)
            self.assertTrue(item["outcome_metric_id"])
            self.assertTrue(item["resolver_policy_id"])
            self.assertEqual(EXPECTED_READY[item["question_ref"]], item["resolver_implementation_ref"])

        guarantees = certificate["guarantees"]
        self.assertTrue(all(guarantees.values()))
        authority = certificate["authority"]
        self.assertTrue(authority["defines_examination_truth"])
        for key in (
            "selects_model",
            "claims_model_competence",
            "sets_adaptive_weights",
            "capital_decision",
            "risk_authorization",
            "external_execution",
        ):
            self.assertFalse(authority[key])

    def test_definition_or_leakage_guard_cannot_mutate_after_freeze(self):
        snapshot = self._snapshot()
        entries = list(snapshot.entries)
        index = next(
            i for i, entry in enumerate(entries)
            if entry.definition.question_ref == "ECONOMIC_ROOT_DIRECTION_10S@1.0.0"
        )
        definition = entries[index].definition
        weakened = replace(
            definition,
            forbidden_feature_families=tuple(
                item for item in definition.forbidden_feature_families
                if item != "POST_CUTOFF_MARKET_DATA"
            ),
        )
        entries[index] = replace(entries[index], definition=weakened)
        mutated = replace(snapshot, entries=tuple(entries))
        with self.assertRaisesRegex(QuestionContractError, "definition changed retrospectively"):
            certify_question_registry_v1(mutated)

    def test_relationship_normalization_contract_cannot_be_weakened(self):
        snapshot = self._snapshot()
        entries = list(snapshot.entries)
        for target in (
            "SPOT_DERIVATIVE_BASIS_CHANGE_5M@1.0.0",
            "SPOT_DERIVATIVE_RELATIVE_VALUE_CONVERGENCE_5M@1.0.0",
        ):
            index = next(i for i, entry in enumerate(entries) if entry.definition.question_ref == target)
            definition = entries[index].definition
            weakened = replace(
                definition,
                parameters=dict(definition.parameters, quote_unit_policy="ALLOW_UNPROVEN_QUOTE_EQUIVALENCE"),
            )
            changed_entries = list(entries)
            changed_entries[index] = replace(entries[index], definition=weakened)
            mutated = replace(snapshot, entries=tuple(changed_entries))
            with self.assertRaises(QuestionContractError):
                certify_question_registry_v1(mutated)

    def test_regime_truth_and_reversal_path_contracts_are_frozen(self):
        snapshot = self._snapshot()
        by_ref = {entry.definition.question_ref: entry for entry in snapshot.entries}

        for target in (
            "MARKET_DIRECTION_REGIME_15M@1.0.0",
            "MARKET_REGIME_PERSISTENCE_5M@1.0.0",
        ):
            definition = by_ref[target].definition
            self.assertIn("MARKET_WIDE_EXPERIENCE", definition.required_artifact_types)
            self.assertIn("MARKET_WIDE_CONTEXT", definition.required_feature_families)

        reversal = by_ref[REVERSAL_QUESTION_V1_1_REF].definition
        self.assertIn("ECONOMIC_ROOT_PATH", reversal.required_artifact_types)
        self.assertIn("ECONOMIC_ROOT_PATH", reversal.required_feature_families)
        self.assertEqual("QUALIFIED", reversal.parameters["root_path_status"])
        self.assertEqual(
            "EXACT_PREDICTION_BOUND_SPOT_INSTRUMENT",
            reversal.parameters["reference_instrument_policy"],
        )

        weakened = replace(
            reversal,
            parameters=dict(reversal.parameters, root_path_status="DEGRADED_ALLOWED"),
        )
        entries = tuple(
            replace(entry, definition=weakened)
            if entry.definition.question_ref == REVERSAL_QUESTION_V1_1_REF
            else entry
            for entry in snapshot.entries
        )
        with self.assertRaises(QuestionContractError):
            certify_question_registry_v1(replace(snapshot, entries=entries))

    def test_historical_reversal_cannot_be_retroactively_promoted(self):
        snapshot = self._snapshot()
        entries = tuple(
            replace(
                entry,
                lifecycle_state="RESOLVER_READY",
                resolver_implementation_ref="retroactive.reversal.v1",
            )
            if entry.definition.question_ref == REVERSAL_QUESTION_V1_REF
            else entry
            for entry in snapshot.entries
        )
        mutated = replace(snapshot, entries=entries)
        with self.assertRaises(QuestionContractError):
            certify_question_registry_v1(mutated)

    def test_registry_version_and_question_set_are_frozen(self):
        snapshot = self._snapshot()
        with self.assertRaisesRegex(QuestionContractError, "version is not frozen"):
            certify_question_registry_v1(replace(snapshot, version="1.2.1-moving-target"))

        reduced = replace(snapshot, entries=snapshot.entries[:-1])
        with self.assertRaisesRegex(QuestionContractError, "question set changed"):
            certify_question_registry_v1(reduced)

    def test_registry_can_be_committed_to_book_without_copying_learning_journals(self):
        snapshot = self._snapshot()
        certificate = certify_question_registry_v1(snapshot)
        evidence = material_question_registry_evidence(snapshot)
        self.assertEqual("ZLJ.QUESTION_REGISTRY", evidence.event_type)
        self.assertEqual("ANALYTICAL", evidence.evidence_class)
        self.assertIn(snapshot.content_hash(), certificate["registry"]["content_hash"])
        self.assertNotIn("question_predictions.jsonl", evidence.payload)
        self.assertNotIn("question_outcomes.jsonl", evidence.payload)


if __name__ == "__main__":
    unittest.main()
