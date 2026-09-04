from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.operator.shadow_intelligence import build_shadow_intelligence_snapshot


ROOT = Path(__file__).resolve().parents[1]


class ShadowIntelligenceSnapshotTests(unittest.TestCase):
    def test_empty_runtime_never_fabricates_story_similarity_or_competence(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = build_shadow_intelligence_snapshot(Path(directory))

        contract = snapshot["contract"]
        self.assertEqual("PROSPECTIVE_SHADOW", contract["mode"])
        self.assertTrue(contract["read_only"])
        self.assertFalse(contract["capital_authority"])
        self.assertFalse(contract["risk_authority"])
        self.assertFalse(contract["execution_authority"])

        self.assertEqual("UNAVAILABLE", snapshot["market_story"]["context_status"])
        for subject in snapshot["market_story"]["subjects"]:
            self.assertTrue(subject["rows"])
            self.assertTrue(all(row["status"] == "UNAVAILABLE" for row in subject["rows"]))
        self.assertTrue(all(row["status"] == "UNAVAILABLE" for row in snapshot["market_story"]["market"]["rows"]))

        historical = snapshot["historical_context"]
        self.assertEqual("NOT_QUALIFIED", historical["status"])
        self.assertIsNone(historical["comparable_experiences"])
        self.assertEqual(0, historical["eligible_experience_records"])
        self.assertIsNone(historical["similarity_policy"])

        self.assertEqual("COLLECTING", snapshot["experts"]["status"])
        self.assertEqual([], snapshot["experts"]["items"])
        self.assertIn("NO_PERCENTAGE", snapshot["experts"]["competence_policy"])
        self.assertEqual(0, snapshot["learning"]["prediction_count"])
        self.assertEqual(0, snapshot["learning"]["outcome_count"])

    def test_corrupt_prediction_journal_surfaces_invalid_instead_of_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "memory/question_predictions.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not-json}\n", encoding="utf-8")
            snapshot = build_shadow_intelligence_snapshot(root)

        self.assertEqual("INVALID", snapshot["learning"]["prediction_journal_status"])
        self.assertTrue(snapshot["learning"]["prediction_journal_errors"])
        self.assertEqual(0, snapshot["learning"]["prediction_count"])
        self.assertEqual([], snapshot["questions"]["active"])
        self.assertIsNone(snapshot["evidence"]["prediction_journal_last_entry_hash"])

    def test_current_repository_projection_preserves_authority_ceiling_and_no_fake_scores(self):
        snapshot = build_shadow_intelligence_snapshot(ROOT)
        self.assertFalse(snapshot["contract"]["capital_authority"])
        self.assertFalse(snapshot["contract"]["risk_authority"])
        self.assertFalse(snapshot["contract"]["execution_authority"])
        self.assertIsNone(snapshot["historical_context"]["comparable_experiences"])
        for expert in snapshot["experts"]["items"]:
            competence = expert["competence"]
            self.assertEqual("COLLECTING", competence["status"])
            self.assertIsNone(competence["metric"])
            self.assertIsNone(competence["value"])

    def test_forward_question_registry_reports_resolver_readiness_without_model_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = build_shadow_intelligence_snapshot(Path(directory))
        registry = snapshot["questions"]["registry"]
        self.assertGreaterEqual(len(registry), 10)
        resolver_ready = [item for item in registry if item["lifecycle"] == "RESOLVER_READY"]
        self.assertTrue(resolver_ready)
        self.assertTrue(all(item["resolver_implementation_ref"] for item in resolver_ready))
        self.assertTrue(all("competence" not in item for item in registry))

    def test_frontend_loads_shadow_assets_and_keeps_shadow_authority_banner(self):
        html = (ROOT / "monitor/web/index.html").read_text(encoding="utf-8")
        script = (ROOT / "monitor/web/shadow-live.js").read_text(encoding="utf-8")
        self.assertIn("/assets/shadow-live.css", html)
        self.assertIn("/assets/shadow-live.js", html)
        self.assertIn("NO CAPITAL AUTHORITY", script)
        self.assertIn("NO EXECUTION", script)
        self.assertIn("NOT YET QUALIFIED", script)
        self.assertNotIn("4,381", script)
        self.assertNotIn("77%", script)


if __name__ == "__main__":
    unittest.main()
