import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.microstream import StreamJournal
from autonomous_kernel.observation.materialize import materialize_coinbase_stream
from autonomous_kernel.observation.store import validate_canonical_market_data_store


def message(channel, sequence, timestamp="2026-09-02T18:00:00Z", events=None):
    return {"channel": channel, "timestamp": timestamp, "sequence_num": sequence, "events": events or []}


class CanonicalMaterializationTests(unittest.TestCase):
    def test_existing_stream_bundle_rebuilds_canonical_batch_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state").mkdir()
            (root / "state/market_data.json").write_text(
                '{"schema_version":1,"authority":"test","items":[]}\n', encoding="utf-8"
            )
            journal = StreamJournal(root, "Z1-STREAM")
            journal.ingest(
                message(
                    "level2",
                    1,
                    events=[{
                        "type": "snapshot",
                        "product_id": "BTC-USD",
                        "updates": [
                            {"side": "bid", "price_level": "59999", "new_quantity": "1"},
                            {"side": "offer", "price_level": "60001", "new_quantity": "1"},
                        ],
                    }],
                ),
                1788372001000000000,
            )
            journal.ingest(
                message(
                    "market_trades",
                    2,
                    "2026-09-02T18:00:01Z",
                    [{
                        "type": "update",
                        "product_id": "BTC-USD",
                        "trades": [
                            {"trade_id": "2", "price": "60000", "size": "0.01", "side": "BUY"}
                        ],
                    }],
                ),
                1788372002000000000,
            )
            journal.ingest(
                message("heartbeats", 3, "2026-09-02T18:00:02Z", [{"heartbeat_counter": "1"}]),
                1788372003000000000,
            )
            finalized = journal.finalize()
            first = materialize_coinbase_stream(root, "Z1-STREAM", default_symbol="BTC-USD")
            second = materialize_coinbase_stream(root, "Z1-STREAM", default_symbol="BTC-USD")
            self.assertEqual(first, second)
            self.assertEqual(2, first["record_count"])
            self.assertEqual({"BOOK_SNAPSHOT": 1, "TRADE": 1}, first["event_counts"])
            self.assertEqual(finalized["manifest"]["journal_sha256"], first["source_sha256"])
            self.assertEqual([], validate_canonical_market_data_store(root))


if __name__ == "__main__":
    unittest.main()
