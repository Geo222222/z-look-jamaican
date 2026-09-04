from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from autonomous_kernel.evaluation import (
    QuestionBoundEvaluation,
    QuestionBoundOutcome,
    QuestionEvaluationError,
    QuestionEvaluationJournal,
    QuestionEvaluationJournalError,
    QuestionOutcomeJournal,
    ResolutionEvidenceRef,
    build_question_evaluation,
    build_question_outcome_id,
    validate_question_evaluation_journal,
)
from autonomous_kernel.models import (
    QuestionExpertDefinition,
    QuestionExpertRegistryEntry,
    bind_question,
    build_question_expert_registry_snapshot,
)
from autonomous_kernel.prediction import (
    PredictionArtifactRef,
    QuestionExpertPrediction,
    QuestionExpertPredictionJournal,
    QuestionExpertPredictionJournalError,
    QuestionPredictionJournal,
    build_prospective_question_expert_prediction,
    validate_question_expert_prediction_journal,
)
from autonomous_kernel.questions import (
    build_resolver_ready_registry_v1,
    default_question_registry_v1,
)


T = 1_788_400_000_000_000_000


def _question_registry():
    base = default_question_registry_v1(
        registered_at_ns=T - 1_000,
        effective_at_ns=T - 900,
    )
    return build_resolver_ready_registry_v1(
        base,
        version="1.1.0",
        known_at_ns=T - 800,
        effective_at_ns=T - 700,
    )


def _question(registry, question_id):
    return next(
        entry.definition
        for entry in registry.entries
        if entry.definition.question_id == question_id
    )


def _expert(question, *, suffix="CONTROL"):
    return QuestionExpertDefinition(
        expert_id="%s-%s" % (question.question_id, suffix),
        version="1.0.0",
        family="QUALIFICATION_CONTROL",
        implementation_ref="tests.question_expert_evaluation.%s_v1"
        % question.question_id.lower(),
        implementation_version="1.0.0",
        question_bindings=(bind_question(question),),
        required_artifact_types=question.required_artifact_types,
        required_feature_families=question.required_feature_families,
        allowed_feature_families=question.required_feature_families,
        required_timescales=question.required_timescales,
        feature_schema_id="ZLJ.TEST.%s" % question.question_id,
        feature_schema_version="1.0.0",
        training_mode="NONE",
        training_data_cutoff_ns=None,
        training_completed_at_ns=None,
        supported_subject_ids=("ASSET.BTC",),
        parameters={"qualification_control": True},
    )


def _expert_registry(
    expert,
    *,
    version="1.0.0",
    qualification_evidence_refs=("QUAL-QUESTION-EXPERT-001",),
):
    return build_question_expert_registry_snapshot(
        registry_id="ZLJ-QUESTION-EXPERTS",
        version=version,
        entries=(
            QuestionExpertRegistryEntry(
                definition=expert,
                lifecycle_state="SHADOW_QUALIFIED",
                registered_at_ns=T - 500,
                effective_at_ns=T - 400,
                qualification_evidence_refs=qualification_evidence_refs,
            ),
        ),
        known_at_ns=T - 300,
        effective_at_ns=T - 200,
    )


def _artifacts(question):
    refs = []
    for index, artifact_type in enumerate(question.required_artifact_types):
        refs.append(
            PredictionArtifactRef(
                artifact_type=artifact_type,
                artifact_id="%s-%d" % (artifact_type, index),
                content_hash=("%x" % (index + 10)) * 64,
                known_at_ns=T - 10 - index,
                status="QUALIFIED",
                timescales=tuple(question.required_timescales),
                feature_families=tuple(question.required_feature_families),
                subject_ids=("ASSET.BTC",),
            )
        )
    return tuple(refs)


def _prediction_answer(question_id):
    if question_id == "ECONOMIC_ROOT_DIRECTION_10S":
        return {"value": 1, "probability_1": "0.5"}
    if question_id == "ECONOMIC_ROOT_MAGNITUDE_30S":
        return {
            "value": "3.5",
            "interval_low": "-1",
            "interval_high": "5",
        }
    if question_id == "MARKET_DIRECTION_REGIME_15M":
        return {
            "value": "TREND_UP",
            "probabilities": {"TREND_UP": "0.7", "RANGE": "0.3"},
        }
    raise AssertionError("unsupported test question: %s" % question_id)


def _realized_answer(question_id):
    if question_id == "ECONOMIC_ROOT_DIRECTION_10S":
        return {"value": 1}
    if question_id == "ECONOMIC_ROOT_MAGNITUDE_30S":
        return {"value": "2"}
    if question_id == "MARKET_DIRECTION_REGIME_15M":
        return {"value": "RANGE"}
    raise AssertionError("unsupported test question: %s" % question_id)


