from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.experience.contracts import (
    ExperienceSourceFrame,
    ExperienceTimescale,
    ExperienceView,
    MarketExperienceFrame,
)
from autonomous_kernel.experience.root_path import (
    EconomicRootPathExperience,
    RootPathExperienceError,
    RootPathExperienceStore,
    build_economic_root_path,
)
from autonomous_kernel.observation.instruments import CanonicalInstrument
from autonomous_kernel.representation.contracts import RepresentationFrame
from autonomous_kernel.representation.store import RepresentationStore


T = 1_788_400_000_000_000_000
SECOND = 1_000_000_000
SUBJECT = "ASSET.BTC"


def _instrument(symbol="BTC"):
    return CanonicalInstrument(
        canonical_id="CRYPTO.SPOT.%s-USD" % symbol,
        asset_class="CRYPTO",
        market_type="SPOT",
        base_asset=symbol,
        quote_asset="USD",
        settlement_asset="USD",
    )


def _frame(frame_id, price, *, cutoff, known=None, instrument=None):
    inst = instrument or _instrument()
    known_at = cutoff if known is None else int(known)
    return RepresentationFrame(
        frame_id=frame_id,
        representation_type="INSTRUMENT_STATE",
        instrument=inst,
        window_start_ns=max(0, cutoff - SECOND),
        cutoff_at_ns=int(cutoff),
        known_at_ns=known_at,
        latest_source_event_at_ns=known_at,
        status="QUALIFIED",
        builder_version="root-path-test-source-v1",
        parameters={"depth_bands_bps": [10]},
        state={"aggregate": {"mean_venue_midpoint": str(price)}},
        source_observation_ids=("OBS-%s" % frame_id,),
        source_content_hashes=(hashlib.sha256(frame_id.encode("utf-8")).hexdigest(),),
        source_providers=("TEST",),
        source_venues=("TEST",),
    )


def _persist(root, frame):
    RepresentationStore(root).persist(
        frame,
        source_batches=(
            {
                "batch_id": "BATCH-%s" % frame.frame_id,
                "manifest_ref": "TEST",
                "manifest_content_hash": "a" * 64,
            },
        ),
    )


def _experience(cutoff_frame, *, builder_version="root-path-experience-v1"):
    source = ExperienceSourceFrame(
        frame_id=cutoff_frame.frame_id,
        frame_hash=cutoff_frame.content_hash(),
        representation_type=cutoff_frame.representation_type,
        instrument_id=cutoff_frame.instrument.canonical_id,
        market_type=cutoff_frame.instrument.market_type,
        window_start_ns=cutoff_frame.window_start_ns,
        cutoff_at_ns=cutoff_frame.cutoff_at_ns,
        known_at_ns=cutoff_frame.known_at_ns,
        status=cutoff_frame.status,
    )
    view = ExperienceView(
        timescale=ExperienceTimescale.SHORT,
        lookback_ns=60 * SECOND,
        window_start_ns=T - 60 * SECOND,
        cutoff_at_ns=T,
        status="QUALIFIED",
        source_frames=(source,),
        feature_family_status={"SPOT_MICROSTRUCTURE": "QUALIFIED"},
    )
    return MarketExperienceFrame(
        experience_id="EXP-ROOT-PATH-%s" % builder_version.replace("_", "-"),
        economic_root_id=SUBJECT,
        cutoff_at_ns=T,
        known_at_ns=cutoff_frame.known_at_ns,
        status="QUALIFIED",
        builder_version=builder_version,
        graph_id="GRAPH-BTC",
        graph_version="1",
        graph_hash="1" * 64,
        context_id="CTX-BTC",
        context_hash="2" * 64,
        context_status="QUALIFIED",
        views=(view,),
    )


def _full_history(root, *, include_competing_cutoff=False):
    frames = []
    for index, offset in enumerate(range(-60, 1, 10)):
        frame = _frame(
            "REP-BTC-%02d" % index,
            str(100 + index),
            cutoff=T + offset * SECOND,
        )
        _persist(root, frame)
        frames.append(frame)
    if include_competing_cutoff:
        competing = _frame("REP-BTC-COMPETING-CUTOFF", "999", cutoff=T)
        _persist(root, competing)
    return tuple(frames)


def _build(root, experience):
    return build_economic_root_path(
        root,
        experience,
        grid_interval_ns=10 * SECOND,
        max_point_lag_ns=SECOND,
        max_source_age_ns=SECOND,
    )


