from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.evaluation.question_resolvers import QuestionOutcomePendingError
from autonomous_kernel.evaluation.reversal_resolver import (
    REVERSAL_ROOT_PATH_RESOLVER_IMPLEMENTATION_REF,
    ReversalResolverError,
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
    QuestionPredictionError,
    build_question_bound_prediction,
)
from autonomous_kernel.questions import default_question_registry_v1
from autonomous_kernel.questions.evolution import (
    REVERSAL_QUESTION_V1_1_REF,
    build_reversal_v1_1_registry,
    reversal_question_v1_1,
)
from autonomous_kernel.questions.readiness import build_resolver_ready_registry_v1
from autonomous_kernel.representation.contracts import RepresentationFrame


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
        builder_version="reversal-test-frame-v1",
        parameters={"test": True},
        state={"aggregate": {"mean_venue_midpoint": str(price)}},
        source_observation_ids=("OBS-%s" % frame_id,),
        source_content_hashes=(hashlib.sha256(frame_id.encode("utf-8")).hexdigest(),),
        source_providers=("TEST",),
        source_venues=("TEST",),
    )


def _source(frame):
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


def _experience(baseline):
    return MarketExperienceFrame(
        experience_id="EXP-REVERSAL-BTC-T",
        economic_root_id=SUBJECT,
        cutoff_at_ns=T,
        known_at_ns=baseline.known_at_ns,
        status="QUALIFIED",
        builder_version="reversal-test-experience-v1",
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


def _root_path(experience, baseline, first_price, last_price=None, *, status="QUALIFIED", missing=()):
    last = str(first_price if last_price is None else last_price)
    prices = [str(first_price), "100.5", "101", "101.5", "101.8", "101.9", last]
    points = []
    targets = tuple(range(T - 60 * SECOND, T + 1, 10 * SECOND))
    missing_set = set(missing)
    for index, target in enumerate(targets):
        if target in missing_set:
            continue
        if target == T:
            frame_id = baseline.frame_id
            frame_hash = baseline.content_hash()
            midpoint = last
        else:
            frame_id = "REP-TRAIL-%02d" % index
            frame_hash = hashlib.sha256(frame_id.encode("utf-8")).hexdigest()
            midpoint = prices[index]
        points.append(
            RootPathPoint(
                target_at_ns=target,
                frame_id=frame_id,
                frame_content_hash=frame_hash,
                frame_cutoff_at_ns=target,
                frame_known_at_ns=target,
                midpoint=midpoint,
                midpoint_source="MEAN_QUALIFIED_VENUE_MIDPOINT_V1",
            )
        )
    material = "%s|%s|%s" % (first_price, last, ",".join(str(value) for value in sorted(missing_set)))
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
        status=status,
        baseline_experience_id=experience.experience_id,
        baseline_experience_hash=experience.content_hash(),
        baseline_spot_frame_id=baseline.frame_id,
        baseline_spot_frame_hash=baseline.content_hash(),
        points=tuple(points),
        missing_target_ns=tuple(sorted(missing_set)),
    )


def _registry():
    base = default_question_registry_v1(registered_at_ns=T - 20 * SECOND, effective_at_ns=T - 19 * SECOND)
    ready = build_resolver_ready_registry_v1(
        base,
        version="1.1.0-nine-resolvers",
        known_at_ns=T - 18 * SECOND,
        effective_at_ns=T - 17 * SECOND,
    )
    return build_reversal_v1_1_registry(
        ready,
        version="1.2.0-reversal-v1.1",
        known_at_ns=T - 16 * SECOND,
        effective_at_ns=T - 15 * SECOND,
    )


