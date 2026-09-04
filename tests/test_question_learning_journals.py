from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from autonomous_kernel.book_bridge import ZLJBookSigner
from autonomous_kernel.book_outbox import BookOutbox
from autonomous_kernel.evaluation import QuestionBoundOutcome, QuestionOutcomeJournal, QuestionOutcomeJournalError, ResolutionEvidenceRef, build_question_outcome_id, validate_question_outcome_journal
from autonomous_kernel.experience.contracts import ExperienceTimescale
from autonomous_kernel.prediction import PredictionArtifactRef, QuestionPredictionJournal, build_question_bound_prediction
from autonomous_kernel.questions import QuestionRegistryEntry, build_learning_journal_commitment, build_question_registry_snapshot, question_catalog_v1

T = 1_788_400_000_000_000_000
SECOND = 1_000_000_000
SUBJECT = "ASSET.BTC"


def _prediction():
    question = question_catalog_v1()[0]
    registry = build_question_registry_snapshot(registry_id="ZLJ-MARKET-QUESTIONS", version="1.1-test", entries=(QuestionRegistryEntry(definition=question, lifecycle_state="RESOLVER_READY", registered_at_ns=T - 2 * SECOND, effective_at_ns=T - SECOND, resolver_implementation_ref="direction-resolver-v1"),), known_at_ns=T - 2 * SECOND, effective_at_ns=T - SECOND)
    return build_question_bound_prediction(registry=registry, question=question, subject_id=SUBJECT, mode="PROSPECTIVE_SHADOW", evidence_class="FORWARD_EVALUABLE", cutoff_at_ns=T, created_at_ns=T, answer={"value": 1, "probability_1": "0.55"}, model_refs=("MODEL-TEST-1",), artifact_refs=(PredictionArtifactRef(artifact_type="MARKET_EXPERIENCE", artifact_id="EXP-T", content_hash="a" * 64, known_at_ns=T - 1, status="QUALIFIED", timescales=(ExperienceTimescale.MICRO,), feature_families=("SPOT_MICROSTRUCTURE",), subject_ids=(SUBJECT,)),))


def _outcome(prediction, prediction_entry, value=1):
    return QuestionBoundOutcome(outcome_id=build_question_outcome_id(prediction.prediction_id, prediction.resolver_policy_id, "direction-resolver-v1"), prediction_id=prediction.prediction_id, prediction_content_hash=prediction.content_hash(), prediction_journal_entry_hash=str(prediction_entry["entry_hash"]), question_ref=prediction.question_ref, question_definition_hash=prediction.question_definition_hash, question_registry_hash=prediction.question_registry_hash, subject_id=prediction.subject_id, answer_kind=prediction.answer_kind, outcome_metric_id=prediction.outcome_metric_id, resolver_policy_id=prediction.resolver_policy_id, resolver_implementation_ref="direction-resolver-v1", status="RESOLVED", cutoff_at_ns=prediction.cutoff_at_ns, target_resolves_at_ns=prediction.resolves_at_ns, max_resolution_lag_ns=prediction.max_resolution_lag_ns, decided_at_ns=prediction.resolves_at_ns, realized_answer={"value": value}, resolution_evidence=(ResolutionEvidenceRef(evidence_family="SPOT_MICROSTRUCTURE", artifact_type="REPRESENTATION_FRAME", artifact_id="REP-RESOLUTION", content_hash="b" * 64, known_at_ns=prediction.resolves_at_ns, role="FORWARD", subject_ids=(SUBJECT,)),))


