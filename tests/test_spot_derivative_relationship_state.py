from __future__ import annotations

import unittest

from autonomous_kernel.context.contracts import MarketContextFrame
from autonomous_kernel.experience import (
    EconomicInstrumentGraph,
    EconomicInstrumentNode,
    EconomicRelationship,
    EconomicRelationshipType,
    ExperienceTimescale,
    InstrumentRole,
    MarketExperienceFrame,
    RelationshipStateError,
    TimescaleSpec,
    build_market_experience,
    build_spot_derivative_relationship_state,
)
from autonomous_kernel.observation.instruments import CanonicalInstrument
from autonomous_kernel.representation.contracts import RepresentationFrame


T = 1_788_400_000_000_000_000
SECOND = 1_000_000_000
LOOKBACK = 10 * SECOND


def _instrument(market_type: str, quote: str = "USD") -> CanonicalInstrument:
    if market_type == "SPOT":
        return CanonicalInstrument(
            canonical_id=f"CRYPTO.SPOT.BTC-{quote}",
            asset_class="CRYPTO",
            market_type="SPOT",
            base_asset="BTC",
            quote_asset=quote,
            settlement_asset=quote,
        )
    return CanonicalInstrument(
        canonical_id=f"CRYPTO.PERP.BTC-{quote}",
        asset_class="CRYPTO",
        market_type="PERPETUAL",
        base_asset="BTC",
        quote_asset=quote,
        settlement_asset=quote,
    )


def _price_frame(
    name: str,
    instrument: CanonicalInstrument,
    midpoint: str,
    cutoff: int,
    *,
    spread_bps: str = "1",
    status: str = "QUALIFIED",
) -> RepresentationFrame:
    token = (name.encode("utf-8").hex() * 64)[:64]
    return RepresentationFrame(
        frame_id=name,
        representation_type="INSTRUMENT_STATE",
        instrument=instrument,
        window_start_ns=cutoff - LOOKBACK,
        cutoff_at_ns=cutoff,
        known_at_ns=cutoff - 1,
        latest_source_event_at_ns=cutoff - 2,
        status=status,
        builder_version="relationship-test-price-v1",
        parameters={"test": True},
        state={
            "aggregate": {
                "mean_venue_midpoint": midpoint,
                "cross_venue_spread_bps": spread_bps,
            }
        },
        source_observation_ids=(f"OBS-{name}",),
        source_content_hashes=(token,),
        source_providers=("TEST",),
        source_venues=("TEST",),
    )


def _derivative_state(name: str, cutoff: int, value: str, *, provider: str = "DERIV_A", venue: str = "A") -> RepresentationFrame:
    token = (name.encode("utf-8").hex() * 64)[:64]
    instrument = _instrument("PERPETUAL")
    return RepresentationFrame(
        frame_id=name,
        representation_type="DERIVATIVE_STATE",
        instrument=instrument,
        window_start_ns=cutoff - LOOKBACK,
        cutoff_at_ns=cutoff,
        known_at_ns=cutoff - 1,
        latest_source_event_at_ns=cutoff - 2,
        status="QUALIFIED",
        builder_version="relationship-test-derivative-v1",
        parameters={"test": True},
        state={
            "feature_family_status": {
                "FUNDING": "QUALIFIED",
                "OPEN_INTEREST": "QUALIFIED",
                "MARK_INDEX": "UNAVAILABLE",
                "LIQUIDATIONS": "UNAVAILABLE",
            },
            "funding": {"status": "QUALIFIED", "value": "0.0001"},
            "open_interest": {
                "status": "QUALIFIED",
                "value": value,
                "provider": provider,
                "venue": venue,
                "unit_semantics": "PROVIDER_NATIVE_UNSPECIFIED",
            },
            "mark_index": {"status": "UNAVAILABLE"},
            "liquidations": {"status": "UNAVAILABLE"},
        },
        source_observation_ids=(f"OBS-{name}",),
        source_content_hashes=(token,),
        source_providers=(provider,),
        source_venues=(venue,),
    )


def _graph(*, derivative_quote: str = "USD") -> EconomicInstrumentGraph:
    spot = EconomicInstrumentNode(
        node_id="BTC-SPOT",
        instrument=_instrument("SPOT", "USD"),
        role=InstrumentRole.SPOT,
        economic_root_id="ASSET.BTC",
        quote_family_id="QUOTE.USD_FAMILY",
    )
    perp = EconomicInstrumentNode(
        node_id="BTC-PERP",
        instrument=_instrument("PERPETUAL", derivative_quote),
        role=InstrumentRole.PERPETUAL,
        economic_root_id="ASSET.BTC",
        quote_family_id="QUOTE.USD_FAMILY",
        contract_spec_ref="spec://btc-perp-v1",
    )
    return EconomicInstrumentGraph(
        graph_id="BTC-REL-GRAPH",
        graph_version="1.0.0",
        effective_at_ns=T - 1000 * SECOND,
        known_at_ns=T - 999 * SECOND,
        nodes=(spot, perp),
        relationships=(
            EconomicRelationship(
                relationship_id="BTC-SPOT-PERP",
                relationship_type=EconomicRelationshipType.SPOT_DERIVATIVE,
                source_node_id="BTC-SPOT",
                target_node_id="BTC-PERP",
                rationale="same BTC economic root across spot and perpetual markets",
            ),
        ),
    )


