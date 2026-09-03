from __future__ import annotations

import hashlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Optional

from autonomous_kernel.context import MarketContextBuildError, MarketContextFrame, MarketContextStore, build_market_context, validate_market_context_store
from autonomous_kernel.observation import CanonicalInstrument, default_instrument_registry
from autonomous_kernel.representation import RepresentationFrame, RepresentationStore


PROVIDER = "coinbase_advanced_trade_public_websocket"
BTC = default_instrument_registry().resolve(PROVIDER, "BTC-USD")
ETH = default_instrument_registry().resolve(PROVIDER, "ETH-USD")
BTC_FUTURE = CanonicalInstrument(canonical_id="CRYPTO.FUTURE.BTC-USD.2026-12-31", asset_class="CRYPTO", market_type="FUTURE", base_asset="BTC", quote_asset="USD", settlement_asset="USD", expiry="2026-12-31")


def frame(instrument, index: int, midpoint: object, *, known_at_ns: Optional[int] = None, status: str = "QUALIFIED") -> RepresentationFrame:
    known = index * 1_000 if known_at_ns is None else int(known_at_ns); mid = Decimal(str(midpoint)); digest = hashlib.sha256((instrument.canonical_id + str(index)).encode()).hexdigest()
    return RepresentationFrame(frame_id="REP-%s-%d" % (hashlib.sha256(instrument.canonical_id.encode()).hexdigest()[:8], index), representation_type="INSTRUMENT_STATE", instrument=instrument, window_start_ns=max(0, known - 100), cutoff_at_ns=known, known_at_ns=known, latest_source_event_at_ns=max(0, known - 1), status=status, builder_version="z9-test-v1", parameters={"depth_bands_bps": [10]}, state={"venue_states": {"TEST": {"book": {"status": "QUALIFIED", "spread_bps": "1", "depth_bands_bps": {"10": {"bid_quote_notional": "500", "ask_quote_notional": "500"}}}, "trade_flow": {}}}, "aggregate": {"cross_venue_book_state": "NORMAL", "cross_venue_best_bid": format(mid - Decimal("0.01"), "f"), "cross_venue_best_ask": format(mid + Decimal("0.01"), "f"), "cross_venue_spread_bps": "1", "mean_venue_midpoint": format(mid, "f"), "trade_flow": {"reported_buy_quote_notional": "60", "reported_sell_quote_notional": "40"}}, "input_quality": {"status_counts": {"VALID": 1}, "degraded_reasons": []}}, source_observation_ids=("OBS-%s-%d" % (instrument.canonical_id.replace(".", "-"), index),), source_content_hashes=(digest,), source_providers=(PROVIDER,), source_venues=("TEST",))


def histories():
    btc = [frame(BTC, index, value) for index, value in enumerate((100, 101, 102.01, 103.0301, 104.060401), 1)]
    eth = [frame(ETH, index, value) for index, value in enumerate((200, 202, 204.02, 206.0602, 208.120802), 1)]
    return btc, eth


class MarketContextTests(unittest.TestCase):
    def test_market_context_is_deterministic_order_invariant_and_market_wide(self):
        btc, eth = histories(); source = tuple(btc + eth)
        first = build_market_context(source, minimum_history_points=3); second = build_market_context(tuple(reversed(source)), minimum_history_points=3)
        self.assertEqual(first.to_wire(), second.to_wire()); self.assertEqual("QUALIFIED", first.status); self.assertEqual("RISK_ON", first.state["regimes"]["direction"]); self.assertEqual(2, first.state["market"]["qualified_spot_count"]); self.assertEqual(Decimal("100"), Decimal(first.state["market"]["aggregate_return_bps"])); self.assertEqual("QUALIFIED", first.state["feature_quality"]["CORRELATION"]["status"]); self.assertEqual(first.to_wire(), MarketContextFrame.from_wire(first.to_wire()).to_wire())

    def test_future_known_source_is_hard_rejected(self):
        btc, eth = histories()
        with self.assertRaises(MarketContextBuildError): build_market_context(tuple(btc + eth), cutoff_at_ns=4_999)

    def test_spot_future_basis_and_lead_lag_are_explicit_noncausal_proxies(self):
        btc, eth = histories(); future = [frame(BTC_FUTURE, index, value) for index, value in enumerate((101, 102.01, 103.0301, 104.060401, 105.10100501), 1)]
        context = build_market_context(tuple(btc + eth + future), minimum_history_points=3, minimum_lead_lag_pairs=3)
        self.assertEqual(1, context.state["derivatives"]["relationship_count"]); relation = context.state["derivatives"]["relationships"][0]; self.assertGreater(Decimal(relation["basis_bps"]), Decimal("0")); self.assertEqual("ALIGNED_RETURN_SEQUENCE_LAG_PROXY_NOT_CAUSALITY", relation["lead_lag"]["truth_class"]); self.assertEqual("QUALIFIED", context.state["feature_quality"]["DERIVATIVES"]["status"])

    def test_store_requires_durable_exact_z2_lineage_and_detects_tamper(self):
        btc, eth = histories(); context = build_market_context(tuple(btc + eth), minimum_history_points=3)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); store = RepresentationStore(root)
            for source in tuple(btc + eth): store.persist(source, source_batches=({"batch_id": "BATCH-%s" % source.frame_id, "manifest_ref": "TEST", "manifest_content_hash": "a" * 64},))
            context_store = MarketContextStore(root); context_store.persist(context, source_frames=tuple(btc + eth)); self.assertEqual([], validate_market_context_store(root)); self.assertEqual(context.to_wire(), context_store.load(context.context_id).to_wire())
            source_path = root / "artifacts/market_data/representations" / (btc[-1].frame_id + ".json"); source_path.write_text("{}\n", encoding="utf-8"); self.assertTrue(validate_market_context_store(root))


if __name__ == "__main__": unittest.main()
