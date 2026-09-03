import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from autonomous_kernel.observation import CanonicalBatchStore, ProviderRecord, adapt_coinbase_advanced_trade
from autonomous_kernel.operations import canonical_hash


class CanonicalGzipDeterminismTests(unittest.TestCase):
    def test_canonical_container_uses_version_stable_gzip_header(self):
        message = {
            "channel": "market_trades",
            "timestamp": "2026-09-02T18:00:00.123456789Z",
            "sequence_num": 1,
            "events": [
                {
                    "type": "update",
                    "product_id": "BTC-USD",
                    "trades": [
                        {"trade_id": "1", "price": "60000", "size": "0.1", "side": "BUY"}
                    ],
                }
            ],
        }
        received_ns = int(datetime(2026, 9, 2, 18, 0, 1, tzinfo=timezone.utc).timestamp()) * 1_000_000_000
        record = ProviderRecord(
            provider="coinbase_advanced_trade_public_websocket",
            stream_id="CB-GZIP",
            received_at_ns=received_ns,
            message=message,
            message_hash=canonical_hash(message),
            raw_ref="artifacts/raw/CB-GZIP.json#event",
        )
        observations = adapt_coinbase_advanced_trade(record, default_symbol="BTC-USD")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = CanonicalBatchStore(root).persist_batch(
                batch_id="CAN-CB-GZIP",
                observations=observations,
                source_ref="artifacts/raw/CB-GZIP.json",
                source_sha256="a" * 64,
            )
            compressed = (root / manifest["path"]).read_bytes()
            self.assertEqual(b"\x1f\x8b", compressed[:2])
            self.assertEqual(b"\x00\x00\x00\x00", compressed[4:8])
            self.assertEqual(255, compressed[9])
            raw = gzip.decompress(compressed)
            self.assertEqual(manifest["canonical_jsonl_sha256"], __import__("hashlib").sha256(raw).hexdigest())
            parsed = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
            self.assertEqual(1, len(parsed))


if __name__ == "__main__":
    unittest.main()
