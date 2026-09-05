from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from autonomous_kernel.microstream import StreamJournal
from autonomous_kernel.observer_perception import propagate_captured_window
from autonomous_kernel.operator.perception import perception_projection
from monitor.app.read_model import slice_context, slice_market


def _message(channel, sequence, timestamp="2026-09-04T18:00:00Z", events=None):
    return {"channel": channel, "timestamp": timestamp, "sequence_num": sequence, "events": events or []}


def _write_stream(root: Path) -> str:
    (root / "state").mkdir()
    (root / "state/market_data.json").write_text(
        '{"schema_version":1,"authority":"test","items":[]}\n', encoding="utf-8"
    )
    stream_id = "COINBASE-BTC-USD-OBS-TEST"
    journal = StreamJournal(root, stream_id)
    journal.ingest(
        _message(
            "level2",
            1,
            events=[{
                "type": "snapshot",
                "product_id": "BTC-USD",
                "updates": [
                    {"side": "bid", "price_level": "109999", "new_quantity": "1.5"},
                    {"side": "offer", "price_level": "110001", "new_quantity": "2"},
                ],
            }],
        ),
        1788372001000000000,
    )
    journal.ingest(
        _message(
            "level2",
            2,
            "2026-09-04T18:00:01Z",
            [{
                "type": "update",
                "product_id": "BTC-USD",
                "updates": [{"side": "bid", "price_level": "109998", "new_quantity": "0.4"}],
            }],
        ),
        1788372002000000000,
    )
    journal.ingest(
        _message(
            "market_trades",
            3,
            "2026-09-04T18:00:02Z",
            [{
                "type": "update",
                "product_id": "BTC-USD",
                "trades": [{"trade_id": "9", "price": "110000", "size": "0.01", "side": "BUY"}],
            }],
        ),
        1788372003000000000,
    )
    journal.ingest(_message("heartbeats", 4, "2026-09-04T18:00:03Z", [{"heartbeat_counter": "1"}]), 1788372004000000000)
    journal.finalize()
    return stream_id


class ObserverPerceptionTests(unittest.TestCase):
    def test_captured_window_materializes_z1_z2_and_truthful_degraded_z9(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stream_id = _write_stream(root)
            result = propagate_captured_window(root, {"stream_id": stream_id})
            self.assertEqual("PROPAGATED", result["status"])
            self.assertTrue(str(result["canonical_batch_id"]).startswith("CAN-"))
            self.assertGreater(int(result["canonical_record_count"]), 0)
            self.assertEqual("CRYPTO.SPOT.BTC-USD", json.loads((root / "artifacts/market_data/representations" / (result["representation_frame_id"] + ".json")).read_text(encoding="utf-8"))["frame"]["instrument"]["canonical_id"])
            self.assertIn(result["z9"]["status"], {"DEGRADED", "UNAVAILABLE"})
            self.assertIn("INSUFFICIENT_CORE_MARKET_BREADTH", result["z9"]["degraded_reasons"])
            self.assertFalse(result["authority"]["capital_allocation"])
            self.assertNotIn("buy", result)
            self.assertNotIn("position_size", result)

    def test_perception_projection_is_stale_without_inventing_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state").mkdir()
            (root / "state/market_observer.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "observer_id": "PUBLIC-MICROSTRUCTURE-OBSERVER-001",
                        "status": "IDLE",
                        "last_success_at": "2026-09-04T18:00:00Z",
                        "windows": [{"window_id": "W1", "quality": "VALID", "completed_at": "2026-09-04T18:00:00Z"}],
                    }
                ),
                encoding="utf-8",
            )
            (root / "state/canonical_market_data.json").write_text('{"schema_version":1,"items":[]}\n', encoding="utf-8")
            (root / "state/representations.json").write_text('{"schema_version":1,"representation_contract_version":"1.0","authority":"t","items":[]}\n', encoding="utf-8")
            (root / "state/market_context.json").write_text('{"schema_version":1,"context_contract_version":"1.0","authority":"t","items":[]}\n', encoding="utf-8")
            now_ns = int(datetime(2026, 9, 4, 19, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
            projection = perception_projection(root, now_ns=now_ns)
            self.assertEqual("STALE", projection["feed_status"])
            self.assertEqual("NO CURRENT FRAME", projection["z2_status"])
            self.assertEqual("NO CURRENT FRAME", projection["z9_status"])
            self.assertIsNone(projection["latest_instrument_state"])
            market = slice_market({"perception": projection, "stages": [{"id": "Z1"}, {"id": "Z2"}], "monitor": {"sections": {}}})
            self.assertIsNone(market["latest_instrument_state"])
            self.assertNotIn("best_bid", market)
            context = slice_context({"perception": projection, "stages": [{"id": "Z9"}], "certification": {}})
            self.assertEqual("NO CURRENT FRAME", context["operational_status"])

    def test_live_feed_status_uses_recent_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state").mkdir()
            (root / "state/market_observer.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "observer_id": "PUBLIC-MICROSTRUCTURE-OBSERVER-001",
                        "status": "IDLE",
                        "last_success_at": "2026-09-04T18:00:00Z",
                        "windows": [{"window_id": "W1", "quality": "VALID"}],
                    }
                ),
                encoding="utf-8",
            )
            live_ns = int(datetime(2026, 9, 4, 18, 0, 20, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
            projection = perception_projection(root, now_ns=live_ns)
            self.assertEqual("LIVE", projection["feed_status"])


if __name__ == "__main__":
    unittest.main()
