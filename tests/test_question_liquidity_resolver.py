from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.evaluation.liquidity_resolver import (
    LIQUIDITY_RESOLVER_IMPLEMENTATION_REF,
    QuestionOutcomePendingError,
    QuestionResolverError,
    resolve_liquidity_question,
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
from autonomous_kernel.questions.readiness import build_resolver_ready_registry
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


def _book(bid, ask, bid_depth, ask_depth, *, include_10=True):
    bands = {}
    if include_10:
        bands["10"] = {
            "bid_quote_notional": str(bid_depth),
            "ask_quote_notional": str(ask_depth),
        }
    return {
        "status": "QUALIFIED",
        "best_bid": str(bid),
        "best_ask": str(ask),
        "depth_bands_bps": bands,
    }


def _frame(frame_id, *, cutoff, known=None, books=None, instrument=None):
    inst = instrument or _instrument()
    known_at = cutoff if known is None else known
    venue_books = books or {
        "COINBASE": _book("99.9", "100.1", "1000", "1000"),
        "KRAKEN": _book("99.8", "100.2", "1000", "1000"),
    }
    return RepresentationFrame(
        frame_id=frame_id,
        representation_type="INSTRUMENT_STATE",
        instrument=inst,
        window_start_ns=cutoff - 10 * SECOND,
        cutoff_at_ns=cutoff,
        known_at_ns=known_at,
        latest_source_event_at_ns=known_at,
        status="QUALIFIED",
        builder_version="liquidity-test-v1",
        parameters={"depth_bands_bps": [10]},
        state={
            "venue_states": {
                venue: {"book": book, "trade_flow": {}, "source_providers": ("TEST",)}
                for venue, book in venue_books.items()
            },
            "aggregate": {"mean_venue_midpoint": "100"},
        },
        source_observation_ids=("OBS-%s" % frame_id,),
        source_content_hashes=(hashlib.sha256(frame_id.encode("utf-8")).hexdigest(),),
        source_providers=("TEST",),
        source_venues=tuple(sorted(venue_books)),
    )


def _experience(frame):
    source = ExperienceSourceFrame(
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
    view = ExperienceView(
        timescale=ExperienceTimescale.MICRO,
        lookback_ns=10 * SECOND,
        window_start_ns=T - 10 * SECOND,
        cutoff_at_ns=T,
        status="QUALIFIED",
        source_frames=(source,),
        feature_family_status={"SPOT_MICROSTRUCTURE": "QUALIFIED"},
    )
    return MarketExperienceFrame(
        experience_id="EXP-BTC-LIQUIDITY",
        economic_root_id=SUBJECT,
        cutoff_at_ns=T,
        known_at_ns=T - 1,
        status="QUALIFIED",
        builder_version="liquidity-experience-v1",
        graph_id="GRAPH-BTC",
        graph_version="1",
        graph_hash="1" * 64,
        context_id="CTX-BTC",
        context_hash="2" * 64,
        context_status="QUALIFIED",
        views=(view,),
    )


def _question():
    return question_catalog_v1()[3]


def _registry(question):
    return build_question_registry_snapshot(
        registry_id="ZLJ-MARKET-QUESTIONS",
        version="liquidity-resolver-test",
        entries=(
            QuestionRegistryEntry(
                definition=question,
                lifecycle_state="RESOLVER_READY",
                registered_at_ns=T - 2 * SECOND,
                effective_at_ns=T - SECOND,
                resolver_implementation_ref=LIQUIDITY_RESOLVER_IMPLEMENTATION_REF,
            ),
        ),
        known_at_ns=T - 2 * SECOND,
        effective_at_ns=T - SECOND,
    )


def _prediction(root, experience):
    question = _question()
    prediction = build_question_bound_prediction(
        registry=_registry(question),
        question=question,
        subject_id=SUBJECT,
        mode="PROSPECTIVE_SHADOW",
        evidence_class="FORWARD_EVALUABLE",
        cutoff_at_ns=T,
        created_at_ns=T,
        answer={"value": 1, "probability_1": "0.6"},
        model_refs=("MODEL-LIQUIDITY-TEST",),
        artifact_refs=(
            PredictionArtifactRef(
                artifact_type="MARKET_EXPERIENCE",
                artifact_id=experience.experience_id,
                content_hash=experience.content_hash(),
                known_at_ns=experience.known_at_ns,
                status="QUALIFIED",
                timescales=(ExperienceTimescale.MICRO,),
                feature_families=("SPOT_MICROSTRUCTURE",),
                subject_ids=(SUBJECT,),
            ),
        ),
    )
    QuestionPredictionJournal(root).append(prediction, journaled_at_ns=T + 1)
    return prediction


class LiquidityResolverTests(unittest.TestCase):
    def test_spread_wider_and_quote_depth_lower_resolves_deterioration(self):
        baseline = _frame("REP-BASE", cutoff=T, known=T - 1)
        experience = _experience(baseline)
        forward = _frame(
            "REP-FWD",
            cutoff=T + 30 * SECOND,
            books={
                "COINBASE": _book("99.7", "100.3", "500", "500"),
                "KRAKEN": _book("99.6", "100.4", "500", "500"),
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, experience)
            outcome = resolve_liquidity_question(
                root,
                prediction.prediction_id,
                baseline_experience=experience,
                baseline_frames=(baseline,),
                forward_frames=(forward,),
                now_at_ns=T + 31 * SECOND,
            )
            self.assertEqual("RESOLVED", outcome.status)
            self.assertEqual({"value": 1}, outcome.realized_answer)
            self.assertEqual("REP-FWD", outcome.resolution_evidence[1].artifact_id)

    def test_spread_wider_without_depth_fall_is_not_deterioration(self):
        baseline = _frame("REP-BASE", cutoff=T, known=T - 1)
        experience = _experience(baseline)
        forward = _frame(
            "REP-FWD",
            cutoff=T + 30 * SECOND,
            books={
                "COINBASE": _book("99.7", "100.3", "1500", "1500"),
                "KRAKEN": _book("99.6", "100.4", "1500", "1500"),
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, experience)
            outcome = resolve_liquidity_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(baseline,), forward_frames=(forward,), now_at_ns=T + 31 * SECOND)
            self.assertEqual({"value": 0}, outcome.realized_answer)

    def test_extra_future_venue_cannot_improve_frozen_baseline_venue_measurement(self):
        baseline = _frame("REP-BASE", cutoff=T, known=T - 1)
        experience = _experience(baseline)
        forward = _frame(
            "REP-FWD",
            cutoff=T + 30 * SECOND,
            books={
                "COINBASE": _book("99.7", "100.3", "500", "500"),
                "KRAKEN": _book("99.6", "100.4", "500", "500"),
                "NEWVENUE": _book("99.99", "100.01", "1000000", "1000000"),
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, experience)
            outcome = resolve_liquidity_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(baseline,), forward_frames=(forward,), now_at_ns=T + 31 * SECOND)
            self.assertEqual({"value": 1}, outcome.realized_answer)

    def test_first_incomplete_future_book_is_skipped_for_first_eligible_same_venue_state(self):
        baseline = _frame("REP-BASE", cutoff=T, known=T - 1)
        experience = _experience(baseline)
        incomplete = _frame(
            "REP-INCOMPLETE",
            cutoff=T + 30 * SECOND,
            known=T + 30 * SECOND,
            books={"COINBASE": _book("99.7", "100.3", "500", "500")},
        )
        complete = _frame(
            "REP-COMPLETE",
            cutoff=T + 31 * SECOND,
            known=T + 31 * SECOND,
            books={
                "COINBASE": _book("99.7", "100.3", "500", "500"),
                "KRAKEN": _book("99.6", "100.4", "500", "500"),
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, experience)
            outcome = resolve_liquidity_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(baseline,), forward_frames=(incomplete, complete), now_at_ns=T + 32 * SECOND)
            self.assertEqual("REP-COMPLETE", outcome.resolution_evidence[1].artifact_id)

    def test_wrong_instrument_is_never_used_as_liquidity_endpoint(self):
        baseline = _frame("REP-BASE", cutoff=T, known=T - 1)
        experience = _experience(baseline)
        wrong = _frame("REP-USDT", cutoff=T + 30 * SECOND, instrument=_instrument("USDT"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, experience)
            with self.assertRaisesRegex(QuestionOutcomePendingError, "window remains open"):
                resolve_liquidity_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(baseline,), forward_frames=(wrong,), now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns)
            outcome = resolve_liquidity_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(baseline,), forward_frames=(wrong,), now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns + 1)
            self.assertEqual("UNRESOLVABLE", outcome.status)

    def test_missing_10_bps_baseline_depth_fails_closed(self):
        baseline = _frame(
            "REP-BASE",
            cutoff=T,
            known=T - 1,
            books={"COINBASE": _book("99.9", "100.1", "1000", "1000", include_10=False)},
        )
        experience = _experience(baseline)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, experience)
            with self.assertRaisesRegex(QuestionResolverError, "10-bps"):
                resolve_liquidity_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(baseline,), forward_frames=(), now_at_ns=T + 40 * SECOND)

    def test_depth_fall_without_spread_widening_is_not_deterioration(self):
        baseline = _frame("REP-BASE", cutoff=T, known=T - 1)
        experience = _experience(baseline)
        forward = _frame(
            "REP-FWD",
            cutoff=T + 30 * SECOND,
            books={
                "COINBASE": _book("99.9", "100.1", "400", "400"),
                "KRAKEN": _book("99.8", "100.2", "400", "400"),
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, experience)
            outcome = resolve_liquidity_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(baseline,), forward_frames=(forward,), now_at_ns=T + 31 * SECOND)
            self.assertEqual({"value": 0}, outcome.realized_answer)

    def test_spread_improves_while_depth_falls_is_not_deterioration(self):
        baseline = _frame("REP-BASE", cutoff=T, known=T - 1)
        experience = _experience(baseline)
        forward = _frame(
            "REP-FWD",
            cutoff=T + 30 * SECOND,
            books={
                "COINBASE": _book("99.95", "100.05", "400", "400"),
                "KRAKEN": _book("99.94", "100.06", "400", "400"),
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, experience)
            outcome = resolve_liquidity_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(baseline,), forward_frames=(forward,), now_at_ns=T + 31 * SECOND)
            self.assertEqual({"value": 0}, outcome.realized_answer)

    def test_quote_notional_not_base_units_controls_depth_truth(self):
        baseline = _frame("REP-BASE", cutoff=T, known=T - 1)
        experience = _experience(baseline)
        forward = _frame(
            "REP-FWD",
            cutoff=T + 30 * SECOND,
            books={
                "COINBASE": {
                    **_book("99.7", "100.3", "1500", "1500"),
                    "depth_bands_bps": {
                        "10": {
                            "bid_quote_notional": "1500",
                            "ask_quote_notional": "1500",
                            "bid_base": "1",
                            "ask_base": "1",
                        }
                    },
                },
                "KRAKEN": {
                    **_book("99.6", "100.4", "1500", "1500"),
                    "depth_bands_bps": {
                        "10": {
                            "bid_quote_notional": "1500",
                            "ask_quote_notional": "1500",
                            "bid_base": "1",
                            "ask_base": "1",
                        }
                    },
                },
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, experience)
            outcome = resolve_liquidity_question(root, prediction.prediction_id, baseline_experience=experience, baseline_frames=(baseline,), forward_frames=(forward,), now_at_ns=T + 31 * SECOND)
            self.assertEqual({"value": 0}, outcome.realized_answer)

        question = _question()
        base = build_question_registry_snapshot(
            registry_id="ZLJ-MARKET-QUESTIONS",
            version="before-liquidity",
            entries=(QuestionRegistryEntry(question, "DEFINED", T - 3 * SECOND, T - 2 * SECOND),),
            known_at_ns=T - 3 * SECOND,
            effective_at_ns=T - 2 * SECOND,
        )
        promoted = build_resolver_ready_registry(
            base,
            version="after-liquidity",
            known_at_ns=T - SECOND,
            effective_at_ns=T,
            resolver_implementations={question.question_ref: LIQUIDITY_RESOLVER_IMPLEMENTATION_REF},
        )
        self.assertEqual("RESOLVER_READY", promoted.entries[0].lifecycle_state)
        self.assertEqual(LIQUIDITY_RESOLVER_IMPLEMENTATION_REF, promoted.entries[0].resolver_implementation_ref)


if __name__ == "__main__":
    unittest.main()
