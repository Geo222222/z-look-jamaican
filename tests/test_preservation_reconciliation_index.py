from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "artifacts/evidence/preservation/msi-reconciliation-index-20260904.json"


class PreservationReconciliationIndexTests(unittest.TestCase):
    def _index(self):
        value = json.loads(INDEX.read_text(encoding="utf-8"))
        self.assertEqual(1, value["schema_version"])
        self.assertEqual("PRESERVATION_RECONCILIATION_INDEX", value["artifact_type"])
        return value

    def test_index_preserves_four_remote_workstreams_without_granting_authority(self):
        value = self._index()
        workstreams = {item["workstream"]: item for item in value["preserved_workstreams"]}
        self.assertEqual(
            {
                "coinbase_public_market_observer",
                "old_generated_research_and_data",
                "old_market_object_source",
                "old_orphaned_committed_history",
            },
            set(workstreams),
        )
        authority = value["authority"]
        self.assertTrue(authority["evidence_discovery"])
        for field in (
            "rewrites_canonical_state",
            "qualifies_strategy",
            "selects_model",
            "claims_model_competence",
            "economic_decision",
            "capital_allocation",
            "risk_authorization",
            "external_execution",
        ):
            self.assertFalse(authority[field])

    def test_raw_binance_data_remains_preservation_branch_only(self):
        value = self._index()
        research = next(item for item in value["preserved_workstreams"] if item["workstream"] == "old_generated_research_and_data")
        self.assertEqual("preservation_branch_only", research["raw_dataset_disposition"])
        self.assertEqual(96, research["binance_archive_count"])
        self.assertTrue(research["binance_archives_checksum_verified"])
        self.assertEqual(64, len(research["binance_raw_family_aggregate_sha256"]))
        self.assertEqual(5, len(research["historical_results"]))
        self.assertIn("negative research evidence", research["research_memory"])

    def test_observer_state_snapshots_are_not_promoted_to_canonical_state(self):
        value = self._index()
        observer = next(item for item in value["preserved_workstreams"] if item["workstream"] == "coinbase_public_market_observer")
        self.assertEqual(5, len(observer["completed_windows"]))
        self.assertEqual(4, observer["failed_window_count"])
        self.assertIn("source-checkout snapshots only", observer["canonical_state_policy"])
        self.assertIn("rebuild current projections", observer["canonical_state_policy"])
        self.assertEqual("7762aacae1d670857d0d22c28e82033f46dd38c2", observer["tip_commit"])

    def test_old_strategy_and_opportunity_layers_remain_historical_only(self):
        value = self._index()
        source = next(item for item in value["preserved_workstreams"] if item["workstream"] == "old_market_object_source")
        self.assertIn("perception_lineage_graph", source["adapted_responsibilities"])
        self.assertIn("generic_falsification_controls", source["adapted_responsibilities"])
        self.assertEqual(
            {"strategy_registry", "strategy_applicability", "opportunity_candidates"},
            set(source["historical_only_responsibilities"]),
        )

    def test_all_remote_identity_fields_are_full_git_object_ids(self):
        value = self._index()
        for item in value["preserved_workstreams"]:
            self.assertEqual(40, len(item["tip_commit"]))
            self.assertTrue(all(ch in "0123456789abcdef" for ch in item["tip_commit"]))
        observer = value["preserved_workstreams"][0]
        self.assertEqual(40, len(observer["manifest_git_blob_sha1"]))
        research = value["preserved_workstreams"][1]
        self.assertEqual(40, len(research["manifest_git_blob_sha1"]))
        for artifact in research["historical_results"]:
            self.assertEqual(40, len(artifact["git_blob_sha1"]))


if __name__ == "__main__":
    unittest.main()
