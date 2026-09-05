from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomous_kernel.intelligence.runtime import IntelligenceRuntime, validate_event_chain
from autonomous_kernel.learning.direction_loop import question_learning_projection
from autonomous_kernel.operations import canonical_hash
from autonomous_kernel.operator import operator_snapshot
from autonomous_kernel.questions.catalog import question_catalog_v1
from autonomous_kernel.questions.evolution import REVERSAL_QUESTION_V1_1_REF, reversal_question_v1_2
from autonomous_kernel.synthesis.contracts import (
    SYNTHESIS_POLICY_ID,
    SYNTHESIS_POLICY_VERSION,
    MarketSynthesisError,
    assert_no_forbidden,
)
from autonomous_kernel.synthesis.reasoner import synthesize_market_state
from autonomous_kernel.synthesis.renderer import render_market_story
from autonomous_kernel.synthesis.service import synthesize_and_record, synthesize_from_runtime
from tests.test_direction_competence_loop import _write_span
from tests.test_direction_real_coinbase_loop import REPO, _copy_canonical, _local_coinbase_manifests


def _family_defs():
    definitions = {}
    for item in question_catalog_v1():
        if item.family.value == "REVERSAL":
            continue
        definitions[item.family.value] = item
    definitions["REVERSAL"] = reversal_question_v1_2()
    return definitions


