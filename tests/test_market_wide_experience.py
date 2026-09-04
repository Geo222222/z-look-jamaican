from __future__ import annotations

import unittest

from autonomous_kernel.context.contracts import MarketContextFrame
from autonomous_kernel.experience import (
    ExperienceTimescale,
    MarketWideExperienceError,
    MarketWideExperienceState,
    build_market_wide_experience,
)


START = 1_000_000
MID = 2_000_000
END = 3_000_000


def context(
    context_id: str,
    cutoff: int,
    *,
    aggregate_return: str,
    breadth: str,
    dispersion: str,
    volatility: str,
    spread: str,
    liquidity_hhi: str,
    correlation: str,
    btc_return: str,
    eth_return: str,
    direction: str,
    correlation_regime: str,
    status: str = "QUALIFIED",
) -> MarketContextFrame:
    frame_ids = (f"REP-BTC-{context_id}", f"REP-ETH-{context_id}")
    frame_hashes = ("a" * 64, "b" * 64)
    instrument_ids = ("CRYPTO.SPOT.BTC-USD", "CRYPTO.SPOT.ETH-USD")
    state = {
        "members": {
            instrument_ids[0]: {
                "frame_id": frame_ids[0],
                "frame_content_hash": frame_hashes[0],
                "latest_return_bps": btc_return,
            },
            instrument_ids[1]: {
                "frame_id": frame_ids[1],
                "frame_content_hash": frame_hashes[1],
                "latest_return_bps": eth_return,
            },
        },
        "market": {
            "member_instrument_count": 2,
            "aggregate_return_bps": aggregate_return,
            "breadth_positive": breadth,
            "cross_sectional_return_dispersion_bps": dispersion,
            "median_realized_volatility_bps": volatility,
            "median_spread_bps": spread,
            "liquidity_concentration_hhi": liquidity_hhi,
            "median_absolute_pairwise_correlation": correlation,
        },
        "regimes": {
            "direction": direction,
            "volatility": "NORMAL",
            "liquidity": "NORMAL",
            "correlation": correlation_regime,
            "derivatives": "UNAVAILABLE",
            "structure": "ORDERLY",
        },
        "feature_quality": {
            "CORE_MARKET": {"status": status},
            "CROSS_ASSET": {"status": status},
            "LIQUIDITY": {"status": status},
            "CORRELATION": {"status": status},
            "DERIVATIVES": {"status": "UNAVAILABLE"},
            "LEAD_LAG": {"status": "UNAVAILABLE"},
        },
    }
    return MarketContextFrame(
        context_id=context_id,
        context_type="MARKET_CONTEXT",
        cutoff_at_ns=cutoff,
        known_at_ns=cutoff,
        status=status,
        builder_version="market-context-test-v1",
        parameters={"test": True},
        state=state,
        source_frame_ids=frame_ids,
        source_frame_hashes=frame_hashes,
        source_instrument_ids=instrument_ids,
    )


def history():
    return (
        context(
            "CTX-A",
            START,
            aggregate_return="12",
            breadth="0.80",
            dispersion="6",
            volatility="8",
            spread="1",
            liquidity_hhi="0.50",
            correlation="0.20",
            btc_return="15",
            eth_return="9",
            direction="RISK_ON",
            correlation_regime="FRAGMENTED",
        ),
        context(
            "CTX-B",
            MID,
            aggregate_return="7",
            breadth="0.60",
            dispersion="9",
            volatility="12",
            spread="2",
            liquidity_hhi="0.58",
            correlation="0.48",
            btc_return="5",
            eth_return="11",
            direction="MIXED",
            correlation_regime="NORMAL",
        ),
        context(
            "CTX-C",
            END,
            aggregate_return="2",
            breadth="0.40",
            dispersion="15",
            volatility="18",
            spread="4",
            liquidity_hhi="0.66",
            correlation="0.78",
            btc_return="8",
            eth_return="-3",
            direction="MIXED",
            correlation_regime="COHERENT",
        ),
    )


