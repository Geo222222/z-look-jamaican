from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.evaluation.question_resolvers import (
    MIDPOINT_RESOLVER_IMPLEMENTATION_REF,
    QuestionOutcomePendingError,
    QuestionResolverError,
    resolve_midpoint_question,
)
from autonomous_kernel.experience.contracts import (
    ExperienceSourceFrame,
    ExperienceTimescale,
    ExperienceView,
    MarketExperienceFrame,
)
from autonomous_kernel.observation.instruments import CanonicalInstrument
from autonomous_kernel.prediction import (
    PredictionArtifactRef,
    QuestionPredictionJournal,
    build_question_bound_prediction,
)
from autonomous_kernel.questions.catalog import question_catalog_v1
from autonomous_kernel.questions.contracts import QuestionRegistryEntry, build_question_registry_snapshot
from autonomous_kernel.representation.contracts import RepresentationFrame


T = 1_788_400_000_000_000_000
SECOND = 1_000_000_000
SUBJECT = "ASSET.BTC"


def _instrument(quote="USD"):
    return CanonicalInstrument(
        canonical_id="CRYPTO.SPOT.BTC-%s" % quote,
        asset_class="CRYPTO",
        market_type="SPOT",
        base_asset="BTC",
        quote_asset=quote,
        settlement_asset=quote,
    )


