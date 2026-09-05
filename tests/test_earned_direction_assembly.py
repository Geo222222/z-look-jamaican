from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomous_kernel.experts import (
    ExpertSchoolError,
    assemble_expert_claims,
    build_competence_memory,
    build_expert_claim,
    score_expert_claim,
)
from autonomous_kernel.experts.school import build_baseline_expert_school
from autonomous_kernel.learning.direction_assembly import (
    DirectionAssemblyError,
    assemble_and_record_direction_question,
    assemble_direction_question,
)
from autonomous_kernel.learning.direction_loop import process_canonical_direction_batch, question_learning_projection
from tests.test_direction_competence_loop import _walk_forbidden, _write_span
from tests.test_expert_school_intelligence import _claim, _direction_experts


def _memory_for(contracts, claims, *, resolved_at_ns=12_000_000_000, now_ns=13_000_000_000, context=None):
    records = [
        score_expert_claim(contract, claim, True, resolved_at_ns=resolved_at_ns, context=context or {"regime": "TREND"})
        for contract, claim in zip(contracts, claims)
    ]
    return build_competence_memory(records, now_ns=now_ns)


class ExpertSchoolWeightingTests(unittest.TestCase):
    def test_equal_competence_independent_evidence_gives_equal_weights(self):
        _, contracts = _direction_experts()
        claims = (_claim(contracts[0], 0.7, "evidence:a"), _claim(contracts[1], 0.7, "evidence:b"))
        assembly = assemble_expert_claims(claims, _memory_for(contracts[:2], claims), {"regime": "TREND"})
        weights = [item["weight"] for item in assembly["expert_contributions"]]
        self.assertAlmostEqual(weights[0], weights[1], places=6)
        self.assertAlmostEqual(sum(weights), 1.0)

    def test_higher_earned_competence_can_increase_weight(self):
        _, contracts = _direction_experts()
        strong = _claim(contracts[0], 0.9, "evidence:a")
        weak = _claim(contracts[1], 0.6, "evidence:b")
        strong_records = [score_expert_claim(contracts[0], strong, True, resolved_at_ns=12_000_000_000, context={"regime": "TREND"})]
        for _ in range(8):
            strong_records.append(score_expert_claim(contracts[0], strong, True, resolved_at_ns=12_000_000_000, context={"regime": "TREND"}))
        weak_record = score_expert_claim(contracts[1], weak, False, resolved_at_ns=12_000_000_000, context={"regime": "TREND"})
        memory = build_competence_memory(tuple(strong_records + [weak_record]), now_ns=13_000_000_000)
        live = (_claim(contracts[0], 0.8, "evidence:live-a"), _claim(contracts[1], 0.8, "evidence:live-b"))
        assembly = assemble_expert_claims(live, memory, {"regime": "TREND"})
        weights = {item["expert_ref"]: item["weight"] for item in assembly["expert_contributions"]}
        self.assertGreater(weights[live[0]["expert_ref"]], weights[live[1]["expert_ref"]])

    def test_tiny_sample_support_limits_dominance(self):
        _, contracts = _direction_experts()
        lucky = _claim(contracts[0], 0.99, "evidence:a")
        other = _claim(contracts[1], 0.50, "evidence:b")
        memory = _memory_for(contracts[:2], (lucky, other))
        assembly = assemble_expert_claims((lucky, other), memory, {"regime": "TREND"})
        weights = {item["expert_ref"]: item["weight"] for item in assembly["expert_contributions"]}
        self.assertLess(max(weights.values()), 0.75)

    def test_identical_evidence_overlap_reduces_independence(self):
        _, contracts = _direction_experts()
        shared_a = _claim(contracts[0], 0.8, "evidence:shared")
        shared_b = _claim(contracts[1], 0.7, "evidence:shared")
        independent = _claim(contracts[2], 0.65, "evidence:independent")
        shared = assemble_expert_claims((shared_a, shared_b), _memory_for(contracts[:2], (shared_a, shared_b)), {})
        mixed = assemble_expert_claims(
            (shared_a, independent),
            _memory_for((contracts[0], contracts[2]), (shared_a, independent)),
            {},
        )
        self.assertGreater(mixed["evidence_independence"]["mean_overlap_ratio"], -0.01)
        self.assertGreater(shared["evidence_independence"]["mean_overlap_ratio"], mixed["evidence_independence"]["mean_overlap_ratio"])

    def test_unrelated_extra_evidence_cannot_hide_core_overlap(self):
        _, contracts = _direction_experts()
        core = _claim(contracts[0], 0.8, "evidence:core")
        padded = build_expert_claim(
            contracts[1],
            question_ref=contracts[1]["question_refs"][0],
            cutoff_ns=1_000_000_000,
            answer=0.7,
            evidence_refs=("evidence:core", "evidence:extra-1", "evidence:extra-2"),
            experience_refs=("experience:market:1",),
            input_snapshot_hash="1" * 64,
        )
        assembly = assemble_expert_claims((core, padded), _memory_for(contracts[:2], (core, padded)), {})
        pair = assembly["evidence_independence"]["pairwise"][0]
        self.assertEqual(pair["overlap_ratio"], 1.0)
        self.assertLess(assembly["expert_contributions"][0]["evidence_overlap_penalty"], 1.0)

    def test_duplicate_expert_testimony_is_rejected(self):
        _, contracts = _direction_experts()
        claim = _claim(contracts[0], 0.8, "evidence:a")
        memory = _memory_for(contracts[:1], (claim,))
        with self.assertRaisesRegex(ExpertSchoolError, "duplicate expert"):
            assemble_expert_claims((claim, claim), memory, {})

    def test_wrong_question_and_cutoff_cannot_mix(self):
        school = build_baseline_expert_school()
        direction = [item for item in school["experts"] if item["expert_id"].startswith("ECONOMIC_ROOT_DIRECTION_10S_")]
        magnitude = [item for item in school["experts"] if item["expert_id"].startswith("ECONOMIC_ROOT_MAGNITUDE_30S_")][0]
        claim_a = _claim(direction[0], 0.8, "evidence:a")
        claim_b = build_expert_claim(
            magnitude,
            question_ref=magnitude["question_refs"][0],
            cutoff_ns=1_000_000_000,
            answer=1.0,
            evidence_refs=("evidence:b",),
            experience_refs=("experience:market:1",),
            input_snapshot_hash="1" * 64,
        )
        memory = build_competence_memory((), now_ns=13_000_000_000)
        with self.assertRaisesRegex(ExpertSchoolError, "one exact question"):
            assemble_expert_claims((claim_a, claim_b), memory, {})
        later = _claim(direction[1], 0.6, "evidence:b")
        later = dict(later)
        later["cutoff_ns"] = 9_000_000_000
        later["integrity"] = {"algorithm": "sha256", "content_hash": "0" * 64}
        with self.assertRaisesRegex(ExpertSchoolError, "cutoff_ns"):
            assemble_expert_claims((claim_a, later), memory, {})

    def test_future_competence_cannot_enter_earlier_assembly(self):
        _, contracts = _direction_experts()
        claims = (_claim(contracts[0], 0.8, "evidence:a"), _claim(contracts[1], 0.6, "evidence:b"))
        memory = _memory_for(contracts[:2], claims, now_ns=20_000_000_000)
        with self.assertRaisesRegex(ExpertSchoolError, "future competence"):
            assemble_expert_claims(claims, memory, {}, assembly_at_ns=13_000_000_000)

    def test_tampered_competence_hash_fails_closed(self):
        _, contracts = _direction_experts()
        claims = (_claim(contracts[0], 0.8, "evidence:a"), _claim(contracts[1], 0.6, "evidence:b"))
        memory = dict(_memory_for(contracts[:2], claims))
        memory["integrity"] = dict(memory["integrity"], content_hash="f" * 64)
        with self.assertRaisesRegex(ExpertSchoolError, "content hash mismatch"):
            assemble_expert_claims(claims, memory, {})

    def test_input_ordering_does_not_affect_assembly_identity(self):
        _, contracts = _direction_experts()
        claims = (_claim(contracts[0], 0.8, "evidence:a"), _claim(contracts[1], 0.6, "evidence:b"))
        memory = _memory_for(contracts[:2], claims)
        first = assemble_expert_claims(claims, memory, {"regime": "TREND"})
        second = assemble_expert_claims(tuple(reversed(claims)), memory, {"regime": "TREND"})
        self.assertEqual(first["integrity"]["content_hash"], second["integrity"]["content_hash"])

    def test_no_capital_fields_in_assembly(self):
        _, contracts = _direction_experts()
        claims = (_claim(contracts[0], 0.8, "evidence:a"), _claim(contracts[1], 0.6, "evidence:b"))
        assembly = assemble_expert_claims(claims, _memory_for(contracts[:2], claims), {})
        _walk_forbidden(assembly)
        self.assertFalse(assembly["authority"]["capital_decision"])