class MarketWideExperienceTests(unittest.TestCase):
    def test_market_wide_experience_is_deterministic_and_temporal(self) -> None:
        source = history()
        first = build_market_wide_experience(
            source,
            timescale=ExperienceTimescale.MACRO_STRUCTURAL,
            window_start_ns=START,
            cutoff_at_ns=END,
            minimum_contexts=3,
        )
        second = build_market_wide_experience(
            tuple(reversed(source)),
            timescale=ExperienceTimescale.MACRO_STRUCTURAL,
            window_start_ns=START,
            cutoff_at_ns=END,
            minimum_contexts=3,
        )
        self.assertEqual(first.to_wire(), second.to_wire())
        self.assertEqual("QUALIFIED", first.status)
        trajectory = first.state["trajectory"]
        self.assertEqual("FALLING", trajectory["breadth_positive"]["trend"])
        self.assertEqual("-0.40", trajectory["breadth_positive"]["delta"])
        self.assertEqual("RISING", trajectory["median_absolute_pairwise_correlation"]["trend"])
        self.assertEqual("0.58", trajectory["median_absolute_pairwise_correlation"]["delta"])
        self.assertEqual("RISING", trajectory["median_realized_volatility_bps"]["trend"])
        self.assertEqual("RISING", trajectory["median_spread_bps"]["trend"])
        self.assertEqual("CRYPTO.SPOT.BTC-USD", first.state["leadership"]["current_leader"])
        self.assertEqual(2, first.state["leadership"]["leader_transition_count"])
        self.assertEqual(1, first.state["regime_history"]["direction"]["transition_count"])
        restored = MarketWideExperienceState.from_wire(first.to_wire())
        self.assertEqual(first.content_hash(), restored.content_hash())

    def test_exact_source_context_changes_identity(self) -> None:
        source = list(history())
        altered = context(
            "CTX-C2",
            END,
            aggregate_return="3",
            breadth="0.45",
            dispersion="15",
            volatility="18",
            spread="4",
            liquidity_hhi="0.66",
            correlation="0.78",
            btc_return="8",
            eth_return="-3",
            direction="MIXED",
            correlation_regime="COHERENT",
        )
        first = build_market_wide_experience(
            source,
            timescale=ExperienceTimescale.SESSION,
            window_start_ns=START,
            cutoff_at_ns=END,
        )
        second = build_market_wide_experience(
            tuple(source[:-1]) + (altered,),
            timescale=ExperienceTimescale.SESSION,
            window_start_ns=START,
            cutoff_at_ns=END,
        )
        self.assertNotEqual(first.market_wide_experience_id, second.market_wide_experience_id)
        self.assertNotEqual(first.content_hash(), second.content_hash())

    def test_lookahead_context_is_hard_rejected(self) -> None:
        source = history() + (
            context(
                "CTX-FUTURE",
                END + 1,
                aggregate_return="1",
                breadth="0.5",
                dispersion="1",
                volatility="1",
                spread="1",
                liquidity_hhi="0.5",
                correlation="0.5",
                btc_return="1",
                eth_return="1",
                direction="NEUTRAL",
                correlation_regime="NORMAL",
            ),
        )
        with self.assertRaisesRegex(MarketWideExperienceError, "lookahead"):
            build_market_wide_experience(
                source,
                timescale=ExperienceTimescale.MACRO_STRUCTURAL,
                window_start_ns=START,
                cutoff_at_ns=END,
            )

    def test_context_before_declared_window_is_rejected(self) -> None:
        with self.assertRaisesRegex(MarketWideExperienceError, "precedes"):
            build_market_wide_experience(
                history(),
                timescale=ExperienceTimescale.SHORT,
                window_start_ns=START + 1,
                cutoff_at_ns=END,
            )

    def test_incomplete_history_degrades_instead_of_fabricating_complete_macro_state(self) -> None:
        frame = build_market_wide_experience(
            history()[1:],
            timescale=ExperienceTimescale.MACRO_STRUCTURAL,
            window_start_ns=START,
            cutoff_at_ns=END,
            minimum_contexts=3,
        )
        self.assertEqual("DEGRADED", frame.status)
        self.assertFalse(frame.state["coverage"]["window_complete"])
        self.assertEqual(2, frame.state["coverage"]["context_count"])

    def test_tamper_is_detected(self) -> None:
        frame = build_market_wide_experience(
            history(),
            timescale=ExperienceTimescale.MACRO_STRUCTURAL,
            window_start_ns=START,
            cutoff_at_ns=END,
        )
        wire = frame.to_wire()
        wire["state"]["leadership"]["current_leader"] = "CRYPTO.SPOT.ETH-USD"
        with self.assertRaisesRegex(MarketWideExperienceError, "content hash mismatch"):
            MarketWideExperienceState.from_wire(wire)


if __name__ == "__main__":
    unittest.main()
