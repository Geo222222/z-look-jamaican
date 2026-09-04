from __future__ import annotations

from decimal import Decimal
import hashlib
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.evaluation import (
    REVERSAL_MATERIAL_RESOLVER_IMPLEMENTATION_REF,
    resolve_reversal_question,
)
from autonomous_kernel.experience.contracts import (
    ExperienceSourceFrame,
    ExperienceTimescale,
    ExperienceView,
    MarketExperienceFrame,
)
from autonomous_kernel.experience.root_path import (
    EconomicRootPathExperience,
    RootPathExperienceStore,
    RootPathPoint,
)
from autonomous_kernel.observation.instruments import CanonicalInstrument
from autonomous_kernel.prediction import (
    PredictionArtifactRef,
    QuestionPredictionJournal,
    build_question_bound_prediction,
)
from autonomous_kernel.questions import (
    REVERSAL_QUESTION_V1_1_REF,
    REVERSAL_QUESTION_V1_2_REF,
    REVERSAL_QUESTION_V1_REF,
    build_complete_resolver_ready_registry_v1_1,
    build_question_registry_v1_qualified,
    default_question_registry_v1,
    reversal_question_v1_1,
    reversal_question_v1_2,
)
from autonomous_kernel.representation.contracts import RepresentationFrame


T = 1_788_600_000_000_000_000
SECOND = 1_000_000_000
SUBJECT = "ASSET.BTC"


def _price_after_bps(price: str, bps: str) -> str:
    value = Decimal(price) * (Decimal("1") + Decimal(bps) / Decimal("10000"))
    return format(value, "f")


def _instrument() -> CanonicalInstrument:
    return CanonicalInstrument(
        canonical_id="CRYPTO.SPOT.BTC-USD",
        asset_class="CRYPTO",
        market_type="SPOT",
        base_asset="BTC",
        quote_asset="USD",
        settlement_asset="USD",
    )


def _frame(frame_id: str, price: str, *, cutoff: int) -> RepresentationFrame:
    return RepresentationFrame(
        frame_id=frame_id,
        representation_type="INSTRUMENT_STATE",
        instrument=_instrument(),
        window_start_ns=max(0, cutoff - SECOND),
        cutoff_at_ns=cutoff,
        known_at_ns=cutoff,
        latest_source_event_at_ns=cutoff,
        status="QUALIFIED",
        builder_version="material-reversal-test-v1",
        parameters={"test": True},
        state={"aggregate": {"mean_venue_midpoint": str(price)}},
        source_observation_ids=("OBS-%s" % frame_id,),
        source_content_hashes=(hashlib.sha256(frame_id.encode("utf-8")).hexdigest(),),
        source_providers=("TEST",),
        source_venues=("TEST",),
    )


def _source(frame: RepresentationFrame) -> ExperienceSourceFrame:
    return ExperienceSourceFrame(
        frame_id=frame.frame_id,
        frame_hash=frame.content_hash(),
        representation_type=frame.representation_type,
        instrument_id=frame.instrument.canonical_id,
        market_type=frame.instrument.market_type,
        window_start_ns=frame.window_start_ns,
        cutoff_at_ns=frame.cutoff_at_ns,
        known_at_ns=frame.known_at_ns,
        status=frame.status,
    )


def _experience(baseline: RepresentationFrame) -> MarketExperienceFrame:
    return MarketExperienceFrame(
        experience_id="EXP-MATERIAL-REVERSAL-BTC-T",
        economic_root_id=SUBJECT,
        cutoff_at_ns=T,
        known_at_ns=T,
        status="QUALIFIED",
        builder_version="material-reversal-experience-v1",
        graph_id="GRAPH-BTC",
        graph_version="1",
        graph_hash="1" * 64,
        context_id="CTX-BTC",
        context_hash="2" * 64,
        context_status="QUALIFIED",
        views=(
            ExperienceView(
                timescale=ExperienceTimescale.SHORT,
                lookback_ns=60 * SECOND,
                window_start_ns=T - 60 * SECOND,
                cutoff_at_ns=T,
                status="QUALIFIED",
                source_frames=(_source(baseline),),
                feature_family_status={"SPOT_MICROSTRUCTURE": "QUALIFIED"},
            ),
        ),
    )