def _frame(frame_id, price, *, cutoff, known=None, instrument=None, window_start=None):
    inst = instrument or _instrument()
    known_at = cutoff if known is None else known
    start = cutoff - 10 * SECOND if window_start is None else window_start
    token = (frame_id.encode("utf-8").hex() * 64)[:64]
    return RepresentationFrame(
        frame_id=frame_id,
        representation_type="INSTRUMENT_STATE",
        instrument=inst,
        window_start_ns=start,
        cutoff_at_ns=cutoff,
        known_at_ns=known_at,
        latest_source_event_at_ns=known_at,
        status="QUALIFIED",
        builder_version="test-v1",
        parameters={"test": True},
        state={"aggregate": {"mean_venue_midpoint": str(price)}},
        source_observation_ids=("OBS-%s" % frame_id,),
        source_content_hashes=(token,),
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


def _experience(micro_frames, *, include_short=False):
    views = [
        ExperienceView(
            timescale=ExperienceTimescale.MICRO,
            lookback_ns=10 * SECOND,
            window_start_ns=T - 10 * SECOND,
            cutoff_at_ns=T,
            status="QUALIFIED",
            source_frames=tuple(_source(frame) for frame in micro_frames),
            feature_family_status={"SPOT_MICROSTRUCTURE": "QUALIFIED"},
        )
    ]
    if include_short:
        short_frame = _frame(
            "REP-SHORT",
            "100",
            cutoff=T,
            known=T - 1,
            window_start=T - 5 * 60 * SECOND,
        )
        views.append(
            ExperienceView(
                timescale=ExperienceTimescale.SHORT,
                lookback_ns=5 * 60 * SECOND,
                window_start_ns=T - 5 * 60 * SECOND,
                cutoff_at_ns=T,
                status="QUALIFIED",
                source_frames=(_source(short_frame),),
                feature_family_status={
                    "SPOT_MICROSTRUCTURE": "QUALIFIED",
                    "MARKET_WIDE_CONTEXT": "QUALIFIED",
                },
            )
        )
    return MarketExperienceFrame(
        experience_id="EXP-BTC-%s" % ("SHORT" if include_short else "MICRO"),
        economic_root_id=SUBJECT,
        cutoff_at_ns=T,
        known_at_ns=T - 1,
        status="QUALIFIED",
        builder_version="test-experience-v1",
        graph_id="GRAPH-BTC",
        graph_version="1",
        graph_hash="1" * 64,
        context_id="CTX-BTC",
        context_hash="2" * 64,
        context_status="QUALIFIED",
        views=tuple(views),
    )


def _registry(question):
    return build_question_registry_snapshot(
        registry_id="ZLJ-MARKET-QUESTIONS",
        version="resolver-test",
        entries=(
            QuestionRegistryEntry(
                definition=question,
                lifecycle_state="RESOLVER_READY",
                registered_at_ns=T - 2 * SECOND,
                effective_at_ns=T - SECOND,
                resolver_implementation_ref=MIDPOINT_RESOLVER_IMPLEMENTATION_REF,
            ),
        ),
        known_at_ns=T - 2 * SECOND,
        effective_at_ns=T - SECOND,
    )


def _prediction(root, question, experience, *, magnitude=False):
    artifact_refs = [
        PredictionArtifactRef(
            artifact_type="MARKET_EXPERIENCE",
            artifact_id=experience.experience_id,
            content_hash=experience.content_hash(),
            known_at_ns=experience.known_at_ns,
            status="QUALIFIED",
            timescales=tuple(view.timescale for view in experience.views),
            feature_families=("SPOT_MICROSTRUCTURE", "MARKET_WIDE_CONTEXT") if magnitude else ("SPOT_MICROSTRUCTURE",),
            subject_ids=(SUBJECT,),
        )
    ]
    if magnitude:
        artifact_refs.append(
            PredictionArtifactRef(
                artifact_type="MARKET_WIDE_EXPERIENCE",
                artifact_id="MW-BTC-T",
                content_hash="3" * 64,
                known_at_ns=T - 1,
                status="QUALIFIED",
                timescales=(ExperienceTimescale.SHORT,),
                feature_families=("MARKET_WIDE_CONTEXT",),
                subject_ids=(SUBJECT,),
            )
        )
    prediction = build_question_bound_prediction(
        registry=_registry(question),
        question=question,
        subject_id=SUBJECT,
        mode="PROSPECTIVE_SHADOW",
        evidence_class="FORWARD_EVALUABLE",
        cutoff_at_ns=T,
        created_at_ns=T,
        answer={"value": "25"} if magnitude else {"value": 1, "probability_1": "0.6"},
        model_refs=("MODEL-TEST",),
        artifact_refs=tuple(artifact_refs),
    )
    QuestionPredictionJournal(root).append(prediction, journaled_at_ns=T + 1)
    return prediction


class MidpointQuestionResolverTests(unittest.TestCase):
    def test_direction_uses_first_same_instrument_forward_frame_not_best_endpoint(self):
        baseline = _frame("REP-BASE", "100", cutoff=T, known=T - 1)
        experience = _experience((baseline,))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, question_catalog_v1()[0], experience)
            wrong = _frame("REP-WRONG", "150", cutoff=T + 10 * SECOND, instrument=_instrument("USDT"))
            first = _frame("REP-FIRST", "101", cutoff=T + 10 * SECOND, known=T + 10 * SECOND)
            later = _frame("REP-LATER", "110", cutoff=T + 11 * SECOND, known=T + 11 * SECOND)
            outcome = resolve_midpoint_question(
                root,
                prediction.prediction_id,
                baseline_experience=experience,
                baseline_frames=(baseline,),
                forward_frames=(later, wrong, first),
                now_at_ns=T + 12 * SECOND,
            )
            self.assertEqual("RESOLVED", outcome.status)
            self.assertEqual({"value": 1}, outcome.realized_answer)
            self.assertEqual("REP-FIRST", outcome.resolution_evidence[1].artifact_id)
            self.assertEqual(SUBJECT, outcome.subject_id)

    def test_direction_zero_return_is_not_positive(self):
        baseline = _frame("REP-BASE", "100", cutoff=T, known=T - 1)
        experience = _experience((baseline,))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, question_catalog_v1()[0], experience)
            forward = _frame("REP-FLAT", "100", cutoff=T + 10 * SECOND)
            outcome = resolve_midpoint_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(baseline,), forward_frames=(forward,), now_at_ns=T + 11 * SECOND)
            self.assertEqual({"value": 0}, outcome.realized_answer)

    def test_magnitude_returns_signed_basis_points(self):
        baseline = _frame("REP-BASE", "100", cutoff=T, known=T - 1)
        experience = _experience((baseline,), include_short=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, question_catalog_v1()[1], experience, magnitude=True)
            forward = _frame("REP-TARGET", "100.25", cutoff=T + 30 * SECOND)
            outcome = resolve_midpoint_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(baseline,), forward_frames=(forward,), now_at_ns=T + 31 * SECOND)
            self.assertEqual({"value": "25.0000"}, outcome.realized_answer)

    def test_ambiguous_cutoff_spot_expressions_are_rejected_not_cherry_picked(self):
        usd = _frame("REP-USD", "100", cutoff=T, known=T - 1)
        usdt = _frame("REP-USDT", "101", cutoff=T, known=T - 1, instrument=_instrument("USDT"))
        experience = _experience((usd, usdt))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, question_catalog_v1()[0], experience)
            with self.assertRaisesRegex(QuestionResolverError, "unambiguous"):
                resolve_midpoint_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(usd, usdt), forward_frames=(), now_at_ns=T + 20 * SECOND)

    def test_missing_forward_evidence_is_pending_then_unresolvable(self):
        baseline = _frame("REP-BASE", "100", cutoff=T, known=T - 1)
        experience = _experience((baseline,))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, question_catalog_v1()[0], experience)
            with self.assertRaisesRegex(QuestionOutcomePendingError, "window remains open"):
                resolve_midpoint_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(baseline,), forward_frames=(), now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns)
            outcome = resolve_midpoint_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(baseline,), forward_frames=(), now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns + 1)
            self.assertEqual("UNRESOLVABLE", outcome.status)
            self.assertEqual((), outcome.resolution_evidence)

    def test_baseline_experience_hash_mismatch_is_rejected(self):
        baseline = _frame("REP-BASE", "100", cutoff=T, known=T - 1)
        experience = _experience((baseline,))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, question_catalog_v1()[0], experience)
            changed = MarketExperienceFrame(
                experience_id=experience.experience_id,
                economic_root_id=experience.economic_root_id,
                cutoff_at_ns=experience.cutoff_at_ns,
                known_at_ns=experience.known_at_ns,
                status=experience.status,
                builder_version="changed-builder",
                graph_id=experience.graph_id,
                graph_version=experience.graph_version,
                graph_hash=experience.graph_hash,
                context_id=experience.context_id,
                context_hash=experience.context_hash,
                context_status=experience.context_status,
                views=experience.views,
            )
            with self.assertRaisesRegex(QuestionResolverError, "content hash"):
                resolve_midpoint_question(root, prediction.prediction_id, baseline_experience=changed, baseline_frames=(baseline,), forward_frames=(), now_at_ns=T + 20 * SECOND)


if __name__ == "__main__":
    unittest.main()