def _histories(*, derivative_quote: str = "USD"):
    spot = []
    derivative = []
    for index in range(6):
        cutoff = T - (5 - index) * 20 * SECOND
        spot.append(
            _price_frame(
                f"SPOT-{index}-{derivative_quote}",
                _instrument("SPOT", "USD"),
                str(100 + index),
                cutoff,
                spread_bps="1",
            )
        )
        derivative.append(
            _price_frame(
                f"PERP-{index}-{derivative_quote}",
                _instrument("PERPETUAL", derivative_quote),
                str(101 + index),
                cutoff,
                spread_bps="2",
            )
        )
    return tuple(spot), tuple(derivative)


def _context(spot: RepresentationFrame, derivative: RepresentationFrame) -> MarketContextFrame:
    frames = (spot, derivative)
    return MarketContextFrame(
        context_id="CTX-REL",
        context_type="MARKET_CONTEXT",
        cutoff_at_ns=T,
        known_at_ns=T - 1,
        status="QUALIFIED",
        builder_version="relationship-context-v1",
        parameters={"test": True},
        state={
            "members": {
                frame.instrument.canonical_id: {
                    "frame_id": frame.frame_id,
                    "frame_content_hash": frame.content_hash(),
                }
                for frame in frames
            }
        },
        source_frame_ids=tuple(frame.frame_id for frame in frames),
        source_frame_hashes=tuple(frame.content_hash() for frame in frames),
        source_instrument_ids=tuple(frame.instrument.canonical_id for frame in frames),
    )