def _root_path(experience: MarketExperienceFrame, baseline: RepresentationFrame, first_price: str) -> EconomicRootPathExperience:
    targets = tuple(range(T - 60 * SECOND, T + 1, 10 * SECOND))
    first = Decimal(first_price)
    last = Decimal(str(baseline.state["aggregate"]["mean_venue_midpoint"]))
    points = []
    for index, target in enumerate(targets):
        fraction = Decimal(index) / Decimal(len(targets) - 1)
        midpoint = first + (last - first) * fraction
        if target == T:
            frame_id = baseline.frame_id
            frame_hash = baseline.content_hash()
            midpoint = last
        else:
            frame_id = "REP-MAT-TRAIL-%02d" % index
            frame_hash = hashlib.sha256(frame_id.encode("utf-8")).hexdigest()
        points.append(
            RootPathPoint(
                target_at_ns=target,
                frame_id=frame_id,
                frame_content_hash=frame_hash,
                frame_cutoff_at_ns=target,
                frame_known_at_ns=target,
                midpoint=format(midpoint, "f"),
                midpoint_source="MEAN_QUALIFIED_VENUE_MIDPOINT_V1",
            )
        )
    material = "%s|%s" % (first_price, format(last, "f"))
    return EconomicRootPathExperience(
        root_path_id="ROOTPATH-%s" % hashlib.sha256(material.encode("utf-8")).hexdigest()[:32],
        economic_root_id=SUBJECT,
        instrument_id=baseline.instrument.canonical_id,
        timescale=ExperienceTimescale.SHORT,
        window_start_ns=T - 60 * SECOND,
        cutoff_at_ns=T,
        known_at_ns=T,
        grid_interval_ns=10 * SECOND,
        max_point_lag_ns=SECOND,
        max_source_age_ns=SECOND,
        status="QUALIFIED",
        baseline_experience_id=experience.experience_id,
        baseline_experience_hash=experience.content_hash(),
        baseline_spot_frame_id=baseline.frame_id,
        baseline_spot_frame_hash=baseline.content_hash(),
        points=tuple(points),
        missing_target_ns=(),
    )


def _qualified_registry():
    base = default_question_registry_v1(
        registered_at_ns=T - 20 * SECOND,
        effective_at_ns=T - 19 * SECOND,
    )
    return build_question_registry_v1_qualified(
        base,
        known_at_ns=T - 18 * SECOND,
        effective_at_ns=T - 17 * SECOND,
    )


def _sign_registry():
    base = default_question_registry_v1(
        registered_at_ns=T - 20 * SECOND,
        effective_at_ns=T - 19 * SECOND,
    )
    return build_complete_resolver_ready_registry_v1_1(
        base,
        version="material-test-sign-registry",
        known_at_ns=T - 18 * SECOND,
        effective_at_ns=T - 17 * SECOND,
    )


def _prediction(root: Path, experience: MarketExperienceFrame, path_state: EconomicRootPathExperience, *, material: bool):
    registry = _qualified_registry() if material else _sign_registry()
    question = reversal_question_v1_2() if material else reversal_question_v1_1()
    prediction = build_question_bound_prediction(
        registry=registry,
        question=question,
        subject_id=SUBJECT,
        mode="PROSPECTIVE_SHADOW",
        evidence_class="FORWARD_EVALUABLE",
        cutoff_at_ns=T,
        created_at_ns=T,
        answer={"value": 1, "probability_1": "0.6"},
        model_refs=("EXPERT-MATERIAL-REVERSAL-TEST",),
        artifact_refs=(
            PredictionArtifactRef(
                artifact_type="MARKET_EXPERIENCE",
                artifact_id=experience.experience_id,
                content_hash=experience.content_hash(),
                known_at_ns=experience.known_at_ns,
                status=experience.status,
                timescales=(ExperienceTimescale.SHORT,),
                feature_families=("SPOT_MICROSTRUCTURE",),
                subject_ids=(SUBJECT,),
            ),
            PredictionArtifactRef(
                artifact_type="ECONOMIC_ROOT_PATH",
                artifact_id=path_state.root_path_id,
                content_hash=path_state.content_hash(),
                known_at_ns=path_state.known_at_ns,
                status=path_state.status,
                timescales=(ExperienceTimescale.SHORT,),
                feature_families=("ECONOMIC_ROOT_PATH",),
                subject_ids=(SUBJECT,),
            ),
        ),
    )
    QuestionPredictionJournal(root).append(prediction, journaled_at_ns=T + 1)
    return prediction


