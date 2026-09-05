from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from autonomous_kernel.evaluation.question_journal import QuestionOutcomeJournal
from autonomous_kernel.evaluation.question_outcome import QuestionBoundOutcome
from autonomous_kernel.evaluation.question_resolvers import QuestionResolverError, resolve_midpoint_question
from autonomous_kernel.experts.adapters import question_prediction_to_expert_claim, implemented_baseline_expert_contracts
from autonomous_kernel.experts.sync import sync_expert_learning
from autonomous_kernel.intelligence.runtime import IntelligenceRuntime
from autonomous_kernel.experience.store import MarketExperienceStore
from autonomous_kernel.learning.direction_loop import (
    DIRECTION_QUESTION_REF,
    DirectionLoopError,
    FORBIDDEN_FIELDS,
    HORIZON_NS,
    INSTRUMENT_ID,
    materialize_cutoff_frame,
    process_canonical_direction_batch,
    question_learning_projection,
    resolve_direction_prediction,
)
from autonomous_kernel.observation import CanonicalBatchStore, CanonicalObservation, default_instrument_registry
from autonomous_kernel.prediction.question_bound import QuestionBoundPrediction
from autonomous_kernel.prediction.question_journal import QuestionPredictionJournal
from autonomous_kernel.representation.contracts import RepresentationFrame
from autonomous_kernel.representation.materialize import materialize_instrument_state


T = 1_800_000_000_000_000_000
SECOND = 1_000_000_000
VALID = {"status": "VALID", "action_permitted": True}


def _obs(observation_id, *, known_ns, event_type="BOOK_SNAPSHOT", symbol="BTC-USD", bid="100.00", ask="100.20", payload=None, sequence="1"):
    instrument = default_instrument_registry().resolve("coinbase_advanced_trade_public_websocket", symbol)
    if payload is None:
        if event_type == "TRADE":
            payload = {"price": bid, "size": "0.01", "side": "BUY", "trade_id": observation_id}
        else:
            payload = {"updates": [{"side": "BID", "price": bid, "size": "2"}, {"side": "ASK", "price": ask, "size": "2"}]}
    digest = hashlib.sha256(observation_id.encode("utf-8")).hexdigest()
    return CanonicalObservation(
        observation_id=observation_id,
        instrument=instrument,
        event_type=event_type,
        provider="coinbase_advanced_trade_public_websocket",
        venue="COINBASE",
        provider_symbol=symbol,
        channel="level2" if event_type.startswith("BOOK_") else "trades",
        source_event_at_ns=known_ns - 1,
        received_at_ns=known_ns,
        known_at_ns=known_ns,
        sequence=str(sequence),
        sequence_scope="PROVIDER_EVENT",
        stream_id="STREAM-DIRECTION-TEST",
        payload=payload,
        quality=VALID,
        raw_event_sha256=digest,
        raw_ref="raw/%s" % observation_id,
    )


def _write_span(root: Path, *, seconds: int, start_ns: int = T, batch_id: str = "CAN-DIRECTION-TEST", price_start: float = 100.0) -> str:
    observations = []
    for index in range(seconds + 1):
        known = start_ns + index * SECOND
        mid = price_start + (0.05 * index)
        bid = "%.2f" % (mid - 0.10)
        ask = "%.2f" % (mid + 0.10)
        observations.append(
            _obs("OBS-BOOK-%d" % index, known_ns=known, bid=bid, ask=ask, sequence=str(index + 1))
        )
        if index % 5 == 0:
            observations.append(
                _obs(
                    "OBS-TRADE-%d" % index,
                    known_ns=known + 1,
                    event_type="TRADE",
                    bid="%.2f" % mid,
                    sequence=str(index + 1000),
                )
            )
    CanonicalBatchStore(root).persist_batch(
        batch_id=batch_id,
        observations=tuple(observations),
        source_ref="tests/direction-competence",
        source_sha256="a" * 64,
    )
    return batch_id


