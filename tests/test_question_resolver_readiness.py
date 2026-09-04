from __future__ import annotations

import unittest

from autonomous_kernel.evaluation import (
    FIXED_GRID_RESOLVER_IMPLEMENTATION_REF,
    LIQUIDITY_RESOLVER_IMPLEMENTATION_REF,
    MIDPOINT_RESOLVER_IMPLEMENTATION_REF,
    REGIME_ENDPOINT_IMPLEMENTATION_REF,
    REGIME_PERSISTENCE_IMPLEMENTATION_REF,
    RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF,
)
from autonomous_kernel.questions import (
    RESOLVER_READY_IMPLEMENTATIONS_V1,
    UNRESOLVED_QUESTION_IDS_V1,
    QuestionContractError,
    build_resolver_ready_registry,
    build_resolver_ready_registry_v1,
    default_question_registry_v1,
    resolver_ready_refs_v1,
)


T = 1_788_400_000_000_000_000


EXPECTED_IMPLEMENTATIONS = {
    "ECONOMIC_ROOT_DIRECTION_10S": MIDPOINT_RESOLVER_IMPLEMENTATION_REF,
    "ECONOMIC_ROOT_MAGNITUDE_30S": MIDPOINT_RESOLVER_IMPLEMENTATION_REF,
    "ECONOMIC_ROOT_VOLATILITY_60S": FIXED_GRID_RESOLVER_IMPLEMENTATION_REF,
    "ECONOMIC_ROOT_FRAGILITY_MAE_60S": FIXED_GRID_RESOLVER_IMPLEMENTATION_REF,
    "ECONOMIC_ROOT_LIQUIDITY_DETERIORATION_30S": LIQUIDITY_RESOLVER_IMPLEMENTATION_REF,
    "SPOT_DERIVATIVE_BASIS_CHANGE_5M": RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF,
    "SPOT_DERIVATIVE_RELATIVE_VALUE_CONVERGENCE_5M": RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF,
    "MARKET_DIRECTION_REGIME_15M": REGIME_ENDPOINT_IMPLEMENTATION_REF,
    "MARKET_REGIME_PERSISTENCE_5M": REGIME_PERSISTENCE_IMPLEMENTATION_REF,
}


class QuestionResolverReadinessTests(unittest.TestCase):
    def test_protocol_mapping_matches_actual_implemented_resolver_exports(self) -> None:
        self.assertEqual(EXPECTED_IMPLEMENTATIONS, RESOLVER_READY_IMPLEMENTATIONS_V1)
        self.assertEqual(("ECONOMIC_ROOT_REVERSAL_60S",), UNRESOLVED_QUESTION_IDS_V1)

    def test_canonical_v1_transition_promotes_only_mechanically_implemented_questions(self) -> None:
        base = default_question_registry_v1(registered_at_ns=T, effective_at_ns=T + 1)
        ready = build_resolver_ready_registry_v1(
            base,
            version="1.1.0-resolver-ready",
            known_at_ns=T + 2,
            effective_at_ns=T + 3,
        )
        by_id = {entry.definition.question_id: entry for entry in ready.entries}
        self.assertEqual(set(EXPECTED_IMPLEMENTATIONS), {
            question_id
            for question_id, entry in by_id.items()
            if entry.lifecycle_state == "RESOLVER_READY"
        })
        for question_id, implementation in EXPECTED_IMPLEMENTATIONS.items():
            entry = by_id[question_id]
            self.assertEqual("RESOLVER_READY", entry.lifecycle_state)
            self.assertEqual(implementation, entry.resolver_implementation_ref)
            self.assertEqual((), entry.qualification_evidence_refs)

        reversal = by_id["ECONOMIC_ROOT_REVERSAL_60S"]
        self.assertEqual("DEFINED", reversal.lifecycle_state)
        self.assertIsNone(reversal.resolver_implementation_ref)
        self.assertEqual((), reversal.qualification_evidence_refs)
        self.assertNotEqual(base.content_hash(), ready.content_hash())

    def test_resolver_ready_refs_use_exact_question_versions_from_snapshot(self) -> None:
        base = default_question_registry_v1(registered_at_ns=T, effective_at_ns=T + 1)
        refs = resolver_ready_refs_v1(base)
        self.assertEqual(len(EXPECTED_IMPLEMENTATIONS), len(refs))
        by_ref = {entry.definition.question_ref: entry.definition.question_id for entry in base.entries}
        self.assertEqual(set(EXPECTED_IMPLEMENTATIONS), {by_ref[ref] for ref in refs})
        for question_ref, implementation in refs.items():
            self.assertEqual(EXPECTED_IMPLEMENTATIONS[by_ref[question_ref]], implementation)

    def test_generic_readiness_cannot_reference_unknown_question_or_rewrite_implementation(self) -> None:
        base = default_question_registry_v1(registered_at_ns=T, effective_at_ns=T + 1)
        with self.assertRaisesRegex(QuestionContractError, "unknown question"):
            build_resolver_ready_registry(
                base,
                version="bad",
                known_at_ns=T + 2,
                effective_at_ns=T + 3,
                resolver_implementations={"DOES_NOT_EXIST@1": "resolver-v1"},
            )
        direction = next(
            entry.definition.question_ref
            for entry in base.entries
            if entry.definition.question_id == "ECONOMIC_ROOT_DIRECTION_10S"
        )
        first = build_resolver_ready_registry(
            base,
            version="1.1.0",
            known_at_ns=T + 2,
            effective_at_ns=T + 3,
            resolver_implementations={direction: MIDPOINT_RESOLVER_IMPLEMENTATION_REF},
        )
        with self.assertRaisesRegex(QuestionContractError, "silently change"):
            build_resolver_ready_registry(
                first,
                version="1.1.1",
                known_at_ns=T + 4,
                effective_at_ns=T + 5,
                resolver_implementations={direction: "different-resolver"},
            )

    def test_canonical_readiness_rejects_partial_registry_that_hides_expected_questions(self) -> None:
        base = default_question_registry_v1(registered_at_ns=T, effective_at_ns=T + 1)
        partial = type(base)(
            registry_id=base.registry_id,
            version="partial",
            entries=tuple(entry for entry in base.entries if entry.definition.question_id != "SPOT_DERIVATIVE_BASIS_CHANGE_5M"),
            known_at_ns=base.known_at_ns,
            effective_at_ns=base.effective_at_ns,
        )
        with self.assertRaisesRegex(QuestionContractError, "missing questions"):
            resolver_ready_refs_v1(partial)


if __name__ == "__main__":
    unittest.main()