class MaterialReversalResolverTests(unittest.TestCase):
    def test_registry_preserves_sign_history_and_activates_material_v1_2(self):
        registry = _qualified_registry()
        by_ref = {entry.definition.question_ref: entry for entry in registry.entries}
        self.assertEqual("DEFINED", by_ref[REVERSAL_QUESTION_V1_REF].lifecycle_state)
        self.assertEqual("RETIRED", by_ref[REVERSAL_QUESTION_V1_1_REF].lifecycle_state)
        self.assertEqual("RESOLVER_READY", by_ref[REVERSAL_QUESTION_V1_2_REF].lifecycle_state)
        self.assertEqual(
            REVERSAL_MATERIAL_RESOLVER_IMPLEMENTATION_REF,
            by_ref[REVERSAL_QUESTION_V1_2_REF].resolver_implementation_ref,
        )

    def _resolve_case(self, trailing_bps: str, forward_bps: str, *, material: bool):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_price = "100"
            cutoff_price = _price_after_bps(first_price, trailing_bps)
            baseline = _frame("REP-MATERIAL-CUTOFF", cutoff_price, cutoff=T)
            experience = _experience(baseline)
            path_state = _root_path(experience, baseline, first_price)
            RootPathExperienceStore(root).persist(path_state)
            prediction = _prediction(root, experience, path_state, material=material)
            forward_price = _price_after_bps(cutoff_price, forward_bps)
            forward = _frame("REP-MATERIAL-FORWARD", forward_price, cutoff=T + 60 * SECOND)
            return resolve_reversal_question(
                root,
                prediction.prediction_id,
                baseline_experience=experience,
                forward_frames=(forward,),
                now_at_ns=T + 61 * SECOND,
            )

    def test_one_bp_opposite_tick_after_eighteen_bp_move_is_not_material_reversal(self):
        outcome = self._resolve_case("18", "-1", material=True)
        self.assertEqual({"value": 0}, outcome.realized_answer)

    def test_sign_reversal_v1_1_remains_replayable_for_same_one_bp_opposite_tick(self):
        outcome = self._resolve_case("18", "-1", material=False)
        self.assertEqual({"value": 1}, outcome.realized_answer)

    def test_relative_floor_requires_twenty_five_percent_retracement(self):
        below = self._resolve_case("18", "-4.49", material=True)
        boundary = self._resolve_case("18", "-4.5", material=True)
        self.assertEqual({"value": 0}, below.realized_answer)
        self.assertEqual({"value": 1}, boundary.realized_answer)

    def test_absolute_two_bp_floor_and_trailing_floor_are_inclusive(self):
        too_small_trailing = self._resolve_case("1.99", "-5", material=True)
        exact_floor = self._resolve_case("2", "-2", material=True)
        self.assertEqual({"value": 0}, too_small_trailing.realized_answer)
        self.assertEqual({"value": 1}, exact_floor.realized_answer)

    def test_negative_trailing_move_uses_same_materiality_contract(self):
        outcome = self._resolve_case("-18", "4.5", material=True)
        self.assertEqual({"value": 1}, outcome.realized_answer)

    def test_same_evidence_replays_to_same_material_outcome(self):
        first = self._resolve_case("18", "-5", material=True)
        second = self._resolve_case("18", "-5", material=True)
        self.assertEqual(first.realized_answer, second.realized_answer)
        self.assertEqual(first.question_ref, second.question_ref)
        self.assertEqual(first.resolver_policy_id, second.resolver_policy_id)
        self.assertEqual(first.resolver_implementation_ref, second.resolver_implementation_ref)


if __name__ == "__main__":
    unittest.main()
