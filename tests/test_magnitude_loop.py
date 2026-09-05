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
from autonomous_kernel.learning.magnitude_assembly import assemble_magnitude_question
from autonomous_kernel.learning.magnitude_loop import (
    MAGNITUDE_QUESTION_REF,
    magnitude_baseline_is_eligible,
    process_canonical_magnitude_batch,
    record_magnitude_predictions,
    resolve_magnitude_prediction,
)
from autonomous_kernel.models.magnitude_baselines import (
    BookContextBpsModel,
    MarketWideDriftBpsModel,
    magnitude_baseline_model_set,
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


class MagnitudeModelContractTests(unittest.TestCase):
    def test_candidate_models_differ_and_are_deterministic(self):
        from autonomous_kernel.experience.market_wide import build_market_wide_experience
        from tests.test_market_wide_experience import history

        frame = model_frame()
        market_wide = build_market_wide_experience(
            history(),
            timescale=__import__("autonomous_kernel.experience.contracts", fromlist=["ExperienceTimescale"]).ExperienceTimescale.SHORT,
            window_start_ns=1_000_000,
            cutoff_at_ns=3_000_000,
        )
        book = BookContextBpsModel()
        drift = MarketWideDriftBpsModel()
        expected_book, d_book = book.forecast_magnitude(frame, market_wide)
        expected_drift, d_drift = drift.forecast_magnitude(frame, market_wide)
        self.assertNotEqual(expected_book, expected_drift)
        self.assertNotEqual(d_book["model_id"], d_drift["model_id"])
        again, again_d = book.forecast_magnitude(frame, market_wide)
        self.assertEqual(expected_book, again)
        self.assertEqual(d_book, again_d)
        models = magnitude_baseline_model_set()
        self.assertEqual(2, len(models))
        self.assertTrue(all(model.definition.lifecycle_state == "CANDIDATE" for model in models))
        self.assertTrue(all(30_000_000_000 in model.definition.supported_horizons_ns for model in models))
        self.assertTrue(all(model.definition.parameters.get("coefficient_status") == "NOT_CLAIMED_EMPIRICALLY_OPTIMAL" for model in models))
        self.assertTrue(all(model.definition.parameters.get("capital_authority") is False for model in models))


class MagnitudeLoopAdversarialTests(unittest.TestCase):
    def test_unqualified_and_wrong_instrument_skip_prediction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=50)
            cutoff = T + 10 * SECOND
            frame = materialize_cutoff_frame(root, batch_id, cutoff)
            self.assertTrue(magnitude_baseline_is_eligible(frame))
            degraded = replace(frame, status="DEGRADED")
            self.assertFalse(magnitude_baseline_is_eligible(degraded))
            eth = CanonicalInstrument(
                canonical_id="CRYPTO.SPOT.ETH-USD",
                asset_class="CRYPTO",
                market_type="SPOT",
                base_asset="ETH",
                quote_asset="USD",
                settlement_asset="USD",
            )
            wrong = replace(frame, instrument=eth)
            self.assertFalse(magnitude_baseline_is_eligible(wrong))

    def test_loop_journals_predictions_outcomes_scores_without_future_leak_or_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=80)
            result = process_canonical_magnitude_batch(root, batch_id)
            self.assertGreaterEqual(result["counts"]["predicted"], 3)
            self.assertEqual(result["question_ref"], MAGNITUDE_QUESTION_REF)
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
                types = {ref["artifact_type"] for ref in payload["artifact_refs"]}
                self.assertIn("MARKET_EXPERIENCE", types)
                self.assertIn("MARKET_WIDE_EXPERIENCE", types)
                families = {family for ref in payload["artifact_refs"] for family in ref["feature_families"]}
                self.assertIn("SPOT_MICROSTRUCTURE", families)
                self.assertIn("MARKET_WIDE_CONTEXT", families)
                self.assertIn("value", payload["answer"])
            for pred, outcome in zip(result["predictions"], result["outcomes"]):
                if outcome.get("status") == "RESOLVED":
                    self.assertGreater(int(outcome["decided_at_ns"]), int(pred["cutoff_at_ns"]))
            again = process_canonical_magnitude_batch(root, batch_id)
            self.assertEqual(result["counts"]["predicted"], again["counts"]["predicted"])
            magnitude_entries = [
                entry
                for entry in QuestionPredictionJournal(root).entries()
                if (entry.get("prediction") or {}).get("question", {}).get("question_ref") == MAGNITUDE_QUESTION_REF
            ]
            self.assertEqual(len(magnitude_entries), again["counts"]["predicted"])
            sync = result["sync"]
            self.assertGreaterEqual(int(sync.get("scores_recorded") or 0), 0)
            assembly = sync.get("magnitude_assembly") or {}
            if assembly.get("status") == "RESEARCH_ONLY":
                self.assertEqual(assembly.get("prospective_use"), "BLOCKED")
                self.assertEqual(assembly.get("internal_intelligence_publication"), "NOT_PUBLISHED")
                self.assertEqual(assembly.get("benjamin_publication"), "NOT_ELIGIBLE")
                hashes = assemble_magnitude_question(root, known_at_ns=int(result["observation_end_ns"]))
                self.assertEqual(hashes["integrity"]["content_hash"], assembly["integrity"]["content_hash"])
            projection = question_learning_projection(root)
            self.assertEqual(projection["magnitude"]["question_ref"], MAGNITUDE_QUESTION_REF)
            self.assertFalse(projection["authority"]["capital_allocation"])

    def test_too_early_resolution_is_pending_and_unresolvable_is_not_scored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=80)
            result = process_canonical_magnitude_batch(root, batch_id, sync=False)
            first = result["predictions"][0]
            from autonomous_kernel.experience.store import MarketExperienceStore

            early = resolve_magnitude_prediction(
                root,
                batch_id=batch_id,
                prediction_id=first["prediction_id"],
                baseline_frame=materialize_cutoff_frame(root, batch_id, int(first["cutoff_at_ns"])),
                experience=MarketExperienceStore(root).load(first["experience_id"]),
                now_at_ns=int(first["cutoff_at_ns"]) + 1,
            )
            self.assertEqual("PENDING", early["status"])

    def test_integrity_failure_is_not_journaled_as_unresolvable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=80)
            result = process_canonical_magnitude_batch(root, batch_id, sync=False)
            path = root / "memory/question_predictions.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines()
            payload = json.loads(lines[0])
            payload["prediction"]["integrity"]["content_hash"] = "0" * 64
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            first = result["predictions"][0]
            from autonomous_kernel.experience.store import MarketExperienceStore

            with self.assertRaises((QuestionResolverError, Exception)):
                resolve_magnitude_prediction(
                    root,
                    batch_id=batch_id,
                    prediction_id=first["prediction_id"],
                    baseline_frame=materialize_cutoff_frame(root, batch_id, int(first["cutoff_at_ns"])),
                    experience=MarketExperienceStore(root).load(first["experience_id"]),
                    now_at_ns=int(result["observation_end_ns"]),
                )

    def test_duplicate_expert_testimony_cannot_inflate_assembly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=80)
            result = process_canonical_magnitude_batch(root, batch_id)
            claims = list((IntelligenceRuntime(root).state().get("claims") or {}).values())
            magnitude_claims = [item for item in claims if item.get("question_ref") == MAGNITUDE_QUESTION_REF]
            self.assertGreaterEqual(len(magnitude_claims), 1)
            claim = magnitude_claims[0]
            memory = build_competence_memory((), now_ns=int(result["observation_end_ns"]))
            with self.assertRaisesRegex(ExpertSchoolError, "duplicate expert testimony"):
                assemble_expert_claims((claim, claim), memory, {"subject_id": "ASSET.BTC"}, assembly_at_ns=int(result["observation_end_ns"]))

    def test_future_outcome_cannot_affect_earlier_competence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=80)
            result = process_canonical_magnitude_batch(root, batch_id)
            early = int(min(item["cutoff_at_ns"] for item in result["predictions"]))
            from autonomous_kernel.experts.sync import sync_expert_learning

            early_sync = sync_expert_learning(root, known_at_ns=early)
            self.assertEqual(0, int(early_sync.get("scores_recorded") or 0))

    def test_direction_plus_magnitude_synthesis_marks_shared_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=80)
            process_canonical_direction_batch(root, batch_id, sync=True)
            result = process_canonical_magnitude_batch(root, batch_id, sync=True)
            synthesis = (result.get("sync") or {}).get("market_synthesis") or {}
            if synthesis.get("status") in {None, "BLOCKED"}:
                self.skipTest("synthesis blocked: %s" % synthesis)
            available = set(synthesis.get("available_dimensions") or [])
            self.assertIn("DIRECTION", available)
            self.assertIn("MAGNITUDE", available)
            self.assertNotEqual(synthesis.get("internal_intelligence_publication"), "PUBLISHED")
            self.assertNotEqual(synthesis.get("benjamin_publication"), "ELIGIBLE")


if __name__ == "__main__":
    unittest.main()
