from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from autonomous_kernel.observation import (
    PublicSourceCaptureError,
    RawPublicSourceJournal,
    binance_spot_source,
    capture_public_source_window,
    kraken_spot_source,
)
from autonomous_kernel.observation.instruments import InstrumentIdentityError


TS = "2026-09-04T01:00:00.000000000Z"
EVENT_MS = int(datetime(2026, 9, 4, 1, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
RECEIVED_NS = EVENT_MS * 1_000_000 + 1_000_000


class FakeSocket:
    def __init__(self, messages):
        self.messages = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in messages]
        self.sent = []

    async def send(self, value):
        self.sent.append(json.loads(value))

    async def recv(self):
        if not self.messages:
            raise AssertionError("fake source exhausted before capture bound")
        return self.messages.pop(0)


class FakeConnection:
    def __init__(self, socket):
        self.socket = socket

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _connect_factory(messages, holder):
    def connect(endpoint, **kwargs):
        socket = FakeSocket(messages)
        holder["endpoint"] = endpoint
        holder["kwargs"] = kwargs
        holder["socket"] = socket
        return FakeConnection(socket)

    return connect


def _clock(values):
    items = list(values)

    def now_ns():
        if not items:
            raise AssertionError("clock exhausted")
        return items.pop(0)

    return now_ns


def _kraken_messages():
    return [
        {
            "channel": "book",
            "type": "snapshot",
            "data": [
                {
                    "symbol": "BTC/USD",
                    "timestamp": TS,
                    "checksum": 123,
                    "bids": [{"price": "59999", "qty": "1.2"}],
                    "asks": [{"price": "60001", "qty": "0.8"}],
                }
            ],
        },
        {
            "channel": "book",
            "type": "update",
            "data": [
                {
                    "symbol": "BTC/USD",
                    "timestamp": TS,
                    "checksum": 124,
                    "bids": [["60000", "0.4"]],
                    "asks": [],
                }
            ],
        },
        {
            "channel": "trade",
            "type": "update",
            "data": [
                {
                    "symbol": "BTC/USD",
                    "trade_id": 9001,
                    "price": "60000",
                    "qty": "0.01",
                    "side": "buy",
                    "timestamp": TS,
                }
            ],
        },
    ]


def _binance_messages():
    return [
        {
            "stream": "btcusdt@trade",
            "data": {
                "e": "trade",
                "E": EVENT_MS,
                "s": "BTCUSDT",
                "t": 9001,
                "p": "60000.00",
                "q": "0.010",
                "b": 88,
                "a": 99,
                "T": EVENT_MS,
                "m": True,
                "M": True,
            },
        },
        {
            "stream": "btcusdt@depth@100ms",
            "data": {
                "e": "depthUpdate",
                "E": EVENT_MS,
                "s": "BTCUSDT",
                "U": 100,
                "u": 105,
                "b": [["59999.00", "1.2"]],
                "a": [["60001.00", "0.8"]],
            },
        },
    ]


def _capture(root, spec, messages, stream_id):
    holder = {}
    return holder, capture_public_source_window(
        root,
        spec,
        stream_id=stream_id,
        capture_seconds=5,
        maximum_messages=len(messages),
        maximum_uncompressed_bytes=1_000_000,
        message_idle_timeout_seconds=2,
        connect_factory=_connect_factory(messages, holder),
        clock_ns=_clock(RECEIVED_NS + index for index in range(len(messages))),
    )


class PublicSourceCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_kraken_capture_preserves_raw_evidence_and_canonical_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = kraken_spot_source()
            holder, pending = _capture(root, spec, _kraken_messages(), "KR-CAPTURE-001")
            result = await pending

            self.assertEqual("wss://ws.kraken.com/v2", holder["endpoint"])
            self.assertEqual([dict(item) for item in spec.subscription_messages], holder["socket"].sent)
            self.assertTrue(all("api_key" not in json.dumps(item).lower() for item in holder["socket"].sent))
            self.assertEqual("kraken_websocket_v2", result["provider"])
            self.assertEqual("CRYPTO.SPOT.BTC-USD", result["canonical_instrument_id"])
            self.assertEqual(
                {"BOOK_DELTA": 1, "BOOK_SNAPSHOT": 1, "TRADE": 1},
                result["event_counts"],
            )
            self.assertEqual("CAPTURED_NOT_SOURCE_QUALIFIED", result["qualification_claim"])
            self.assertFalse(result["authority"]["authentication"])
            self.assertFalse(result["authority"]["capital_decision"])
            self.assertFalse(result["authority"]["risk_authorization"])
            self.assertFalse(result["authority"]["external_execution"])

            raw_manifest_path = root / result["raw_manifest_path"]
            raw_manifest = json.loads(raw_manifest_path.read_text(encoding="utf-8"))
            compressed_path = root / raw_manifest["compressed_path"]
            raw = gzip.decompress(compressed_path.read_bytes())
            self.assertEqual(result["raw_journal_sha256"], __import__("hashlib").sha256(raw).hexdigest())
            raw_entries = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
            self.assertEqual(3, len(raw_entries))
            self.assertIsNone(raw_entries[0]["previous_entry_hash"])
            self.assertEqual(raw_entries[0]["entry_hash"], raw_entries[1]["previous_entry_hash"])
            self.assertEqual(raw_entries[1]["entry_hash"], raw_entries[2]["previous_entry_hash"])

            canonical_manifest_path = root / "artifacts/market_data/canonical/CANON-KR-CAPTURE-001.manifest.json"
            canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(raw_manifest_path.relative_to(root).as_posix(), canonical_manifest["source_ref"])
            self.assertEqual(3, canonical_manifest["record_count"])
            self.assertEqual(["kraken_websocket_v2"], canonical_manifest["providers"])

    async def test_binance_capture_is_trade_plus_delta_only_and_keeps_usdt_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = binance_spot_source()
            holder, pending = _capture(root, spec, _binance_messages(), "BN-CAPTURE-001")
            result = await pending

            self.assertEqual("wss://stream.binance.com:9443/stream", holder["endpoint"])
            self.assertEqual([dict(item) for item in spec.subscription_messages], holder["socket"].sent)
            self.assertEqual("CRYPTO.SPOT.BTC-USDT", result["canonical_instrument_id"])
            self.assertEqual({"BOOK_DELTA": 1, "TRADE": 1}, result["event_counts"])
            self.assertNotIn("BOOK_SNAPSHOT", result["event_counts"])
            self.assertEqual("DELTA_ONLY_NO_QUALIFIED_SNAPSHOT_IN_MS2", result["book_snapshot_semantics"])
            self.assertEqual("CAPTURED_NOT_SOURCE_QUALIFIED", result["qualification_claim"])
            self.assertEqual("PUBLIC_READ_ONLY", result["authority"]["network_access"])
            self.assertFalse(result["authority"]["authentication"])

    async def test_same_input_stream_is_reproducible_across_independent_roots(self):
        results = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                spec = binance_spot_source()
                holder, pending = _capture(root, spec, _binance_messages(), "BN-DETERMINISTIC-001")
                result = await pending
                manifest = json.loads(
                    (root / "artifacts/market_data/canonical/CANON-BN-DETERMINISTIC-001.manifest.json").read_text(
                        encoding="utf-8"
                    )
                )
                results.append(
                    (
                        result["raw_journal_sha256"],
                        result["canonical_manifest_hash"],
                        manifest["observation_set_hash"],
                    )
                )
        self.assertEqual(results[0], results[1])

    async def test_adapter_failure_preserves_raw_message_before_canonicalization(self):
        malformed = {
            "stream": "btcusdt@depth@100ms",
            "data": {
                "e": "depthUpdate",
                "E": EVENT_MS,
                "s": "BTCUSDT",
                "U": 106,
                "u": 105,
                "b": [["59999", "1"]],
                "a": [["60001", "1"]],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = binance_spot_source()
            holder = {}
            with self.assertRaisesRegex(Exception, "range is invalid"):
                await capture_public_source_window(
                    root,
                    spec,
                    stream_id="BN-RAW-FIRST-001",
                    capture_seconds=5,
                    maximum_messages=1,
                    maximum_uncompressed_bytes=100_000,
                    message_idle_timeout_seconds=2,
                    connect_factory=_connect_factory([malformed], holder),
                    clock_ns=_clock([RECEIVED_NS]),
                )
            journal = RawPublicSourceJournal(root, "BN-RAW-FIRST-001", spec)
            entries = journal.entries()
            self.assertEqual(1, len(entries))
            self.assertEqual(malformed, entries[0]["message"])
            self.assertFalse(journal.immutable_manifest_path.exists())

    async def test_reusing_nonempty_stream_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = binance_spot_source()
            holder, pending = _capture(root, spec, _binance_messages(), "BN-REPLAY-001")
            await pending
            with self.assertRaisesRegex(PublicSourceCaptureError, "reuse"):
                await capture_public_source_window(
                    root,
                    spec,
                    stream_id="BN-REPLAY-001",
                    capture_seconds=5,
                    maximum_messages=1,
                    maximum_uncompressed_bytes=100_000,
                    message_idle_timeout_seconds=2,
                    connect_factory=_connect_factory([_binance_messages()[0]], {}),
                    clock_ns=_clock([RECEIVED_NS]),
                )

    async def test_raw_journal_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = kraken_spot_source()
            journal = RawPublicSourceJournal(root, "KR-TAMPER-001", spec)
            journal.append(_kraken_messages()[0], RECEIVED_NS)
            value = json.loads(journal.path.read_text(encoding="utf-8"))
            value["message"]["type"] = "update"
            journal.path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(PublicSourceCaptureError, "entry hash mismatch"):
                journal.entries()

    def test_public_source_specs_are_read_only_and_symbol_registry_fails_closed(self):
        for spec in (kraken_spot_source(), binance_spot_source()):
            body = spec.body()
            self.assertEqual("PUBLIC_READ_ONLY", body["network_policy"])
            self.assertFalse(body["authentication_allowed"])
            self.assertFalse(body["orders_allowed"])
            self.assertFalse(body["wallets_allowed"])
            self.assertEqual("NONE", body["capital_effect"])
        with self.assertRaises(PublicSourceCaptureError):
            binance_spot_source("BTCUSD")


if __name__ == "__main__":
    unittest.main()
