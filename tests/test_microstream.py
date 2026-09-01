import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.microstream import StreamJournal, replay_records, validate_stream_bundles


def message(channel, sequence, timestamp="1970-01-01T00:16:40Z", events=None):
    return {"channel": channel, "timestamp": timestamp, "sequence_num": sequence, "events": events or []}


class MicrostreamTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "state").mkdir()
        (self.root / "state/market_data.json").write_text('{"schema_version":1,"authority":"test","items":[]}\n', encoding="utf-8")
        self.journal = StreamJournal(self.root, "TEST-STREAM")

    def tearDown(self):
        self.temporary.cleanup()

    def populate(self):
        snapshot = message("level2", 10, events=[{"type": "snapshot", "updates": [{"side": "bid", "price_level": "99", "new_quantity": "1"}, {"side": "offer", "price_level": "101", "new_quantity": "1"}]}])
        update = message("level2", 11, "1970-01-01T00:16:41Z", [{"type": "update", "updates": [{"side": "bid", "price_level": "100", "new_quantity": "1"}]}])
        trade = message("market_trades", 3, "1970-01-01T00:16:41Z", [{"type": "update", "trades": [{"trade_id": "1", "price": "100", "size": "0.1"}]}])
        heartbeat = message("heartbeats", 7, "1970-01-01T00:16:41Z", [{"heartbeat_counter": "1"}])
        for index, item in enumerate((snapshot, update, trade, heartbeat), 1):
            self.assertTrue(self.journal.ingest(item, index * 1_000_000_000))
        return snapshot

    def test_append_restart_duplicate_and_conflict_semantics(self):
        snapshot = self.populate()
        restarted = StreamJournal(self.root, "TEST-STREAM")
        self.assertEqual(len(restarted.records()), 4)
        self.assertFalse(restarted.ingest(snapshot, 9_000_000_000))
        conflicting = dict(snapshot)
        conflicting["events"] = []
        with self.assertRaisesRegex(RuntimeError, "conflicting duplicate"):
            restarted.ingest(conflicting, 10_000_000_000)

    def test_replay_detects_gaps_and_reproduces_book(self):
        self.populate()
        gap = message("heartbeats", 9, "1970-01-01T00:16:42Z", [{"heartbeat_counter": "3"}])
        self.journal.ingest(gap, 5_000_000_000)
        summary = replay_records(self.journal.records())
        self.assertEqual(summary["gaps"], [{"channel": "heartbeats", "after": 7, "before": 9, "missing": 1}])
        self.assertEqual(summary["level2_snapshot_count"], 1)
        self.assertEqual(summary["level2_update_count"], 1)
        self.assertEqual(summary["final_book"]["bids"][0], ("100", "1"))

    def test_deterministic_finalize_and_corruption_detection(self):
        self.populate()
        first = self.journal.finalize()
        second = self.journal.finalize()
        self.assertEqual(first["manifest"]["compressed_sha256"], second["manifest"]["compressed_sha256"])
        self.assertEqual(validate_stream_bundles(self.root), [])
        path = self.root / first["manifest"]["compressed_path"]
        content = bytearray(path.read_bytes())
        content[-1] ^= 1
        path.write_bytes(content)
        self.assertTrue(any("compressed stream hash mismatch" in error for error in validate_stream_bundles(self.root)))

    def test_corrupt_partial_journal_fails_restart(self):
        self.populate()
        with self.journal.path.open("a", encoding="utf-8") as handle:
            handle.write("{partial")
        with self.assertRaisesRegex(RuntimeError, "corrupt stream journal"):
            self.journal.records()


if __name__ == "__main__":
    unittest.main()
