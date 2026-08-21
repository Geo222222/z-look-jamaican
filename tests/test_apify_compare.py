import unittest

from experiments.apify_store_compare import compare_snapshots
from experiments.apify_store_snapshot import COLLECTOR_REVISION


class ApifyCompareTests(unittest.TestCase):
    def _snapshot(self, captured_at, users, runs, reviews):
        return {
            "experiment_id": "EXP-OPP-001",
            "collector_revision": COLLECTOR_REVISION,
            "captured_at": captured_at,
            "queries": [
                {
                    "term": "example",
                    "summary": {"aggregate_users_30_days": users},
                    "items": [{"id": "actor", "runs_30_days": runs, "review_count": reviews}],
                }
            ],
        }

    def test_compare_calculates_deltas(self):
        result = compare_snapshots(
            self._snapshot("2026-08-21T00:00:00Z", 5, 10, 1),
            self._snapshot("2026-08-22T00:00:00Z", 7, 13, 2),
        )
        comparison = result["comparisons"][0]
        self.assertEqual(2, comparison["aggregate_users_30_days_delta"])
        self.assertEqual(3, comparison["aggregate_runs_30_days_delta"])
        self.assertEqual(1, comparison["aggregate_reviews_delta"])

    def test_compare_rejects_old_collector_revision(self):
        first = self._snapshot("2026-08-21T00:00:00Z", 5, 10, 1)
        first["collector_revision"] = 1
        with self.assertRaises(ValueError):
            compare_snapshots(first, self._snapshot("2026-08-22T00:00:00Z", 7, 13, 2))


if __name__ == "__main__":
    unittest.main()
