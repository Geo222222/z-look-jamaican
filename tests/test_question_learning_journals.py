from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from autonomous_kernel.book_bridge import ZLJBookSigner
from autonomous_kernel.book_outbox import BookOutbox
from autonomous_kernel.evaluation import (
    QuestionBoundOutcome,
    QuestionOutcomeJournal,
    QuestionOutcomeJournalError,
    ResolutionEvidenceRef,
    build_question_outcome_id,
    validate_question_outcome_journal,
)
from autonomous_kernel.experience.contracts import ExperienceTimescale
from autonomous_kernel.prediction import (
    PredictionArtifactRef,
    QuestionPredictionJournal,
    build_question_bound_prediction,
)
from autonomous_kernel.questions import (
    QuestionRegistryEntry,
    build_learning_journal_commitment,
    build_question_registry_snapshot,
    question_catalog_v1,
)


T = 1_788_400_000_000_000_000
SECOND = 1_000_000_000


def _prediction():
    question = question_catalog_v1()[0]
    registry = build_question_registry_snapshot(
        registry_id="ZLJ-MARKET-QUESTIONS",
        version="1.1-test",
        entries=(
            QuestionRegistryEntry(
                definition=question,
                lifecycle_state="RESOLVER_READY",
                registered_at_ns=T - 2 * SECOND,
                effective_at_ns=T - SECOND,
                resolver_implementation_ref="direction-resolver-v1",
            ),
        ),
        known_at_ns=T - 2 * SECOND,
        effective_at_ns=T - SECOND,
    )
    return build_question_bound_prediction(
        registry=registry,
        question=question,
        mode="PROSPECTIVE_SHADOW",
        evidence_class="FORWARD_EVALUABLE",
        cutoff_at_ns=T,
        created_at_ns=T,
        answer={"value": 1, "probability_1": "0.55"},
        model_refs=("MODEL-TEST-1",),
        artifact_refs=(
            PredictionArtifactRef(
                artifact_type="MARKET_EXPERIENCE",
                artifact_id="EXP-T",
                content_hash="a" * 64,
                known_at_ns=T - 1,
                status="QUALIFIED",
                timescales=(ExperienceTimescale.MICRO,),
                feature_families=("SPOT_MICROSTRUCTURE",),
            ),
        ),
    )


def _outcome(prediction, prediction_entry):
    return QuestionBoundOutcome(
        outcome_id=build_question_outcome_id(
            prediction.prediction_id,
            prediction.resolver_policy_id,
            "direction-resolver-v1",
        ),
        prediction_id=prediction.prediction_id,
        prediction_content_hash=prediction.content_hash(),
        prediction_journal_entry_hash=str(prediction_entry["entry_hash"]),
        question_ref=prediction.question_ref,
        question_definition_hash=prediction.question_definition_hash,
        question_registry_hash=prediction.question_registry_hash,
        answer_kind=prediction.answer_kind,
        outcome_metric_id=prediction.outcome_metric_id,
        resolver_policy_id=prediction.resolver_policy_id,
        resolver_implementation_ref="direction-resolver-v1",
        status="RESOLVED",
        cutoff_at_ns=prediction.cutoff_at_ns,
        target_resolves_at_ns=prediction.resolves_at_ns,
        max_resolution_lag_ns=prediction.max_resolution_lag_ns,
        decided_at_ns=prediction.resolves_at_ns,
        realized_answer={"value": 1},
        resolution_evidence=(
            ResolutionEvidenceRef(
                evidence_family="SPOT_MICROSTRUCTURE",
                artifact_type="REPRESENTATION_FRAME",
                artifact_id="REP-RESOLUTION",
                content_hash="b" * 64,
                known_at_ns=prediction.resolves_at_ns,
                role="FORWARD",
            ),
        ),
    )


