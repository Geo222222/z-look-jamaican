from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.experts import (
    build_baseline_expert_school,
    build_expert_claim,
    score_expert_claim,
)
from autonomous_kernel.intelligence import IntelligenceRuntime, IntelligenceRuntimeError
from autonomous_kernel.operator.intelligence_projection import expert_intelligence_projection


class ExpertIntelligenceRuntimeTests(unittest.TestCase):
    def _contract_and_claim(self):
        school = build_baseline_expert_school()
        contract = next(item for item in school["experts"] if item["expert_id"].startswith("ECONOMIC_ROOT_DIRECTION_10S_"))
        claim = build_expert_claim(
            contract,
            question_ref=contract["question_refs"][0],
            cutoff_ns=1_000_000_000,
            answer=0.75,
            evidence_refs=("evidence:runtime:a",),
            experience_refs=("experience:runtime:a",),
            input_snapshot_hash="a" * 64,
        )
        return contract, claim

    def test_runtime_persists_claim_score_and_rebuilds_competence(self):
        contract, claim = self._contract_and_claim()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = IntelligenceRuntime(root)
            runtime.record_claim(contract, claim, occurred_at_ns=1_000_000_001)
            score = score_expert_claim(contract, claim, True, resolved_at_ns=12_000_000_000, context={"regime": "TREND"})
            runtime.record_score(score, occurred_at_ns=12_000_000_001)
            competence = runtime.rebuild_competence(known_at_ns=13_000_000_000)
            state = runtime.state()
            self.assertEqual(state["event_count"], 3)
            self.assertEqual(len(state["claims"]), 1)
            self.assertEqual(len(state["scores"]), 1)
            self.assertEqual(competence["entry_count"], 1)
            rebuilt = runtime.rebuild_state()
            self.assertEqual(rebuilt["last_event_hash"], state["last_event_hash"])

    def test_runtime_rejects_score_for_unknown_claim(self):
        contract, claim = self._contract_and_claim()
        score = score_expert_claim(contract, claim, True, resolved_at_ns=12_000_000_000)
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            with self.assertRaises(IntelligenceRuntimeError):
                runtime.record_score(score, occurred_at_ns=12_000_000_001)

    def test_operator_projection_distinguishes_implementation_from_earned_competence(self):
        with tempfile.TemporaryDirectory() as temporary:
            projection = expert_intelligence_projection(Path(temporary))
            self.assertEqual(projection["construction"]["intelligence_publication"], "BUILT")
            self.assertEqual(projection["construction"]["operational_prediction_adapter"], "BUILT")
            self.assertEqual(projection["qualification"]["expert_population"], "IMPLEMENTED_CANDIDATES_PRESENT")
            self.assertGreater(projection["school"]["implemented_expert_count"], 0)
            self.assertGreater(projection["school"]["curriculum_expert_count"], projection["school"]["implemented_expert_count"])
            self.assertEqual(projection["qualification"]["earned_competence"], "NOT_YET_EARNED")
            self.assertEqual(projection["qualification"]["benjamin_handoff"], "NO_RUNTIME_PUBLICATION")
            self.assertEqual(projection["qualification"]["live_capital_authority"], "NONE")


if __name__ == "__main__":
    unittest.main()
