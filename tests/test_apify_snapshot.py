import unittest

from experiments.apify_store_snapshot import normalize_item, summarize


class ApifySnapshotTests(unittest.TestCase):
    def test_normalize_and_summarize(self):
        item = normalize_item(
            {
                "id": "actor-1",
                "title": "Example",
                "url": "https://example.invalid/actor",
                "currentPricingInfo": {"pricingModel": "PAY_PER_EVENT", "pricePerUnitUsd": 0.01},
                "stats": {
                    "totalUsers30Days": 7,
                    "totalRuns30Days": 11,
                    "totalRuns": 20,
                    "actorReviewCount": 3,
                    "actorReviewRating": 4.5,
                    "publicActorRunStats30Days": {"SUCCEEDED": 8, "FAILED": 2, "TOTAL": 10},
                },
            }
        )
        self.assertEqual(7, item["users_30_days"])
        self.assertEqual(10, item["runs_30_days"])
        self.assertEqual(3, item["review_count"])
        self.assertEqual(4.5, item["rating"])
        self.assertEqual(0.8, item["success_rate_30_days"])
        summary = summarize([item])
        self.assertEqual(7, summary["aggregate_users_30_days"])
        self.assertEqual(1, summary["paid_results_with_at_least_five_users"])

    def test_unknown_external_shapes_fail_safe_to_zero(self):
        item = normalize_item({"id": "actor-2", "stats": "bad", "currentPricingInfo": []})
        self.assertEqual(0, item["users_30_days"])
        self.assertIsNone(item["success_rate_30_days"])


if __name__ == "__main__":
    unittest.main()
