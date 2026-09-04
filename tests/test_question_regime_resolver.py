from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.context.contracts import MarketContextFrame
from autonomous_kernel.context.store import MarketContextStore
from autonomous_kernel.evaluation.regime_resolver import (
    REGIME_ENDPOINT_IMPLEMENTATION_REF,
    REGIME_PERSISTENCE_IMPLEMENTATION_REF,
    QuestionOutcomePendingError,
    resolve_market_regime_question,
    resolve_regime_persistence_question,
)
from autonomous_kernel.experience.contracts import ExperienceTimescale
from autonomous_kernel.experience.market_wide import MarketWideExperienceState
from autonomous_kernel.observation.instruments import CanonicalInstrument
from autonomous_kernel.prediction import PredictionArtifactRef, QuestionPredictionJournal, build_question_bound_prediction
from autonomous_kernel.questions.catalog import question_catalog_v1
from autonomous_kernel.questions.contracts import QuestionRegistryEntry, build_question_registry_snapshot
from autonomous_kernel.questions.readiness import build_resolver_ready_registry
from autonomous_kernel.representation.contracts import RepresentationFrame
from autonomous_kernel.representation.store import RepresentationStore


T = 1_788_400_000_000_000_000
SECOND = 1_000_000_000
MINUTE = 60 * SECOND
MARKET_SUBJECT = "MARKET.CRYPTO"
BASE_PARAMS = {
    "regime_thresholds": {
        "direction": "2",
        "breadth": "0.60",
        "high_vol": "15",
        "low_vol": "3",
        "stressed_spread": "5",
        "coherent_corr": "0.65",
        "fragmented_corr": "0.25",
        "basis": "2",
    },
    "minimum_core_instruments": 2,
    "weighting_authority": "REPRESENTATIONAL_ONLY_NO_CAPITAL_AUTHORITY",
}


def _instrument(symbol):
    return CanonicalInstrument(
        canonical_id="CRYPTO.SPOT.%s-USD" % symbol,
        asset_class="CRYPTO",
        market_type="SPOT",
        base_asset=symbol,
        quote_asset="USD",
        settlement_asset="USD",
    )


def _frame(symbol, suffix, cutoff):
    instrument = _instrument(symbol)
    digest = hashlib.sha256((symbol + suffix).encode("utf-8")).hexdigest()
    return RepresentationFrame(
        frame_id="REP-%s-%s" % (symbol, suffix),
        representation_type="INSTRUMENT_STATE",
        instrument=instrument,
        window_start_ns=cutoff - SECOND,
        cutoff_at_ns=cutoff,
        known_at_ns=cutoff,
        latest_source_event_at_ns=cutoff,
        status="QUALIFIED",
        builder_version="regime-source-v1",
        parameters={"depth_bands_bps": [10]},
        state={
            "venue_states": {},
            "aggregate": {"mean_venue_midpoint": "100"},
        },
        source_observation_ids=("OBS-%s-%s" % (symbol, suffix),),
        source_content_hashes=(digest,),
        source_providers=("TEST",),
        source_venues=("TEST",),
    )


def _context(context_id, cutoff, direction, *, members=("BTC", "ETH"), params=None):
    frames = tuple(_frame(symbol, context_id, cutoff) for symbol in members)
    ordered = tuple(sorted(frames, key=lambda frame: (frame.instrument.canonical_id, frame.cutoff_at_ns, frame.known_at_ns, frame.frame_id)))
    member_state = {
        frame.instrument.canonical_id: {
            "frame_id": frame.frame_id,
            "frame_content_hash": frame.content_hash(),
            "status": "QUALIFIED",
        }
        for frame in ordered
    }
    context = MarketContextFrame(
        context_id=context_id,
        context_type="MARKET_CONTEXT",
        cutoff_at_ns=cutoff,
        known_at_ns=cutoff,
        status="QUALIFIED",
        builder_version="market-context-v1",
        parameters=dict(BASE_PARAMS if params is None else params),
        state={
            "members": member_state,
            "market": {"member_instrument_count": len(member_state)},
            "regimes": {
                "direction": direction,
                "volatility": "NORMAL",
                "liquidity": "NORMAL",
                "correlation": "NORMAL",
                "derivatives": "UNAVAILABLE",
                "structure": "ORDERLY",
            },
            "feature_quality": {"CORE_MARKET": {"status": "QUALIFIED"}},
        },
        source_frame_ids=tuple(frame.frame_id for frame in ordered),
        source_frame_hashes=tuple(frame.content_hash() for frame in ordered),
        source_instrument_ids=tuple(frame.instrument.canonical_id for frame in ordered),
    )
    return context, ordered


