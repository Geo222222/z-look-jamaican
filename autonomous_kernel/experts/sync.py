from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from ..evaluation.question_journal import QuestionOutcomeJournal, validate_question_outcome_journal
from ..evaluation.question_outcome import QuestionBoundOutcome
from ..intelligence.runtime import IntelligenceRuntime, validate_event_chain
from ..prediction.question_bound import QuestionBoundPrediction
from ..prediction.question_journal import QuestionPredictionJournal, validate_question_prediction_journal
from .adapters import implemented_baseline_expert_contracts, question_prediction_to_expert_claim
from .school import score_expert_claim


class ExpertLearningSyncError(RuntimeError):
    pass


def _prediction_entries(root: Path) -> Tuple[Tuple[QuestionBoundPrediction, int], ...]:
    errors = validate_question_prediction_journal(root)
    if errors:
        raise ExpertLearningSyncError("question prediction journal invalid: " + "; ".join(errors))
    output: List[Tuple[QuestionBoundPrediction, int]] = []
    for entry in QuestionPredictionJournal(root).entries():
        prediction = QuestionBoundPrediction.from_wire(entry.get("prediction", {}))
        output.append((prediction, int(entry["journaled_at_ns"])))
    return tuple(output)


def _outcomes(root: Path) -> Dict[str, QuestionBoundOutcome]:
    errors = validate_question_outcome_journal(root)
    if errors:
        raise ExpertLearningSyncError("question outcome journal invalid: " + "; ".join(errors))
    output: Dict[str, QuestionBoundOutcome] = {}
    for entry in QuestionOutcomeJournal(root).entries():
        outcome = QuestionBoundOutcome.from_wire(entry.get("outcome", {}))
        if outcome.prediction_id in output:
            raise ExpertLearningSyncError("prediction has duplicate question outcomes")
        output[outcome.prediction_id] = outcome
    return output


def _resolved_value(outcome: QuestionBoundOutcome) -> Any:
    if outcome.status != "RESOLVED" or outcome.realized_answer is None:
        raise ExpertLearningSyncError("cannot score an unresolved question outcome")
    if outcome.answer_kind in {"BINARY", "CONTINUOUS", "CATEGORICAL"}:
        return outcome.realized_answer.get("value")
    raise ExpertLearningSyncError("distribution outcome scoring requires a dedicated scoring contract")


def _matching_contracts(prediction: QuestionBoundPrediction) -> Tuple[Mapping[str, Any], ...]:
    prediction_models = set(str(ref) for ref in prediction.model_refs)
    matches = []
    for contract in implemented_baseline_expert_contracts():
        if prediction.question_ref not in contract["question_refs"]:
            continue
        contract_models = set(str(ref) for ref in contract["model_refs"])
        if prediction_models and prediction_models.issubset(contract_models):
            matches.append(contract)
    return tuple(matches)


def sync_expert_learning(root: Path, *, known_at_ns: int) -> Mapping[str, Any]:
    """Project already-durable question evidence into earned Expert School state.

    The sync never creates predictions or outcomes. It only consumes validated
    append-only journals that were knowable by ``known_at_ns``, adapts model
    claims into expert claims, scores them when a resolver has produced a final
    outcome by that same knowledge cutoff, and reconstructs competence from the
    resulting immutable score history.
    """
    root = root.resolve()
    known_at = int(known_at_ns)
    if known_at < 0:
        raise ExpertLearningSyncError("known_at_ns must be non-negative")
    all_predictions = _prediction_entries(root)
    all_outcomes = _outcomes(root)
    predictions = tuple((prediction, journaled) for prediction, journaled in all_predictions if journaled <= known_at)
    outcomes = {prediction_id: outcome for prediction_id, outcome in all_outcomes.items() if outcome.decided_at_ns <= known_at}
    runtime = IntelligenceRuntime(root)
    runtime_errors = validate_event_chain(runtime.events())
    if runtime_errors:
        raise ExpertLearningSyncError("expert intelligence journal invalid: " + "; ".join(runtime_errors))

    state = runtime.state()
    known_claim_hashes = set(str(value) for value in (state.get("claims") or {}).keys())
    known_score_claim_hashes = {
        str(item.get("claim_hash")) for item in (state.get("scores") or []) if isinstance(item, Mapping)
    }
    claims_recorded = 0
    scores_recorded = 0
    skipped_unimplemented = 0
    unresolved = 0
    unresolvable = 0
    adapted: Dict[str, List[Tuple[Mapping[str, Any], Mapping[str, Any]]]] = {}

    for prediction, journaled_at_ns in predictions:
        contracts = _matching_contracts(prediction)
        if not contracts:
            skipped_unimplemented += 1
            continue
        for contract in contracts:
            claim = question_prediction_to_expert_claim(contract, prediction)
            claim_hash = str(claim["integrity"]["content_hash"])
            adapted.setdefault(prediction.prediction_id, []).append((contract, claim))
            if claim_hash not in known_claim_hashes:
                runtime.record_claim(contract, claim, occurred_at_ns=journaled_at_ns)
                known_claim_hashes.add(claim_hash)
                claims_recorded += 1

    for prediction_id, pairs in adapted.items():
        outcome = outcomes.get(prediction_id)
        if outcome is None:
            unresolved += 1
            continue
        if outcome.status == "UNRESOLVABLE":
            unresolvable += 1
            continue
        realized = _resolved_value(outcome)
        for contract, claim in pairs:
            claim_hash = str(claim["integrity"]["content_hash"])
            if claim_hash in known_score_claim_hashes:
                continue
            score = score_expert_claim(
                contract,
                claim,
                realized,
                resolved_at_ns=outcome.decided_at_ns,
                context={"subject_id": outcome.subject_id},
            )
            runtime.record_score(score, occurred_at_ns=outcome.decided_at_ns)
            known_score_claim_hashes.add(claim_hash)
            scores_recorded += 1

    final_state = runtime.state()
    competence = final_state.get("competence")
    if final_state.get("scores") and (scores_recorded > 0 or competence is None):
        competence = runtime.rebuild_competence(known_at_ns=known_at)
        final_state = runtime.state()

    return {
        "status": "OK",
        "known_at_ns": known_at,
        "journal_prediction_count": len(all_predictions),
        "journal_outcome_count": len(all_outcomes),
        "eligible_prediction_count": len(predictions),
        "eligible_outcome_count": len(outcomes),
        "claims_recorded": claims_recorded,
        "scores_recorded": scores_recorded,
        "skipped_unimplemented_predictions": skipped_unimplemented,
        "awaiting_outcome_predictions": unresolved,
        "unresolvable_predictions": unresolvable,
        "competence_entry_count": 0 if competence is None else int(competence.get("entry_count", 0)),
        "runtime_event_count": int(final_state.get("event_count", 0)),
        "capital_authority": "NONE",
    }
