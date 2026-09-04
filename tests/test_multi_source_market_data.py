from __future__ import annotations

import unittest
from datetime import datetime, timezone

from autonomous_kernel.observation import (
    ProviderRecord,
    adapt_binance_spot,
    adapt_coinbase_advanced_trade,
    adapt_kraken_v2,
    default_instrument_registry,
)
from autonomous_kernel.observation.adapters import ProviderAdapterError
from autonomous_kernel.observation.instruments import InstrumentIdentityError
from autonomous_kernel.operations import canonical_hash
from autonomous_kernel.representation import build_instrument_state
from autonomous_kernel.representation.builder import RepresentationError


TS = "2026-09-04T01:00:00.000000000Z"
EVENT_MS = int(datetime(2026, 9, 4, 1, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
RECEIVED_NS = EVENT_MS * 1_000_000 + 1_000_000


def _record(provider, stream_id, message):
    return ProviderRecord(
        provider=provider,
        stream_id=stream_id,
        received_at_ns=RECEIVED_NS,
        message=message,
        message_hash=canonical_hash(message),
        raw_ref="artifacts/raw/%s.json#event" % stream_id,
    )


def _coinbase_trade(price="60000.00", size="0.010"):
    return {
        "channel": "market_trades",
        "timestamp": TS,
        "sequence_num": 41,
        "events": [
            {
                "type": "update",
                "product_id": "BTC-USD",
                "trades": [
                    {"trade_id": "9001", "price": price, "size": size, "side": "BUY"}
                ],
            }
        ],
    }


def _kraken_trade(price="60000.00", size="0.010"):
    return {
        "channel": "trade",
        "type": "update",
        "data": [
            {
                "symbol": "BTC/USD",
                "trade_id": "9001",
                "price": price,
                "qty": size,
                "side": "buy",
                "timestamp": TS,
            }
        ],
    }


def _binance_trade(symbol="BTCUSDT", *, buyer_is_maker=True):
    return {
        "e": "trade",
        "E": EVENT_MS,
        "s": symbol,
        "t": 9001,
        "p": "60000.00",
        "q": "0.010",
        "b": 88,
        "a": 99,
        "T": EVENT_MS,
        "m": buyer_is_maker,
        "M": True,
    }


def _binance_depth(first=100, final=105, symbol="BTCUSDT"):
    return {
        "e": "depthUpdate",
        "E": EVENT_MS,
        "s": symbol,
        "U": first,
        "u": final,
        "b": [["59999.00", "1.2"], ["59998.00", "0"]],
        "a": [["60001.00", "0.8"], ["60002.00", "0"]],
    }


class MultiSourceMarketDataTests(unittest.TestCase):
    def test_coinbase_and_kraken_share_usd_spot_identity(self):
        registry = default_instrument_registry()
        coinbase = registry.resolve("coinbase_advanced_trade_public_websocket", "BTC-USD")
        kraken = registry.resolve("kraken_websocket_v2", "BTC/USD")
        legacy = registry.resolve("kraken_websocket_v2", "XBT/USD")
        self.assertEqual("CRYPTO.SPOT.BTC-USD", coinbase.canonical_id)
        self.assertEqual(coinbase, kraken)
        self.assertEqual(coinbase, legacy)

    def test_binance_usdt_is_not_silently_normalized_to_usd(self):
        registry = default_instrument_registry()
        usd = registry.resolve("coinbase_advanced_trade_public_websocket", "BTC-USD")
        usdt = registry.resolve("binance_spot_public_websocket", "BTCUSDT")
        self.assertEqual("BTC", usd.base_asset)
        self.assertEqual(usd.base_asset, usdt.base_asset)
        self.assertEqual("USD", usd.quote_asset)
        self.assertEqual("USDT", usdt.quote_asset)
        self.assertEqual("CRYPTO.SPOT.BTC-USDT", usdt.canonical_id)
        self.assertNotEqual(usd, usdt)
        self.assertNotEqual(usd.canonical_id, usdt.canonical_id)

    def test_binance_raw_and_combined_trade_are_canonical_but_side_remains_unknown(self):
        raw_message = _binance_trade(buyer_is_maker=True)
        combined_message = {"stream": "btcusdt@trade", "data": _binance_trade(buyer_is_maker=False)}
        raw = adapt_binance_spot(
            _record("binance_spot_public_websocket", "BN-RAW", raw_message)
        )[0]
        combined = adapt_binance_spot(
            _record("binance_spot_public_websocket", "BN-COMBINED", combined_message)
        )[0]
        self.assertEqual("TRADE", raw.event_type)
        self.assertEqual("BINANCE", raw.venue)
        self.assertEqual("CRYPTO.SPOT.BTC-USDT", raw.instrument.canonical_id)
        self.assertEqual("UNKNOWN", raw.to_wire()["payload"]["side"])
        self.assertEqual("UNKNOWN", combined.to_wire()["payload"]["side"])
        self.assertEqual(raw.normalized_payload_hash(), combined.normalized_payload_hash())
        self.assertNotEqual(raw.content_hash(), combined.content_hash())

    def test_binance_depth_is_delta_with_exact_instrument_sequence(self):
        item = adapt_binance_spot(
            _record("binance_spot_public_websocket", "BN-DEPTH", _binance_depth())
        )[0]
        self.assertEqual("BOOK_DELTA", item.event_type)
        self.assertEqual("BINANCE", item.venue)
        self.assertEqual("100:105", item.sequence)
        self.assertEqual("INSTRUMENT", item.sequence_scope)
        self.assertEqual("BID", item.to_wire()["payload"]["updates"][0]["side"])
        self.assertEqual("ASK", item.to_wire()["payload"]["updates"][2]["side"])

    def test_binance_depth_without_snapshot_cannot_become_qualified_book_truth(self):
        item = adapt_binance_spot(
            _record("binance_spot_public_websocket", "BN-DELTA-ONLY", _binance_depth())
        )[0]
        frame = build_instrument_state((item,), cutoff_at_ns=item.known_at_ns)
        self.assertEqual("UNAVAILABLE", frame.status)
        venue = frame.state["venue_states"]["BINANCE"]
        self.assertEqual("UNAVAILABLE_NO_SNAPSHOT", venue["book"]["status"])
        self.assertIn("DELTA_BEFORE_SNAPSHOT", venue["book"]["issues"])

    def test_binance_invalid_update_range_fails_closed(self):
        with self.assertRaisesRegex(ProviderAdapterError, "range is invalid"):
            adapt_binance_spot(
                _record("binance_spot_public_websocket", "BN-BAD-RANGE", _binance_depth(106, 105))
            )
        malformed = _binance_depth()
        malformed["U"] = "100"
        with self.assertRaisesRegex(ProviderAdapterError, "must be an integer"):
            adapt_binance_spot(
                _record("binance_spot_public_websocket", "BN-BAD-TYPE", malformed)
            )

    def test_unregistered_binance_symbol_fails_closed(self):
        with self.assertRaises(InstrumentIdentityError):
            adapt_binance_spot(
                _record(
                    "binance_spot_public_websocket",
                    "BN-UNKNOWN",
                    _binance_trade(symbol="BTCUSD"),
                )
            )

    def test_usd_and_usdt_observations_cannot_be_mixed_as_one_instrument_state(self):
        coinbase = adapt_coinbase_advanced_trade(
            _record("coinbase_advanced_trade_public_websocket", "CB-MIX", _coinbase_trade()),
            default_symbol="BTC-USD",
        )[0]
        binance = adapt_binance_spot(
            _record("binance_spot_public_websocket", "BN-MIX", _binance_trade())
        )[0]
        with self.assertRaisesRegex(RepresentationError, "cannot mix canonical instruments"):
            build_instrument_state((coinbase, binance), cutoff_at_ns=max(coinbase.known_at_ns, binance.known_at_ns))

    def test_kraken_book_snapshot_and_delta_keep_checksum_as_integrity_not_sequence(self):
        snapshot_message = {
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
        }
        delta_message = {
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
        }
        snapshot = adapt_kraken_v2(
            _record("kraken_websocket_v2", "KR-SNAPSHOT", snapshot_message)
        )[0]
        delta = adapt_kraken_v2(
            _record("kraken_websocket_v2", "KR-DELTA", delta_message)
        )[0]
        self.assertEqual("BOOK_SNAPSHOT", snapshot.event_type)
        self.assertEqual("BOOK_DELTA", delta.event_type)
        self.assertEqual("KRAKEN", snapshot.venue)
        self.assertEqual("KRAKEN", delta.venue)
        self.assertIsNone(snapshot.sequence)
        self.assertIsNone(delta.sequence)
        self.assertEqual("NONE", snapshot.sequence_scope)
        self.assertEqual("NONE", delta.sequence_scope)
        self.assertEqual("123", snapshot.to_wire()["payload"]["checksum"])
        self.assertEqual("124", delta.to_wire()["payload"]["checksum"])

    def test_coinbase_and_kraken_same_trade_payload_preserve_distinct_provenance(self):
        coinbase = adapt_coinbase_advanced_trade(
            _record("coinbase_advanced_trade_public_websocket", "CB-PROV", _coinbase_trade()),
            default_symbol="BTC-USD",
        )[0]
        kraken = adapt_kraken_v2(
            _record("kraken_websocket_v2", "KR-PROV", _kraken_trade())
        )[0]
        self.assertEqual(coinbase.instrument, kraken.instrument)
        self.assertEqual(coinbase.normalized_payload_hash(), kraken.normalized_payload_hash())
        self.assertEqual("9001", kraken.sequence)
        self.assertEqual("INSTRUMENT", kraken.sequence_scope)
        self.assertNotEqual(coinbase.provider, kraken.provider)
        self.assertNotEqual(coinbase.content_hash(), kraken.content_hash())

    def test_binance_control_message_is_not_market_truth(self):
        ack = {"result": None, "id": 1}
        self.assertEqual(
            (),
            adapt_binance_spot(
                _record("binance_spot_public_websocket", "BN-ACK", ack)
            ),
        )


if __name__ == "__main__":
    unittest.main()
