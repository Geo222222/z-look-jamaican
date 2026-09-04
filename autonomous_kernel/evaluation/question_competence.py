"""Question-bound expert competence derived from prospective learning journals.

Outcome truth remains owned by QuestionBoundOutcome. This module scores expert
answers against those immutable outcomes without changing expert lifecycle or
claiming qualification. Metrics are always labeled and sample counts explicit.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from ..operations import canonical_hash
from ..prediction.question_bound import QuestionBoundPrediction
from ..prediction.question_journal import QuestionPredictionJournal, validate_question_prediction_journal
from .question_journal import QuestionOutcomeJournal, validate_question_outcome_journal
from .question_outcome import QuestionBoundOutcome


QUESTION_COMPETENCE_SCHEMA_VERSION = "1.0"


class QuestionCompetenceError(RuntimeError):
    pass


def _mean(values: Sequence[Decimal]) -> Decimal:
    return Decimal("0") if not values else sum(values, Decimal("0")) / Decimal(len(values))


def _text(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True)
class QuestionExpertCompetenceProfile:
    expert_ref: str
    question_ref: str
    subject_id: str
    answer_kind: str
    evidence_class: str
    as_of_ns: int
    prediction_count: int
    resolved_count: int
    unresolvable_count: int
    pending_count: int
    metrics: Mapping[str, Any]
    prediction_hashes: Tuple[str, ...]
    outcome_hashes: Tuple[str, ...]
    schema_version: str = QUESTION_COMPETENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != QUESTION_COMPETENCE_SCHEMA_VERSION:
            raise QuestionCompetenceError("unsupported question competence schema")
        for field in ("expert_ref", "question_ref", "subject_id", "answer_kind", "evidence_class"):
            if not str(getattr(self, field)).strip():
                raise QuestionCompetenceError("%s is required" % field)
        if self.as_of_ns < 0:
            raise QuestionCompetenceError("as_of_ns must be non-negative")
        if min(self.prediction_count, self.resolved_count, self.unresolvable_count, self.pending_count) < 0:
            raise QuestionCompetenceError("competence counts cannot be negative")
        if self.resolved_count + self.unresolvable_count + self.pending_count != self.prediction_count:
            raise QuestionCompetenceError("question competence outcome counts must partition predictions")
        if len(self.prediction_hashes) != self.prediction_count:
            raise QuestionCompetenceError("prediction lineage count mismatch")
        if len(self.outcome_hashes) != self.resolved_count + self.unresolvable_count:
            raise QuestionCompetenceError("outcome lineage count mismatch")

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "identity": {
                "expert_ref": self.expert_ref,
                "question_ref": self.question_ref,
                "subject_id": self.subject_id,
                "answer_kind": self.answer_kind,
                "evidence_class": self.evidence_class,
            },
            "as_of_ns": self.as_of_ns,
            "counts": {
                "prediction_count": self.prediction_count,
                "resolved_count": self.resolved_count,
                "unresolvable_count": self.unresolvable_count,
                "pending_count": self.pending_count,
            },
            "metrics": dict(self.metrics),
            "lineage": {
                "prediction_hashes": list(self.prediction_hashes),
                "outcome_hashes": list(self.outcome_hashes),
                "prediction_set_hash": canonical_hash({"hashes": list(self.prediction_hashes)}),
                "outcome_set_hash": canonical_hash({"hashes": list(self.outcome_hashes)}),
            },
            "authority": {
                "expert_qualification": False,
                "capital_decision": False,
                "risk_authorization": False,
                "external_execution": False,
            },
        }

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        value = self.body()
        value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}
        return value


def _binary_metrics(pairs: Sequence[Tuple[QuestionBoundPrediction, QuestionBoundOutcome]]) -> Mapping[str, Any]:
    if not pairs:
        return {"primary_metric": "directional_accuracy", "directional_accuracy": None, "brier_score": None}
    hits: List[Decimal] = []
    briers: List[Decimal] = []
    probability_count = 0
    for prediction, outcome in pairs:
        actual = int(outcome.realized_answer["value"])
        predicted = int(prediction.answer["value"])
        hits.append(Decimal("1") if predicted == actual else Decimal("0"))
        if prediction.answer.get("probability_1") is not None:
            probability = Decimal(str(prediction.answer["probability_1"]))
            briers.append((probability - Decimal(actual)) ** 2)
            probability_count += 1
    return {
        "primary_metric": "directional_accuracy",
        "directional_accuracy": _text(_mean(hits)),
        "brier_score": None if not briers else _text(_mean(briers)),
        "probability_sample_count": probability_count,
    }


def _continuous_metrics(pairs: Sequence[Tuple[QuestionBoundPrediction, QuestionBoundOutcome]]) -> Mapping[str, Any]:
    if not pairs:
        return {"primary_metric": "mean_absolute_error", "mean_absolute_error": None, "root_mean_squared_error": None, "mean_bias": None}
    errors: List[Decimal] = []
    for prediction, outcome in pairs:
        expected = Decimal(str(prediction.answer["value"]))
        actual = Decimal(str(outcome.realized_answer["value"]))
        errors.append(actual - expected)
    abs_errors = [abs(value) for value in errors]
    squares = [value * value for value in errors]
    return {
        "primary_metric": "mean_absolute_error",
        "mean_absolute_error": _text(_mean(abs_errors)),
        "root_mean_squared_error": _text(_mean(squares).sqrt()),
        "mean_bias": _text(_mean(errors)),
    }


def build_question_expert_competence(root: Path, *, as_of_ns: int) -> Tuple[QuestionExpertCompetenceProfile, ...]:
    root = root.resolve()
    if as_of_ns < 0:
        raise QuestionCompetenceError("as_of_ns must be non-negative")
    prediction_errors = validate_question_prediction_journal(root)
    outcome_errors = validate_question_outcome_journal(root)
    if prediction_errors or outcome_errors:
        raise QuestionCompetenceError("question learning journals invalid: " + "; ".join(prediction_errors + outcome_errors))

    predictions: Dict[str, QuestionBoundPrediction] = {}
    for entry in QuestionPredictionJournal(root).entries():
        prediction = QuestionBoundPrediction.from_wire(entry["prediction"])
        if prediction.created_at_ns <= as_of_ns:
            predictions[prediction.prediction_id] = prediction
    outcomes: Dict[str, QuestionBoundOutcome] = {}
    for entry in QuestionOutcomeJournal(root).entries():
        outcome = QuestionBoundOutcome.from_wire(entry["outcome"])
        if outcome.decided_at_ns <= as_of_ns:
            outcomes[outcome.prediction_id] = outcome

    groups: Dict[Tuple[str, str, str, str, str], List[QuestionBoundPrediction]] = {}
    for prediction in predictions.values():
        if len(prediction.model_refs) != 1:
            continue
        key = (prediction.model_refs[0], prediction.question_ref, prediction.subject_id, prediction.answer_kind, prediction.evidence_class)
        groups.setdefault(key, []).append(prediction)

    profiles = []
    for key in sorted(groups):
        expert_ref, question_ref, subject_id, answer_kind, evidence_class = key
        group = sorted(groups[key], key=lambda item: (item.cutoff_at_ns, item.created_at_ns, item.prediction_id))
        final_outcomes: List[QuestionBoundOutcome] = []
        resolved_pairs: List[Tuple[QuestionBoundPrediction, QuestionBoundOutcome]] = []
        unresolvable = 0
        for prediction in group:
            outcome = outcomes.get(prediction.prediction_id)
            if outcome is None:
                continue
            final_outcomes.append(outcome)
            if outcome.status == "RESOLVED":
                resolved_pairs.append((prediction, outcome))
            else:
                unresolvable += 1
        metrics = _binary_metrics(resolved_pairs) if answer_kind == "BINARY" else _continuous_metrics(resolved_pairs)
        profiles.append(QuestionExpertCompetenceProfile(
            expert_ref=expert_ref,
            question_ref=question_ref,
            subject_id=subject_id,
            answer_kind=answer_kind,
            evidence_class=evidence_class,
            as_of_ns=int(as_of_ns),
            prediction_count=len(group),
            resolved_count=len(resolved_pairs),
            unresolvable_count=unresolvable,
            pending_count=len(group) - len(final_outcomes),
            metrics=metrics,
            prediction_hashes=tuple(item.content_hash() for item in group),
            outcome_hashes=tuple(item.content_hash() for item in sorted(final_outcomes, key=lambda value: value.prediction_id)),
        ))
    return tuple(profiles)


def persist_question_expert_competence(root: Path, *, as_of_ns: int) -> Mapping[str, Any]:
    root = root.resolve()
    profiles = build_question_expert_competence(root, as_of_ns=as_of_ns)
    body = {
        "schema_version": 1,
        "contract_version": QUESTION_COMPETENCE_SCHEMA_VERSION,
        "as_of_ns": int(as_of_ns),
        "profiles": [profile.to_wire() for profile in profiles],
        "authority": "measured question-bound performance only; does not promote expert lifecycle or grant capital authority",
    }
    value = dict(body)
    value["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
    path = root / "state/question_expert_competence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return value
