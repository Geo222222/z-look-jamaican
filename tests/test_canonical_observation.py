import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from autonomous_kernel.operations import canonical_hash
from autonomous_kernel.observation import (
    CanonicalBatchStore,
    CanonicalObservation,
    ProviderRecord,
    adapt_coinbase_advanced_trade,
    adapt_kraken_v2,
    default_instrument_registry,
    validate_canonical_market_data_store,
)
from autonomous_kernel.observation.contracts import ObservationContractError


TS = "2026-09-02T18:00:00.123456789Z"
RECEIVED_NS = int(datetime(2026, 9, 2, 18, 0, 1, tzinfo=timezone.utc).timestamp()) * 1_000_000_000


def provider_record(provider, stream_id, message):
    return ProviderRecord(
        provider=provider,
        stream_id=stream_id,
        received_at_ns=RECEIVED_NS,
        message=message,
        message_hash=canonical_hash(message),
        raw_ref="artifacts/raw/%s.json#event" % stream_id,
    )


class CanonicalObservationTests(unittest.TestCase):
    def test_provider_symbols_resolve_to_one_economic_instrument(self):
        registry = default_instrument_registry()
        coinbase = registry.resolve("coinbase_advanced_trade_public_websocket", "BTC-USD")
        kraken = registry.resolve("kraken_websocket_v2", "BTC/USD")
        legacy_kraken = registry.resolve("kraken_websocket_v2", "XBT/USD")
        self.assertEqual("CRYPTO.SPOT.BTC-USD", coinbase.canonical_id)
        self.assertEqual(coinbase, kraken)
        self.assertEqual(coinbase, legacy_kraken)

    def test_coinbase_and_kraken_trades_share_canonical_semantics(self):
        coinbase_message = {
            "channel": "market_trades",
            "timestamp": TS,
            "sequence_num": 41,
            "events": [
                {
                    "type": "update",
                    "product_id": "BTC-USD",
                    "trades": [
                        {"trade_id": "9001", "price": "60000.00", "size": "0.010", "side": "BUY"}
                    ],
                }
            ],
        }
        kraken_message = {
            "channel": "trade",
            "type": "update",
            "data": [
                {
                    "symbol": "BTC/USD",
                    "trade_id": "9001",
                    "price": "60000.00",
                    "qty": "0.010",
                    "side": "buy",
                    "timestamp": TS,
                }
            ],
        }
        coinbase = adapt_coinbase_advanced_trade(
            provider_record("coinbase_advanced_trade_public_websocket", "CB-1", coinbase_message),
            default_symbol="BTC-USD",
        )[0]
        kraken = adapt_kraken_v2(
            provider_record("kraken_websocket_v2", "KR-1", kraken_message)
        )[0]
        self.assertEqual("TRADE", coinbase.event_type)
        self.assertEqual(coinbase.event_type, kraken.event_type)
        self.assertEqual(coinbase.instrument, kraken.instrument)
        self.assertEqual(coinbase.to_wire()["payload"], kraken.to_wire()["payload"])
        self.assertEqual(coinbase.normalized_payload_hash(), kraken.normalized_payload_hash())
        self.assertNotEqual(coinbase.content_hash(), kraken.content_hash())
        self.assertEqual("COINBASE", coinbase.venue)
        self.assertEqual("KRAKEN", kraken.venue)

    def test_coinbase_book_snapshot_and_delta_are_typed(self):
        snapshot = {
            "channel": "level2",
            "timestamp": TS,
            "sequence_num": 10,
            "events": [
                {
                    "type": "snapshot",
                    "product_id": "BTC-USD",
                    "updates": [
                        {"side": "bid", "price_level": "59999", "new_quantity": "1.2"},
                        {"side": "offer", "price_level": "60001", "new_quantity": "0.8"},
                    ],
                }
            ],
        }
        delta = dict(snapshot)
        delta["sequence_num"] = 11
        delta["events"] = [
            {
                "type": "update",
                "product_id": "BTC-USD",
                "updates": [{"side": "bid", "price_level": "60000", "new_quantity": "0.4"}],
            }
        ]
        snapshot_item = adapt_coinbase_advanced_trade(
            provider_record("coinbase_advanced_trade_public_websocket", "CB-L2", snapshot),
            default_symbol="BTC-USD",
        )[0]
        delta_item = adapt_coinbase_advanced_trade(
            provider_record("coinbase_advanced_trade_public_websocket", "CB-L2", delta),
            default_symbol="BTC-USD",
        )[0]
        self.assertEqual("BOOK_SNAPSHOT", snapshot_item.event_type)
        self.assertEqual("BOOK_DELTA", delta_item.event_type)
        self.assertEqual("BID", snapshot_item.to_wire()["payload"]["updates"][0]["side"])
        self.assertEqual("CONNECTION_GLOBAL", snapshot_item.to_wire()["source"]["sequence_scope"])

    def test_wire_integrity_rejects_mutation(self):
        message = {
            "channel": "market_trades",
            "timestamp": TS,
            "sequence_num": 1,
            "events": [{"type": "update", "product_id": "BTC-USD", "trades": [{"trade_id": "1", "price": "100", "size": "1", "side": "BUY"}]}],
        }
        item = adapt_coinbase_advanced_trade(
            provider_record("coinbase_advanced_trade_public_websocket", "CB-X", message),
            default_symbol="BTC-USD",
        )[0]
        wire = item.to_wire()
        self.assertEqual(item, CanonicalObservation.from_wire(wire))
        changed = json.loads(json.dumps(wire))
        changed["payload"]["price"] = "999"
        with self.assertRaises(ObservationContractError):
            CanonicalObservation.from_wire(changed)

    def test_canonical_batch_is_deterministic_idempotent_and_tamper_evident(self):
        message = {
            "channel": "market_trades",
            "timestamp": TS,
            "sequence_num": 8,
            "events": [{"type": "update", "product_id": "BTC-USD", "trades": [
                {"trade_id": "8", "price": "60000", "size": "0.1", "side": "BUY"},
                {"trade_id": "9", "price": "60001", "size": "0.2", "side": "SELL"},
            ]}],
        }
        items = adapt_coinbase_advanced_trade(
            provider_record("coinbase_advanced_trade_public_websocket", "CB-BATCH", message),
            default_symbol="BTC-USD",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = CanonicalBatchStore(root)
            first = store.persist_batch(
                batch_id="CAN-CB-BATCH",
                observations=items,
                source_ref="artifacts/market_data/streams/CB-BATCH.jsonl.gz",
                source_sha256="a" * 64,
            )
            second = store.persist_batch(
                batch_id="CAN-CB-BATCH",
                observations=items,
                source_ref="artifacts/market_data/streams/CB-BATCH.jsonl.gz",
                source_sha256="a" * 64,
            )
            self.assertEqual(first, second)
            self.assertEqual(2, first["record_count"])
            self.assertEqual([], validate_canonical_market_data_store(root))
            data_path = root / first["path"]
            content = bytearray(data_path.read_bytes())
            content[-1] ^= 1
            data_path.write_bytes(content)
            self.assertTrue(any("compressed canonical batch hash mismatch" in error for error in validate_canonical_market_data_store(root)))


if __name__ == "__main__":
    unittest.main()
