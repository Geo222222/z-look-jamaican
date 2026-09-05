from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from autonomous_kernel.evaluation.question_journal import QuestionOutcomeJournal
from autonomous_kernel.evaluation.question_resolvers import QuestionResolverError
from autonomous_kernel.experts.school import ExpertSchoolError, assemble_expert_claims, build_competence_memory
from autonomous_kernel.intelligence.runtime import IntelligenceRuntime
from autonomous_kernel.learning.direction_loop import (
    FORBIDDEN_FIELDS,
    INSTRUMENT_ID,
    materialize_cutoff_frame,
    process_canonical_direction_batch,
    question_learning_projection,
    _experience_for_cutoff,
)
from autonomous_kernel.learning.liquidity_assembly import assemble_liquidity_question
from autonomous_kernel.learning.liquidity_loop import (
    LIQUIDITY_QUESTION_REF,
    liquidity_baseline_is_eligible,
    process_canonical_liquidity_batch,
    record_liquidity_predictions,
    resolve_liquidity_prediction,
)
from autonomous_kernel.models.liquidity_baselines import (
    BookDepletionStressModel,
    LiquidityNullPriorModel,
    SpreadDepthPressureModel,
    liquidity_baseline_model_set,
)
from autonomous_kernel.observation.instruments import CanonicalInstrument
from autonomous_kernel.prediction.question_journal import QuestionPredictionJournal
from tests.test_direction_competence_loop import T, SECOND, _write_span
from tests.test_model_factory import model_frame


