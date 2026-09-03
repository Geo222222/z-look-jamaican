from __future__ import annotations

import unittest
from typing import Optional

from autonomous_kernel.observation.contracts import CanonicalObservation
from autonomous_kernel.observation.instruments import CanonicalInstrument
from autonomous_kernel.representation import DerivativeStateError, RepresentationFrame, build_derivative_state


T = 1_788_400_000_000_000_000
SECOND = 1_000_000_000


def _instrument(symbol: str = "BTC") -> CanonicalInstrument:
    return CanonicalInstrument(
        canonical_id=f"CRYPTO.PERP.{symbol}-USD",
        asset_class="CRYPTO",
        market_type="PERPETUAL",
        base_asset=symbol,
        quote_asset="USD",
        settlement_asset="USD",
    )


def _observation(
    event_type: str,
    payload,
    *,
    offset: int,
    instrument: Optional[CanonicalInstrument] = None,
    quality: str = "VALID",
    provider: str = "TEST_DERIVATIVES",
    venue: str = "TEST",
) -> CanonicalObservation:
    item_instrument = instrument or _instrument()
    known = T - 20 * SECOND + offset * SECOND
    return CanonicalObservation(
        observation_id=f"DEROBS-{event_type}-{offset}-{item_instrument.base_asset}",
        instrument=item_instrument,
        event_type=event_type,
        provider=provider,
        venue=venue,
        provider_symbol=f"{item_instrument.base_asset}USD-PERP",
        channel=event_type.lower(),
        source_event_at_ns=known - 2,
        received_at_ns=known - 1,
        known_at_ns=known,
        sequence=str(offset),
        sequence_scope="PROVIDER_EVENT",
        stream_id="DERIVATIVE-TEST-STREAM",
        payload=payload,
        quality={"status": quality, "action_permitted": quality == "VALID"},
        raw_event_sha256=(f"{offset + 1:x}" * 64)[:64],
        raw_ref=f"raw://derivative/{event_type}/{offset}",
    )


def _full_set():
    return (
        _observation("FUNDING", {"rate": "0.0001"}, offset=1),
        _observation("OPEN_INTEREST", {"open_interest": "1000"}, offset=2),
        _observation("INDEX_PRICE", {"price": "100"}, offset=3),
        _observation("MARK_PRICE", {"price": "101"}, offset=4),
        _observation("LIQUIDATION", {"price": "100", "size": "2", "side": "SELL"}, offset=5),
        _observation("LIQUIDATION", {"price": "102", "size": "1", "side": "BUY"}, offset=6),
    )