def _expert_prediction(question_id="ECONOMIC_ROOT_DIRECTION_10S"):
    registry = _question_registry()
    question = _question(registry, question_id)
    expert = _expert(question)
    expert_registry = _expert_registry(expert)
    wrapped = build_prospective_question_expert_prediction(
        question_registry=registry,
        question=question,
        expert_registry=expert_registry,
        expert_definition_ref=expert.definition_ref,
        subject_id="ASSET.BTC",
        cutoff_at_ns=T,
        created_at_ns=T + 1,
        answer=_prediction_answer(question_id),
        artifact_refs=_artifacts(question),
    )
    return registry, question, expert, expert_registry, wrapped


def _journal_prediction(root, wrapped):
    base_entry = QuestionPredictionJournal(root).append(
        wrapped.prediction,
        journaled_at_ns=T + 2,
    )
    expert_entry = QuestionExpertPredictionJournal(root).append(
        wrapped,
        base_prediction_journal_entry_hash=str(base_entry["entry_hash"]),
        journaled_at_ns=T + 3,
    )
    return base_entry, expert_entry


def _outcome(wrapped, base_entry, *, status="RESOLVED"):
    prediction = wrapped.prediction
    implementation = "tests.question_expert_evaluation.resolver_v1"
    outcome_id = build_question_outcome_id(
        prediction.prediction_id,
        prediction.resolver_policy_id,
        implementation,
    )
    if status == "RESOLVED":
        target = prediction.resolves_at_ns
        evidence = (
            ResolutionEvidenceRef(
                evidence_family="SPOT_MICROSTRUCTURE",
                artifact_type="REPRESENTATION_FRAME",
                artifact_id="FRAME-BASELINE",
                content_hash="b" * 64,
                known_at_ns=T,
                role="BASELINE",
                subject_ids=("ASSET.BTC",),
            ),
            ResolutionEvidenceRef(
                evidence_family="SPOT_MICROSTRUCTURE",
                artifact_type="REPRESENTATION_FRAME",
                artifact_id="FRAME-FORWARD",
                content_hash="c" * 64,
                known_at_ns=target,
                role="FORWARD",
                subject_ids=("ASSET.BTC",),
            ),
        )
        decided_at_ns = target + 1
        answer = _realized_answer(prediction.question_ref.split("@")[0])
    else:
        evidence = ()
        answer = None
        decided_at_ns = (
            prediction.resolves_at_ns
            + prediction.max_resolution_lag_ns
            + 1
        )
    return QuestionBoundOutcome(
        outcome_id=outcome_id,
        prediction_id=prediction.prediction_id,
        prediction_content_hash=prediction.content_hash(),
        prediction_journal_entry_hash=str(base_entry["entry_hash"]),
        question_ref=prediction.question_ref,
        question_definition_hash=prediction.question_definition_hash,
        question_registry_hash=prediction.question_registry_hash,
        subject_id=prediction.subject_id,
        answer_kind=prediction.answer_kind,
        outcome_metric_id=prediction.outcome_metric_id,
        resolver_policy_id=prediction.resolver_policy_id,
        resolver_implementation_ref=implementation,
        status=status,
        cutoff_at_ns=prediction.cutoff_at_ns,
        target_resolves_at_ns=prediction.resolves_at_ns,
        max_resolution_lag_ns=prediction.max_resolution_lag_ns,
        decided_at_ns=decided_at_ns,
        realized_answer=answer,
        resolution_evidence=evidence,
    )


def _full_sources(
    root,
    question_id="ECONOMIC_ROOT_DIRECTION_10S",
    *,
    status="RESOLVED",
):
    _, _, _, _, wrapped = _expert_prediction(question_id)
    base_entry, expert_entry = _journal_prediction(root, wrapped)
    outcome = _outcome(wrapped, base_entry, status=status)
    outcome_entry = QuestionOutcomeJournal(root).append(outcome)
    evaluation = build_question_evaluation(
        expert_prediction=wrapped,
        expert_prediction_journal_entry_hash=str(expert_entry["entry_hash"]),
        outcome=outcome,
        outcome_journal_entry_hash=str(outcome_entry["entry_hash"]),
        evaluated_at_ns=outcome.decided_at_ns + 1,
    )
    return wrapped, base_entry, expert_entry, outcome, outcome_entry, evaluation


