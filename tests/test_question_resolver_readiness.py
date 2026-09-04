from __future__ import annotations

import unittest

from autonomous_kernel.evaluation import MIDPOINT_RESOLVER_IMPLEMENTATION_REF
from autonomous_kernel.questions import (
    QuestionContractError,
    build_resolver_ready_registry,
    default_question_registry_v1,
)


T = 1_788_400_000_000_000_000


class QuestionResolverReadinessTests(unittest.TestCase):
    def test_only_implemented_questions_transition_to_resolver_ready(self) -> None:
        base = default_question_registry_v1(registered_at_ns=T, effective_at_ns=T + 1)
        implemented = {
            entry.definition.question_ref: MIDPOINT_RESOLVER_IMPLEMENTATION_REF
            for entry in base.entries
            if entry.definition.question_id in {
                "ECONOMIC_ROOT_DIRECTION_10S",
                "ECONOMIC_ROOT_MAGNITUDE_30S",
            }
        }
        ready = build_resolver_ready_registry(
            base,
            version="1.1.0",
            known_at_ns=T + 2,
            effective_at_ns=T + 3,
            resolver_implementations=implemented,
        )
        states = {entry.definition.question_id: entry.lifecycle_state for entry in ready.entries}
        self.assertEqual("RESOLVER_READY", states["ECONOMIC_ROOT_DIRECTION_10S"])
        self.assertEqual("RESOLVER_READY", states["ECONOMIC_ROOT_MAGNITUDE_30S"])
        self.assertEqual("DEFINED", states["ECONOMIC_ROOT_VOLATILITY_60S"])
        self.assertEqual("DEFINED", states["SPOT_DERIVATIVE_BASIS_CHANGE_5M"])
        self.assertNotEqual(base.content_hash(), ready.content_hash())
        for entry in ready.entries:
            if entry.lifecycle_state == "RESOLVER_READY":
                self.assertEqual(MIDPOINT_RESOLVER_IMPLEMENTATION_REF, entry.resolver_implementation_ref)
                self.assertEqual((), entry.qualification_evidence_refs)

    def test_readiness_cannot_reference_unknown_question_or_rewrite_implementation(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