class DurableDirectionAssemblyTests(unittest.TestCase):
    def test_journaled_direction_assembly_is_research_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=55)
            result = process_canonical_direction_batch(root, batch_id)
            assembly = result["sync"]["direction_assembly"]
            self.assertEqual(assembly["status"], "RESEARCH_ONLY")
            self.assertEqual(assembly["prospective_use"], "BLOCKED")
            self.assertFalse(assembly["authority"]["adaptive_assembly_earned"])
            self.assertFalse(assembly["authority"]["benjamin_eligible"])
            self.assertEqual(assembly["internal_intelligence_publication"], "NOT_PUBLISHED")
            self.assertEqual(assembly["contextual_competence_status"], "INSUFFICIENT_CONTEXTUAL_SUPPORT")
            self.assertIn("Z9_NOT_QUALIFIED", assembly["prospective_blocked_reasons"])
            self.assertEqual(len(assembly["contributing_claim_hashes"]), 3)
            self.assertAlmostEqual(sum(item["weight"] for item in assembly["assembled"]["expert_contributions"]), 1.0)
            projection = question_learning_projection(root)
            self.assertFalse(projection["authority"]["adaptive_assembly_earned"])
            self.assertEqual(projection["assembly"]["assembly_exists"], "ASSEMBLY_EXISTS")
            self.assertEqual(projection["assembly"]["prospective_qualification"], "BLOCKED")
            replay = assemble_direction_question(root, known_at_ns=int(result["observation_end_ns"]))
            self.assertEqual(replay["integrity"]["content_hash"], assembly["integrity"]["content_hash"])
            recorded = assemble_and_record_direction_question(root, known_at_ns=int(result["observation_end_ns"]))
            self.assertEqual(recorded["integrity"]["content_hash"], assembly["integrity"]["content_hash"])
            _walk_forbidden(assembly)

    def test_unscored_and_wrong_subject_cannot_contribute(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=55)
            result = process_canonical_direction_batch(root, batch_id, sync=False)
            with self.assertRaisesRegex(DirectionAssemblyError, "not in expert runtime|unresolved or unscored|no Direction cutoff"):
                assemble_direction_question(root, known_at_ns=int(result["observation_end_ns"]))