class QuestionExpertPredictionJournalTests(unittest.TestCase):
    def test_sidecar_preserves_exact_expert_eligibility_without_rewriting_base_journal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, _, _, wrapped = _expert_prediction()
            base_entry, expert_entry = _journal_prediction(root, wrapped)
            self.assertEqual([], validate_question_expert_prediction_journal(root))
            self.assertTrue((root / "memory/question_predictions.jsonl").is_file())
            self.assertTrue(
                (root / "memory/question_expert_predictions.jsonl").is_file()
            )
            self.assertEqual(
                base_entry["entry_hash"],
                expert_entry["base_prediction_journal_entry_hash"],
            )
            self.assertEqual(
                wrapped.expert_registry_hash,
                expert_entry["expert_prediction"]["expert_registry"]["content_hash"],
            )
            state = QuestionExpertPredictionJournal(root).rebuild_state()
            self.assertEqual(1, state["entry_count"])

    def test_sidecar_refuses_missing_or_false_base_prediction_lineage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, _, _, wrapped = _expert_prediction()
            journal = QuestionExpertPredictionJournal(root)
            with self.assertRaisesRegex(
                QuestionExpertPredictionJournalError,
                "exactly one base question prediction",
            ):
                journal.append(
                    wrapped,
                    base_prediction_journal_entry_hash="d" * 64,
                    journaled_at_ns=T + 3,
                )
            base_entry = QuestionPredictionJournal(root).append(
                wrapped.prediction,
                journaled_at_ns=T + 2,
            )
            self.assertNotEqual("d" * 64, base_entry["entry_hash"])
            with self.assertRaisesRegex(
                QuestionExpertPredictionJournalError,
                "base prediction journal entry hash mismatch",
            ):
                journal.append(
                    wrapped,
                    base_prediction_journal_entry_hash="d" * 64,
                    journaled_at_ns=T + 3,
                )

    def test_sidecar_refuses_retrospective_journaling_and_conflicting_expert_lineage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, expert, _, wrapped = _expert_prediction()
            base_entry = QuestionPredictionJournal(root).append(
                wrapped.prediction,
                journaled_at_ns=T + 2,
            )
            journal = QuestionExpertPredictionJournal(root)
            with self.assertRaisesRegex(
                QuestionExpertPredictionJournalError,
                "before resolution horizon",
            ):
                journal.append(
                    wrapped,
                    base_prediction_journal_entry_hash=str(base_entry["entry_hash"]),
                    journaled_at_ns=wrapped.prediction.resolves_at_ns,
                )
            original_entry = journal.append(
                wrapped,
                base_prediction_journal_entry_hash=str(base_entry["entry_hash"]),
                journaled_at_ns=T + 3,
            )
            alternate_registry = _expert_registry(
                expert,
                version="1.0.1",
                qualification_evidence_refs=("QUAL-QUESTION-EXPERT-ALT",),
            )
            alternate = QuestionExpertPrediction(
                prediction=wrapped.prediction,
                expert_registry_id=alternate_registry.registry_id,
                expert_registry_version=alternate_registry.version,
                expert_registry_hash=alternate_registry.content_hash(),
                expert_definition_ref=expert.definition_ref,
                expert_definition_hash=expert.content_hash(),
                expert_lifecycle_state="SHADOW_QUALIFIED",
                qualification_evidence_refs=("QUAL-QUESTION-EXPERT-ALT",),
            )
            self.assertEqual(
                wrapped.prediction.prediction_id,
                alternate.prediction.prediction_id,
            )
            self.assertNotEqual(
                wrapped.expert_registry_hash,
                alternate.expert_registry_hash,
            )
            with self.assertRaisesRegex(
                QuestionExpertPredictionJournalError,
                "different expert lineage",
            ):
                journal.append(
                    alternate,
                    base_prediction_journal_entry_hash=str(base_entry["entry_hash"]),
                    journaled_at_ns=T + 4,
                )
            self.assertEqual(original_entry, journal.entries()[0])

    def test_sidecar_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, _, _, wrapped = _expert_prediction()
            _journal_prediction(root, wrapped)
            path = root / "memory/question_expert_predictions.jsonl"
            entry = json.loads(path.read_text(encoding="utf-8"))
            entry["base_prediction_journal_entry_hash"] = "e" * 64
            path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
            errors = validate_question_expert_prediction_journal(root)
            self.assertTrue(errors)
            self.assertIn("entry hash mismatch", "; ".join(errors))


