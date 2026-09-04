import unittest

from autonomous_kernel.market_data_quality import (
    DEGRADED,
    STALE,
    UNAVAILABLE,
    VALID,
    classify_market_data,
)


class MarketDataQualityTests(unittest.TestCase):
    def test_bounded_source_clock_lead_is_explicitly_tolerated(self):
        result = classify_market_data(provider="test", source_event_at=101, received_at=100, observed_at=100, max_event_age_seconds=10, max_transport_age_seconds=10, max_clock_skew_seconds=1)
        self.assertEqual(result.status, "VALID")
        self.assertEqual(result.source_clock_ahead_seconds, 1)
        self.assertEqual(result.clock_skew_tolerance_seconds, 1)
        self.assertEqual(result.event_age_seconds, 0)

    def classify(self, **overrides):
        values = {
            "provider": "public-venue",
            "source_event_at": 100,
            "received_at": 102,
            "observed_at": 105,
            "max_event_age_seconds": 10,
            "max_transport_age_seconds": 5,
        }
        values.update(overrides)
        return classify_market_data(**values)

    def test_fresh_complete_chain_is_valid(self):
        result = self.classify()
        self.assertEqual(VALID, result.status)
        self.assertTrue(result.action_permitted)
        self.assertEqual(5, result.event_age_seconds)

    def test_missing_provenance_is_unavailable(self):
        result = self.classify(provider=None)
        self.assertEqual(UNAVAILABLE, result.status)
        self.assertFalse(result.action_permitted)

    def test_future_or_reversed_chain_fails_closed(self):
        result = self.classify(source_event_at=104, received_at=103)
        self.assertEqual(UNAVAILABLE, result.status)
        self.assertIn("source_event_after_receive", result.reasons)

    def test_old_event_is_stale(self):
        result = self.classify(observed_at=120)
        self.assertEqual(STALE, result.status)
        self.assertFalse(result.action_permitted)

    def test_slow_transport_is_degraded_and_blocked(self):
        result = self.classify(received_at=108, observed_at=109)
        self.assertEqual(DEGRADED, result.status)
        self.assertFalse(result.action_permitted)

    def test_contract_serializes_with_schema_and_reasons(self):
        payload = self.classify().to_dict()
        self.assertEqual(1, payload["schema_version"])
        self.assertEqual([], payload["reasons"])


if __name__ == "__main__":
    unittest.main()