class DerivativeStateTests(unittest.TestCase):
    def test_builds_deterministic_derivative_state_and_round_trips(self) -> None:
        first = build_derivative_state(_full_set(), cutoff_at_ns=T)
        second = build_derivative_state(tuple(reversed(_full_set())), cutoff_at_ns=T)
        self.assertEqual(first.representation_type, "DERIVATIVE_STATE")
        self.assertEqual(first.content_hash(), second.content_hash())
        self.assertEqual(first.status, "QUALIFIED")
        self.assertEqual(first.state["funding"]["status"], "QUALIFIED")
        self.assertEqual(first.state["funding"]["value"], "0.0001")
        self.assertEqual(first.state["open_interest"]["value"], "1000")
        self.assertEqual(first.state["open_interest"]["unit_semantics"], "PROVIDER_NATIVE_UNSPECIFIED")
        self.assertEqual(first.state["mark_index"]["mark_index_divergence_bps"], "100")
        self.assertEqual(first.state["liquidations"]["event_count"], 2)
        self.assertEqual(first.state["liquidations"]["reported_sell_size"], "2")
        self.assertEqual(first.state["liquidations"]["reported_buy_size"], "1")
        self.assertEqual(first.state["liquidations"]["truth_class"], "PROVIDER_REPORTED_SIDE_UNINTERPRETED")
        self.assertFalse(first.state["comparability"]["open_interest_cross_venue_comparable"])
        self.assertFalse(first.state["comparability"]["liquidation_size_cross_venue_comparable"])
        restored = RepresentationFrame.from_wire(first.to_wire())
        self.assertEqual(restored.content_hash(), first.content_hash())

    def test_missing_families_remain_unavailable_not_zero(self) -> None:
        frame = build_derivative_state(
            (
                _observation("INDEX_PRICE", {"price": "100"}, offset=1),
                _observation("MARK_PRICE", {"price": "100.5"}, offset=2),
            ),
            cutoff_at_ns=T,
        )
        self.assertEqual(frame.state["feature_family_status"]["FUNDING"], "UNAVAILABLE")
        self.assertEqual(frame.state["feature_family_status"]["OPEN_INTEREST"], "UNAVAILABLE")
        self.assertEqual(frame.state["feature_family_status"]["LIQUIDATIONS"], "UNAVAILABLE")
        self.assertEqual(frame.state["funding"], {"status": "UNAVAILABLE"})
        self.assertEqual(frame.state["open_interest"], {"status": "UNAVAILABLE"})

    def test_future_known_observation_is_rejected(self) -> None:
        future = _observation("FUNDING", {"rate": "0.001"}, offset=30)
        with self.assertRaisesRegex(DerivativeStateError, "lookahead rejected"):
            build_derivative_state((future,), cutoff_at_ns=T)

    def test_negative_open_interest_is_rejected(self) -> None:
        observation = _observation("OPEN_INTEREST", {"open_interest": "-1"}, offset=2)
        with self.assertRaisesRegex(DerivativeStateError, "cannot be negative"):
            build_derivative_state((observation,), cutoff_at_ns=T)

    def test_non_valid_source_degrades_frame_and_does_not_become_state_truth(self) -> None:
        frame = build_derivative_state(
            (
                _observation("FUNDING", {"rate": "0.05"}, offset=1, quality="DEGRADED"),
                _observation("OPEN_INTEREST", {"open_interest": "1000"}, offset=2),
            ),
            cutoff_at_ns=T,
        )
        self.assertEqual(frame.status, "DEGRADED")
        self.assertEqual(frame.state["funding"], {"status": "UNAVAILABLE"})
        self.assertEqual(frame.state["open_interest"]["status"], "QUALIFIED")
        self.assertEqual(frame.state["input_quality"]["status_counts"]["DEGRADED"], 1)

    def test_mixed_economic_instruments_are_rejected(self) -> None:
        with self.assertRaisesRegex(DerivativeStateError, "cannot mix canonical instruments"):
            build_derivative_state(
                (
                    _observation("FUNDING", {"rate": "0.001"}, offset=1),
                    _observation(
                        "OPEN_INTEREST",
                        {"open_interest": "500"},
                        offset=2,
                        instrument=_instrument("ETH"),
                    ),
                ),
                cutoff_at_ns=T,
            )

    def test_provider_native_open_interest_is_not_declared_cross_venue_comparable(self) -> None:
        frame = build_derivative_state(
            (
                _observation(
                    "OPEN_INTEREST",
                    {"open_interest": "1000"},
                    offset=1,
                    provider="VENUE_A",
                    venue="A",
                ),
                _observation(
                    "OPEN_INTEREST",
                    {"open_interest": "2"},
                    offset=2,
                    provider="VENUE_B",
                    venue="B",
                ),
            ),
            cutoff_at_ns=T,
        )
        self.assertEqual(frame.state["open_interest"]["value"], "2")
        self.assertEqual(frame.state["open_interest"]["provider"], "VENUE_B")
        self.assertEqual(frame.state["open_interest"]["unit_semantics"], "PROVIDER_NATIVE_UNSPECIFIED")
        self.assertFalse(frame.state["comparability"]["open_interest_cross_venue_comparable"])


if __name__ == "__main__":
    unittest.main()