class QuestionBoundEvaluationTests(unittest.TestCase):
    def test_binary_evaluation_scores_prediction_without_mutating_market_truth(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wrapped, _, expert_entry, outcome, outcome_entry, evaluation = _full_sources(root)
            self.assertEqual("SCORED", evaluation.status)
            self.assertEqual(1, evaluation.metrics["exact_hit"])
            self.assertEqual("0.25", evaluation.metrics["brier_score"])
            self.assertEqual(outcome.content_hash(), evaluation.outcome_content_hash)
            self.assertEqual(
                wrapped.expert_definition_ref,
                evaluation.expert_definition_ref,
            )
            authority = evaluation.to_wire()["authority"]
            self.assertTrue(authority["model_evaluation_only"])
            self.assertFalse(authority["market_truth_mutation"])
            self.assertFalse(authority["model_competence"])
            self.assertFalse(authority["adaptive_weighting"])
            self.assertFalse(authority["capital_confidence"])
            self.assertFalse(authority["capital_decision"])
            restored = QuestionBoundEvaluation.from_wire(evaluation.to_wire())
            self.assertEqual(evaluation.to_wire(), restored.to_wire())
            reproduced = build_question_evaluation(
                expert_prediction=wrapped,
                expert_prediction_journal_entry_hash=str(expert_entry["entry_hash"]),
                outcome=outcome,
                outcome_journal_entry_hash=str(outcome_entry["entry_hash"]),
                evaluated_at_ns=evaluation.evaluated_at_ns,
            )
            self.assertEqual(evaluation.to_wire(), reproduced.to_wire())

    def test_continuous_and_categorical_scoring_are_question_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            *_, continuous = _full_sources(
                Path(temporary),
                "ECONOMIC_ROOT_MAGNITUDE_30S",
            )
            self.assertEqual("1.5", continuous.metrics["signed_error"])
            self.assertEqual("1.5", continuous.metrics["absolute_error"])
            self.assertEqual("2.25", continuous.metrics["squared_error"])
            self.assertEqual(1, continuous.metrics["interval_covered"])
        with tempfile.TemporaryDirectory() as temporary:
            *_, categorical = _full_sources(
                Path(temporary),
                "MARKET_DIRECTION_REGIME_15M",
            )
            self.assertEqual("TREND_UP", categorical.metrics["predicted_label"])
            self.assertEqual("RANGE", categorical.metrics["realized_label"])
            self.assertEqual(0, categorical.metrics["exact_hit"])
            self.assertEqual("0.3", categorical.metrics["realized_probability"])

    def test_unresolvable_market_truth_is_not_scored_as_model_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, _, outcome, _, evaluation = _full_sources(
                Path(temporary),
                status="UNRESOLVABLE",
            )
            self.assertEqual("UNRESOLVABLE", outcome.status)
            self.assertEqual("NOT_SCORABLE_UNRESOLVABLE", evaluation.status)
            self.assertEqual({}, evaluation.metrics)
            self.assertEqual(
                "NO_SCORE_UNRESOLVABLE_V1",
                evaluation.scoring_policy_id,
            )

    def test_evaluation_refuses_pre_outcome_time_and_substituted_outcome_lineage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wrapped, _, expert_entry, outcome, outcome_entry, _ = _full_sources(root)
            with self.assertRaisesRegex(
                QuestionEvaluationError,
                "cannot predate mechanical outcome",
            ):
                build_question_evaluation(
                    expert_prediction=wrapped,
                    expert_prediction_journal_entry_hash=str(expert_entry["entry_hash"]),
                    outcome=outcome,
                    outcome_journal_entry_hash=str(outcome_entry["entry_hash"]),
                    evaluated_at_ns=outcome.decided_at_ns - 1,
                )
            substituted = replace(outcome, question_registry_hash="d" * 64)
            with self.assertRaisesRegex(
                QuestionEvaluationError,
                "outcome question registry hash mismatch",
            ):
                build_question_evaluation(
                    expert_prediction=wrapped,
                    expert_prediction_journal_entry_hash=str(expert_entry["entry_hash"]),
                    outcome=substituted,
                    outcome_journal_entry_hash=str(outcome_entry["entry_hash"]),
                    evaluated_at_ns=outcome.decided_at_ns + 1,
                )

    def test_evaluation_identity_binds_evaluation_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wrapped, _, expert_entry, outcome, outcome_entry, evaluation = _full_sources(root)
            later = build_question_evaluation(
                expert_prediction=wrapped,
                expert_prediction_journal_entry_hash=str(expert_entry["entry_hash"]),
                outcome=outcome,
                outcome_journal_entry_hash=str(outcome_entry["entry_hash"]),
                evaluated_at_ns=evaluation.evaluated_at_ns + 1,
            )
            self.assertNotEqual(evaluation.evaluation_id, later.evaluation_id)
            self.assertNotEqual(evaluation.content_hash(), later.content_hash())

    def test_wire_tamper_and_authority_escalation_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            *_, evaluation = _full_sources(Path(temporary))
            escalated = copy.deepcopy(evaluation.to_wire())
            escalated["authority"]["model_competence"] = True
            with self.assertRaisesRegex(
                QuestionEvaluationError,
                "authority boundary",
            ):
                QuestionBoundEvaluation.from_wire(escalated)
            tampered = copy.deepcopy(evaluation.to_wire())
            tampered["metrics"]["exact_hit"] = 0
            with self.assertRaisesRegex(
                QuestionEvaluationError,
                "content hash mismatch",
            ):
                QuestionBoundEvaluation.from_wire(tampered)


class QuestionEvaluationJournalTests(unittest.TestCase):
    def test_evaluation_journal_reproduces_score_from_exact_journaled_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            *_, evaluation = _full_sources(root)
            journal = QuestionEvaluationJournal(root)
            entry = journal.append(
                evaluation,
                journaled_at_ns=evaluation.evaluated_at_ns + 1,
            )
            self.assertEqual([], validate_question_evaluation_journal(root))
            self.assertEqual(
                evaluation.content_hash(),
                QuestionBoundEvaluation.from_wire(entry["evaluation"]).content_hash(),
            )
            state = journal.rebuild_state()
            self.assertEqual(1, state["entry_count"])

    def test_journal_rejects_manual_score_even_when_object_integrity_is_self_consistent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            *_, evaluation = _full_sources(root)
            malicious = replace(
                evaluation,
                metrics={
                    "predicted_value": 1,
                    "realized_value": 1,
                    "exact_hit": 0,
                    "probability_1": "0.5",
                    "brier_score": "0",
                },
            )
            self.assertNotEqual(evaluation.content_hash(), malicious.content_hash())
            with self.assertRaisesRegex(
                QuestionEvaluationJournalError,
                "mechanically reproduced score",
            ):
                QuestionEvaluationJournal(root).append(
                    malicious,
                    journaled_at_ns=malicious.evaluated_at_ns + 1,
                )

    def test_journal_rejects_false_expert_or_outcome_journal_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            *_, evaluation = _full_sources(root)
            false_expert = replace(
                evaluation,
                expert_prediction_journal_entry_hash="d" * 64,
            )
            with self.assertRaisesRegex(
                QuestionEvaluationJournalError,
                "expert prediction journal entry hash mismatch",
            ):
                QuestionEvaluationJournal(root).append(
                    false_expert,
                    journaled_at_ns=false_expert.evaluated_at_ns + 1,
                )
            false_outcome = replace(
                evaluation,
                outcome_journal_entry_hash="e" * 64,
            )
            with self.assertRaisesRegex(
                QuestionEvaluationJournalError,
                "outcome journal entry hash mismatch",
            ):
                QuestionEvaluationJournal(root).append(
                    false_outcome,
                    journaled_at_ns=false_outcome.evaluated_at_ns + 1,
                )

    def test_one_prediction_cannot_acquire_two_different_evaluations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wrapped, _, expert_entry, outcome, outcome_entry, evaluation = _full_sources(root)
            journal = QuestionEvaluationJournal(root)
            journal.append(
                evaluation,
                journaled_at_ns=evaluation.evaluated_at_ns + 1,
            )
            later = build_question_evaluation(
                expert_prediction=wrapped,
                expert_prediction_journal_entry_hash=str(expert_entry["entry_hash"]),
                outcome=outcome,
                outcome_journal_entry_hash=str(outcome_entry["entry_hash"]),
                evaluated_at_ns=evaluation.evaluated_at_ns + 2,
            )
            with self.assertRaisesRegex(
                QuestionEvaluationJournalError,
                "different evaluation",
            ):
                journal.append(
                    later,
                    journaled_at_ns=later.evaluated_at_ns + 1,
                )

    def test_evaluation_journal_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            *_, evaluation = _full_sources(root)
            QuestionEvaluationJournal(root).append(
                evaluation,
                journaled_at_ns=evaluation.evaluated_at_ns + 1,
            )
            path = root / "memory/question_evaluations.jsonl"
            entry = json.loads(path.read_text(encoding="utf-8"))
            entry["journaled_at_ns"] += 1
            path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
            errors = validate_question_evaluation_journal(root)
            self.assertTrue(errors)
            self.assertIn("entry hash mismatch", "; ".join(errors))


if __name__ == "__main__":
    unittest.main()