class QuestionLearningJournalTests(unittest.TestCase):
    def test_outcome_journal_requires_exact_prediction_lineage_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prediction = _prediction(); prediction_entry = QuestionPredictionJournal(root).append(prediction, journaled_at_ns=T + 1); outcome = _outcome(prediction, prediction_entry); journal = QuestionOutcomeJournal(root)
            first = journal.append(outcome); replay = journal.append(outcome)
            self.assertEqual(first["entry_hash"], replay["entry_hash"]); self.assertEqual([], validate_question_outcome_journal(root))

    def test_outcome_journal_rejects_conflicting_final_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prediction = _prediction(); entry = QuestionPredictionJournal(root).append(prediction, journaled_at_ns=T + 1); journal = QuestionOutcomeJournal(root); journal.append(_outcome(prediction, entry, 1))
            with self.assertRaisesRegex(QuestionOutcomeJournalError, "different final"):
                journal.append(_outcome(prediction, entry, 0))

    def test_outcome_journal_rejects_subject_lineage_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prediction = _prediction(); entry = QuestionPredictionJournal(root).append(prediction, journaled_at_ns=T + 1); outcome = _outcome(prediction, entry)
            bad = QuestionBoundOutcome(outcome_id=outcome.outcome_id, prediction_id=outcome.prediction_id, prediction_content_hash=outcome.prediction_content_hash, prediction_journal_entry_hash=outcome.prediction_journal_entry_hash, question_ref=outcome.question_ref, question_definition_hash=outcome.question_definition_hash, question_registry_hash=outcome.question_registry_hash, subject_id="ASSET.ETH", answer_kind=outcome.answer_kind, outcome_metric_id=outcome.outcome_metric_id, resolver_policy_id=outcome.resolver_policy_id, resolver_implementation_ref=outcome.resolver_implementation_ref, status=outcome.status, cutoff_at_ns=outcome.cutoff_at_ns, target_resolves_at_ns=outcome.target_resolves_at_ns, max_resolution_lag_ns=outcome.max_resolution_lag_ns, decided_at_ns=outcome.decided_at_ns, realized_answer=outcome.realized_answer, resolution_evidence=(ResolutionEvidenceRef(evidence_family="SPOT_MICROSTRUCTURE", artifact_type="REPRESENTATION_FRAME", artifact_id="REP-ETH", content_hash="c" * 64, known_at_ns=prediction.resolves_at_ns, role="FORWARD", subject_ids=("ASSET.ETH",)),))
            with self.assertRaisesRegex(QuestionOutcomeJournalError, "subject mismatch"):
                QuestionOutcomeJournal(root).append(bad)

    def test_prediction_and_outcome_journal_tamper_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prediction = _prediction(); entry = QuestionPredictionJournal(root).append(prediction, journaled_at_ns=T + 1); journal = QuestionOutcomeJournal(root); journal.append(_outcome(prediction, entry))
            record = json.loads(journal.path.read_text(encoding="utf-8").splitlines()[0]); record["outcome"]["status"] = "UNRESOLVABLE"; journal.path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            self.assertTrue(any("hash mismatch" in error for error in validate_question_outcome_journal(root)))

    def test_learning_journal_commitments_are_compact_and_book_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prediction = _prediction(); pred_journal = QuestionPredictionJournal(root); entry = pred_journal.append(prediction, journaled_at_ns=T + 1); out_journal = QuestionOutcomeJournal(root); out_journal.append(_outcome(prediction, entry))
            pred_commitment = build_learning_journal_commitment(journal_name="ZLJ.QUESTION_PREDICTIONS.v1", records=pred_journal.entries()); out_commitment = build_learning_journal_commitment(journal_name="ZLJ.QUESTION_OUTCOMES.v1", records=out_journal.entries())
            self.assertEqual(1, pred_commitment.event_count); self.assertEqual(1, out_commitment.event_count)
            self.assertNotIn("artifact_refs", pred_commitment.material_evidence().payload.decode("utf-8")); self.assertNotIn("realized_answer", out_commitment.material_evidence().payload.decode("utf-8"))
            signer = ZLJBookSigner(key_id="learning-test", private_key=Ed25519PrivateKey.generate()); intent = out_commitment.material_evidence(payload_ref="zlj://learning-journal/question-outcomes/0-0"); produced_at = datetime.fromtimestamp((intent.known_at_ns + 1) / 1_000_000_000, tz=timezone.utc)
            envelope = intent.sign(signer=signer, receipt_id="ZLJ-LEARNING-COMMIT-1", produced_at=produced_at, visibility_scope=("INSTITUTION", "BENJAMIN")); self.assertEqual("ZLJ.LEARNING_JOURNAL_COMMITMENT", envelope["event_type"]); self.assertEqual("PENDING", BookOutbox(root / "book-outbox").enqueue(envelope=envelope, payload=intent.payload)["state"])


if __name__ == "__main__": unittest.main()