class QuestionLearningJournalTests(unittest.TestCase):
    def test_outcome_journal_requires_exact_prediction_lineage_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction()
            prediction_entry = QuestionPredictionJournal(root).append(prediction, journaled_at_ns=T + 1)
            outcome = _outcome(prediction, prediction_entry)
            journal = QuestionOutcomeJournal(root)
            first = journal.append(outcome)
            replay = journal.append(outcome)
            self.assertEqual(first["entry_hash"], replay["entry_hash"])
            self.assertEqual(1, len(journal.entries()))
            self.assertEqual([], validate_question_outcome_journal(root))

    def test_outcome_journal_rejects_conflicting_final_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction()
            prediction_entry = QuestionPredictionJournal(root).append(prediction, journaled_at_ns=T + 1)
            outcome = _outcome(prediction, prediction_entry)
            journal = QuestionOutcomeJournal(root)
            journal.append(outcome)
            wire = outcome.to_wire()
            wire["realized_answer"]["value"] = 0
            wire["integrity"]["content_hash"] = "0" * 64
            # Rebuilding a conflicting valid outcome is not possible without a
            # different content hash; the journal must never accept a second
            # final truth for the same prediction.
            conflict = QuestionBoundOutcome(
                outcome_id=outcome.outcome_id,
                prediction_id=outcome.prediction_id,
                prediction_content_hash=outcome.prediction_content_hash,
                prediction_journal_entry_hash=outcome.prediction_journal_entry_hash,
                question_ref=outcome.question_ref,
                question_definition_hash=outcome.question_definition_hash,
                question_registry_hash=outcome.question_registry_hash,
                answer_kind=outcome.answer_kind,
                outcome_metric_id=outcome.outcome_metric_id,
                resolver_policy_id=outcome.resolver_policy_id,
                resolver_implementation_ref=outcome.resolver_implementation_ref,
                status="RESOLVED",
                cutoff_at_ns=outcome.cutoff_at_ns,
                target_resolves_at_ns=outcome.target_resolves_at_ns,
                max_resolution_lag_ns=outcome.max_resolution_lag_ns,
                decided_at_ns=outcome.decided_at_ns,
                realized_answer={"value": 0},
                resolution_evidence=outcome.resolution_evidence,
            )
            with self.assertRaisesRegex(QuestionOutcomeJournalError, "different final"):
                journal.append(conflict)

    def test_prediction_and_outcome_journal_tamper_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction()
            prediction_journal = QuestionPredictionJournal(root)
            prediction_entry = prediction_journal.append(prediction, journaled_at_ns=T + 1)
            outcome_journal = QuestionOutcomeJournal(root)
            outcome_journal.append(_outcome(prediction, prediction_entry))
            lines = outcome_journal.path.read_text(encoding="utf-8").splitlines()
            record = json.loads(lines[0])
            record["outcome"]["status"] = "UNRESOLVABLE"
            outcome_journal.path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            errors = validate_question_outcome_journal(root)
            self.assertTrue(any("hash mismatch" in error for error in errors))

    def test_learning_journal_commitments_are_compact_and_book_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction()
            prediction_journal = QuestionPredictionJournal(root)
            prediction_entry = prediction_journal.append(prediction, journaled_at_ns=T + 1)
            outcome_journal = QuestionOutcomeJournal(root)
            outcome_journal.append(_outcome(prediction, prediction_entry))

            prediction_commitment = build_learning_journal_commitment(
                journal_name="ZLJ.QUESTION_PREDICTIONS.v1",
                records=prediction_journal.entries(),
            )
            outcome_commitment = build_learning_journal_commitment(
                journal_name="ZLJ.QUESTION_OUTCOMES.v1",
                records=outcome_journal.entries(),
            )
            self.assertEqual(1, prediction_commitment.event_count)
            self.assertEqual(1, outcome_commitment.event_count)
            self.assertNotIn("artifact_refs", prediction_commitment.material_evidence().payload.decode("utf-8"))
            self.assertNotIn("realized_answer", outcome_commitment.material_evidence().payload.decode("utf-8"))

            signer = ZLJBookSigner(key_id="learning-test", private_key=Ed25519PrivateKey.generate())
            intent = outcome_commitment.material_evidence(
                payload_ref="zlj://learning-journal/question-outcomes/0-0"
            )
            produced_at = datetime.fromtimestamp((intent.known_at_ns + 1) / 1_000_000_000, tz=timezone.utc)
            envelope = intent.sign(
                signer=signer,
                receipt_id="ZLJ-LEARNING-COMMIT-1",
                produced_at=produced_at,
                visibility_scope=("INSTITUTION", "BENJAMIN"),
            )
            self.assertEqual("ZLJ.LEARNING_JOURNAL_COMMITMENT", envelope["event_type"])
            record = BookOutbox(root / "book-outbox").enqueue(envelope=envelope, payload=intent.payload)
            self.assertEqual("PENDING", record["state"])


if __name__ == "__main__":
    unittest.main()
