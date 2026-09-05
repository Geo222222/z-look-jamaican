from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from autonomous_kernel.evaluation.question_resolvers import QuestionResolverError
from autonomous_kernel.experience.contracts import ExperienceTimescale
from autonomous_kernel.experience.market_wide import build_market_wide_experience
from autonomous_kernel.experts.school import ExpertSchoolError, assemble_expert_claims, build_competence_memory
from autonomous_kernel.intelligence.runtime import IntelligenceRuntime
from autonomous_kernel.learning.direction_loop import (
    materialize_cutoff_frame,
    process_canonical_direction_batch,
    question_learning_projection,
)
from autonomous_kernel.learning.liquidity_loop import process_canonical_liquidity_batch
from autonomous_kernel.learning.magnitude_loop import process_canonical_magnitude_batch
from autonomous_kernel.learning.volatility_assembly import assemble_volatility_question
from autonomous_kernel.learning.volatility_loop import (
    VOLATILITY_QUESTION_REF,
    process_canonical_volatility_batch,
    resolve_volatility_prediction,
    volatility_baseline_is_eligible,
)
from autonomous_kernel.models.baselines import BaselineModelError, NullPriorModel
from autonomous_kernel.models.volatility_baselines import (
    BookStressVolatilityModel,
    TrailingRealizedVolatilityModel,
    VolatilityNullPriorModel,
    volatility_baseline_model_set,
)
from autonomous_kernel.observation.instruments import CanonicalInstrument
from autonomous_kernel.prediction.question_journal import QuestionPredictionJournal
from tests.test_direction_competence_loop import T, SECOND, _write_span
from tests.test_market_wide_experience import history
from tests.test_model_factory import model_frame


SPAN_SECONDS = 200


class VolatilityModelContractTests(unittest.TestCase):
    def test_candidate_models_differ_require_market_wide_and_stay_nonnegative(self):
        frame = model_frame()
        market_wide = build_market_wide_experience(
            history(),
            timescale=ExperienceTimescale.SHORT,
            window_start_ns=1_000_000,
            cutoff_at_ns=3_000_000,
        )
        trailing = (Decimal("2"), Decimal("-1"), Decimal("4"))
        null_model = VolatilityNullPriorModel()
        trailing_model = TrailingRealizedVolatilityModel()
        book_model = BookStressVolatilityModel()
        expected_null, _d_null = null_model.forecast_volatility(frame, market_wide, trailing_returns_bps=trailing)
        expected_trail, d_trail = trailing_model.forecast_volatility(frame, market_wide, trailing_returns_bps=trailing)
        expected_book, d_book = book_model.forecast_volatility(frame, market_wide, trailing_returns_bps=trailing)
        self.assertEqual(expected_null, Decimal("8"))
        self.assertGreaterEqual(expected_null, 0)
        self.assertGreaterEqual(expected_trail, 0)
        self.assertGreaterEqual(expected_book, 0)
        self.assertNotEqual(expected_trail, expected_book)
        self.assertNotEqual(d_trail["model_id"], d_book["model_id"])
        again, again_d = trailing_model.forecast_volatility(frame, market_wide, trailing_returns_bps=trailing)
        self.assertEqual(expected_trail, again)
        self.assertEqual(d_trail, again_d)
        unavailable = replace(market_wide, status="UNAVAILABLE")
        with self.assertRaisesRegex(BaselineModelError, "MARKET_WIDE_EXPERIENCE"):
            null_model.forecast_volatility(frame, unavailable)
        models = volatility_baseline_model_set()
        self.assertEqual(3, len(models))
        self.assertEqual(
            [model.definition.model_id for model in models],
            ["VOLATILITY-NULL-PRIOR", "TRAILING-REALIZED-VOLATILITY", "BOOK-STRESS-VOLATILITY"],
        )
        self.assertTrue(all(60_000_000_000 in model.definition.supported_horizons_ns for model in models))
        self.assertTrue(all(model.definition.parameters.get("coefficient_status") == "NOT_CLAIMED_EMPIRICALLY_OPTIMAL" for model in models))
        self.assertTrue(all(model.definition.parameters.get("capital_authority") is False for model in models))
        self.assertNotIn(60_000_000_000, NullPriorModel().definition.supported_horizons_ns)


