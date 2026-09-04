from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from autonomous_kernel.evaluation.question_path_resolvers import (
    FIXED_GRID_RESOLVER_IMPLEMENTATION_REF,
    QuestionOutcomePendingError,
    resolve_fixed_grid_question,
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


def _instrument(symbol="BTC"):
    return CanonicalInstrument(
        canonical_id="CRYPTO.SPOT.%s-USD" % symbol,
        asset_class="CRYPTO",
        market_type="SPOT",
        base_asset=symbol,
        quote_asset="USD",
        settlement_asset="USD",
    )


def _frame(frame_id, price, *, cutoff, known=None, window_start=None, instrument=None):
    known_at = cutoff if known is None else known
    start = cutoff - 10 * SECOND if window_start is None else window_start
    token = (frame_id.encode("utf-8").hex() * 64)[:64]
    return RepresentationFrame(
        frame_id=frame_id,
        representation_type="INSTRUMENT_STATE",
        instrument=instrument or _instrument(),
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


def _baseline_experience():
    micro = _frame("REP-MICRO-BASE", "100", cutoff=T, known=T - 1, window_start=T - 10 * SECOND)
    short = _frame("REP-SHORT-BASE", "100", cutoff=T, known=T - 1, window_start=T - 5 * 60 * SECOND)
    experience = MarketExperienceFrame(
        experience_id="EXP-PATH-BTC",
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
        views=(
            ExperienceView(
                timescale=ExperienceTimescale.MICRO,
                lookback_ns=10 * SECOND,
                window_start_ns=T - 10 * SECOND,
                cutoff_at_ns=T,
                status="QUALIFIED",
                source_frames=(_source(micro),),
                feature_family_status={"SPOT_MICROSTRUCTURE": "QUALIFIED", "MARKET_WIDE_CONTEXT": "QUALIFIED"},
            ),
            ExperienceView(
                timescale=ExperienceTimescale.SHORT,
                lookback_ns=5 * 60 * SECOND,
                window_start_ns=T - 5 * 60 * SECOND,
                cutoff_at_ns=T,
                status="QUALIFIED",
                source_frames=(_source(short),),
                feature_family_status={"SPOT_MICROSTRUCTURE": "QUALIFIED", "MARKET_WIDE_CONTEXT": "QUALIFIED"},
            ),
        ),
    )
    return experience, micro, short


def _registry(question):
    return build_question_registry_snapshot(
        registry_id="ZLJ-MARKET-QUESTIONS",
        version="path-test",
        entries=(
            QuestionRegistryEntry(
                definition=question,
                lifecycle_state="RESOLVER_READY",
                registered_at_ns=T - 2 * SECOND,
                effective_at_ns=T - SECOND,
                resolver_implementation_ref=FIXED_GRID_RESOLVER_IMPLEMENTATION_REF,
            ),
        ),
        known_at_ns=T - 2 * SECOND,
        effective_at_ns=T - SECOND,
    )


def _prediction(root, question, experience):
    prediction = build_question_bound_prediction(
        registry=_registry(question),
        question=question,
        subject_id=SUBJECT,
        mode="PROSPECTIVE_SHADOW",
        evidence_class="FORWARD_EVALUABLE",
        cutoff_at_ns=T,
        created_at_ns=T,
        answer={"value": "12"},
        model_refs=("MODEL-PATH-TEST",),
        artifact_refs=(
            PredictionArtifactRef(
                artifact_type="MARKET_EXPERIENCE",
                artifact_id=experience.experience_id,
                content_hash=experience.content_hash(),
                known_at_ns=experience.known_at_ns,
                status="QUALIFIED",
                timescales=(ExperienceTimescale.MICRO, ExperienceTimescale.SHORT),
                feature_families=("SPOT_MICROSTRUCTURE", "MARKET_WIDE_CONTEXT"),
                subject_ids=(SUBJECT,),
            ),
            PredictionArtifactRef(
                artifact_type="MARKET_WIDE_EXPERIENCE",
                artifact_id="MW-PATH-BTC",
                content_hash="3" * 64,
                known_at_ns=T - 1,
                status="QUALIFIED",
                timescales=(ExperienceTimescale.SHORT,),
                feature_families=("MARKET_WIDE_CONTEXT",),
                subject_ids=(SUBJECT,),
            ),
        ),
    )
    QuestionPredictionJournal(root).append(prediction, journaled_at_ns=T + 1)
    return prediction


def _grid_prices(prices):
    return tuple(
        _frame("REP-GRID-%02d" % index, price, cutoff=T + index * 5 * SECOND)
        for index, price in enumerate(prices, start=1)
    )


class FixedGridQuestionResolverTests(unittest.TestCase):
    def test_volatility_uses_all_preregistered_grid_returns(self):
        experience, micro, short = _baseline_experience()
        question = question_catalog_v1()[2]
        # Alternating +1%/-~1% produces non-zero realized volatility.
        path = _grid_prices((101, 100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, question, experience)
            outcome = resolve_fixed_grid_question(
                root,
                prediction.prediction_id,
                baseline_experience=experience,
                baseline_frames=(micro, short),
                forward_frames=tuple(reversed(path)),
                now_at_ns=T + 65 * SECOND,
            )
            self.assertEqual("RESOLVED", outcome.status)
            self.assertGreater(Decimal(outcome.realized_answer["value"]), Decimal("0"))
            self.assertEqual(13, len(outcome.resolution_evidence))
            self.assertEqual([frame.frame_id for frame in path], [ref.artifact_id for ref in outcome.resolution_evidence[1:]])

    def test_constant_grid_returns_have_zero_volatility(self):
        experience, micro, short = _baseline_experience()
        question = question_catalog_v1()[2]
        path = _grid_prices((100,) * 12)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, question, experience)
            outcome = resolve_fixed_grid_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(micro, short), forward_frames=path, now_at_ns=T + 65 * SECOND)
            self.assertEqual(Decimal("0"), Decimal(outcome.realized_answer["value"]))

    def test_fragility_is_maximum_adverse_excursion_from_cutoff(self):
        experience, micro, short = _baseline_experience()
        question = question_catalog_v1()[4]
        path = _grid_prices((99.5, 99, 98, 99, 100, 101, 102, 101, 100, 99, 100, 101))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, question, experience)
            outcome = resolve_fixed_grid_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(micro, short), forward_frames=path, now_at_ns=T + 65 * SECOND)
            self.assertEqual(Decimal("200"), Decimal(outcome.realized_answer["value"]))

    def test_exact_grid_beats_more_extreme_off_grid_frame(self):
        experience, micro, short = _baseline_experience()
        question = question_catalog_v1()[4]
        path = list(_grid_prices((100,) * 12))
        off_grid = _frame("REP-OFFGRID-DIP", "50", cutoff=T + 7 * SECOND)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, question, experience)
            outcome = resolve_fixed_grid_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(micro, short), forward_frames=tuple(path) + (off_grid,), now_at_ns=T + 65 * SECOND)
            self.assertEqual(Decimal("0"), Decimal(outcome.realized_answer["value"]))
            self.assertNotIn("REP-OFFGRID-DIP", [ref.artifact_id for ref in outcome.resolution_evidence])

    def test_missing_grid_point_is_pending_then_unresolvable(self):
        experience, micro, short = _baseline_experience()
        question = question_catalog_v1()[2]
        path = _grid_prices((100,) * 11)  # missing T+60s
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, question, experience)
            with self.assertRaisesRegex(QuestionOutcomePendingError, "remains open"):
                resolve_fixed_grid_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(micro, short), forward_frames=path, now_at_ns=T + 65 * SECOND)
            outcome = resolve_fixed_grid_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(micro, short), forward_frames=path, now_at_ns=T + 65 * SECOND + 1)
            self.assertEqual("UNRESOLVABLE", outcome.status)

    def test_wrong_instrument_cannot_fill_missing_grid_slot(self):
        experience, micro, short = _baseline_experience()
        question = question_catalog_v1()[2]
        path = list(_grid_prices((100,) * 11))
        path.append(_frame("REP-ETH-60", "100", cutoff=T + 60 * SECOND, instrument=_instrument("ETH")))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, question, experience)
            outcome = resolve_fixed_grid_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(micro, short), forward_frames=tuple(path), now_at_ns=T + 65 * SECOND + 1)
            self.assertEqual("UNRESOLVABLE", outcome.status)


if __name__ == "__main__":
    unittest.main()
