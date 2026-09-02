import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

from autonomous_kernel.monitor import AVAILABILITY_STATES, monitor_snapshot
from autonomous_kernel.store import REQUIRED_JSONL_FILES, REQUIRED_JSON_FILES, repository_root


class MonitorSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.root = repository_root()

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def monitored_files(self):
        paths = [self.root / path for path in REQUIRED_JSON_FILES + REQUIRED_JSONL_FILES]
        paths.extend((self.root / "state/market_shadow.json", self.root / "config/treasury_destinations.yaml"))
        receipt_dir = self.root / "receipts/execution"
        if receipt_dir.is_dir():
            paths.extend(receipt_dir.glob("*.json"))
        return sorted(set(path for path in paths if path.is_file()))

    def test_snapshot_has_every_contract_section_and_provenance(self):
        snapshot = monitor_snapshot(self.root, observed_at="2026-08-31T22:00:00Z")
        expected = {
            "system_health", "active_experiment", "experiment_history", "decisions",
            "evidence_events", "data_quality", "opportunities", "reflections",
            "goals_tasks", "economics", "financial_exposure", "wallets", "treasury",
            "governor", "deployments", "incidents", "runtime_logs",
            "model_provider_qualification",
            "experiment_registry", "capability_registry", "execution_plane",
            "accounting_reconciliation",
            "market_data",
        }
        self.assertEqual(set(snapshot["sections"]), expected)
        self.assertEqual(snapshot["contract"]["schema_version"], "1.1.0")
        self.assertTrue(snapshot["contract"]["read_only"])
        for section in snapshot["sections"].values():
            self.assertIn(section["availability"]["state"], AVAILABILITY_STATES)
            self.assertEqual(
                set(("source", "source_id", "path", "paths", "observed_at", "authoritative_at", "schema_version", "integrity")) - set(section["provenance"]),
                set(),
            )

    def test_current_shadow_counts_and_economics_are_not_misclassified(self):
        snapshot = monitor_snapshot(self.root, observed_at="2026-08-31T22:00:00Z")
        decisions = snapshot["sections"]["decisions"]["data"]
        self.assertEqual(decisions["counts"]["total"], len(decisions["prospective"]) + len(decisions["resolved"]))
        self.assertEqual(decisions["counts"]["timestamp_violations"], 0)
        self.assertEqual(snapshot["sections"]["economics"]["availability"]["state"], "not_earned")
        self.assertTrue(snapshot["sections"]["economics"]["data"]["shadow_pnl_excluded_from_realized"])
        self.assertEqual(snapshot["sections"]["financial_exposure"]["data"]["recorded_current_exposure_usd"], 0)
        self.assertEqual(snapshot["sections"]["wallets"]["availability"]["state"], "not_earned")
        self.assertEqual(snapshot["sections"]["treasury"]["availability"]["state"], "blocked")
        self.assertFalse(snapshot["sections"]["execution_plane"]["data"]["live_enabled"])
        reconciliation = snapshot["sections"]["accounting_reconciliation"]["data"]
        self.assertEqual(reconciliation["state"], "MATCHED")
        self.assertTrue(reconciliation["comparison_performed"])
        self.assertEqual(reconciliation["discrepancy_count"], 0)
        self.assertEqual(reconciliation["truth_sources"], ["DECLARED_SHADOW_FILL_EVENTS"])
        self.assertFalse(reconciliation["external_comparison_performed"])
        self.assertIsNone(reconciliation["external_discrepancy_count"])
        market_data = snapshot["sections"]["market_data"]["data"]
        indexed = market_data["index"]["items"]
        self.assertEqual(market_data["observation_count"], len(indexed))
        self.assertEqual(sum(market_data["quality_counts"].values()), len(indexed))
        self.assertIn("OBS-COINBASE-BTC-USD-1788273600", {item["observation_id"] for item in indexed})
        qualification = market_data["qualification"]
        self.assertTrue(qualification["policy"]["venue_neutral"])
        self.assertTrue(qualification["policy"]["fail_closed"])
        self.assertFalse(qualification["policy"]["retroactive_evidence_binding_permitted"])
        self.assertEqual("NOT_EARNED", qualification["shadow_evidence"]["certification_state"])
        self.assertEqual(
            qualification["shadow_evidence"]["decision_count"],
            qualification["shadow_evidence"]["legacy_unjoined_count"],
        )
        self.assertEqual("NOT_EARNED", market_data["joined_shadow_certification"])
        self.assertGreaterEqual(qualification["market_plane"]["sequence_counts"]["QUALIFIED"], 1)

    def test_library_snapshot_is_byte_for_byte_read_only(self):
        paths = self.monitored_files()
        before = {path: (self.digest(path), path.stat().st_mtime_ns) for path in paths}
        first = monitor_snapshot(self.root, observed_at="2026-08-31T22:00:00Z")
        second = monitor_snapshot(self.root, observed_at="2026-08-31T22:00:00Z")
        after = {path: (self.digest(path), path.stat().st_mtime_ns) for path in paths}
        self.assertEqual(first, second)
        self.assertEqual(before, after)

    def test_cli_emits_json_without_mutating_state(self):
        paths = self.monitored_files()
        before = {path: self.digest(path) for path in paths}
        completed = subprocess.run(
            [sys.executable, "-m", "autonomous_kernel", "--root", str(self.root), "monitor_snapshot", "--json"],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        document = json.loads(completed.stdout)
        after = {path: self.digest(path) for path in paths}
        self.assertEqual(document["contract"]["name"], "z-look-jamaican-monitor-snapshot")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