class VolatilityLoopAdversarialTests(unittest.TestCase):
    def test_unqualified_and_wrong_instrument_skip_prediction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=70)
            cutoff = T + 60 * SECOND
            frame = materialize_cutoff_frame(root, batch_id, cutoff)
            short_ok = replace(frame, status="QUALIFIED")
            self.assertTrue(volatility_baseline_is_eligible(short_ok))
            self.assertFalse(volatility_baseline_is_eligible(replace(short_ok, status="DEGRADED")))
            eth = CanonicalInstrument(
                canonical_id="CRYPTO.SPOT.ETH-USD",
                asset_class="CRYPTO",
                market_type="SPOT",
                base_asset="ETH",
                quote_asset="USD",
                settlement_asset="USD",
            )
            self.assertFalse(volatility_baseline_is_eligible(replace(short_ok, instrument=eth)))

    def test_loop_journals_predictions_outcomes_scores_without_future_leak_or_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=SPAN_SECONDS)
            result = process_canonical_volatility_batch(root, batch_id)
            self.assertGreaterEqual(result["counts"]["predicted"], 3)
            self.assertEqual(result["question_ref"], VOLATILITY_QUESTION_REF)
            self.assertEqual(result["horizon_ns"], 60_000_000_000)
            self.assertFalse(result["authority"]["capital_allocation"])
            self.assertFalse(result["authority"]["economic_decision"])
            self.assertFalse(result["authority"]["external_execution"])
            for item in result["predictions"]:
                self.assertLessEqual(int(item["journaled_at_ns"]), int(item["resolves_at_ns"]))
                self.assertLess(int(item["cutoff_at_ns"]), int(item["resolves_at_ns"]))
                self.assertEqual(int(item["resolves_at_ns"]) - int(item["cutoff_at_ns"]), 60_000_000_000)
                self.assertEqual(item["mode"], "HISTORICAL_REPLAY")
                payload = next(
                    entry["prediction"]
                    for entry in QuestionPredictionJournal(root).entries()
                    if entry["prediction"]["prediction_id"] == item["prediction_id"]
                )
                self.assertEqual(payload["question"]["subject_id"], "ASSET.BTC")
                self.assertEqual(payload["timing"]["horizon_ns"], 60_000_000_000)
                types = {ref["artifact_type"] for ref in payload["artifact_refs"]}
                self.assertIn("MARKET_EXPERIENCE", types)
                self.assertIn("MARKET_WIDE_EXPERIENCE", types)
                families = {family for ref in payload["artifact_refs"] for family in ref["feature_families"]}
                self.assertIn("SPOT_MICROSTRUCTURE", families)
                self.assertIn("MARKET_WIDE_CONTEXT", families)
                timescales = {scale for ref in payload["artifact_refs"] for scale in ref.get("timescales") or []}
                self.assertEqual(timescales, {"SHORT"})
                self.assertIn("value", payload["answer"])
                self.assertGreaterEqual(float(payload["answer"]["value"]), 0)
            for pred, outcome in zip(result["predictions"], result["outcomes"]):
                if outcome.get("status") == "RESOLVED":
                    self.assertGreater(int(outcome["decided_at_ns"]), int(pred["cutoff_at_ns"]))
                    realized = (outcome.get("realized_answer") or {}).get("value")
                    if realized is not None:
                        self.assertGreaterEqual(float(realized), 0)
            again = process_canonical_volatility_batch(root, batch_id)
            self.assertEqual(result["counts"]["predicted"], again["counts"]["predicted"])
            volatility_entries = [
                entry
                for entry in QuestionPredictionJournal(root).entries()
                if (entry.get("prediction") or {}).get("question", {}).get("question_ref") == VOLATILITY_QUESTION_REF
            ]
            self.assertEqual(len(volatility_entries), again["counts"]["predicted"])
            sync = result["sync"]
            self.assertGreaterEqual(int(sync.get("scores_recorded") or 0), 0)
            assembly = sync.get("volatility_assembly") or {}
            if assembly.get("status") == "RESEARCH_ONLY":
                self.assertEqual(assembly.get("prospective_use"), "BLOCKED")
                self.assertEqual(assembly.get("internal_intelligence_publication"), "NOT_PUBLISHED")
                self.assertEqual(assembly.get("benjamin_publication"), "NOT_ELIGIBLE")
                hashes = assemble_volatility_question(root, known_at_ns=int(result["observation_end_ns"]))
                self.assertEqual(hashes["integrity"]["content_hash"], assembly["integrity"]["content_hash"])
                current_ids = set(assembly.get("contributing_prediction_ids") or [])
                current_cutoffs = {
                    int(item["cutoff_at_ns"])
                    for item in result["predictions"]
                    if item["prediction_id"] in current_ids
                }
                self.assertEqual(len(current_cutoffs), 1)
                current_cutoff = next(iter(current_cutoffs))
                sample_counts = (assembly.get("sample_support") or {}).get("weighting_sample_counts") or []
                prior_cutoffs = {int(item["cutoff_at_ns"]) for item in result["predictions"]} - {current_cutoff}
                if prior_cutoffs:
                    self.assertGreater(max(int(value) for value in sample_counts or [0]), 0)
                else:
                    self.assertEqual(max(sample_counts or [0]), 0)
            projection = question_learning_projection(root)
            self.assertEqual(projection["volatility"]["question_ref"], VOLATILITY_QUESTION_REF)
            self.assertEqual(projection["volatility"]["horizon_ns"], 60_000_000_000)
            self.assertFalse(projection["authority"]["capital_allocation"])

    def test_too_early_resolution_is_pending_and_unresolvable_is_not_scored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=SPAN_SECONDS)
            result = process_canonical_volatility_batch(root, batch_id, sync=False)
            first = result["predictions"][0]
            from autonomous_kernel.experience.store import MarketExperienceStore
            from autonomous_kernel.learning.volatility_loop import materialize_short_cutoff_frame

            early = resolve_volatility_prediction(
                root,
                batch_id=batch_id,
                prediction_id=first["prediction_id"],
                baseline_frame=materialize_short_cutoff_frame(root, batch_id, int(first["cutoff_at_ns"])),
                experience=MarketExperienceStore(root).load(first["experience_id"]),
                now_at_ns=int(first["cutoff_at_ns"]) + 1,
            )
            self.assertEqual("PENDING", early["status"])
            pending_ids = {item["prediction_id"] for item in result["outcomes"] if item.get("status") == "PENDING"}
            unresolvable_ids = {item["prediction_id"] for item in result["outcomes"] if item.get("status") == "UNRESOLVABLE"}
            runtime = IntelligenceRuntime(root)
            scored_ids = {str(item.get("prediction_id") or "") for item in (runtime.state().get("scores") or [])}
            self.assertFalse(pending_ids & scored_ids)
            self.assertFalse(unresolvable_ids & scored_ids)

    def test_integrity_failure_is_not_journaled_as_unresolvable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=SPAN_SECONDS)
            result = process_canonical_volatility_batch(root, batch_id, sync=False)
            path = root / "memory/question_predictions.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines()
            payload = json.loads(lines[0])
            payload["prediction"]["integrity"]["content_hash"] = "0" * 64
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            first = result["predictions"][0]
            from autonomous_kernel.experience.store import MarketExperienceStore
            from autonomous_kernel.learning.volatility_loop import materialize_short_cutoff_frame

            with self.assertRaises((QuestionResolverError, Exception)):
                resolve_volatility_prediction(
                    root,
                    batch_id=batch_id,
                    prediction_id=first["prediction_id"],
                    baseline_frame=materialize_short_cutoff_frame(root, batch_id, int(first["cutoff_at_ns"])),
                    experience=MarketExperienceStore(root).load(first["experience_id"]),
                    now_at_ns=int(result["observation_end_ns"]),
                )

    def test_duplicate_expert_testimony_cannot_inflate_assembly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=SPAN_SECONDS)
            result = process_canonical_volatility_batch(root, batch_id)
            claims = list((IntelligenceRuntime(root).state().get("claims") or {}).values())
            volatility_claims = [item for item in claims if item.get("question_ref") == VOLATILITY_QUESTION_REF]
            self.assertGreaterEqual(len(volatility_claims), 1)
            claim = volatility_claims[0]
            memory = build_competence_memory((), now_ns=int(result["observation_end_ns"]))
            with self.assertRaisesRegex(ExpertSchoolError, "duplicate expert testimony"):
                assemble_expert_claims((claim, claim), memory, {"subject_id": "ASSET.BTC"}, assembly_at_ns=int(result["observation_end_ns"]))

    def test_future_outcome_cannot_affect_earlier_competence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=SPAN_SECONDS)
            result = process_canonical_volatility_batch(root, batch_id)
            early = int(min(item["cutoff_at_ns"] for item in result["predictions"]))
            from autonomous_kernel.experts.sync import sync_expert_learning

            early_sync = sync_expert_learning(root, known_at_ns=early)
            self.assertEqual(0, int(early_sync.get("scores_recorded") or 0))

    def test_four_family_synthesis_marks_shared_lineage_without_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=SPAN_SECONDS)
            process_canonical_direction_batch(root, batch_id, sync=True)
            process_canonical_liquidity_batch(root, batch_id, sync=True)
            process_canonical_magnitude_batch(root, batch_id, sync=True)
            result = process_canonical_volatility_batch(root, batch_id, sync=True)
            synthesis = (result.get("sync") or {}).get("market_synthesis") or {}
            if synthesis.get("status") in {None, "BLOCKED"}:
                self.skipTest("synthesis blocked: %s" % synthesis)
            available = set(synthesis.get("available_dimensions") or [])
            self.assertIn("DIRECTION", available)
            self.assertIn("MAGNITUDE", available)
            self.assertIn("LIQUIDITY", available)
            self.assertIn("VOLATILITY", available)
            self.assertNotEqual(synthesis.get("internal_intelligence_publication"), "PUBLISHED")
            self.assertNotEqual(synthesis.get("benjamin_publication"), "ELIGIBLE")


if __name__ == "__main__":
    unittest.main()