def _persist_context(root, context, frames):
    representation_store = RepresentationStore(root)
    for frame in frames:
        representation_store.persist(
            frame,
            source_batches=(
                {
                    "batch_id": "BATCH-%s" % frame.frame_id,
                    "manifest_ref": "TEST",
                    "manifest_content_hash": "a" * 64,
                },
            ),
        )
    MarketContextStore(root).persist(context, source_frames=frames)


def _market_wide(baseline):
    return MarketWideExperienceState(
        market_wide_experience_id="MWEXP-REGIME-BASE",
        timescale=ExperienceTimescale.SESSION,
        window_start_ns=T - SECOND,
        cutoff_at_ns=T,
        known_at_ns=T,
        status="QUALIFIED",
        builder_version="market-wide-experience-v1",
        source_context_ids=(baseline.context_id,),
        source_context_hashes=(baseline.content_hash(),),
        state={
            "current": {
                "context_id": baseline.context_id,
                "context_hash": baseline.content_hash(),
                "regimes": dict(baseline.state["regimes"]),
            },
            "regime_history": {
                "direction": {
                    "current": baseline.state["regimes"]["direction"],
                    "transition_count": 0,
                    "observed_states": [baseline.state["regimes"]["direction"]],
                }
            },
        },
        parameters={"minimum_contexts": 1},
    )


def _question(question_id):
    return next(item for item in question_catalog_v1() if item.question_id == question_id)


def _implementation(question_id):
    return REGIME_ENDPOINT_IMPLEMENTATION_REF if question_id == "MARKET_DIRECTION_REGIME_15M" else REGIME_PERSISTENCE_IMPLEMENTATION_REF


def _registry(question):
    return build_question_registry_snapshot(
        registry_id="ZLJ-MARKET-QUESTIONS",
        version="regime-resolver-test",
        entries=(
            QuestionRegistryEntry(
                definition=question,
                lifecycle_state="RESOLVER_READY",
                registered_at_ns=T - 2 * SECOND,
                effective_at_ns=T - SECOND,
                resolver_implementation_ref=_implementation(question.question_id),
            ),
        ),
        known_at_ns=T - 2 * SECOND,
        effective_at_ns=T - SECOND,
    )


def _prediction(root, question, market_wide):
    answer = {"value": "RISK_ON"} if question.question_id == "MARKET_DIRECTION_REGIME_15M" else {"value": 1}
    timescales = (ExperienceTimescale.SESSION, ExperienceTimescale.MACRO_STRUCTURAL) if question.question_id == "MARKET_DIRECTION_REGIME_15M" else (ExperienceTimescale.SESSION,)
    prediction = build_question_bound_prediction(
        registry=_registry(question),
        question=question,
        subject_id=MARKET_SUBJECT,
        mode="PROSPECTIVE_SHADOW",
        evidence_class="FORWARD_EVALUABLE",
        cutoff_at_ns=T,
        created_at_ns=T,
        answer=answer,
        model_refs=("MODEL-REGIME-TEST",),
        artifact_refs=(
            PredictionArtifactRef(
                artifact_type="MARKET_WIDE_EXPERIENCE",
                artifact_id=market_wide.market_wide_experience_id,
                content_hash=market_wide.content_hash(),
                known_at_ns=market_wide.known_at_ns,
                status="QUALIFIED",
                timescales=timescales,
                feature_families=("MARKET_WIDE_CONTEXT", "MARKET_WIDE_TRAJECTORY"),
                subject_ids=(MARKET_SUBJECT,),
            ),
        ),
    )
    QuestionPredictionJournal(root).append(prediction, journaled_at_ns=T + 1)
    return prediction


