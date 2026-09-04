from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.evaluation.question_journal import QuestionOutcomeJournal
from autonomous_kernel.evaluation.question_outcome import QuestionBoundOutcome, ResolutionEvidenceRef, build_question_outcome_id
from autonomous_kernel.experience.contracts import ExperienceTimescale
from autonomous_kernel.experts.adapters import implemented_baseline_expert_contracts
from autonomous_kernel.experts.sync import sync_expert_learning
from autonomous_kernel.intelligence import IntelligenceRuntime
from autonomous_kernel.prediction.question_bound import PredictionArtifactRef, build_question_bound_prediction
from autonomous_kernel.prediction.question_journal import QuestionPredictionJournal
from autonomous_kernel.questions.catalog import question_catalog_v1
from autonomous_kernel.questions.contracts import QuestionRegistryEntry, build_question_registry_snapshot


T = 1_800_000_000_000_000_000
SECOND = 1_000_000_000
SUBJECT = "ASSET.BTC"


def _question():
    return next(item for item in question_catalog_v1() if item.question_id == "ECONOMIC_ROOT_DIRECTION_10S")


def _registry():
    question = _question()
    entry = QuestionRegistryEntry(
        definition=question,
        lifecycle_state="RESOLVER_READY",
        registered_at_ns=T - 2 * SECOND,
        effective_at_ns=T - SECOND,
        resolver_implementation_ref="autonomous_kernel.evaluation.question_resolvers.midpoint_v1",
    )
    return build_question_registry_snapshot(
        registry_id="ZLJ-MARKET-QUESTIONS",
        version="1.3-sync-test",
        entries=(entry,),
        known_at_ns=T - 2 * SECOND,
        effective_at_ns=T - SECOND,
    )


def _prediction(model_ref):
    artifact = PredictionArtifactRef(
        artifact_type="MARKET_EXPERIENCE",
        artifact_id="EXP-BTC-SYNC",
        content_hash="a" * 64,
        known_at_ns=T - 1,
        status="QUALIFIED",
        timescales=(ExperienceTimescale.MICRO,),
        feature_families=("SPOT_MICROSTRUCTURE",),
        subject_ids=(SUBJECT,),
    )
    return build_question_bound_prediction(
        registry=_registry(),
        question=_question(),
        subject_id=SUBJECT,
        mode="PROSPECTIVE_SHADOW",
        evidence_class="FORWARD_EVALUABLE",
        cutoff_at_ns=T,
        created_at_ns=T + 1,
        answer={"value": 1, "probability_1": "0.72"},
        model_refs=(model_ref,),
        artifact_refs=(artifact,),
    )


def _append_prediction_and_outcome(root, model_ref):
    prediction = _prediction(model_ref)
    prediction_entry = QuestionPredictionJournal(root).append(prediction, journaled_at_ns=T + 2)
    decided = prediction.resolves_at_ns + 1
    outcome = QuestionBoundOutcome(
        outcome_id=build_question_outcome_id(prediction.prediction_id, prediction.resolver_policy_id, "autonomous_kernel.evaluation.question_resolvers.midpoint_v1"),
        prediction_id=prediction.prediction_id,
        prediction_content_hash=prediction.content_hash(),
        prediction_journal_entry_hash=prediction_entry["entry_hash"],
        question_ref=prediction.question_ref,
        question_definition_hash=prediction.question_definition_hash,
        question_registry_hash=prediction.question_registry_hash,
        subject_id=prediction.subject_id,
        answer_kind=prediction.answer_kind,
        outcome_metric_id=prediction.outcome_metric_id,
        resolver_policy_id=prediction.resolver_policy_id,
        resolver_implementation_ref="autonomous_kernel.evaluation.question_resolvers.midpoint_v1",
        status="RESOLVED",
        cutoff_at_ns=prediction.cutoff_at_ns,
        target_resolves_at_ns=prediction.resolves_at_ns,
        max_resolution_lag_ns=prediction.max_resolution_lag_ns,
        decided_at_ns=decided,
        realized_answer={"value": 1},
        resolution_evidence=(ResolutionEvidenceRef(
            evidence_family="SPOT_MICROSTRUCTURE",
            artifact_type="REPRESENTATION_FRAME",
            artifact_id="REP-BTC-FORWARD",
            content_hash="b" * 64,
            known_at_ns=decided,
            role="FORWARD",
            subject_ids=(SUBJECT,),
        ),),
    )
    QuestionOutcomeJournal(root).append(outcome)
    return prediction, outcome


class ExpertLearningSyncTests(unittest.TestCase):
    def test_sync_earns_competence_from_durable_prediction_and_resolver_truth(self):
        contract = next(item for item in implemented_baseline_expert_contracts() if "DIRECTION" in item["expert_id"] and item["parameters"]["implementation_class"] == "CANDIDATE_MODEL")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, outcome = _append_prediction_and_outcome(root, contract["model_refs"][0])
            result = sync_expert_learning(root, known_at_ns=outcome.decided_at_ns)
            self.assertEqual(result["claims_recorded"], 1)
            self.assertEqual(result["scores_recorded"], 1)
            self.assertEqual(result["competence_entry_count"], 1)
            self.assertEqual(result["capital_authority"], "NONE")
            state = IntelligenceRuntime(root).state()
            self.assertEqual(len(state["claims"]), 1)
            self.assertEqual(len(state["scores"]), 1)
            self.assertIsNotNone(state["competence"])

    def test_sync_is_idempotent(self):
        contract = next(item for item in implemented_baseline_expert_contracts() if "DIRECTION" in item["expert_id"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, outcome = _append_prediction_and_outcome(root, contract["model_refs"][0])
            first = sync_expert_learning(root, known_at_ns=outcome.decided_at_ns)
            events = IntelligenceRuntime(root).state()["event_count"]
            second = sync_expert_learning(root, known_at_ns=outcome.decided_at_ns)
            self.assertEqual(first["claims_recorded"], 1)
            self.assertEqual(first["scores_recorded"], 1)
            self.assertEqual(second["claims_recorded"], 0)
            self.assertEqual(second["scores_recorded"], 0)
            self.assertEqual(IntelligenceRuntime(root).state()["event_count"], events)

    def test_known_at_cutoff_prevents_future_outcome_from_leaking_into_competence(self):
        contract = next(item for item in implemented_baseline_expert_contracts() if "DIRECTION" in item["expert_id"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction, outcome = _append_prediction_and_outcome(root, contract["model_refs"][0])
            before = sync_expert_learning(root, known_at_ns=prediction.resolves_at_ns - 1)
            self.assertEqual(before["claims_recorded"], 1)
            self.assertEqual(before["scores_recorded"], 0)
            self.assertEqual(before["competence_entry_count"], 0)
            after = sync_expert_learning(root, known_at_ns=outcome.decided_at_ns)
            self.assertEqual(after["scores_recorded"], 1)
            self.assertEqual(after["competence_entry_count"], 1)

    def test_unimplemented_model_prediction_is_preserved_but_not_promoted_to_expert(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction("UNIMPLEMENTED-MODEL@1.0.0")
            QuestionPredictionJournal(root).append(prediction, journaled_at_ns=T + 2)
            result = sync_expert_learning(root, known_at_ns=prediction.resolves_at_ns)
            self.assertEqual(result["skipped_unimplemented_predictions"], 1)
            self.assertEqual(result["claims_recorded"], 0)
            self.assertEqual(result["scores_recorded"], 0)


if __name__ == "__main__":
    unittest.main()
