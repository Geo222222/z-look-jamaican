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
    TimescaleSpec,
    build_market_experience,
)
from autonomous_kernel.observation.instruments import CanonicalInstrument
from autonomous_kernel.representation.contracts import RepresentationFrame


T = 1_788_400_000_000_000_000
SECOND = 1_000_000_000
LOOKBACK = 10 * SECOND


def _spot() -> CanonicalInstrument:
    return CanonicalInstrument(
        canonical_id="CRYPTO.SPOT.BTC-USD",
        asset_class="CRYPTO",
        market_type="SPOT",
        base_asset="BTC",
        quote_asset="USD",
        settlement_asset="USD",
    )


def _perp() -> CanonicalInstrument:
    return CanonicalInstrument(
        canonical_id="CRYPTO.PERP.BTC-USD",
        asset_class="CRYPTO",
        market_type="PERPETUAL",
        base_asset="BTC",
        quote_asset="USD",
        settlement_asset="USD",
    )


def _frame(
    frame_id: str,
    instrument: CanonicalInstrument,
    representation_type: str,
    *,
    status: str = "QUALIFIED",
    state=None,
) -> RepresentationFrame:
    digest = (frame_id.encode("utf-8").hex() * 64)[:64]
    return RepresentationFrame(
        frame_id=frame_id,
        representation_type=representation_type,
        instrument=instrument,
        window_start_ns=T - LOOKBACK,
        cutoff_at_ns=T,
        known_at_ns=T - 1,
        latest_source_event_at_ns=T - 2,
        status=status,
        builder_version="test-v1",
        parameters={"test": True},
        state=state or {"aggregate": {"mean_venue_midpoint": "100"}},
        source_observation_ids=(f"OBS-{frame_id}",),
        source_content_hashes=(digest,),
        source_providers=("TEST",),
        source_venues=("TEST",),
    )


def _graph() -> EconomicInstrumentGraph:
    return EconomicInstrumentGraph(
        graph_id="BTC-GRAPH",
        graph_version="1.0.0",
        effective_at_ns=T - 100 * SECOND,
        known_at_ns=T - 99 * SECOND,
        nodes=(
            EconomicInstrumentNode(
                node_id="BTC-SPOT",
                instrument=_spot(),
                role=InstrumentRole.SPOT,
                economic_root_id="ASSET.BTC",
                quote_family_id="QUOTE.USD",
            ),
            EconomicInstrumentNode(
                node_id="BTC-PERP",
                instrument=_perp(),
                role=InstrumentRole.PERPETUAL,
                economic_root_id="ASSET.BTC",
                quote_family_id="QUOTE.USD",
                contract_spec_ref="spec://btc-perp-v1",
            ),
        ),
        relationships=(
            EconomicRelationship(
                relationship_id="SPOT-PERP",
                relationship_type=EconomicRelationshipType.SPOT_DERIVATIVE,
                source_node_id="BTC-SPOT",
                target_node_id="BTC-PERP",
                rationale="BTC spot/perpetual structural relationship",
            ),
        ),
    )


