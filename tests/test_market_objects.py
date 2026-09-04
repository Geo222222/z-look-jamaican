import copy
import json
import tempfile
import unittest
from pathlib import Path

from market_objects.core import validate_market_object
from market_objects.evidence.price import price_observation
from market_objects.measurements.normalized import normalized_price_series
from market_objects.store import MarketObjectStore, validate_market_object_store
from market_objects.strategies.registry import load_registry


class MarketObjectContractTests(unittest.TestCase):
    def evidence(self, index=0):
        return price_observation(object_id=f"EVIDENCE-TEST-{index:03d}", instrument="BTC-USD", exchange="test", asset="BTC",
            timestamp=f"2026-01-01T{index:02d}:00:00Z", interval="1H", open_price=100+index,
            high_price=102+index, low_price=99+index, close_price=101+index, volume=1,
            quote_volume=101, trade_count=2, taker_buy_quote_volume=51,
            source_record_id=str(index), source_sha256="a"*64, created_at="2026-01-02T00:00:00Z")

    def test_raw_evidence_has_no_interpretation_or_authority(self):
        item = self.evidence()
        self.assertEqual("EVIDENCE", item["layer"])
        self.assertFalse(item["permissions"]["execution_authority"])
        self.assertNotIn("trend", item["payload"])

    def test_derived_object_declares_its_inputs(self):
        evidence = [self.evidence(i) for i in range(21)]
        series = normalized_price_series(object_id="MEAS-TEST-SERIES", evidence_objects=evidence, created_at="2026-01-02T00:00:00Z")
        self.assertEqual(21, len(series["input_refs"]))
        self.assertEqual(set(series["payload"]["evidence_refs"]), {item["ref"] for item in series["input_refs"]})

    def test_integrity_and_embedded_undeclared_refs_are_rejected(self):
        item = copy.deepcopy(self.evidence())
        item["payload"]["bad_ref"] = "market://SILENT-PARENT"
        self.assertTrue(any("undeclared" in error or "integrity" in error for error in validate_market_object(item)))

    def test_store_resolves_lineage_and_rebuilds_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = [self.evidence(i) for i in range(21)]
            store = MarketObjectStore(root); store.persist_many(evidence)
            series = normalized_price_series(object_id="MEAS-TEST-SERIES", evidence_objects=evidence, created_at="2026-01-02T00:00:00Z")
            store.persist(series)
            self.assertEqual([], validate_market_object_store(root))
            self.assertEqual(22, json.loads((root / "state/market_objects.json").read_text())["object_count"])

    def test_registry_is_machine_readable_and_has_no_authority(self):
        registry = load_registry(Path(__file__).parents[1] / "config/strategy_registry.json")
        self.assertGreaterEqual(len(registry["strategies"]), 8)
        self.assertTrue(all(item["capital_authority"] is False and item["order_authority"] is False for item in registry["strategies"]))


if __name__ == "__main__":
    unittest.main()