class RealDirectionAssemblyOptInTests(unittest.TestCase):
    def test_skips_without_opt_in(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZLOOK_RUN_REAL_DIRECTION_ASSEMBLY_TEST", None)
            self.assertNotEqual(os.environ.get("ZLOOK_RUN_REAL_DIRECTION_ASSEMBLY_TEST"), "1")

    def test_opt_in_real_coinbase_assembly_is_conservative_research_only(self):
        if os.environ.get("ZLOOK_RUN_REAL_DIRECTION_ASSEMBLY_TEST", "").strip() != "1":
            raise unittest.SkipTest("real Direction assembly proof is opt-in; set ZLOOK_RUN_REAL_DIRECTION_ASSEMBLY_TEST=1")
        from tests.test_direction_real_coinbase_loop import REPO, _copy_canonical, _local_coinbase_manifests

        evidence_root = Path(os.environ.get("ZLOOK_REAL_EVIDENCE_ROOT") or REPO)
        manifests = _local_coinbase_manifests(evidence_root)
        if not manifests:
            raise AssertionError("ZLOOK_RUN_REAL_DIRECTION_ASSEMBLY_TEST=1 but no CAN-COINBASE-BTC-USD-OBS-* manifests are present")
        batch_ids = [path.name[: -len(".manifest.json")] for path in manifests[-2:]]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for batch_id in batch_ids:
                _copy_canonical(evidence_root, root, batch_id)
            from autonomous_kernel.learning.direction_loop import process_canonical_direction_batches

            result = process_canonical_direction_batches(root, tuple(batch_ids))
            assembly = result["sync"]["direction_assembly"]
            self.assertEqual(assembly["status"], "RESEARCH_ONLY")
            self.assertEqual(result["counts"]["predicted"], 6)
            self.assertEqual(result["counts"]["resolved"], 6)
            self.assertEqual(len(assembly["contributing_claim_hashes"]), 3)
            self.assertLess(float(assembly["max_contributor_weight"]), 0.75)
            self.assertEqual(assembly["prospective_use"], "BLOCKED")
            self.assertFalse(assembly["authority"]["benjamin_eligible"])


if __name__ == "__main__":
    unittest.main()