def _context(spot_state: RepresentationFrame, perp_state: RepresentationFrame) -> MarketContextFrame:
    frames = (spot_state, perp_state)
    return MarketContextFrame(
        context_id="CTX-BTC",
        context_type="MARKET_CONTEXT",
        cutoff_at_ns=T,
        known_at_ns=T - 1,
        status="QUALIFIED",
        builder_version="test-context-v1",
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


def _derivative_state(*, status: str = "QUALIFIED") -> RepresentationFrame:
    return _frame(
        "DER-BTC-PERP",
        _perp(),
        "DERIVATIVE_STATE",
        status=status,
        state={
            "feature_family_status": {
                "FUNDING": "QUALIFIED",
                "OPEN_INTEREST": "QUALIFIED",
                "MARK_INDEX": "QUALIFIED",
                "LIQUIDATIONS": "QUALIFIED",
            },
            "funding": {"status": "QUALIFIED", "value": "0.0001"},
            "open_interest": {"status": "QUALIFIED", "value": "1000"},
        },
    )


class MarketExperienceDerivativeIntegrationTests(unittest.TestCase):
    def test_derivative_structure_families_do_not_exist_without_derivative_state(self) -> None:
        spot_state = _frame("REP-BTC-SPOT", _spot(), "INSTRUMENT_STATE")
        perp_state = _frame("REP-BTC-PERP", _perp(), "INSTRUMENT_STATE")
        experience = build_market_experience(
            economic_root_id="ASSET.BTC",
            graph=_graph(),
            context=_context(spot_state, perp_state),
            timescale_frames={ExperienceTimescale.MICRO: (spot_state, perp_state)},
            timescale_specs=(TimescaleSpec(ExperienceTimescale.MICRO, LOOKBACK),),
            cutoff_at_ns=T,
        )
        micro = experience.views[0]
        self.assertEqual(micro.feature_family_status["DERIVATIVE_MICROSTRUCTURE"], "QUALIFIED")
        self.assertEqual(micro.feature_family_status["DERIVATIVE_POSITIONING"], "UNAVAILABLE")
        self.assertEqual(micro.feature_family_status["DERIVATIVE_FINANCING"], "UNAVAILABLE")
        self.assertEqual(micro.feature_family_status["DERIVATIVE_LIQUIDATIONS"], "UNAVAILABLE")
        self.assertEqual(micro.feature_family_status["MARK_INDEX_DIVERGENCE"], "UNAVAILABLE")

    def test_derivative_state_qualifies_structure_families_without_becoming_microstructure(self) -> None:
        spot_state = _frame("REP-BTC-SPOT", _spot(), "INSTRUMENT_STATE")
        perp_state = _frame("REP-BTC-PERP", _perp(), "INSTRUMENT_STATE")
        derivative_state = _derivative_state()
        experience = build_market_experience(
            economic_root_id="ASSET.BTC",
            graph=_graph(),
            context=_context(spot_state, perp_state),
            timescale_frames={ExperienceTimescale.MICRO: (spot_state, perp_state, derivative_state)},
            timescale_specs=(TimescaleSpec(ExperienceTimescale.MICRO, LOOKBACK),),
            cutoff_at_ns=T,
        )
        micro = experience.views[0]
        self.assertEqual(micro.feature_family_status["SPOT_MICROSTRUCTURE"], "QUALIFIED")
        self.assertEqual(micro.feature_family_status["DERIVATIVE_MICROSTRUCTURE"], "QUALIFIED")
        self.assertEqual(micro.feature_family_status["DERIVATIVE_POSITIONING"], "QUALIFIED")
        self.assertEqual(micro.feature_family_status["DERIVATIVE_FINANCING"], "QUALIFIED")
        self.assertEqual(micro.feature_family_status["DERIVATIVE_LIQUIDATIONS"], "QUALIFIED")
        self.assertEqual(micro.feature_family_status["MARK_INDEX_DIVERGENCE"], "QUALIFIED")
        self.assertEqual(micro.feature_family_status["TERM_STRUCTURE"], "UNAVAILABLE")
        types = {source.representation_type for source in micro.source_frames}
        self.assertEqual(types, {"INSTRUMENT_STATE", "DERIVATIVE_STATE"})

    def test_degraded_derivative_state_degrades_only_its_evidence_families(self) -> None:
        spot_state = _frame("REP-BTC-SPOT", _spot(), "INSTRUMENT_STATE")
        perp_state = _frame("REP-BTC-PERP", _perp(), "INSTRUMENT_STATE")
        derivative_state = _derivative_state(status="DEGRADED")
        experience = build_market_experience(
            economic_root_id="ASSET.BTC",
            graph=_graph(),
            context=_context(spot_state, perp_state),
            timescale_frames={ExperienceTimescale.MICRO: (spot_state, perp_state, derivative_state)},
            timescale_specs=(TimescaleSpec(ExperienceTimescale.MICRO, LOOKBACK),),
            cutoff_at_ns=T,
        )
        micro = experience.views[0]
        self.assertEqual(micro.status, "DEGRADED")
        self.assertEqual(micro.feature_family_status["SPOT_MICROSTRUCTURE"], "QUALIFIED")
        self.assertEqual(micro.feature_family_status["DERIVATIVE_MICROSTRUCTURE"], "QUALIFIED")
        self.assertEqual(micro.feature_family_status["DERIVATIVE_POSITIONING"], "DEGRADED")
        self.assertEqual(micro.feature_family_status["DERIVATIVE_FINANCING"], "DEGRADED")


if __name__ == "__main__":
    unittest.main()
