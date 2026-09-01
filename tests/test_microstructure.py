import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.market_data import MarketDataStore, build_microstructure_observation, validate_market_data_store


class MicrostructureObservationTests(unittest.TestCase):
    def build(self):
        payloads = {
            "product_rules": {"id": "BTC-USD", "base_increment": "0.00000001", "quote_increment": "0.01", "min_market_funds": "10", "status": "online", "post_only": False, "limit_only": False, "cancel_only": False, "trading_disabled": False, "auction_mode": False},
            "level2_book": {"sequence": 123, "time": "1970-01-01T00:16:40Z", "bids": [["99", "0.01", 1], ["98", "1", 1]], "asks": [["101", "0.005", 1], ["102", "1", 1]]},
            "ticker": {"trade_id": 9, "price": "100", "size": "0.1", "time": "1970-01-01T00:16:39Z", "bid": "99", "ask": "101", "volume": "10"},
            "recent_trades": [{"trade_id": 9, "price": "100", "size": "0.1", "time": "1970-01-01T00:16:39Z", "side": "buy"}, {"trade_id": 8, "price": "99", "size": "0.2", "time": "1970-01-01T00:16:38Z", "side": "sell"}],
        }
        surfaces = list(payloads)
        return build_microstructure_observation(
            observation_id="OBS-MICRO-123", provider="test_provider", instrument="BTC-USD",
            payloads=payloads, payload_hashes={name: name * 2 for name in surfaces},
            request_started_at={name: 1000 for name in surfaces}, received_at={name: 1001 for name in surfaces},
            request_duration_ms={name: 25 for name in surfaces}, observed_at=1001,
            max_event_age_seconds=10, max_transport_age_seconds=10,
            test_quantities=["0.001", "0.01"], depth_bands_bps=["100"],
        )

    def test_normalizes_spread_depth_rules_and_unavailable_truth(self):
        document = self.build()
        normalized = document["normalized"]
        self.assertEqual(normalized["quoted_spread_bps"], "200.00")
        self.assertTrue(normalized["depth_walks"]["0.001"]["buy"]["sufficient_depth"])
        self.assertEqual(normalized["depth_walks"]["0.001"]["buy"]["vwap"], "101")
        self.assertEqual(normalized["depth_walks"]["0.01"]["buy"]["vwap"], "101.5")
        self.assertEqual(normalized["product_rules"]["min_market_funds"], "10")
        self.assertIn("actual_fill_truth", normalized["unavailable_for_qualification"])
        self.assertEqual(document["quality"]["status"], "VALID")

    def test_crossed_book_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "crossed or locked"):
            payloads = self.build()["raw"]["provider_payload"]
            payloads["level2_book"]["bids"][0][0] = "101"
            build_microstructure_observation(
                observation_id="OBS-CROSSED", provider="test", instrument="BTC-USD", payloads=payloads,
                payload_hashes={name: "x" for name in payloads}, request_started_at={name: 1000 for name in payloads},
                received_at={name: 1001 for name in payloads}, request_duration_ms={name: 1 for name in payloads},
                observed_at=1001, max_event_age_seconds=10, max_transport_age_seconds=10,
                test_quantities=["0.001"], depth_bands_bps=["5"],
            )

    def test_generic_immutable_store_rebuilds_microstructure_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "state").mkdir()
            (root / "state/market_data.json").write_text('{"schema_version":1,"authority":"test","items":[]}\n', encoding="utf-8")
            store = MarketDataStore(root)
            persisted = store.persist(self.build())
            self.assertEqual(persisted, store.persist(self.build()))
            self.assertEqual(validate_market_data_store(root), [])
            self.assertEqual(store.rebuild_index()["items"][0]["channel"], "microstructure_snapshot")


if __name__ == "__main__":
    unittest.main()
