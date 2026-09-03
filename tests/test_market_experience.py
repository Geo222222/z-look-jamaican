from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from autonomous_kernel.book_bridge import ZLJBookSigner
from autonomous_kernel.book_outbox import BookOutbox
from autonomous_kernel.context.contracts import MarketContextFrame
from autonomous_kernel.experience import (
    EconomicInstrumentGraph,
    EconomicInstrumentNode,
    EconomicRelationship,
    EconomicRelationshipType,
    ExperienceTimescale,
    InstrumentRole,
    MarketExperienceFrame,
    MarketExperienceStore,
    TimescaleSpec,
    build_market_experience,
)
from autonomous_kernel.experience.contracts import MarketExperienceError
from autonomous_kernel.observation.instruments import CanonicalInstrument
from autonomous_kernel.representation.contracts import RepresentationFrame


T = 1_788_400_000_000_000_000
SECOND = 1_000_000_000
LOOKBACKS = {
    ExperienceTimescale.MICRO: 10 * SECOND,
    ExperienceTimescale.SHORT: 5 * 60 * SECOND,
    ExperienceTimescale.SESSION: 60 * 60 * SECOND,
    ExperienceTimescale.MACRO_STRUCTURAL: 24 * 60 * 60 * SECOND,
}


def _instrument(market_type: str) -> CanonicalInstrument:
    if market_type == "SPOT":
        return CanonicalInstrument(
            canonical_id="CRYPTO.SPOT.BTC-USD",
            asset_class="CRYPTO",
            market_type="SPOT",
            base_asset="BTC",
            quote_asset="USD",
            settlement_asset="USD",
        )
    return CanonicalInstrument(
        canonical_id="CRYPTO.PERP.BTC-USD",
        asset_class="CRYPTO",
        market_type="PERPETUAL",
        base_asset="BTC",
        quote_asset="USD",
        settlement_asset="USD",
    )


def _frame(name: str, market_type: str, lookback: int, *, cutoff: int = T, start_adjust: int = 0) -> RepresentationFrame:
    instrument = _instrument(market_type)
    token = (name + market_type).encode("utf-8").hex().ljust(64, "0")[:64]
    return RepresentationFrame(
        frame_id=f"REP-{name}-{market_type}",
        representation_type="INSTRUMENT_STATE",
        instrument=instrument,
        window_start_ns=cutoff - lookback + start_adjust,
        cutoff_at_ns=cutoff,
        known_at_ns=cutoff - 1,
        latest_source_event_at_ns=cutoff - 2,
        status="QUALIFIED",
        builder_version="test-representation-v1",
        parameters={"test": True},
        state={"aggregate": {"mean_venue_midpoint": "100"}},
        source_observation_ids=(f"OBS-{name}-{market_type}",),
        source_content_hashes=(token,),
        source_providers=("TEST",),
        source_venues=("TEST",),
    )


def _graph() -> EconomicInstrumentGraph:
    spot = EconomicInstrumentNode(
        node_id="BTC-SPOT",
        instrument=_instrument("SPOT"),
        role=InstrumentRole.SPOT,
        economic_root_id="ASSET.BTC",
        quote_family_id="QUOTE.USD",
    )
    perp = EconomicInstrumentNode(
        node_id="BTC-PERP",
        instrument=_instrument("PERPETUAL"),
        role=InstrumentRole.PERPETUAL,
        economic_root_id="ASSET.BTC",
        quote_family_id="QUOTE.USD",
        contract_spec_ref="spec://btc-perp-v1",
    )
    return EconomicInstrumentGraph(
        graph_id="CRYPTO-GRAPH",
        graph_version="1.0.0",
        effective_at_ns=T - 2 * 24 * 60 * 60 * SECOND,
        known_at_ns=T - 2 * 24 * 60 * 60 * SECOND + 1,
        nodes=(spot, perp),
        relationships=(
            EconomicRelationship(
                relationship_id="BTC-SPOT-PERP",
                relationship_type=EconomicRelationshipType.SPOT_DERIVATIVE,
                source_node_id="BTC-SPOT",
                target_node_id="BTC-PERP",
                rationale="same BTC economic root across spot and perpetual expressions",
            ),
        ),
    )


