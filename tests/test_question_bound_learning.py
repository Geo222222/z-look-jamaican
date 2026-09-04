from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.evaluation.question_outcome import (
    QuestionBoundOutcome,
    QuestionOutcomeError,
    ResolutionEvidenceRef,
    build_question_outcome_id,
)
from autonomous_kernel.experience.contracts import ExperienceTimescale
from autonomous_kernel.prediction.question_bound import (
    PredictionArtifactRef,
    QuestionBoundPrediction,
    QuestionPredictionError,
    build_question_bound_prediction,
)
from autonomous_kernel.prediction.question_journal import (
    QuestionPredictionJournal,
    QuestionPredictionJournalError,
    validate_question_prediction_journal,
)
from autonomous_kernel.questions.catalog import question_catalog_v1
from autonomous_kernel.questions.contracts import (
    QuestionRegistryEntry,
    build_question_registry_snapshot,
)


T = 1_788_400_000_000_000_000
SECOND = 1_000_000_000


def _question():
    return question_catalog_v1()[0]


def _registry(*, lifecycle="RESOLVER_READY", known_at=T - 2 * SECOND, effective_at=T - SECOND):
    question = _question()
    entry = QuestionRegistryEntry(
        definition=question,
        lifecycle_state=lifecycle,
        registered_at_ns=known_at,
        effective_at_ns=effective_at,
        resolver_implementation_ref=None if lifecycle == "DEFINED" else "autonomous_kernel.evaluation.question_resolvers.direction_v1",
    )
    return build_question_registry_snapshot(
        registry_id="ZLJ-MARKET-QUESTIONS",
        version="1.1-test",
        entries=(entry,),
        known_at_ns=known_at,
        effective_at_ns=effective_at,
    )


def _artifact(*, known_at=T - 1, status="QUALIFIED", features=("SPOT_MICROSTRUCTURE",)):
    return PredictionArtifactRef(
        artifact_type="MARKET_EXPERIENCE",
        artifact_id="EXP-BTC-T",
        content_hash="a" * 64,
        known_at_ns=known_at,
        status=status,
        timescales=(ExperienceTimescale.MICRO,),
        feature_families=features,
    )


def _prediction(*, created_at=T, registry=None, mode="PROSPECTIVE_SHADOW", evidence_class="FORWARD_EVALUABLE", artifact=None):
    return build_question_bound_prediction(
        registry=_registry() if registry is None else registry,
        question=_question(),
        mode=mode,
        evidence_class=evidence_class,
        cutoff_at_ns=T,
        created_at_ns=created_at,
        answer={"value": 1, "probability_1": "0.61"},
        model_refs=("MODEL-DIRECTION-BASELINE-1",),
        artifact_refs=(_artifact() if artifact is None else artifact,),
    )


