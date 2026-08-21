from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from autonomous_kernel.store import repository_root
from experiments.apify_store_continue import decide, preflight


class ApifyContinuationTests(unittest.TestCase):
    def test_current_repository_cold_start_reports_closed_experiment(self) -> None:
        result = preflight(repository_root(), datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc))
        self.assertEqual(result["action"], "closed")
        self.assertEqual(result["reason"], "experiment_completed_and_automation_paused")
        self.assertEqual(result["experiment_id"], "EXP-OPP-001")
        self.assertEqual(result["automation_external_id"], "continue-exp-opp-001-daily")
        self.assertEqual(result["date_distinct_snapshot_count"], 1)

    def test_decision_captures_after_cadence(self) -> None:
        start = datetime(2026, 8, 21, 19, 50, tzinfo=timezone.utc)
        result = decide([{"captured_at": start}], start + timedelta(hours=21), start)
        self.assertEqual(result["action"], "capture")
        self.assertEqual(result["reason"], "cadence_elapsed_and_target_not_reached")

    def test_decision_waits_before_cadence(self) -> None:
        start = datetime(2026, 8, 21, 19, 50, tzinfo=timezone.utc)
        result = decide([{"captured_at": start}], start + timedelta(hours=19), start)
        self.assertEqual(result["action"], "wait")

    def test_decision_finalizes_at_seven_distinct_dates(self) -> None:
        start = datetime(2026, 8, 1, 16, 0, tzinfo=timezone.utc)
        snapshots = [{"captured_at": start + timedelta(days=offset)} for offset in range(7)]
        result = decide(snapshots, start + timedelta(days=7), start)
        self.assertEqual(result["action"], "finalize")
        self.assertEqual(result["date_distinct_snapshot_count"], 7)


if __name__ == "__main__":
    unittest.main()
