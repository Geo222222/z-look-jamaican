from __future__ import annotations

import unittest
from datetime import datetime, timezone

from autonomous_kernel.observation import (
    ProviderRecord,
    PublicSourceCaptureError,
    binance_spot_source,
    canonicalize_public_record,
)
from autonomous_kernel.operations import canonical_hash


EVENT_MS = int(datetime(2026, 9, 4, 1, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)


class PublicSourceBindingTests(unittest.TestCase):
    def test_subscribed_btc_source_rejects_parseable_eth_event(self):
        spec = binance_spot_source("BTCUSDT")
        message = {
            "e": "trade",
            "E": EVENT_MS,
            "s": "ETHUSDT",
            "t": 77,
            "p": "3000.00",
            "q": "0.20",
            "T": EVENT_MS,
            "m": False,
            "M": True,
        }
        record = ProviderRecord(
            provider=spec.provider,
            stream_id="BN-BTC-SOURCE",
            received_at_ns=EVENT_MS * 1_000_000 + 1,
            message=message,
            message_hash=canonical_hash(message),
        )
        with self.assertRaisesRegex(PublicSourceCaptureError, "instrument differs from source spec"):
            canonicalize_public_record(spec, record)

    def test_source_spec_rejects_event_type_outside_declared_contract(self):
        base = binance_spot_source("BTCUSDT")
        spec = type(base)(
            source_id=base.source_id,
            provider=base.provider,
            endpoint=base.endpoint,
            provider_symbol=base.provider_symbol,
            canonical_instrument_id=base.canonical_instrument_id,
            subscription_messages=base.subscription_messages,
            market_event_types=("BOOK_DELTA",),
            book_snapshot_semantics=base.book_snapshot_semantics,
            sequence_semantics=base.sequence_semantics,
        )
        message = {
            "e": "trade",
            "E": EVENT_MS,
            "s": "BTCUSDT",
            "t": 78,
            "p": "60000.00",
            "q": "0.01",
            "T": EVENT_MS,
            "m": True,
            "M": True,
        }
        record = ProviderRecord(
            provider=spec.provider,
            stream_id="BN-DEPTH-ONLY-SOURCE",
            received_at_ns=EVENT_MS * 1_000_000 + 1,
            message=message,
            message_hash=canonical_hash(message),
        )
        with self.assertRaisesRegex(PublicSourceCaptureError, "event type exceeds source spec"):
            canonicalize_public_record(spec, record)


if __name__ == "__main__":
    unittest.main()
