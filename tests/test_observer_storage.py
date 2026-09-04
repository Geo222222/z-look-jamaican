import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.observer_storage import (
    compact_successful_raw_journal,
    observer_storage_status,
)


class ObserverStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir()
        (self.root / "state").mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def write_limits(self, maximum_bytes, minimum_free_bytes=0):
        (self.root / "config/market_observer.json").write_text(
            json.dumps(
                {
                    "maximum_observer_storage_bytes": maximum_bytes,
                    "minimum_free_disk_bytes": minimum_free_bytes,
                }
            ),
            encoding="utf-8",
        )

    def test_storage_guard_blocks_when_observer_footprint_reaches_limit(self):
        self.write_limits(1)
        path = self.root / "artifacts/evidence/market/observer/window.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"xx")
        status = observer_storage_status(self.root)
        self.assertFalse(status["allowed"])
        self.assertIn("observer_storage_limit_reached", status["reasons"])

    def test_raw_journal_compacts_only_after_bundle_and_audit_verify(self):
        self.write_limits(10_000_000)
        stream_id = "COINBASE-BTC-USD-OBS-TEST"
        raw = b'{"schema_version":1}\n'
        compressed = gzip.compress(raw, mtime=0)

        raw_path = self.root / "runtime/market_stream" / (stream_id + ".jsonl")
        raw_path.parent.mkdir(parents=True)
        raw_path.write_bytes(raw)

        stream_dir = self.root / "artifacts/market_data/streams"
        stream_dir.mkdir(parents=True)
        bundle_path = stream_dir / (stream_id + ".jsonl.gz")
        bundle_path.write_bytes(compressed)
        manifest_path = stream_dir / (stream_id + ".manifest.json")
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "stream_id": stream_id,
                    "journal_sha256": hashlib.sha256(raw).hexdigest(),
                    "compressed_sha256": hashlib.sha256(compressed).hexdigest(),
                }
            ),
            encoding="utf-8",
        )

        audit_path = self.root / "evidence/audits/market_observer/window.json"
        audit_path.parent.mkdir(parents=True)
        audit_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "window_id": "window",
                    "stream_id": stream_id,
                    "outcome": "VALID_PUBLIC_OBSERVATION_WINDOW",
                }
            ),
            encoding="utf-8",
        )
        (self.root / "state/market_observer.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "observer_id": "TEST",
                    "windows": [
                        {
                            "stream_id": stream_id,
                            "audit_path": "evidence/audits/market_observer/window.json",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = compact_successful_raw_journal(self.root, stream_id)
        self.assertEqual(result["status"], "COMPACTED")
        self.assertFalse(raw_path.exists())
        self.assertTrue(bundle_path.exists())
        self.assertTrue(manifest_path.exists())
        self.assertTrue(audit_path.exists())

    def test_hash_mismatch_preserves_raw_journal(self):
        self.write_limits(10_000_000)
        stream_id = "COINBASE-BTC-USD-OBS-BAD"
        raw_path = self.root / "runtime/market_stream" / (stream_id + ".jsonl")
        raw_path.parent.mkdir(parents=True)
        raw_path.write_bytes(b"original")

        stream_dir = self.root / "artifacts/market_data/streams"
        stream_dir.mkdir(parents=True)
        bundle_path = stream_dir / (stream_id + ".jsonl.gz")
        bundle_path.write_bytes(gzip.compress(b"different", mtime=0))
        (stream_dir / (stream_id + ".manifest.json")).write_text(
            json.dumps(
                {
                    "journal_sha256": hashlib.sha256(b"original").hexdigest(),
                    "compressed_sha256": "not-the-real-hash",
                }
            ),
            encoding="utf-8",
        )
        audit_path = self.root / "evidence/audits/market_observer/window.json"
        audit_path.parent.mkdir(parents=True)
        audit_path.write_text(
            json.dumps({"outcome": "VALID_PUBLIC_OBSERVATION_WINDOW"}),
            encoding="utf-8",
        )
        (self.root / "state/market_observer.json").write_text(
            json.dumps(
                {
                    "windows": [
                        {
                            "stream_id": stream_id,
                            "audit_path": "evidence/audits/market_observer/window.json",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        result = compact_successful_raw_journal(self.root, stream_id)
        self.assertEqual(result["status"], "SKIPPED")
        self.assertTrue(raw_path.exists())


if __name__ == "__main__":
    unittest.main()