class QuestionBoundLearningTests(unittest.TestCase):
    def test_prediction_binds_question_registry_and_legal_evidence(self) -> None:
        prediction = _prediction()
        self.assertEqual(_question().question_ref, prediction.question_ref)
        self.assertEqual(_question().content_hash(), prediction.question_definition_hash)
        self.assertEqual(_registry().content_hash(), prediction.question_registry_hash)
        self.assertEqual("AGGREGATE_MIDPOINT_DIRECTION_10S_V1", prediction.outcome_metric_id)
        self.assertEqual({"value": 1, "probability_1": "0.61"}, prediction.answer)
        restored = QuestionBoundPrediction.from_wire(prediction.to_wire())
        self.assertEqual(prediction.to_wire(), restored.to_wire())
        self.assertEqual(prediction.content_hash(), restored.content_hash())

    def test_production_time_is_material_prediction_identity(self) -> None:
        first = _prediction(created_at=T)
        second = _prediction(created_at=T + 1)
        self.assertNotEqual(first.prediction_id, second.prediction_id)
        self.assertNotEqual(first.content_hash(), second.content_hash())

    def test_prospective_prediction_requires_question_to_be_resolver_ready_before_cutoff(self) -> None:
        with self.assertRaisesRegex(QuestionPredictionError, "resolver-ready"):
            _prediction(registry=_registry(lifecycle="DEFINED"))
        with self.assertRaisesRegex(QuestionPredictionError, "not knowable/effective"):
            _prediction(registry=_registry(known_at=T + 1, effective_at=T + 2))

    def test_historical_replay_can_use_later_defined_question_but_is_research_only(self) -> None:
        registry = _registry(known_at=T + 100, effective_at=T + 101)
        prediction = _prediction(
            registry=registry,
            mode="HISTORICAL_REPLAY",
            evidence_class="RESEARCH_ONLY",
            created_at=T + 200,
        )
        self.assertEqual("RESEARCH_ONLY", prediction.evidence_class)
        self.assertEqual("HISTORICAL_REPLAY", prediction.mode)

    def test_post_cutoff_or_forbidden_evidence_is_rejected(self) -> None:
        with self.assertRaisesRegex(QuestionPredictionError, "post-cutoff"):
            _prediction(artifact=_artifact(known_at=T + 1))
        with self.assertRaisesRegex(QuestionPredictionError, "forbidden"):
            _prediction(artifact=_artifact(features=("SPOT_MICROSTRUCTURE", "FUTURE_OUTCOME")))

    def test_degraded_artifact_is_not_forward_evaluable(self) -> None:
        with self.assertRaisesRegex(QuestionPredictionError, "qualified artifact"):
            _prediction(artifact=_artifact(status="DEGRADED"))

    def test_prediction_journal_is_append_only_idempotent_and_tamper_evident(self) -> None:
        prediction = _prediction()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = QuestionPredictionJournal(root)
            first = journal.append(prediction, journaled_at_ns=T + 1)
            replay = journal.append(prediction, journaled_at_ns=T + 2)
            self.assertEqual(first["entry_hash"], replay["entry_hash"])
            self.assertEqual(1, len(journal.entries()))
            self.assertEqual([], validate_question_prediction_journal(root))

            lines = journal.path.read_text(encoding="utf-8").splitlines()
            record = json.loads(lines[0])
            record["prediction"]["answer"]["value"] = 0
            journal.path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            errors = validate_question_prediction_journal(root)
            self.assertTrue(any("hash mismatch" in error for error in errors))

    def test_prediction_journal_rejects_late_forward_claim(self) -> None:
        prediction = _prediction()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(QuestionPredictionJournalError, "before resolution"):
                QuestionPredictionJournal(Path(directory)).append(
                    prediction,
                    journaled_at_ns=prediction.resolves_at_ns,
                )

    def test_resolved_outcome_is_market_truth_not_model_score(self) -> None:
        prediction = _prediction()
        journal_hash = "b" * 64
        outcome = QuestionBoundOutcome(
            outcome_id=build_question_outcome_id(
                prediction.prediction_id,
                prediction.resolver_policy_id,
                "direction-resolver-v1",
            ),
            prediction_id=prediction.prediction_id,
            prediction_content_hash=prediction.content_hash(),
            prediction_journal_entry_hash=journal_hash,
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
            decided_at_ns=prediction.resolves_at_ns + 1,
            realized_answer={"value": 0},
            resolution_evidence=(
                ResolutionEvidenceRef(
                    evidence_family="SPOT_MICROSTRUCTURE",
                    artifact_type="REPRESENTATION_FRAME",
                    artifact_id="REP-TARGET",
                    content_hash="c" * 64,
                    known_at_ns=prediction.resolves_at_ns + 1,
                    role="FORWARD",
                ),
            ),
        )
        wire = outcome.to_wire()
        self.assertTrue(wire["authority"]["market_truth_only"])
        self.assertNotIn("score", wire)
        self.assertNotIn("loss", wire)
        restored = QuestionBoundOutcome.from_wire(wire)
        self.assertEqual(outcome.content_hash(), restored.content_hash())

    def test_outcome_rejects_future_evidence_beyond_declared_lag_and_tamper(self) -> None:
        prediction = _prediction()
        with self.assertRaisesRegex(QuestionOutcomeError, "outside allowed causal window"):
            QuestionBoundOutcome(
                outcome_id="QOUT-BAD",
                prediction_id=prediction.prediction_id,
                prediction_content_hash=prediction.content_hash(),
                prediction_journal_entry_hash="b" * 64,
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
                decided_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns + 2,
                realized_answer={"value": 1},
                resolution_evidence=(
                    ResolutionEvidenceRef(
                        evidence_family="SPOT_MICROSTRUCTURE",
                        artifact_type="REPRESENTATION_FRAME",
                        artifact_id="REP-LATE",
                        content_hash="d" * 64,
                        known_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns + 1,
                        role="FORWARD",
                    ),
                ),
            )

        valid = QuestionBoundOutcome(
            outcome_id="QOUT-GOOD",
            prediction_id=prediction.prediction_id,
            prediction_content_hash=prediction.content_hash(),
            prediction_journal_entry_hash="b" * 64,
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
                    artifact_id="REP-TARGET",
                    content_hash="e" * 64,
                    known_at_ns=prediction.resolves_at_ns,
                    role="FORWARD",
                ),
            ),
        )
        wire = valid.to_wire()
        wire["realized_answer"]["value"] = 0
        with self.assertRaisesRegex(QuestionOutcomeError, "content hash mismatch"):
            QuestionBoundOutcome.from_wire(wire)


if __name__ == "__main__":
    unittest.main()
