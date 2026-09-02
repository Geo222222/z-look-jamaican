import hashlib
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.book_outbox import ACKNOWLEDGED, PENDING, BookOutbox, OutboxConflict


class FailingTransport:
    def append_idempotent(self, *, envelope, payload):
        raise ConnectionError("Book unavailable")


class AcceptingTransport:
    def __init__(self):
        self.calls = 0

    def append_idempotent(self, *, envelope, payload):
        self.calls += 1
        return {
            "receipt_id": envelope["receipt_id"],
            "sequence": 7,
            "entry_hash": "a" * 64,
            "recorded_at": "2026-09-02T19:00:00Z",
            "accepted": True,
            "duplicate_replay": self.calls > 1,
        }


def envelope(payload: bytes):
    return {
        "schema_version": "2.0",
        "receipt_id": "ZLJ-R1",
        "producer": "ZLJ",
        "producer_key_id": "zlj-k1",
        "event_type": "ZLJ.INTELLIGENCE",
        "privacy_class": "CONFIDENTIAL_EVIDENCE",
        "payload_digest": hashlib.sha256(payload).hexdigest(),
        "signature": "signed",
    }


class BookOutboxTests(unittest.TestCase):
    def test_transient_failure_remains_pending_and_retries_same_record(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = BookOutbox(Path(directory))
            payload = b"intel"
            original = outbox.enqueue(envelope=envelope(payload), payload=payload)
            failed = outbox.deliver_one("ZLJ-R1", FailingTransport())
            self.assertEqual(PENDING, failed["state"])
            self.assertEqual(1, failed["attempt_count"])
            self.assertEqual(original["envelope_digest"], failed["envelope_digest"])
            self.assertEqual(("ZLJ-R1",), outbox.pending_receipt_ids())

            accepted = outbox.deliver_one("ZLJ-R1", AcceptingTransport())
            self.assertEqual(ACKNOWLEDGED, accepted["state"])
            self.assertEqual(2, accepted["attempt_count"])
            self.assertEqual(7, accepted["book_receipt"]["sequence"])
            self.assertEqual((), outbox.pending_receipt_ids())

    def test_receipt_id_cannot_be_reused_for_different_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = BookOutbox(Path(directory))
            first = b"one"
            outbox.enqueue(envelope=envelope(first), payload=first)
            second = b"two"
            with self.assertRaises(OutboxConflict):
                outbox.enqueue(envelope=envelope(second), payload=second)


if __name__ == "__main__":
    unittest.main()