def _context(*, cutoff: int = T) -> MarketContextFrame:
    spot = _frame("CTX-SPOT", "SPOT", 60 * SECOND, cutoff=cutoff)
    perp = _frame("CTX-PERP", "PERPETUAL", 60 * SECOND, cutoff=cutoff)
    frames = (spot, perp)
    return MarketContextFrame(
        context_id=f"CTX-{cutoff}",
        context_type="MARKET_CONTEXT",
        cutoff_at_ns=cutoff,
        known_at_ns=cutoff - 1,
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


def _specs(micro_lookback: int = LOOKBACKS[ExperienceTimescale.MICRO]) -> tuple[TimescaleSpec, ...]:
    return (
        TimescaleSpec(ExperienceTimescale.MICRO, micro_lookback),
        TimescaleSpec(ExperienceTimescale.SHORT, LOOKBACKS[ExperienceTimescale.SHORT]),
        TimescaleSpec(ExperienceTimescale.SESSION, LOOKBACKS[ExperienceTimescale.SESSION]),
        TimescaleSpec(ExperienceTimescale.MACRO_STRUCTURAL, LOOKBACKS[ExperienceTimescale.MACRO_STRUCTURAL]),
    )


def _timescale_frames(*, cutoff: int = T, micro_start_adjust: int = 0):
    result = {}
    for timescale, lookback in LOOKBACKS.items():
        adjustment = micro_start_adjust if timescale is ExperienceTimescale.MICRO else 0
        result[timescale] = (
            _frame(f"{timescale.value}-SPOT-{cutoff}", "SPOT", lookback, cutoff=cutoff, start_adjust=adjustment),
            _frame(f"{timescale.value}-PERP-{cutoff}", "PERPETUAL", lookback, cutoff=cutoff, start_adjust=adjustment),
        )
    return result


def _experience(*, cutoff: int = T, micro_start_adjust: int = 0, micro_lookback: int | None = None) -> MarketExperienceFrame:
    specs = _specs() if micro_lookback is None else _specs(micro_lookback)
    frames = _timescale_frames(cutoff=cutoff, micro_start_adjust=micro_start_adjust)
    if micro_lookback is not None:
        frames[ExperienceTimescale.MICRO] = (
            _frame(f"MICRO-SPOT-{cutoff}", "SPOT", micro_lookback, cutoff=cutoff),
            _frame(f"MICRO-PERP-{cutoff}", "PERPETUAL", micro_lookback, cutoff=cutoff),
        )
    return build_market_experience(
        economic_root_id="ASSET.BTC",
        graph=_graph(),
        context=_context(cutoff=cutoff),
        timescale_frames=frames,
        timescale_specs=specs,
        cutoff_at_ns=cutoff,
    )


class MarketExperienceTests(unittest.TestCase):
    def test_experience_is_deterministic_causal_and_has_no_outcome_payload(self) -> None:
        first = _experience()
        second = _experience()
        self.assertEqual(first.experience_id, second.experience_id)
        self.assertEqual(first.content_hash(), second.content_hash())
        self.assertEqual(first.status, "QUALIFIED")
        wire = first.to_wire()
        self.assertNotIn("outcome", wire)
        self.assertNotIn("future_path", wire)
        restored = MarketExperienceFrame.from_wire(wire)
        self.assertEqual(restored.content_hash(), first.content_hash())
        micro = next(view for view in first.views if view.timescale is ExperienceTimescale.MICRO)
        self.assertEqual(micro.feature_family_status["DERIVATIVE_MICROSTRUCTURE"], "QUALIFIED")
        self.assertEqual(micro.feature_family_status["DERIVATIVE_POSITIONING"], "UNAVAILABLE")
        self.assertEqual(micro.feature_family_status["DERIVATIVE_FINANCING"], "UNAVAILABLE")

    def test_timescale_definition_is_part_of_experience_identity(self) -> None:
        first = _experience()
        second = _experience(micro_lookback=5 * SECOND)
        self.assertNotEqual(first.experience_id, second.experience_id)
        self.assertNotEqual(first.content_hash(), second.content_hash())

    def test_source_contaminating_short_view_with_older_history_is_rejected(self) -> None:
        with self.assertRaisesRegex(MarketExperienceError, "before timescale view start"):
            _experience(micro_start_adjust=-1)

    def test_incomplete_timescale_is_explicitly_degraded(self) -> None:
        frame = _experience(micro_start_adjust=1)
        micro = next(view for view in frame.views if view.timescale is ExperienceTimescale.MICRO)
        self.assertEqual(micro.status, "DEGRADED")
        self.assertEqual(frame.status, "DEGRADED")
        self.assertEqual(micro.feature_family_status["SPOT_MICROSTRUCTURE"], "DEGRADED")

    def test_future_known_source_frame_is_rejected(self) -> None:
        frames = _timescale_frames()
        lookback = LOOKBACKS[ExperienceTimescale.MICRO]
        frames[ExperienceTimescale.MICRO] = (
            _frame("FUTURE", "SPOT", lookback, cutoff=T + 1),
        )
        with self.assertRaisesRegex(MarketExperienceError, "lookahead source frame"):
            build_market_experience(
                economic_root_id="ASSET.BTC",
                graph=_graph(),
                context=_context(),
                timescale_frames=frames,
                timescale_specs=_specs(),
                cutoff_at_ns=T,
            )

    def test_immutable_store_is_idempotent_and_detects_journal_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketExperienceStore(Path(directory))
            first = _experience()
            second = _experience(cutoff=T + 100 * SECOND)
            receipt1 = store.persist(first)
            replay = store.persist(first)
            receipt2 = store.persist(second)
            self.assertTrue(receipt1.appended_event)
            self.assertFalse(replay.appended_event)
            self.assertEqual(replay.sequence, receipt1.sequence)
            self.assertEqual(store.load(first.experience_id).content_hash(), first.content_hash())
            valid, errors = store.verify()
            self.assertTrue(valid, errors)

            lines = store.journal_path.read_text(encoding="utf-8").splitlines()
            event = json.loads(lines[0])
            event["status"] = "UNAVAILABLE"
            lines[0] = json.dumps(event, sort_keys=True, separators=(",", ":"))
            store.journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            valid, errors = store.verify()
            self.assertFalse(valid)
            self.assertTrue(any("content hash mismatch" in error for error in errors))
            self.assertTrue(any("previous hash mismatch" in error for error in errors))
            self.assertEqual(receipt2.sequence, 2)

    def test_compact_experience_commitment_is_book_bound_not_raw_tick_dump(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = MarketExperienceStore(root / "experience")
            store.persist(_experience())
            store.persist(_experience(cutoff=T + 100 * SECOND))
            commitment = store.commitment(start_sequence=1, end_sequence=2)
            intent = commitment.material_evidence(payload_ref="zlj://experience-journal/commitments/1-2")
            payload_text = intent.payload.decode("utf-8")
            self.assertNotIn("source_frames", payload_text)
            self.assertNotIn("OBS-", payload_text)
            self.assertNotIn("REP-", payload_text)
            self.assertEqual(commitment.event_count, 2)

            signer = ZLJBookSigner(key_id="experience-test", private_key=Ed25519PrivateKey.generate())
            produced_at = datetime.fromtimestamp(commitment.known_at_ns / 1_000_000_000 + 1, tz=timezone.utc)
            envelope = intent.sign(
                signer=signer,
                receipt_id="ZLJ-EXPERIENCE-COMMIT-1",
                produced_at=produced_at,
                visibility_scope=("INSTITUTION", "BENJAMIN"),
            )
            self.assertEqual(envelope["event_type"], "ZLJ.EXPERIENCE_JOURNAL_COMMITMENT")
            record = BookOutbox(root / "book-outbox").enqueue(envelope=envelope, payload=intent.payload)
            self.assertEqual(record["state"], "PENDING")


if __name__ == "__main__":
    unittest.main()