def _prediction(root, experience, path_state):
    question = reversal_question_v1_1()
    registry = _registry()
    prediction = build_question_bound_prediction(
        registry=registry,
        question=question,
        subject_id=SUBJECT,
        mode="PROSPECTIVE_SHADOW",
        evidence_class="FORWARD_EVALUABLE",
        cutoff_at_ns=T,
        created_at_ns=T,
        answer={"value": 1, "probability_1": "0.6"},
        model_refs=("EXPERT-REVERSAL-TEST",),
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


class ReversalResolverTests(unittest.TestCase):
    def test_registry_versions_reversal_without_rewriting_v1(self):
        registry = _registry()
        old = [entry for entry in registry.entries if entry.definition.question_ref == "ECONOMIC_ROOT_REVERSAL_60S@1.0.0"]
        new = [entry for entry in registry.entries if entry.definition.question_ref == REVERSAL_QUESTION_V1_1_REF]
        self.assertEqual(1, len(old))
        self.assertEqual("DEFINED", old[0].lifecycle_state)
        self.assertIsNone(old[0].resolver_implementation_ref)
        self.assertEqual(1, len(new))
        self.assertEqual("RESOLVER_READY", new[0].lifecycle_state)
        self.assertEqual(REVERSAL_ROOT_PATH_RESOLVER_IMPLEMENTATION_REF, new[0].resolver_implementation_ref)
        self.assertNotEqual(old[0].definition.content_hash(), new[0].definition.content_hash())
        self.assertIn("ECONOMIC_ROOT_PATH", new[0].definition.required_artifact_types)

    def test_positive_trailing_negative_forward_is_reversal_and_first_endpoint_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = _frame("REP-CUTOFF", "102", cutoff=T)
            experience = _experience(baseline)
            path_state = _root_path(experience, baseline, "100", "102")
            RootPathExperienceStore(root).persist(path_state)
            prediction = _prediction(root, experience, path_state)
            first = _frame("REP-FIRST", "101", cutoff=T + 60 * SECOND)
            later = _frame("REP-LATER", "103", cutoff=T + 61 * SECOND)
            outcome = resolve_reversal_question(
                root,
                prediction.prediction_id,
                baseline_experience=experience,
                forward_frames=(later, first),
                now_at_ns=T + 62 * SECOND,
            )
            self.assertEqual("RESOLVED", outcome.status)
            self.assertEqual({"value": 1}, outcome.realized_answer)
            self.assertEqual(path_state.root_path_id, outcome.resolution_evidence[0].artifact_id)
            self.assertEqual("REP-FIRST", outcome.resolution_evidence[1].artifact_id)

    def test_negative_trailing_positive_forward_is_reversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = _frame("REP-CUTOFF", "100", cutoff=T)
            experience = _experience(baseline)
            path_state = _root_path(experience, baseline, "102", "100")
            RootPathExperienceStore(root).persist(path_state)
            prediction = _prediction(root, experience, path_state)
            forward = _frame("REP-FORWARD", "101", cutoff=T + 60 * SECOND)
            outcome = resolve_reversal_question(root, prediction.prediction_id, baseline_experience=experience, forward_frames=(forward,), now_at_ns=T + 61 * SECOND)
            self.assertEqual({"value": 1}, outcome.realized_answer)

    def test_same_sign_and_zero_returns_are_not_reversals(self):
        cases = (
            ("100", "102", "103"),
            ("100", "100", "101"),
            ("100", "102", "102"),
        )
        for index, (first_price, cutoff_price, forward_price) in enumerate(cases):
            with self.subTest(case=index), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                baseline = _frame("REP-CUTOFF-%d" % index, cutoff_price, cutoff=T)
                experience = _experience(baseline)
                path_state = _root_path(experience, baseline, first_price, cutoff_price)
                RootPathExperienceStore(root).persist(path_state)
                prediction = _prediction(root, experience, path_state)
                forward = _frame("REP-FORWARD-%d" % index, forward_price, cutoff=T + 60 * SECOND)
                outcome = resolve_reversal_question(root, prediction.prediction_id, baseline_experience=experience, forward_frames=(forward,), now_at_ns=T + 61 * SECOND)
                self.assertEqual({"value": 0}, outcome.realized_answer)

    def test_wrong_instrument_cannot_substitute_and_missing_forward_becomes_unresolvable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = _frame("REP-CUTOFF", "102", cutoff=T)
            experience = _experience(baseline)
            path_state = _root_path(experience, baseline, "100", "102")
            RootPathExperienceStore(root).persist(path_state)
            prediction = _prediction(root, experience, path_state)
            wrong = _frame("REP-ETH", "90", cutoff=T + 60 * SECOND, instrument=_instrument("ETH"))
            with self.assertRaisesRegex(QuestionOutcomePendingError, "window remains open"):
                resolve_reversal_question(
                    root,
                    prediction.prediction_id,
                    baseline_experience=experience,
                    forward_frames=(wrong,),
                    now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns,
                )
            outcome = resolve_reversal_question(
                root,
                prediction.prediction_id,
                baseline_experience=experience,
                forward_frames=(wrong,),
                now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns + 1,
            )
            self.assertEqual("UNRESOLVABLE", outcome.status)
            self.assertEqual((), outcome.resolution_evidence)

    def test_rebuilt_or_tampered_root_path_cannot_substitute_for_prediction_bound_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = _frame("REP-CUTOFF", "102", cutoff=T)
            experience = _experience(baseline)
            path_state = _root_path(experience, baseline, "100", "102")
            store = RootPathExperienceStore(root)
            store.persist(path_state)
            prediction = _prediction(root, experience, path_state)
            snapshot = store.snapshot_dir / (path_state.root_path_id + ".json")
            document = snapshot.read_text(encoding="utf-8").replace('"102"', '"103"', 1)
            snapshot.write_text(document, encoding="utf-8")
            forward = _frame("REP-FORWARD", "101", cutoff=T + 60 * SECOND)
            with self.assertRaisesRegex(ReversalResolverError, "Root Path store invalid"):
                resolve_reversal_question(root, prediction.prediction_id, baseline_experience=experience, forward_frames=(forward,), now_at_ns=T + 61 * SECOND)

    def test_degraded_or_missing_root_path_cannot_form_prospective_v1_1_prediction(self):
        baseline = _frame("REP-CUTOFF", "102", cutoff=T)
        experience = _experience(baseline)
        missing_target = T - 30 * SECOND
        degraded = _root_path(experience, baseline, "100", "102", status="DEGRADED", missing=(missing_target,))
        question = reversal_question_v1_1()
        registry = _registry()
        market_ref = PredictionArtifactRef(
            artifact_type="MARKET_EXPERIENCE",
            artifact_id=experience.experience_id,
            content_hash=experience.content_hash(),
            known_at_ns=experience.known_at_ns,
            status="QUALIFIED",
            timescales=(ExperienceTimescale.SHORT,),
            feature_families=("SPOT_MICROSTRUCTURE",),
            subject_ids=(SUBJECT,),
        )
        degraded_ref = PredictionArtifactRef(
            artifact_type="ECONOMIC_ROOT_PATH",
            artifact_id=degraded.root_path_id,
            content_hash=degraded.content_hash(),
            known_at_ns=degraded.known_at_ns,
            status="DEGRADED",
            timescales=(ExperienceTimescale.SHORT,),
            feature_families=("ECONOMIC_ROOT_PATH",),
            subject_ids=(SUBJECT,),
        )
        with self.assertRaisesRegex(QuestionPredictionError, "qualified artifact"):
            build_question_bound_prediction(
                registry=registry,
                question=question,
                subject_id=SUBJECT,
                mode="PROSPECTIVE_SHADOW",
                evidence_class="FORWARD_EVALUABLE",
                cutoff_at_ns=T,
                created_at_ns=T,
                answer={"value": 0},
                model_refs=("MODEL",),
                artifact_refs=(market_ref, degraded_ref),
            )
        with self.assertRaisesRegex(QuestionPredictionError, "required artifact type"):
            build_question_bound_prediction(
                registry=registry,
                question=question,
                subject_id=SUBJECT,
                mode="PROSPECTIVE_SHADOW",
                evidence_class="FORWARD_EVALUABLE",
                cutoff_at_ns=T,
                created_at_ns=T,
                answer={"value": 0},
                model_refs=("MODEL",),
                artifact_refs=(market_ref,),
            )

    def test_v1_reversal_never_gains_prospective_authority_retroactively(self):
        registry = _registry()
        old = next(entry.definition for entry in registry.entries if entry.definition.question_ref == "ECONOMIC_ROOT_REVERSAL_60S@1.0.0")
        baseline = _frame("REP-CUTOFF", "102", cutoff=T)
        experience = _experience(baseline)
        market_ref = PredictionArtifactRef(
            artifact_type="MARKET_EXPERIENCE",
            artifact_id=experience.experience_id,
            content_hash=experience.content_hash(),
            known_at_ns=experience.known_at_ns,
            status="QUALIFIED",
            timescales=(ExperienceTimescale.SHORT,),
            feature_families=("SPOT_MICROSTRUCTURE", "MARKET_WIDE_CONTEXT"),
            subject_ids=(SUBJECT,),
        )
        wide_ref = PredictionArtifactRef(
            artifact_type="MARKET_WIDE_EXPERIENCE",
            artifact_id="MW-BTC",
            content_hash="3" * 64,
            known_at_ns=T,
            status="QUALIFIED",
            timescales=(ExperienceTimescale.SHORT,),
            feature_families=("MARKET_WIDE_CONTEXT",),
            subject_ids=(SUBJECT,),
        )
        with self.assertRaisesRegex(QuestionPredictionError, "resolver-ready"):
            build_question_bound_prediction(
                registry=registry,
                question=old,
                subject_id=SUBJECT,
                mode="PROSPECTIVE_SHADOW",
                evidence_class="FORWARD_EVALUABLE",
                cutoff_at_ns=T,
                created_at_ns=T,
                answer={"value": 0},
                model_refs=("MODEL",),
                artifact_refs=(market_ref, wide_ref),
            )


if __name__ == "__main__":
    unittest.main()