class SpotDerivativeRelationshipStateTests(unittest.TestCase):
    def test_same_quote_price_basis_is_allowed_but_amount_depth_comparison_is_air_gapped(self) -> None:
        spot, derivative = _histories()
        state = build_spot_derivative_relationship_state(
            graph=_graph(),
            relationship_id="BTC-SPOT-PERP",
            spot_frames=spot,
            derivative_frames=derivative,
            cutoff_at_ns=T,
        )
        self.assertEqual(state.status, "QUALIFIED")
        self.assertEqual(state.state["basis"]["status"], "QUALIFIED")
        self.assertEqual(state.state["basis"]["spot_quote_unit"], "USD")
        self.assertEqual(state.state["basis"]["derivative_quote_unit"], "USD")
        self.assertEqual(state.state["relative_liquidity"]["spread_comparison_status"], "QUALIFIED")
        self.assertEqual(state.state["relative_liquidity"]["depth_comparison_status"], "UNAVAILABLE")
        self.assertIsNone(state.state["relative_liquidity"]["derivative_to_spot_depth_ratio"])
        self.assertFalse(state.state["unit_air_gap"]["spot_derivative_amounts_directly_comparable"])
        self.assertFalse(state.state["truth_boundaries"]["lagged_association_is_causality"])
        self.assertEqual(
            state.state["lagged_association"]["truth_class"],
            "CAUSAL_CUTOFF_LAGGED_ASSOCIATION_NOT_CAUSALITY",
        )

    def test_quote_unit_mismatch_blocks_basis_even_when_underlying_is_same(self) -> None:
        spot, derivative = _histories(derivative_quote="USDT")
        state = build_spot_derivative_relationship_state(
            graph=_graph(derivative_quote="USDT"),
            relationship_id="BTC-SPOT-PERP",
            spot_frames=spot,
            derivative_frames=derivative,
            cutoff_at_ns=T,
        )
        self.assertEqual(state.state["basis"]["status"], "UNAVAILABLE")
        self.assertEqual(
            state.state["basis"]["reason"],
            "QUOTE_UNIT_MISMATCH_REQUIRES_NORMALIZATION_PROOF",
        )
        self.assertFalse(state.state["unit_air_gap"]["price_basis_directly_comparable"])

    def test_future_known_source_is_rejected(self) -> None:
        spot, derivative = _histories()
        future = _price_frame("FUTURE-PERP", _instrument("PERPETUAL"), "110", T + SECOND)
        with self.assertRaisesRegex(RelationshipStateError, "lookahead relationship source frame rejected"):
            build_spot_derivative_relationship_state(
                graph=_graph(),
                relationship_id="BTC-SPOT-PERP",
                spot_frames=spot,
                derivative_frames=derivative + (future,),
                cutoff_at_ns=T,
            )

    def test_open_interest_change_qualifies_only_inside_unchanged_provider_native_series(self) -> None:
        spot, derivative = _histories()
        prior = _derivative_state("OI-PRIOR", T - 30 * SECOND, "1000", provider="DERIV_A", venue="A")
        current = _derivative_state("OI-CURRENT", T - 10 * SECOND, "1100", provider="DERIV_A", venue="A")
        state = build_spot_derivative_relationship_state(
            graph=_graph(),
            relationship_id="BTC-SPOT-PERP",
            spot_frames=spot,
            derivative_frames=derivative,
            derivative_states=(prior, current),
            cutoff_at_ns=T,
        )
        change = state.state["derivative_structure"]["open_interest_change"]
        self.assertEqual(change["status"], "QUALIFIED")
        self.assertEqual(change["change_bps"], "1000.0")
        self.assertFalse(change["cross_venue_comparable"])

    def test_open_interest_change_fails_closed_when_exchange_source_changes(self) -> None:
        spot, derivative = _histories()
        prior = _derivative_state("OI-A", T - 30 * SECOND, "1000", provider="DERIV_A", venue="A")
        current = _derivative_state("OI-B", T - 10 * SECOND, "1100", provider="DERIV_B", venue="B")
        state = build_spot_derivative_relationship_state(
            graph=_graph(),
            relationship_id="BTC-SPOT-PERP",
            spot_frames=spot,
            derivative_frames=derivative,
            derivative_states=(prior, current),
            cutoff_at_ns=T,
        )
        change = state.state["derivative_structure"]["open_interest_change"]
        self.assertEqual(change["status"], "UNAVAILABLE")
        self.assertEqual(change["reason"], "UNIT_OR_SOURCE_SEMANTICS_CHANGED")

    def test_market_experience_binds_exact_relationship_state_hash(self) -> None:
        spot, derivative = _histories()
        relationship = build_spot_derivative_relationship_state(
            graph=_graph(),
            relationship_id="BTC-SPOT-PERP",
            spot_frames=spot,
            derivative_frames=derivative,
            cutoff_at_ns=T,
        )
        latest_spot = spot[-1]
        latest_derivative = derivative[-1]
        experience = build_market_experience(
            economic_root_id="ASSET.BTC",
            graph=_graph(),
            context=_context(latest_spot, latest_derivative),
            timescale_frames={ExperienceTimescale.MICRO: (latest_spot, latest_derivative)},
            timescale_specs=(TimescaleSpec(ExperienceTimescale.MICRO, LOOKBACK),),
            cutoff_at_ns=T,
            relationship_states=(relationship,),
        )
        self.assertEqual(len(experience.relationship_states), 1)
        ref = experience.relationship_states[0]
        self.assertEqual(ref.relationship_state_id, relationship.relationship_state_id)
        self.assertEqual(ref.relationship_state_hash, relationship.content_hash())
        restored = MarketExperienceFrame.from_wire(experience.to_wire())
        self.assertEqual(restored.content_hash(), experience.content_hash())
        self.assertEqual(restored.relationship_states[0].relationship_state_hash, relationship.content_hash())

    def test_relationship_state_changes_experience_identity(self) -> None:
        spot, derivative = _histories()
        relationship_a = build_spot_derivative_relationship_state(
            graph=_graph(),
            relationship_id="BTC-SPOT-PERP",
            spot_frames=spot,
            derivative_frames=derivative,
            cutoff_at_ns=T,
            lag_margin="0.10",
        )
        relationship_b = build_spot_derivative_relationship_state(
            graph=_graph(),
            relationship_id="BTC-SPOT-PERP",
            spot_frames=spot,
            derivative_frames=derivative,
            cutoff_at_ns=T,
            lag_margin="0.20",
        )
        latest_spot = spot[-1]
        latest_derivative = derivative[-1]
        kwargs = dict(
            economic_root_id="ASSET.BTC",
            graph=_graph(),
            context=_context(latest_spot, latest_derivative),
            timescale_frames={ExperienceTimescale.MICRO: (latest_spot, latest_derivative)},
            timescale_specs=(TimescaleSpec(ExperienceTimescale.MICRO, LOOKBACK),),
            cutoff_at_ns=T,
        )
        first = build_market_experience(relationship_states=(relationship_a,), **kwargs)
        second = build_market_experience(relationship_states=(relationship_b,), **kwargs)
        self.assertNotEqual(relationship_a.content_hash(), relationship_b.content_hash())
        self.assertNotEqual(first.experience_id, second.experience_id)
        self.assertNotEqual(first.content_hash(), second.content_hash())


if __name__ == "__main__":
    unittest.main()