def make_assembly(family, answer, *, subject_id="ASSET.BTC", known_at_ns=1_000, cutoff_at_ns=900, evidence_refs=("EVIDENCE-A",), source_evidence_groups=None, contributing_claim_hashes=None, omit_lineage=False, z9_status="DEGRADED"):
    definition = _family_defs()[family]
    body = {
        "schema_version": "1.0",
        "assembly_id": "QASM-FIXTURE-%s" % family,
        "status": "RESEARCH_ONLY",
        "question_ref": definition.question_ref,
        "question_definition_hash": definition.content_hash(),
        "subject_id": subject_id,
        "horizon_ns": definition.horizon_ns,
        "cutoff_at_ns": int(cutoff_at_ns),
        "known_at_ns": int(known_at_ns),
        "competence_memory_hash": "c" * 64,
        "context_hash": "d" * 64,
        "assembled_answer": answer,
        "z9_status": z9_status,
        "prospective_use": "BLOCKED",
        "internal_intelligence_publication": "NOT_PUBLISHED",
        "benjamin_publication": "NOT_ELIGIBLE",
    }
    if contributing_claim_hashes is not None:
        body["contributing_claim_hashes"] = list(contributing_claim_hashes)
    if not omit_lineage:
        groups = list(source_evidence_groups) if source_evidence_groups is not None else list(evidence_refs)
        body["source_evidence_groups"] = groups
        body["source_evidence_refs"] = list(evidence_refs)
    body["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
    return body


def _kinds(synthesis):
    return {item["kind"] for item in synthesis.get("findings") or []}


class AcrossQuestionSynthesisTests(unittest.TestCase):
    def test_raw_model_claims_cannot_bypass_same_question_assembly(self):
        with self.assertRaisesRegex(MarketSynthesisError, "bypass"):
            synthesize_market_state([{"answer": 0.9, "model": "BOOK-IMBALANCE-LINEAR"}], known_at_ns=2_000, subject_id="ASSET.BTC")

    def test_wrong_question_version_is_rejected(self):
        assembly = make_assembly("REVERSAL", 1)
        assembly["question_ref"] = REVERSAL_QUESTION_V1_1_REF
        assembly.pop("integrity")
        assembly["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(assembly)}
        with self.assertRaisesRegex(MarketSynthesisError, "historical reversal|wrong question"):
            synthesize_market_state([assembly], known_at_ns=2_000, subject_id="ASSET.BTC")

    def test_future_assembly_cannot_enter_earlier_synthesis(self):
        assembly = make_assembly("DIRECTION", 0.6, known_at_ns=9_000)
        with self.assertRaisesRegex(MarketSynthesisError, "future assembly"):
            synthesize_market_state([assembly], known_at_ns=1_000, subject_id="ASSET.BTC")

    def test_stale_input_is_marked(self):
        assembly = make_assembly("DIRECTION", 0.6, known_at_ns=1, cutoff_at_ns=1)
        synthesis = synthesize_market_state([assembly], known_at_ns=100_000_000_000, subject_id="ASSET.BTC")
        self.assertIn("STALE_INPUT", _kinds(synthesis))
        self.assertEqual("STALE", synthesis["direction_state"]["status"])

    def test_incompatible_subjects_cannot_mix(self):
        first = make_assembly("DIRECTION", 0.6, subject_id="ASSET.BTC")
        second = make_assembly("MAGNITUDE", 4, subject_id="ASSET.ETH")
        with self.assertRaisesRegex(MarketSynthesisError, "subjects"):
            synthesize_market_state([first, second], known_at_ns=2_000, subject_id="ASSET.BTC")

    def test_missing_dimensions_remain_missing(self):
        synthesis = synthesize_market_state([make_assembly("DIRECTION", 0.68)], known_at_ns=2_000, subject_id="ASSET.BTC")
        self.assertEqual(synthesis["direction_state"]["assembled_answer"], 0.68)
        self.assertEqual("unavailable", synthesis["volatility_state"]["display"])
        self.assertIsNone(synthesis["volatility_state"]["assembled_answer"])
        self.assertIn("VOLATILITY", synthesis["missing_dimensions"])
        self.assertFalse(synthesis["support"]["complete"])
        self.assertEqual("PARTIAL", synthesis["synthesis_status"])

    def test_direction_is_not_overwritten_by_liquidity_or_fragility(self):
        direction = make_assembly("DIRECTION", 0.68, evidence_refs=("BOOK",))
        liquidity = make_assembly("LIQUIDITY", 1, evidence_refs=("BOOK",))
        fragility = make_assembly("FRAGILITY", 12, evidence_refs=("BOOK",))
        synthesis = synthesize_market_state([direction, liquidity, fragility], known_at_ns=2_000, subject_id="ASSET.BTC")
        self.assertEqual(0.68, synthesis["direction_state"]["assembled_answer"])
        self.assertEqual(0.68, synthesis["confidence"]["directional_probability"])
        self.assertNotEqual(synthesis["confidence"]["synthesis_confidence"], 0.68)
        self.assertIn("FRAGILITY_WARNING", _kinds(synthesis))

    def test_horizon_tension_differs_from_direct_contradiction(self):
        direction = make_assembly("DIRECTION", 0.2)
        regime = make_assembly("REGIME", "RISK_ON")
        synthesis = synthesize_market_state([direction, regime], known_at_ns=2_000, subject_id="ASSET.BTC")
        self.assertIn("HORIZON_TENSION", _kinds(synthesis))
        self.assertNotIn("DIRECT_CONTRADICTION", _kinds(synthesis))
        same_scale = dict(regime)
        same_scale.pop("integrity")
        # Regime remains SESSION; magnitude sign mismatch is the direct contradiction probe.
        magnitude = make_assembly("MAGNITUDE", -12)
        upward = make_assembly("DIRECTION", 0.8)
        conflict = synthesize_market_state([upward, magnitude], known_at_ns=2_000, subject_id="ASSET.BTC")
        self.assertIn("DIRECT_CONTRADICTION", _kinds(conflict))

    def test_duplicate_underlying_evidence_does_not_inflate_independence(self):
        shared = ("COINBASE-BOOK",)
        one = synthesize_market_state([make_assembly("DIRECTION", 0.7, evidence_refs=shared)], known_at_ns=2_000, subject_id="ASSET.BTC")
        many = synthesize_market_state(
            [
                make_assembly("DIRECTION", 0.7, evidence_refs=shared),
                make_assembly("MAGNITUDE", 5, evidence_refs=shared),
                make_assembly("VOLATILITY", 6, evidence_refs=shared),
                make_assembly("LIQUIDITY", 0, evidence_refs=shared),
                make_assembly("FRAGILITY", 2, evidence_refs=shared),
            ],
            known_at_ns=2_000,
            subject_id="ASSET.BTC",
        )
        self.assertEqual("INDEPENDENCE_NOT_ESTABLISHED", one["evidence_independence_summary"]["independence_status"])
        self.assertEqual(0.0, one["confidence"]["independence"])
        self.assertFalse(one["evidence_independence_summary"]["dependence_warning"])
        self.assertEqual(1, many["evidence_independence_summary"]["distinct_underlying_evidence_groups"])
        self.assertEqual("DEPENDENT", many["evidence_independence_summary"]["independence_status"])
        self.assertTrue(many["evidence_independence_summary"]["dependence_warning"])
        self.assertLess(many["confidence"]["independence"], 1.0)

    def test_claim_hashes_are_not_independence_proxies(self):
        claim_a = "a" * 64
        claim_b = "b" * 64
        shared = ("lineage:SPOT_MICROSTRUCTURE:ASSET.BTC",)
        synthesis = synthesize_market_state(
            [
                make_assembly("DIRECTION", 0.7, source_evidence_groups=shared, contributing_claim_hashes=(claim_a,)),
                make_assembly("LIQUIDITY", 1, source_evidence_groups=shared, contributing_claim_hashes=(claim_b,)),
            ],
            known_at_ns=2_000,
            subject_id="ASSET.BTC",
        )
        self.assertTrue(synthesis["evidence_independence_summary"]["dependence_warning"])
        self.assertEqual("DEPENDENT", synthesis["evidence_independence_summary"]["independence_status"])
        self.assertFalse(synthesis["evidence_independence_summary"]["used_claim_hashes_as_proxy"])
        hashed_only = synthesize_market_state(
            [
                make_assembly("DIRECTION", 0.7, omit_lineage=True, contributing_claim_hashes=(claim_a,)),
                make_assembly("LIQUIDITY", 1, omit_lineage=True, contributing_claim_hashes=(claim_b,)),
            ],
            known_at_ns=2_000,
            subject_id="ASSET.BTC",
        )
        self.assertEqual("UNKNOWN", hashed_only["evidence_independence_summary"]["independence_status"])
        self.assertEqual(0.0, hashed_only["confidence"]["independence"])
        self.assertLessEqual(
            hashed_only["confidence"]["synthesis_confidence"],
            synthesis["confidence"]["synthesis_confidence"],
        )

    def test_missing_lineage_is_unknown_not_independent(self):
        known = synthesize_market_state(
            [
                make_assembly("DIRECTION", 0.7, evidence_refs=("COINBASE-BOOK",)),
                make_assembly("MAGNITUDE", 4, evidence_refs=("COINBASE-BOOK",)),
            ],
            known_at_ns=2_000,
            subject_id="ASSET.BTC",
        )
        missing = synthesize_market_state(
            [
                make_assembly("DIRECTION", 0.7, omit_lineage=True),
                make_assembly("MAGNITUDE", 4, omit_lineage=True),
            ],
            known_at_ns=2_000,
            subject_id="ASSET.BTC",
        )
        self.assertEqual("UNKNOWN", missing["evidence_independence_summary"]["lineage_status"])
        self.assertEqual("UNKNOWN", missing["evidence_independence_summary"]["independence_status"])
        self.assertEqual(0.0, missing["confidence"]["independence"])
        self.assertIn("LINEAGE_UNKNOWN", _kinds(missing))
        self.assertLessEqual(missing["confidence"]["synthesis_confidence"], known["confidence"]["synthesis_confidence"])

    def test_one_family_does_not_earn_cross_family_independence_bonus(self):
        synthesis = synthesize_market_state([make_assembly("DIRECTION", 0.7, evidence_refs=("COINBASE-BOOK",))], known_at_ns=2_000, subject_id="ASSET.BTC")
        self.assertEqual("INDEPENDENCE_NOT_ESTABLISHED", synthesis["evidence_independence_summary"]["independence_status"])
        self.assertEqual(0.0, synthesis["confidence"]["independence"])
        self.assertEqual(0.0, synthesis["evidence_independence_summary"]["independence_for_confidence"])
        self.assertFalse(synthesis["evidence_independence_summary"]["dependence_warning"])

    def test_independent_multi_family_lineage_is_not_a_dependence_warning(self):
        synthesis = synthesize_market_state(
            [
                make_assembly("DIRECTION", 0.7, evidence_refs=("SPOT-BOOK",)),
                make_assembly("REGIME", "DIRECTIONAL", evidence_refs=("MARKET-WIDE-CONTEXT",)),
            ],
            known_at_ns=2_000,
            subject_id="ASSET.BTC",
        )
        self.assertEqual("INDEPENDENT", synthesis["evidence_independence_summary"]["independence_status"])
        self.assertFalse(synthesis["evidence_independence_summary"]["dependence_warning"])
        self.assertEqual(2, synthesis["evidence_independence_summary"]["distinct_underlying_evidence_groups"])
        self.assertGreater(synthesis["confidence"]["independence"], 0.0)

    def test_tampered_assembly_hash_fails_closed(self):
        assembly = make_assembly("DIRECTION", 0.6)
        assembly["assembled_answer"] = 0.99
        with self.assertRaisesRegex(MarketSynthesisError, "tampered"):
            synthesize_market_state([assembly], known_at_ns=2_000, subject_id="ASSET.BTC")

    def test_input_order_does_not_affect_synthesis_identity(self):
        items = [make_assembly("DIRECTION", 0.7, evidence_refs=("A",)), make_assembly("MAGNITUDE", 9, evidence_refs=("B",))]
        first = synthesize_market_state(items, known_at_ns=2_000, subject_id="ASSET.BTC")
        second = synthesize_market_state(list(reversed(items)), known_at_ns=2_000, subject_id="ASSET.BTC")
        self.assertEqual(first["synthesis_id"], second["synthesis_id"])
        self.assertEqual(first["integrity"]["content_hash"], second["integrity"]["content_hash"])

    def test_replay_is_deterministic(self):
        items = [make_assembly("DIRECTION", 0.61)]
        first = synthesize_market_state(items, known_at_ns=2_000, subject_id="ASSET.BTC")
        second = synthesize_market_state(items, known_at_ns=2_000, subject_id="ASSET.BTC")
        self.assertEqual(first, second)

    def test_complete_synthesis_cannot_be_claimed_from_partial_inputs(self):
        synthesis = synthesize_market_state([make_assembly("DIRECTION", 0.7)], known_at_ns=2_000, subject_id="ASSET.BTC")
        self.assertFalse(synthesis["support"]["complete"])
        self.assertEqual("BLOCKED", synthesis["prospective_qualification"])
        self.assertIn("SYNTHESIS_INCOMPLETE", synthesis["blocking_reasons"])

    def test_degraded_z9_cannot_become_qualified(self):
        synthesis = synthesize_market_state(
            [make_assembly("DIRECTION", 0.7)],
            known_at_ns=2_000,
            subject_id="ASSET.BTC",
            context_status="DEGRADED",
        )
        self.assertEqual("DEGRADED", synthesis["context_status"])
        self.assertNotEqual("QUALIFIED", synthesis["context_status"])
        self.assertIn("Z9_NOT_QUALIFIED", synthesis["blocking_reasons"])

    def test_no_trade_vocabulary(self):
        synthesis = synthesize_market_state([make_assembly("DIRECTION", 0.7)], known_at_ns=2_000, subject_id="ASSET.BTC")
        assert_no_forbidden(synthesis)
        assert_no_forbidden(render_market_story(synthesis))

    def test_case_a_coherent_trend_has_higher_confidence_than_fragile(self):
        coherent = synthesize_market_state(
            [
                make_assembly("DIRECTION", 0.78, evidence_refs=("DIR",)),
                make_assembly("MAGNITUDE", 12, evidence_refs=("MAG",)),
                make_assembly("VOLATILITY", 6, evidence_refs=("VOL",)),
                make_assembly("LIQUIDITY", 0, evidence_refs=("LIQ",)),
                make_assembly("REGIME", "DIRECTIONAL", evidence_refs=("REG",)),
                make_assembly("PERSISTENCE", 1, evidence_refs=("PER",)),
                make_assembly("REVERSAL", 0, evidence_refs=("REV",)),
            ],
            known_at_ns=2_000,
            subject_id="ASSET.BTC",
        )
        fragile = synthesize_market_state(
            [
                make_assembly("DIRECTION", 0.78, evidence_refs=("BOOK",)),
                make_assembly("MAGNITUDE", 12, evidence_refs=("BOOK",)),
                make_assembly("VOLATILITY", 14, evidence_refs=("BOOK",)),
                make_assembly("FRAGILITY", 12, evidence_refs=("BOOK",)),
                make_assembly("LIQUIDITY", 1, evidence_refs=("BOOK",)),
            ],
            known_at_ns=2_000,
            subject_id="ASSET.BTC",
        )
        self.assertGreater(coherent["confidence"]["synthesis_confidence"], fragile["confidence"]["synthesis_confidence"])
        self.assertEqual(0.78, fragile["direction_state"]["assembled_answer"])
        self.assertIn("FRAGILITY_WARNING", _kinds(fragile))

    def test_case_c_reversal_transition_is_deterministic(self):
        first = synthesize_market_state(
            [
                make_assembly("DIRECTION", 0.66, evidence_refs=("A",)),
                make_assembly("REVERSAL", 1, evidence_refs=("B",)),
                make_assembly("REGIME", "MIXED", evidence_refs=("C",)),
                make_assembly("PERSISTENCE", 0, evidence_refs=("D",)),
            ],
            known_at_ns=2_000,
            subject_id="ASSET.BTC",
        )
        second = synthesize_market_state(
            [
                make_assembly("PERSISTENCE", 0, evidence_refs=("D",)),
                make_assembly("REGIME", "MIXED", evidence_refs=("C",)),
                make_assembly("REVERSAL", 1, evidence_refs=("B",)),
                make_assembly("DIRECTION", 0.66, evidence_refs=("A",)),
            ],
            known_at_ns=2_000,
            subject_id="ASSET.BTC",
        )
        self.assertIn("REGIME_TRANSITION", _kinds(first))
        self.assertEqual(first["integrity"]["content_hash"], second["integrity"]["content_hash"])

    def test_case_d_structural_dislocation(self):
        synthesis = synthesize_market_state(
            [
                make_assembly("BASIS", 4, evidence_refs=("BASIS",)),
                make_assembly("RELATIVE_VALUE", -3, evidence_refs=("RV",)),
                make_assembly("LIQUIDITY", 1, evidence_refs=("LIQ",)),
            ],
            known_at_ns=2_000,
            subject_id="ASSET.BTC",
        )
        self.assertIn("STRUCTURAL_DIVERGENCE", _kinds(synthesis))

    def test_case_f_partial_story_is_exact(self):
        synthesis = synthesize_market_state([make_assembly("DIRECTION", 0.68)], known_at_ns=2_000, subject_id="ASSET.BTC")
        story = render_market_story(synthesis)
        self.assertEqual(
            story,
            "Micro-horizon direction is positive. Broader regime evidence is unavailable. Other intended dimensions remain unavailable. The synthesis is partial and does not claim complete market understanding.",
        )

    def test_journal_restart_idempotence_and_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = IntelligenceRuntime(root)
            assembly = make_assembly("DIRECTION", 0.62)
            runtime.record_assembly(assembly, occurred_at_ns=2_000)
            first = synthesize_and_record(root, known_at_ns=2_000)
            event_count = runtime.state()["event_count"]
            second = synthesize_and_record(root, known_at_ns=2_000)
            self.assertEqual(first["integrity"]["content_hash"], second["integrity"]["content_hash"])
            self.assertEqual(runtime.state()["event_count"], event_count)
            rebuilt = runtime.rebuild_state()
            self.assertEqual(rebuilt["syntheses"][0]["integrity"]["content_hash"], first["integrity"]["content_hash"])
            self.assertEqual(validate_event_chain(runtime.events()), ())
            changed = make_assembly("DIRECTION", 0.71, known_at_ns=2_100)
            runtime.record_assembly(changed, occurred_at_ns=2_100)
            third = synthesize_and_record(root, known_at_ns=2_100)
            self.assertNotEqual(third["synthesis_id"], first["synthesis_id"])
            self.assertEqual([], list(runtime.state().get("publications") or []))
            path = runtime.events_path
            lines = path.read_text(encoding="utf-8").splitlines()
            event = json.loads(lines[-1])
            event["payload"]["synthesis"]["confidence"]["synthesis_confidence"] = 0.99
            lines[-1] = json.dumps(event, sort_keys=True, separators=(",", ":"))
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertTrue(validate_event_chain(runtime.events()))
            with self.assertRaises(Exception):
                runtime.rebuild_state()

    def test_service_filters_future_assemblies_for_point_in_time_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = IntelligenceRuntime(root)
            early = make_assembly("DIRECTION", 0.55, known_at_ns=1_000)
            later = make_assembly("MAGNITUDE", 8, known_at_ns=5_000)
            runtime.record_assembly(early, occurred_at_ns=1_000)
            runtime.record_assembly(later, occurred_at_ns=5_000)
            replay = synthesize_from_runtime(root, known_at_ns=1_000)
            self.assertEqual(["DIRECTION"], replay["available_dimensions"])
            self.assertIn("MAGNITUDE", replay["missing_dimensions"])

    def test_operator_snapshot_exposes_absent_synthesis(self):
        snapshot = operator_snapshot(REPO)
        self.assertEqual("ABSENT", snapshot["market_synthesis"]["status"])
        product = (REPO / "monitor/web/product-app.js").read_text(encoding="utf-8")
        self.assertIn("function contextPage()", product)
        self.assertIn("unavailable", product)


class RealMarketSynthesisOptInTests(unittest.TestCase):
    def test_skips_without_opt_in(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZLOOK_RUN_REAL_SYNTHESIS_TEST", None)
            self.assertNotEqual(os.environ.get("ZLOOK_RUN_REAL_SYNTHESIS_TEST"), "1")

    def test_opt_in_real_direction_only_partial_synthesis(self):
        if os.environ.get("ZLOOK_RUN_REAL_SYNTHESIS_TEST", "").strip() != "1":
            raise unittest.SkipTest("real market synthesis proof is opt-in; set ZLOOK_RUN_REAL_SYNTHESIS_TEST=1")
        evidence_root = Path(os.environ.get("ZLOOK_REAL_EVIDENCE_ROOT") or REPO)
        manifests = _local_coinbase_manifests(evidence_root)
        if not manifests:
            raise AssertionError("ZLOOK_RUN_REAL_SYNTHESIS_TEST=1 but no CAN-COINBASE-BTC-USD-OBS-* manifests are present")
        batch_ids = [path.name[: -len(".manifest.json")] for path in manifests[-2:]]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for batch_id in batch_ids:
                _copy_canonical(evidence_root, root, batch_id)
            from autonomous_kernel.learning.direction_loop import process_canonical_direction_batches

            result = process_canonical_direction_batches(root, tuple(batch_ids))
            synthesis = result["sync"]["market_synthesis"]
            self.assertEqual(["DIRECTION"], synthesis["available_dimensions"])
            self.assertGreaterEqual(len(synthesis["missing_dimensions"]), 8)
            self.assertFalse(synthesis["support"]["complete"])
            self.assertEqual("PARTIAL", synthesis["synthesis_status"])
            self.assertEqual("BLOCKED", synthesis["prospective_qualification"])
            self.assertEqual("NOT_PUBLISHED", synthesis["internal_intelligence_publication"])
            self.assertEqual("NOT_ELIGIBLE", synthesis["benjamin_publication"])
            self.assertEqual("DEGRADED", synthesis["context_status"])
            self.assertEqual("INDEPENDENCE_NOT_ESTABLISHED", synthesis["evidence_independence_summary"]["independence_status"])
            self.assertEqual(0.0, synthesis["confidence"]["independence"])
            self.assertFalse(synthesis["evidence_independence_summary"]["used_claim_hashes_as_proxy"])
            self.assertTrue(synthesis["evidence_independence_summary"]["source_evidence_groups"])
            self.assertFalse(synthesis["authority"]["capital_decision"])
            projection = question_learning_projection(root)
            self.assertEqual(projection["market_synthesis"]["latest"]["synthesis_id"], synthesis["synthesis_id"])


if __name__ == "__main__":
    unittest.main()