class RegimeResolverTests(unittest.TestCase):
    def test_regime_endpoint_uses_first_durable_same_universe_same_contract_context(self):
        baseline, baseline_frames = _context("CTX-BASE", T, "RISK_ON")
        wrong_universe, wrong_frames = _context("CTX-WRONG-UNIVERSE", T + 15 * MINUTE, "RISK_ON", members=("BTC", "ETH", "SOL"))
        endpoint, endpoint_frames = _context("CTX-END", T + 15 * MINUTE + SECOND, "RISK_OFF")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for context, frames in ((baseline, baseline_frames), (wrong_universe, wrong_frames), (endpoint, endpoint_frames)):
                _persist_context(root, context, frames)
            market_wide = _market_wide(baseline)
            prediction = _prediction(root, _question("MARKET_DIRECTION_REGIME_15M"), market_wide)
            outcome = resolve_market_regime_question(root, prediction.prediction_id, baseline_market_wide=market_wide, now_at_ns=T + 15 * MINUTE + 2 * SECOND)
            self.assertEqual("RESOLVED", outcome.status)
            self.assertEqual({"value": "RISK_OFF"}, outcome.realized_answer)
            self.assertEqual("CTX-END", outcome.resolution_evidence[1].artifact_id)

    def test_regime_endpoint_contract_change_cannot_be_skipped_for_later_old_contract(self):
        baseline, baseline_frames = _context("CTX-BASE", T, "RISK_ON")
        changed_params = dict(BASE_PARAMS)
        changed_params["regime_thresholds"] = dict(BASE_PARAMS["regime_thresholds"], direction="99")
        changed, changed_frames = _context("CTX-CHANGED", T + 15 * MINUTE, "NEUTRAL", params=changed_params)
        later, later_frames = _context("CTX-LATER", T + 15 * MINUTE + 5 * SECOND, "RISK_ON")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for context, frames in ((baseline, baseline_frames), (changed, changed_frames), (later, later_frames)):
                _persist_context(root, context, frames)
            market_wide = _market_wide(baseline)
            prediction = _prediction(root, _question("MARKET_DIRECTION_REGIME_15M"), market_wide)
            outcome = resolve_market_regime_question(
                root,
                prediction.prediction_id,
                baseline_market_wide=market_wide,
                now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns + 1,
            )
            self.assertEqual("UNRESOLVABLE", outcome.status)

    def test_persistence_reads_all_durable_contexts_and_detects_intermediate_transition(self):
        baseline, baseline_frames = _context("CTX-BASE", T, "RISK_ON")
        middle, middle_frames = _context("CTX-MID", T + 2 * MINUTE, "RISK_OFF")
        endpoint, endpoint_frames = _context("CTX-END", T + 5 * MINUTE, "RISK_ON")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for context, frames in ((baseline, baseline_frames), (middle, middle_frames), (endpoint, endpoint_frames)):
                _persist_context(root, context, frames)
            market_wide = _market_wide(baseline)
            prediction = _prediction(root, _question("MARKET_REGIME_PERSISTENCE_5M"), market_wide)
            outcome = resolve_regime_persistence_question(root, prediction.prediction_id, baseline_market_wide=market_wide, now_at_ns=T + 5 * MINUTE + SECOND)
            self.assertEqual({"value": 0}, outcome.realized_answer)
            self.assertIn("CTX-MID", [item.artifact_id for item in outcome.resolution_evidence])

    def test_persistence_is_one_when_every_durable_same_contract_context_matches(self):
        baseline, baseline_frames = _context("CTX-BASE", T, "RISK_ON")
        middle, middle_frames = _context("CTX-MID", T + 2 * MINUTE, "RISK_ON")
        endpoint, endpoint_frames = _context("CTX-END", T + 5 * MINUTE, "RISK_ON")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for context, frames in ((baseline, baseline_frames), (middle, middle_frames), (endpoint, endpoint_frames)):
                _persist_context(root, context, frames)
            market_wide = _market_wide(baseline)
            prediction = _prediction(root, _question("MARKET_REGIME_PERSISTENCE_5M"), market_wide)
            outcome = resolve_regime_persistence_question(root, prediction.prediction_id, baseline_market_wide=market_wide, now_at_ns=T + 5 * MINUTE + SECOND)
            self.assertEqual({"value": 1}, outcome.realized_answer)

    def test_persistence_contract_change_in_interval_fails_closed(self):
        baseline, baseline_frames = _context("CTX-BASE", T, "RISK_ON")
        changed_params = dict(BASE_PARAMS)
        changed_params["minimum_core_instruments"] = 3
        changed, changed_frames = _context("CTX-CHANGED", T + 2 * MINUTE, "RISK_ON", params=changed_params)
        endpoint, endpoint_frames = _context("CTX-END", T + 5 * MINUTE, "RISK_ON")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for context, frames in ((baseline, baseline_frames), (changed, changed_frames), (endpoint, endpoint_frames)):
                _persist_context(root, context, frames)
            market_wide = _market_wide(baseline)
            prediction = _prediction(root, _question("MARKET_REGIME_PERSISTENCE_5M"), market_wide)
            outcome = resolve_regime_persistence_question(
                root,
                prediction.prediction_id,
                baseline_market_wide=market_wide,
                now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns + 1,
            )
            self.assertEqual("UNRESOLVABLE", outcome.status)

    def test_missing_terminal_context_is_pending_then_unresolvable(self):
        baseline, baseline_frames = _context("CTX-BASE", T, "RISK_ON")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _persist_context(root, baseline, baseline_frames)
            market_wide = _market_wide(baseline)
            prediction = _prediction(root, _question("MARKET_REGIME_PERSISTENCE_5M"), market_wide)
            with self.assertRaisesRegex(QuestionOutcomePendingError, "terminal context"):
                resolve_regime_persistence_question(
                    root,
                    prediction.prediction_id,
                    baseline_market_wide=market_wide,
                    now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns,
                )
            outcome = resolve_regime_persistence_question(
                root,
                prediction.prediction_id,
                baseline_market_wide=market_wide,
                now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns + 1,
            )
            self.assertEqual("UNRESOLVABLE", outcome.status)

    def test_registry_promotes_only_regime_resolvers_implemented(self):
        regime = _question("MARKET_DIRECTION_REGIME_15M")
        persistence = _question("MARKET_REGIME_PERSISTENCE_5M")
        base = build_question_registry_snapshot(
            registry_id="ZLJ-MARKET-QUESTIONS",
            version="before-regime",
            entries=(
                QuestionRegistryEntry(regime, "DEFINED", T - 3 * SECOND, T - 2 * SECOND),
                QuestionRegistryEntry(persistence, "DEFINED", T - 3 * SECOND, T - 2 * SECOND),
            ),
            known_at_ns=T - 3 * SECOND,
            effective_at_ns=T - 2 * SECOND,
        )
        promoted = build_resolver_ready_registry(
            base,
            version="after-regime",
            known_at_ns=T - SECOND,
            effective_at_ns=T,
            resolver_implementations={
                regime.question_ref: REGIME_ENDPOINT_IMPLEMENTATION_REF,
                persistence.question_ref: REGIME_PERSISTENCE_IMPLEMENTATION_REF,
            },
        )
        self.assertEqual({"RESOLVER_READY"}, {entry.lifecycle_state for entry in promoted.entries})


if __name__ == "__main__":
    unittest.main()