class EconomicRootPathTests(unittest.TestCase):
    def test_full_fixed_grid_path_is_qualified_round_trip_stable_and_cutoff_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = _full_history(root, include_competing_cutoff=True)
            experience = _experience(frames[-1])
            path = _build(root, experience)
            self.assertEqual("QUALIFIED", path.status)
            self.assertEqual(7, len(path.points))
            self.assertEqual((), path.missing_target_ns)
            self.assertEqual(frames[-1].frame_id, path.points[-1].frame_id)
            self.assertEqual(frames[-1].content_hash(), path.points[-1].frame_content_hash)
            self.assertNotEqual("REP-BTC-COMPETING-CUTOFF", path.points[-1].frame_id)
            restored = EconomicRootPathExperience.from_wire(path.to_wire())
            self.assertEqual(path.to_wire(), restored.to_wire())
            self.assertEqual(path.content_hash(), restored.content_hash())

    def test_wrong_instrument_and_future_known_frame_cannot_fill_grid_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = list(_full_history(root))
            target = T - 30 * SECOND
            # Remove the exact BTC target from the durable store by building a
            # fresh repository with every slot except T-30s.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kept = []
            cutoff_frame = None
            for index, offset in enumerate(range(-60, 1, 10)):
                if offset == -30:
                    continue
                frame = _frame("REP-KEEP-%02d" % index, str(100 + index), cutoff=T + offset * SECOND)
                _persist(root, frame)
                kept.append(frame)
                if offset == 0:
                    cutoff_frame = frame
            wrong = _frame("REP-ETH-TARGET", "100", cutoff=target, instrument=_instrument("ETH"))
            _persist(root, wrong)
            future_known = _frame(
                "REP-BTC-FUTURE-KNOWN",
                "100",
                cutoff=target,
                known=T + SECOND,
            )
            _persist(root, future_known)
            assert cutoff_frame is not None
            path = _build(root, _experience(cutoff_frame))
            self.assertEqual("DEGRADED", path.status)
            self.assertIn(target, path.missing_target_ns)
            self.assertNotIn("REP-ETH-TARGET", {point.frame_id for point in path.points})
            self.assertNotIn("REP-BTC-FUTURE-KNOWN", {point.frame_id for point in path.points})

    def test_stale_source_is_not_carried_forward_and_one_frame_cannot_fill_two_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cutoff = _frame("REP-CUTOFF", "106", cutoff=T)
            _persist(root, cutoff)
            # Frame cutoff is inside the T-30 slot, but its own knowledge is too
            # old for the declared one-second source-age bound.
            stale = _frame(
                "REP-STALE",
                "103",
                cutoff=T - 30 * SECOND,
                known=T - 35 * SECOND,
            )
            _persist(root, stale)
            # One frame near T-20 cannot satisfy any other grid point because
            # the grid slots and used-frame rule are explicit.
            one = _frame("REP-ONE", "104", cutoff=T - 20 * SECOND)
            _persist(root, one)
            path = _build(root, _experience(cutoff))
            self.assertEqual("DEGRADED", path.status)
            self.assertIn(T - 30 * SECOND, path.missing_target_ns)
            self.assertEqual(1, sum(point.frame_id == "REP-ONE" for point in path.points))

    def test_prediction_time_cutoff_frame_must_be_durably_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = _full_history(root)
            unpersisted = _frame("REP-UNPERSISTED-CUTOFF", "100", cutoff=T)
            experience = _experience(unpersisted)
            with self.assertRaisesRegex(RootPathExperienceError, "cutoff spot frame is not durably recoverable"):
                _build(root, experience)
            self.assertTrue(frames)

    def test_market_experience_hash_is_part_of_root_path_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = _full_history(root)
            first_experience = _experience(frames[-1], builder_version="experience-a")
            second_experience = _experience(frames[-1], builder_version="experience-b")
            first = _build(root, first_experience)
            second = _build(root, second_experience)
            self.assertNotEqual(first_experience.content_hash(), second_experience.content_hash())
            self.assertNotEqual(first.root_path_id, second.root_path_id)
            self.assertNotEqual(first.content_hash(), second.content_hash())

    def test_wire_tamper_and_truth_boundary_weakening_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = _full_history(root)
            path = _build(root, _experience(frames[-1]))
            changed = copy.deepcopy(path.to_wire())
            changed["points"][0]["midpoint"] = "999"
            with self.assertRaisesRegex(RootPathExperienceError, "content hash mismatch"):
                EconomicRootPathExperience.from_wire(changed)

            weakened = copy.deepcopy(path.to_wire())
            weakened["truth_boundary"]["stale_carry_forward"] = True
            # Re-signing a weakened document must still fail semantic recovery,
            # not merely integrity verification.
            body = {key: value for key, value in weakened.items() if key != "integrity"}
            from autonomous_kernel.book_bridge import canonical_json
            weakened["integrity"]["content_hash"] = hashlib.sha256(canonical_json(body)).hexdigest()
            with self.assertRaisesRegex(RootPathExperienceError, "stale-carry-forward"):
                EconomicRootPathExperience.from_wire(weakened)

    def test_store_is_idempotent_hash_chained_and_book_commitment_is_compact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = _full_history(root)
            first = _build(root, _experience(frames[-1], builder_version="experience-a"))
            second = _build(root, _experience(frames[-1], builder_version="experience-b"))
            store = RootPathExperienceStore(root)
            first_event = store.persist(first)
            replay = store.persist(first)
            self.assertEqual(first_event, replay)
            store.persist(second)
            ok, errors = store.verify()
            self.assertTrue(ok, errors)
            commitment = store.commitment()
            body = commitment.body()
            self.assertEqual(2, body["range"]["event_count"])
            self.assertEqual(store.JOURNAL_NAME, body["journal_name"])
            serialized = json.dumps(body, sort_keys=True)
            self.assertNotIn("midpoint", serialized)
            self.assertNotIn("points", serialized)
            self.assertNotIn(frames[0].frame_id, serialized)

            lines = store.journal_path.read_text(encoding="utf-8").splitlines()
            tampered = json.loads(lines[0])
            tampered["content_hash"] = "f" * 64
            lines[0] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
            store.journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            ok, errors = store.verify()
            self.assertFalse(ok)
            self.assertTrue(any("event hash mismatch" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
