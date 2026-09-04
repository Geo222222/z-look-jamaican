import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.market_data import MarketDataStore, build_candle_observation, validate_market_data_store


def observation(observation_id="OBS-1"):
    return build_candle_observation(
        observation_id=observation_id, provider="provider-a", instrument="BTC-USD",
        interval_seconds=300, candle_start_at=1000, received_at=1302, observed_at=1305,
        open_price="100", high_price="102", low_price="99", close_price="101", volume="4.5",
        max_event_age_seconds=10, max_transport_age_seconds=5,
    )


class MarketDataStoreTests(unittest.TestCase):
    def test_raw_normalized_lineage_and_quality_are_preserved(self):
        item = observation()
        self.assertEqual(item["observation_id"], item["normalized"]["raw_observation_id"])
        self.assertEqual("VALID", item["quality"]["status"])
        self.assertNotEqual(item["raw"], item["normalized"])

    def test_persist_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory))
            self.assertEqual(store.persist(observation()), store.persist(observation()))
            self.assertEqual([], validate_market_data_store(Path(directory)))

    def test_conflicting_observation_id_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory))
            store.persist(observation())
            changed = dict(observation())
            changed["integrity"] = {"algorithm": "sha256", "content_hash": "different"}
            with self.assertRaises(ValueError):
                store.persist(changed)

    def test_orphan_bundle_is_recovered_by_index_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = MarketDataStore(root)
            store.directory.mkdir(parents=True)
            path = store.directory / "OBS-1.json"
            path.write_text(json.dumps(observation()), encoding="utf-8")
            index = store.rebuild_index()
            self.assertEqual("OBS-1", index["items"][0]["observation_id"])

    def test_bundle_tampering_fails_validation_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = MarketDataStore(root)
            store.persist(observation())
            path = store.directory / "OBS-1.json"
            changed = json.loads(path.read_text(encoding="utf-8"))
            changed["normalized"]["close"] = "999"
            path.write_text(json.dumps(changed), encoding="utf-8")
            self.assertTrue(validate_market_data_store(root))
            with self.assertRaises(RuntimeError):
                store.rebuild_index()


if __name__ == "__main__":
    unittest.main()