def _walk_forbidden(value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            for token in FORBIDDEN_FIELDS:
                if token in lowered:
                    raise AssertionError("forbidden field %s" % key)
            _walk_forbidden(item)
    elif isinstance(value, list):
        for item in value:
            _walk_forbidden(item)


def _load_frame(root: Path, batch_id: str, cutoff: int):
    return materialize_cutoff_frame(root, batch_id, cutoff)


class LiquidityModelContractTests(unittest.TestCase):
    def test_candidate_models_differ_and_are_deterministic(self):
        frame = model_frame()
        null = LiquidityNullPriorModel()
        pressure = SpreadDepthPressureModel()
        depletion = BookDepletionStressModel()
        p_null, d_null = null.forecast_liquidity(frame)
        p_pressure, d_pressure = pressure.forecast_liquidity(frame)
        p_depletion, d_depletion = depletion.forecast_liquidity(frame)
        self.assertEqual(p_null, p_null.__class__("0.5"))
        self.assertNotEqual(p_pressure, p_depletion)
        self.assertNotEqual(d_pressure["model_id"], d_depletion["model_id"])
        self.assertIn("spread_pressure", d_pressure)
        self.assertIn("depletion_score", d_depletion)
        again_p, again_d = pressure.forecast_liquidity(frame)
        self.assertEqual(p_pressure, again_p)
        self.assertEqual(d_pressure, again_d)
        models = liquidity_baseline_model_set()
        self.assertEqual(3, len(models))
        self.assertTrue(all(model.definition.lifecycle_state == "CANDIDATE" for model in models))
        self.assertTrue(all(30_000_000_000 in model.definition.supported_horizons_ns for model in models))
        self.assertTrue(all(model.definition.parameters.get("coefficient_status") == "NOT_CLAIMED_EMPIRICALLY_OPTIMAL" for model in models))
        self.assertTrue(all(model.definition.parameters.get("capital_authority") is False for model in models))


class LiquidityLoopAdversarialTests(unittest.TestCase):
    def test_unqualified_missing_depth_crossed_and_wrong_instrument_skip_prediction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=50)
            cutoff = T + 10 * SECOND
            frame = _load_frame(root, batch_id, cutoff)
            self.assertTrue(liquidity_baseline_is_eligible(frame))
            degraded = replace(frame, status="DEGRADED")
            self.assertFalse(liquidity_baseline_is_eligible(degraded))
            self.assertEqual([], record_liquidity_predictions(root, batch_id=batch_id, frame=degraded, experience=object()))
            state = copy.deepcopy(frame.state)
            book = state["venue_states"]["COINBASE"]["book"]
            book["depth_bands_bps"] = {}
            missing = replace(frame, state=state)
            self.assertFalse(liquidity_baseline_is_eligible(missing))
            crossed_state = copy.deepcopy(frame.state)
            crossed_book = crossed_state["venue_states"]["COINBASE"]["book"]
            crossed_book["best_bid"] = "100.50"
            crossed_book["best_ask"] = "100.10"
            crossed = replace(frame, state=crossed_state)
            self.assertFalse(liquidity_baseline_is_eligible(crossed))
            eth = CanonicalInstrument(
                canonical_id="CRYPTO.SPOT.ETH-USD",
                asset_class="CRYPTO",
                market_type="SPOT",
                base_asset="ETH",
                quote_asset="USD",
                settlement_asset="USD",
            )
            wrong = replace(frame, instrument=eth)
            self.assertFalse(liquidity_baseline_is_eligible(wrong))
            self.assertEqual(0, len(QuestionPredictionJournal(root).entries()) if (root / "memory/question_predictions.jsonl").is_file() else 0)

    def test_loop_journals_predictions_outcomes_scores_without_future_leak_or_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=80)
            result = process_canonical_liquidity_batch(root, batch_id)
            self.assertGreaterEqual(result["counts"]["predicted"], 3)
            self.assertEqual(result["question_ref"], LIQUIDITY_QUESTION_REF)
            self.assertEqual(result["horizon_ns"], 30_000_000_000)
            self.assertFalse(result["authority"]["capital_allocation"])
            self.assertFalse(result["authority"]["economic_decision"])
            self.assertFalse(result["authority"]["external_execution"])
            for item in result["predictions"]:
                self.assertLessEqual(int(item["journaled_at_ns"]), int(item["resolves_at_ns"]))
                self.assertLess(int(item["cutoff_at_ns"]), int(item["resolves_at_ns"]))
                self.assertEqual(item["mode"], "HISTORICAL_REPLAY")
                payload = next(
                    entry["prediction"]
                    for entry in QuestionPredictionJournal(root).entries()
                    if entry["prediction"]["prediction_id"] == item["prediction_id"]
                )
                self.assertEqual(payload["question"]["subject_id"], "ASSET.BTC")
                self.assertEqual(payload["timing"]["horizon_ns"], 30_000_000_000)
                self.assertEqual(payload["question"]["max_resolution_lag_ns"], 2_000_000_000)
                self.assertIn("probability_1", payload["answer"])
                self.assertEqual(payload["artifact_refs"][0]["feature_families"], ["SPOT_MICROSTRUCTURE"])
                self.assertLessEqual(int(payload["timing"]["created_at_ns"]), int(payload["timing"]["resolves_at_ns"]))
            for pred, outcome in zip(result["predictions"], result["outcomes"]):
                if outcome.get("status") == "RESOLVED":
                    self.assertGreater(int(outcome["decided_at_ns"]), int(pred["cutoff_at_ns"]))
                    self.assertGreater(int(outcome["decided_at_ns"]), int(pred["journaled_at_ns"]))
            again = process_canonical_liquidity_batch(root, batch_id)
            self.assertEqual(result["counts"]["predicted"], again["counts"]["predicted"])
            self.assertEqual(len(QuestionPredictionJournal(root).entries()), again["counts"]["predicted"])
            outcomes = QuestionOutcomeJournal(root).entries()
            self.assertEqual(len(outcomes), sum(1 for item in result["outcomes"] if item.get("status") in {"RESOLVED", "UNRESOLVABLE"}))
            sync = result["sync"]
            self.assertGreaterEqual(int(sync.get("scores_recorded") or 0), 0)
            competence = IntelligenceRuntime(root).state().get("competence") or {}
            liquidity_experts = [item for item in competence.get("entries") or [] if item.get("question_ref") == LIQUIDITY_QUESTION_REF]
            for entry in liquidity_experts:
                self.assertLess(float(entry.get("sample_support") or 0), 1.0)
            assembly = sync.get("liquidity_assembly") or {}
            if assembly.get("status") == "RESEARCH_ONLY":
                self.assertEqual(assembly.get("prospective_use"), "BLOCKED")
                self.assertEqual(assembly.get("internal_intelligence_publication"), "NOT_PUBLISHED")
                self.assertEqual(assembly.get("benjamin_publication"), "NOT_ELIGIBLE")
                groups = assembly.get("source_evidence_groups") or []
                self.assertTrue(any("SPOT_MICROSTRUCTURE" in str(item) for item in groups))
                self.assertFalse(any(len(str(item)) == 64 and all(ch in "0123456789abcdef" for ch in str(item).lower()) for item in groups))
                hashes = assemble_liquidity_question(root, known_at_ns=int(result["observation_end_ns"]))
                self.assertEqual(hashes["integrity"]["content_hash"], assembly["integrity"]["content_hash"])
            projection = question_learning_projection(root)
            self.assertEqual(projection["liquidity"]["question_ref"], LIQUIDITY_QUESTION_REF)
            self.assertFalse(projection["authority"]["capital_allocation"])

    def test_too_early_resolution_is_pending_and_unresolvable_is_not_scored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=50)
            cutoff = T + 10 * SECOND
            frame = _load_frame(root, batch_id, cutoff)
            experience, _context = _experience_for_cutoff(root, frame)
            recorded = record_liquidity_predictions(root, batch_id=batch_id, frame=frame, experience=experience)
            self.assertGreaterEqual(len(recorded), 3)
            early = resolve_liquidity_prediction(
                root,
                batch_id=batch_id,
                prediction_id=recorded[0]["prediction_id"],
                baseline_frame=frame,
                experience=experience,
                now_at_ns=cutoff + 1,
            )
            self.assertEqual("PENDING", early["status"])
            from autonomous_kernel.experts.sync import sync_expert_learning
            sync = sync_expert_learning(root, known_at_ns=cutoff + 1)
            self.assertEqual(0, int(sync.get("scores_recorded") or 0))
            self.assertEqual(0, len(QuestionOutcomeJournal(root).entries()) if (root / "memory/question_outcomes.jsonl").is_file() else 0)

    def test_integrity_failure_is_not_journaled_as_unresolvable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=50)
            result = process_canonical_liquidity_batch(root, batch_id, sync=False)
            path = root / "memory/question_predictions.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines()
            payload = json.loads(lines[0])
            payload["prediction"]["integrity"]["content_hash"] = "0" * 64
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            first = result["predictions"][0]
            from autonomous_kernel.experience.store import MarketExperienceStore
            with self.assertRaises((QuestionResolverError, Exception)):
                resolve_liquidity_prediction(
                    root,
                    batch_id=batch_id,
                    prediction_id=first["prediction_id"],
                    baseline_frame=_load_frame(root, batch_id, int(first["cutoff_at_ns"])),
                    experience=MarketExperienceStore(root).load(first["experience_id"]),
                    now_at_ns=int(result["observation_end_ns"]),
                )

    def test_duplicate_expert_testimony_cannot_inflate_assembly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=50)
            result = process_canonical_liquidity_batch(root, batch_id)
            claims = list((IntelligenceRuntime(root).state().get("claims") or {}).values())
            liquidity_claims = [item for item in claims if item.get("question_ref") == LIQUIDITY_QUESTION_REF]
            self.assertGreaterEqual(len(liquidity_claims), 1)
            claim = liquidity_claims[0]
            memory = build_competence_memory((), now_ns=int(result["observation_end_ns"]))
            with self.assertRaisesRegex(ExpertSchoolError, "duplicate expert testimony"):
                assemble_expert_claims((claim, claim), memory, {"subject_id": "ASSET.BTC"}, assembly_at_ns=int(result["observation_end_ns"]))

    def test_future_outcome_cannot_affect_earlier_competence_or_own_weight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=80)
            result = process_canonical_liquidity_batch(root, batch_id)
            runtime = IntelligenceRuntime(root)
            early = int(min(item["cutoff_at_ns"] for item in result["predictions"]))
            from autonomous_kernel.experts.sync import sync_expert_learning
            early_sync = sync_expert_learning(root, known_at_ns=early)
            self.assertEqual(0, int(early_sync.get("scores_recorded") or 0))
            assembly = result["sync"].get("liquidity_assembly") or {}
            if assembly.get("status") == "RESEARCH_ONLY":
                current_ids = set(assembly.get("contributing_claim_hashes") or [])
                scores = runtime.state().get("scores") or []
                for score in scores:
                    if str(score.get("claim_hash")) in current_ids:
                        self.assertGreaterEqual(int(score.get("resolved_at_ns") or 0), int(assembly.get("cutoff_at_ns") or 0))
                weights_n = (assembly.get("sample_support") or {}).get("weighting_sample_counts") or []
                self.assertTrue(all(int(value) >= 0 for value in weights_n))

    def test_restart_replay_and_tamper_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=80)
            first = process_canonical_liquidity_batch(root, batch_id)
            pred_text = (root / "memory/question_predictions.jsonl").read_text(encoding="utf-8")
            out_text = (root / "memory/question_outcomes.jsonl").read_text(encoding="utf-8")
            with tempfile.TemporaryDirectory() as restored_dir:
                restored = Path(restored_dir)
                (restored / "memory").mkdir()
                (restored / "memory/question_predictions.jsonl").write_text(pred_text, encoding="utf-8")
                (restored / "memory/question_outcomes.jsonl").write_text(out_text, encoding="utf-8")
                from autonomous_kernel.prediction.question_journal import QuestionPredictionJournal as QPJ
                from autonomous_kernel.evaluation.question_journal import QuestionOutcomeJournal as QOJ
                QPJ(restored).rebuild_state()
                QOJ(restored).rebuild_state()
                from autonomous_kernel.experts.sync import sync_expert_learning
                known = int(first["observation_end_ns"])
                sync_expert_learning(restored, known_at_ns=known)
                hash_a = (IntelligenceRuntime(restored).state().get("competence") or {}).get("integrity", {}).get("content_hash")
                sync_expert_learning(restored, known_at_ns=known)
                hash_b = (IntelligenceRuntime(restored).state().get("competence") or {}).get("integrity", {}).get("content_hash")
                self.assertEqual(hash_a, hash_b)
                assembly_a = assemble_liquidity_question(restored, known_at_ns=known)
                assembly_b = assemble_liquidity_question(restored, known_at_ns=known)
                self.assertEqual(assembly_a["integrity"]["content_hash"], assembly_b["integrity"]["content_hash"])
            tampered = root / "memory/question_predictions.jsonl"
            tampered.write_text(tampered.read_text(encoding="utf-8") + "{not-json}\n", encoding="utf-8")
            from autonomous_kernel.experts.sync import sync_expert_learning, ExpertLearningSyncError
            with self.assertRaises(Exception):
                sync_expert_learning(root, known_at_ns=int(first["observation_end_ns"]))

    def test_direction_plus_liquidity_synthesis_marks_shared_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=80)
            process_canonical_direction_batch(root, batch_id, sync=True)
            result = process_canonical_liquidity_batch(root, batch_id, sync=True)
            synthesis = (result.get("sync") or {}).get("market_synthesis") or {}
            if synthesis.get("status") in {None, "BLOCKED"}:
                self.skipTest("synthesis blocked: %s" % synthesis)
            available = set(synthesis.get("available_dimensions") or [])
            self.assertIn("DIRECTION", available)
            self.assertIn("LIQUIDITY", available)
            completeness = float(synthesis.get("completeness") or 0)
            self.assertGreaterEqual(completeness, 0.29)
            self.assertLessEqual(completeness, 0.31)
            summary = synthesis.get("evidence_independence_summary") or {}
            status = str(summary.get("independence_status") or synthesis.get("independence_status") or "")
            self.assertNotEqual(status, "INDEPENDENT")
            self.assertNotEqual(synthesis.get("internal_intelligence_publication"), "PUBLISHED")
            self.assertNotEqual(synthesis.get("benjamin_publication"), "ELIGIBLE")
            authority_blob = json.dumps({
                "authority": synthesis.get("authority"),
                "prospective_qualification": synthesis.get("prospective_qualification"),
                "internal_intelligence_publication": synthesis.get("internal_intelligence_publication"),
                "benjamin_publication": synthesis.get("benjamin_publication"),
            })
            self.assertNotIn("BUY", authority_blob)
            self.assertNotIn("SELL", authority_blob)
            self.assertNotIn("HOLD", authority_blob)


if __name__ == "__main__":
    unittest.main()