def _walk_forbidden(value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            for token in FORBIDDEN_FIELDS:
                if token in lowered:
                    raise AssertionError("forbidden field %s" % key)
            _walk_forbidden(item)
    elif isinstance(value, list):
        for item in value:
            _walk_forbidden(item)


class DirectionCompetenceLoopTests(unittest.TestCase):
    def test_future_z2_and_z9_cannot_enter_a_prediction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=55)
            result = process_canonical_direction_batch(root, batch_id)
            self.assertGreaterEqual(result["counts"]["predicted"], 3)
            for item in result["predictions"]:
                self.assertLessEqual(int(item["journaled_at_ns"]), int(item["resolves_at_ns"]))
                self.assertLess(int(item["cutoff_at_ns"]), int(item["resolves_at_ns"]))
                self.assertEqual(item["mode"], "HISTORICAL_REPLAY")
            first = result["predictions"][0]
            cutoff = int(first["cutoff_at_ns"])
            sealed = materialize_cutoff_frame(root, batch_id, cutoff)
            later = materialize_instrument_state(
                root,
                batch_ids=(batch_id,),
                instrument_id=INSTRUMENT_ID,
                cutoff_at_ns=cutoff + HORIZON_NS,
            )
            later_frame = RepresentationFrame.from_wire(later["frame"])
            self.assertNotEqual(sealed.content_hash(), later_frame.content_hash())
            self.assertLessEqual(sealed.known_at_ns, cutoff)
            prediction = QuestionBoundPrediction.from_wire(
                next(
                    entry["prediction"]
                    for entry in QuestionPredictionJournal(root).entries()
                    if entry["prediction"]["prediction_id"] == first["prediction_id"]
                )
            )
            self.assertTrue(all(ref.known_at_ns <= prediction.cutoff_at_ns for ref in prediction.artifact_refs))

    def test_outcome_cannot_resolve_before_horizon_maturity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=55)
            recorded = process_canonical_direction_batch(root, batch_id, sync=False)
            item = recorded["predictions"][0]
            early = resolve_direction_prediction(
                root,
                batch_id=batch_id,
                prediction_id=item["prediction_id"],
                baseline_frame=materialize_cutoff_frame(root, batch_id, int(item["cutoff_at_ns"])),
                experience=MarketExperienceStore(root).load(item["experience_id"]),
                now_at_ns=int(item["resolves_at_ns"]) - 1,
            )
            self.assertEqual(early["status"], "PENDING")

    def test_wrong_instrument_future_evidence_is_rejected_and_missing_evidence_is_unresolvable(self):
        from tests.test_context_materializer import frame as context_frame

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=55)
            recorded = process_canonical_direction_batch(root, batch_id, sync=False)
            item = recorded["predictions"][0]
            prediction = QuestionBoundPrediction.from_wire(
                next(entry["prediction"] for entry in QuestionPredictionJournal(root).entries() if entry["prediction"]["prediction_id"] == item["prediction_id"])
            )
            from dataclasses import replace
            from tests.test_context_materializer import frame as context_frame

            experience = MarketExperienceStore(root).load(item["experience_id"])
            baseline = materialize_cutoff_frame(root, batch_id, prediction.cutoff_at_ns)
            eth = default_instrument_registry().resolve("coinbase_advanced_trade_public_websocket", "ETH-USD")
            foreign = replace(
                context_frame(eth, 99, "3000"),
                known_at_ns=prediction.resolves_at_ns + 1,
                cutoff_at_ns=prediction.resolves_at_ns + 1,
            )
            outcome = resolve_midpoint_question(
                root,
                prediction.prediction_id,
                baseline_experience=experience,
                baseline_frames=(baseline,),
                forward_frames=(foreign,),
                now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns + 2,
            )
            self.assertEqual(outcome.status, "UNRESOLVABLE")
            missing = resolve_midpoint_question(
                root,
                prediction.prediction_id,
                baseline_experience=experience,
                baseline_frames=(baseline,),
                forward_frames=(),
                now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns + 2,
            )
            self.assertEqual(missing.status, "UNRESOLVABLE")
            self.assertIsNone(missing.realized_answer)

    def test_detached_prediction_and_claim_cannot_earn_score_without_journals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=55)
            result = process_canonical_direction_batch(root, batch_id)
            self.assertGreater(result["counts"]["resolved"], 0)
            claim_contract = next(item for item in implemented_baseline_expert_contracts() if "DIRECTION" in item["expert_id"])
            prediction = QuestionBoundPrediction.from_wire(QuestionPredictionJournal(root).entries()[0]["prediction"])
            claim = question_prediction_to_expert_claim(claim_contract, prediction)
            self.assertNotIn("realized_answer", claim)
            self.assertFalse(claim["authority"]["capital_decision"])
            empty = Path(directory) / "empty"
            empty.mkdir()
            sync = sync_expert_learning(empty, known_at_ns=prediction.resolves_at_ns + 10 * SECOND)
            self.assertEqual(sync["scores_recorded"], 0)
            self.assertEqual(sync["claims_recorded"], 0)

    def test_competence_known_at_cutoff_replay_and_idempotence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=55)
            first = process_canonical_direction_batch(root, batch_id)
            self.assertGreater(first["sync"]["scores_recorded"], 0)
            competence = first["sync"]["competence"]
            early = sync_expert_learning(root, known_at_ns=first["predictions"][0]["cutoff_at_ns"])
            self.assertEqual(early["scores_recorded"], 0)
            replay = sync_expert_learning(root, known_at_ns=int(first["observation_end_ns"]))
            self.assertEqual(replay["scores_recorded"], 0)
            self.assertEqual(replay["claims_recorded"], 0)
            rebuilt = IntelligenceRuntime(root).state()["competence"]
            self.assertEqual(rebuilt["integrity"]["content_hash"], competence["integrity"]["content_hash"])
            QuestionPredictionJournal(root).rebuild_state()
            QuestionOutcomeJournal(root).rebuild_state()
            again = process_canonical_direction_batch(root, batch_id)
            self.assertEqual(again["counts"]["predicted"], first["counts"]["predicted"])
            self.assertEqual(again["sync"]["scores_recorded"], 0)
            projection = question_learning_projection(root)
            self.assertEqual(projection["prediction_count"], first["counts"]["predicted"])
            self.assertFalse(projection["authority"]["adaptive_assembly_earned"])
            self.assertEqual("INSUFFICIENT_CONTEXTUAL_SUPPORT", projection["contextual_competence_status"])
            _walk_forbidden(first["predictions"])
            _walk_forbidden(first["outcomes"])
            _walk_forbidden(projection["competence"])
            _walk_forbidden(competence)
            self.assertEqual(DIRECTION_QUESTION_REF, first["question_ref"])
            self.assertEqual(HORIZON_NS, first["horizon_ns"])
            for outcome in first["outcomes"]:
                if outcome.get("status") != "RESOLVED":
                    continue
                matching = next(item for item in first["predictions"] if item["prediction_id"] == outcome["prediction_id"])
                self.assertGreater(int(outcome["decided_at_ns"]), int(matching["cutoff_at_ns"]))
                self.assertGreaterEqual(int(outcome["decided_at_ns"]), int(matching["resolves_at_ns"]))

    def test_no_forward_frame_after_window_close_journals_unresolvable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=55)
            with patch("autonomous_kernel.learning.direction_loop.materialize_forward_frame", return_value=None):
                result = process_canonical_direction_batch(root, batch_id)
            self.assertGreater(result["counts"]["unresolvable"], 0)
            self.assertEqual(result["counts"]["resolved"], 0)
            self.assertEqual(result["counts"]["pending"], 0)
            outcomes = [QuestionBoundOutcome.from_wire(entry["outcome"]) for entry in QuestionOutcomeJournal(root).entries()]
            self.assertTrue(all(item.status == "UNRESOLVABLE" for item in outcomes))
            self.assertTrue(all(item.realized_answer is None for item in outcomes))
            self.assertTrue(all(item.prediction_id for item in outcomes))
            self.assertEqual(len(outcomes), result["counts"]["unresolvable"])
            self.assertEqual(result["sync"]["unresolvable_predictions"], result["counts"]["unresolvable"])
            self.assertEqual(result["sync"]["journal_outcome_count"], result["counts"]["unresolvable"])
            self.assertEqual(result["sync"]["awaiting_outcome_predictions"], 0)
            replay_sync = sync_expert_learning(root, known_at_ns=int(result["observation_end_ns"]))
            self.assertEqual(len(QuestionOutcomeJournal(root).entries()), result["counts"]["unresolvable"])
            self.assertEqual(replay_sync["scores_recorded"], 0)
            self.assertEqual(replay_sync["unresolvable_predictions"], result["counts"]["unresolvable"])

    def test_unusable_forward_after_window_close_journals_unresolvable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=55)

            def unusable_forward(loop_root, loop_batch, resolves_at_ns):
                del loop_root, loop_batch
                return replace(
                    materialize_cutoff_frame(root, batch_id, int(resolves_at_ns) - HORIZON_NS),
                    frame_id="REP-UNUSABLE-FORWARD",
                    status="UNAVAILABLE",
                    known_at_ns=int(resolves_at_ns) + 1,
                    cutoff_at_ns=int(resolves_at_ns) + 1,
                )

            with patch("autonomous_kernel.learning.direction_loop.materialize_forward_frame", side_effect=unusable_forward):
                result = process_canonical_direction_batch(root, batch_id)
            self.assertEqual(result["counts"]["unresolvable"], result["counts"]["predicted"])
            self.assertEqual(result["sync"]["unresolvable_predictions"], result["counts"]["unresolvable"])
            self.assertTrue(all(entry["outcome"]["status"] == "UNRESOLVABLE" for entry in QuestionOutcomeJournal(root).entries()))

    def test_integrity_and_lineage_errors_are_not_journaled_as_market_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=55)
            recorded = process_canonical_direction_batch(root, batch_id, sync=False)
            item = recorded["predictions"][0]
            prediction = QuestionBoundPrediction.from_wire(
                next(entry["prediction"] for entry in QuestionPredictionJournal(root).entries() if entry["prediction"]["prediction_id"] == item["prediction_id"])
            )
            experience = MarketExperienceStore(root).load(item["experience_id"])
            baseline = materialize_cutoff_frame(root, batch_id, prediction.cutoff_at_ns)
            before = len(QuestionOutcomeJournal(root).entries())
            changed = replace(experience, builder_version="tampered-builder")
            with self.assertRaises(QuestionResolverError):
                resolve_midpoint_question(
                    root,
                    prediction.prediction_id,
                    baseline_experience=changed,
                    baseline_frames=(baseline,),
                    forward_frames=(),
                    now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns + 2,
                )
            eth = default_instrument_registry().resolve("coinbase_advanced_trade_public_websocket", "ETH-USD")
            foreign_baseline = replace(baseline, instrument=eth, frame_id="REP-WRONG-INSTRUMENT")
            with self.assertRaises(QuestionResolverError):
                resolve_midpoint_question(
                    root,
                    prediction.prediction_id,
                    baseline_experience=experience,
                    baseline_frames=(foreign_baseline,),
                    forward_frames=(),
                    now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns + 2,
                )
            hashed = replace(baseline, builder_version="lineage-break")
            with self.assertRaises(QuestionResolverError):
                resolve_midpoint_question(
                    root,
                    prediction.prediction_id,
                    baseline_experience=experience,
                    baseline_frames=(hashed,),
                    forward_frames=(),
                    now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns + 2,
                )
            with self.assertRaises(DirectionLoopError):
                resolve_direction_prediction(
                    root,
                    batch_id=batch_id,
                    prediction_id=item["prediction_id"],
                    baseline_frame=hashed,
                    experience=experience,
                    now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns + 2,
                )
            self.assertEqual(len(QuestionOutcomeJournal(root).entries()), before)

    def test_runtime_counts_match_journal_and_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=55)
            result = process_canonical_direction_batch(root, batch_id)
            journal_outcomes = QuestionOutcomeJournal(root).entries()
            self.assertEqual(result["counts"]["predicted"], len(QuestionPredictionJournal(root).entries()))
            self.assertEqual(result["counts"]["resolved"] + result["counts"]["unresolvable"], len(journal_outcomes))
            self.assertEqual(result["counts"]["unresolvable"], result["sync"]["unresolvable_predictions"])
            self.assertEqual(result["sync"]["journal_outcome_count"], len(journal_outcomes))
            self.assertEqual(result["sync"]["journal_prediction_count"], result["counts"]["predicted"])
            self.assertEqual(result["sync"]["awaiting_outcome_predictions"], result["counts"]["pending"])


if __name__ == "__main__":
    unittest.main()
