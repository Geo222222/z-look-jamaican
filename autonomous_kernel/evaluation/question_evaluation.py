from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping

from ..operations import canonical_hash
from ..prediction.question_expert import QuestionExpertPrediction
from ..questions.contracts import AnswerKind
from .question_outcome import QuestionBoundOutcome

QUESTION_EVALUATION_SCHEMA_VERSION = "1.0"
QUESTION_EVALUATION_STATUSES = {"SCORED", "NOT_SCORABLE_UNRESOLVABLE"}
SCORING_POLICY_BY_ANSWER_KIND = {
    AnswerKind.BINARY.value: "BINARY_EXACT_AND_BRIER_V1",
    AnswerKind.CONTINUOUS.value: "CONTINUOUS_ERROR_AND_INTERVAL_V1",
    AnswerKind.CATEGORICAL.value: "CATEGORICAL_EXACT_V1",
}
UNRESOLVABLE_SCORING_POLICY_ID = "NO_SCORE_UNRESOLVABLE_V1"


class QuestionEvaluationError(ValueError):
    pass


def _digest(value: str, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise QuestionEvaluationError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise QuestionEvaluationError("%s must be hexadecimal" % field) from exc
    return text


def _decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise QuestionEvaluationError(
            "%s must be decimal-compatible" % field
        ) from exc
    if not parsed.is_finite():
        raise QuestionEvaluationError("%s must be finite" % field)
    return parsed


def _text(value: Decimal) -> str:
    return format(value, "f")


def _binary_metrics(
    prediction_answer: Mapping[str, Any],
    realized_answer: Mapping[str, Any],
) -> Dict[str, Any]:
    predicted = int(prediction_answer["value"])
    realized = int(realized_answer["value"])
    probability_raw = prediction_answer.get("probability_1")
    probability = (
        None
        if probability_raw is None
        else _decimal(probability_raw, "probability_1")
    )
    brier = None if probability is None else (probability - Decimal(realized)) ** 2
    return {
        "predicted_value": predicted,
        "realized_value": realized,
        "exact_hit": 1 if predicted == realized else 0,
        "probability_1": None if probability is None else _text(probability),
        "brier_score": None if brier is None else _text(brier),
    }


def _continuous_metrics(
    prediction_answer: Mapping[str, Any],
    realized_answer: Mapping[str, Any],
) -> Dict[str, Any]:
    predicted = _decimal(prediction_answer["value"], "predicted continuous value")
    realized = _decimal(realized_answer["value"], "realized continuous value")
    error = predicted - realized
    low_raw = prediction_answer.get("interval_low")
    high_raw = prediction_answer.get("interval_high")
    interval_covered = None
    if low_raw is not None or high_raw is not None:
        if low_raw is None or high_raw is None:
            raise QuestionEvaluationError(
                "continuous prediction interval is incomplete"
            )
        low = _decimal(low_raw, "interval_low")
        high = _decimal(high_raw, "interval_high")
        if low > high:
            raise QuestionEvaluationError(
                "continuous prediction interval is invalid"
            )
        interval_covered = 1 if low <= realized <= high else 0
    return {
        "predicted_value": _text(predicted),
        "realized_value": _text(realized),
        "signed_error": _text(error),
        "absolute_error": _text(abs(error)),
        "squared_error": _text(error * error),
        "interval_covered": interval_covered,
    }


def _categorical_metrics(
    prediction_answer: Mapping[str, Any],
    realized_answer: Mapping[str, Any],
) -> Dict[str, Any]:
    predicted = str(prediction_answer["value"])
    realized = str(realized_answer["value"])
    probabilities = prediction_answer.get("probabilities")
    realized_probability = None
    if probabilities is not None:
        if not isinstance(probabilities, Mapping):
            raise QuestionEvaluationError(
                "categorical prediction probabilities are malformed"
            )
        if realized in probabilities:
            realized_probability = _text(
                _decimal(
                    probabilities[realized],
                    "realized categorical probability",
                )
            )
    return {
        "predicted_label": predicted,
        "realized_label": realized,
        "exact_hit": 1 if predicted == realized else 0,
        "realized_probability": realized_probability,
    }


def _score(
    answer_kind: str,
    prediction_answer: Mapping[str, Any],
    realized_answer: Mapping[str, Any],
) -> Dict[str, Any]:
    if answer_kind == AnswerKind.BINARY.value:
        return _binary_metrics(prediction_answer, realized_answer)
    if answer_kind == AnswerKind.CONTINUOUS.value:
        return _continuous_metrics(prediction_answer, realized_answer)
    if answer_kind == AnswerKind.CATEGORICAL.value:
        return _categorical_metrics(prediction_answer, realized_answer)
    if answer_kind == AnswerKind.DISTRIBUTION.value:
        raise QuestionEvaluationError(
            "distribution questions require a question-specific scoring policy"
        )
    raise QuestionEvaluationError("unsupported answer kind")


@dataclass(frozen=True)
class QuestionBoundEvaluation:
    evaluation_id: str
    prediction_id: str
    prediction_content_hash: str
    expert_prediction_content_hash: str
    expert_prediction_journal_entry_hash: str
    expert_definition_ref: str
    expert_definition_hash: str
    expert_registry_hash: str
    outcome_id: str
    outcome_content_hash: str
    outcome_journal_entry_hash: str
    question_ref: str
    question_definition_hash: str
    question_registry_hash: str
    subject_id: str
    answer_kind: str
    horizon_ns: int
    status: str
    scoring_policy_id: str
    evaluated_at_ns: int
    metrics: Mapping[str, Any]
    schema_version: str = QUESTION_EVALUATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != QUESTION_EVALUATION_SCHEMA_VERSION:
            raise QuestionEvaluationError(
                "unsupported question evaluation schema"
            )
        if not self.evaluation_id or any(
            ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for ch in self.evaluation_id
        ):
            raise QuestionEvaluationError(
                "evaluation_id must be non-empty and file-safe"
            )
        for field in (
            "prediction_id",
            "expert_definition_ref",
            "outcome_id",
            "question_ref",
            "subject_id",
            "answer_kind",
            "scoring_policy_id",
        ):
            if not str(getattr(self, field)).strip():
                raise QuestionEvaluationError("%s is required" % field)
        for field in (
            "prediction_content_hash",
            "expert_prediction_content_hash",
            "expert_prediction_journal_entry_hash",
            "expert_definition_hash",
            "expert_registry_hash",
            "outcome_content_hash",
            "outcome_journal_entry_hash",
            "question_definition_hash",
            "question_registry_hash",
        ):
            _digest(str(getattr(self, field)), field)
        if self.expert_definition_ref.rsplit("#", 1)[-1] != self.expert_definition_hash:
            raise QuestionEvaluationError(
                "expert definition ref/hash mismatch"
            )
        if self.horizon_ns <= 0 or self.evaluated_at_ns < 0:
            raise QuestionEvaluationError("evaluation timing is invalid")
        if self.status not in QUESTION_EVALUATION_STATUSES:
            raise QuestionEvaluationError("evaluation status is invalid")
        if not isinstance(self.metrics, Mapping):
            raise QuestionEvaluationError("evaluation metrics must be a mapping")

        if self.status == "NOT_SCORABLE_UNRESOLVABLE":
            if self.scoring_policy_id != UNRESOLVABLE_SCORING_POLICY_ID:
                raise QuestionEvaluationError(
                    "unresolvable evaluation scoring policy is invalid"
                )
            if self.metrics:
                raise QuestionEvaluationError(
                    "unresolvable evaluation cannot claim score metrics"
                )
        else:
            expected_policy = SCORING_POLICY_BY_ANSWER_KIND.get(self.answer_kind)
            if expected_policy is None:
                raise QuestionEvaluationError(
                    "answer kind has no generic scoring policy"
                )
            if self.scoring_policy_id != expected_policy:
                raise QuestionEvaluationError(
                    "evaluation scoring policy differs from answer kind"
                )
            if not self.metrics:
                raise QuestionEvaluationError(
                    "scored evaluation requires metrics"
                )

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evaluation_id": self.evaluation_id,
            "prediction": {
                "prediction_id": self.prediction_id,
                "content_hash": self.prediction_content_hash,
                "expert_prediction_content_hash": self.expert_prediction_content_hash,
                "expert_prediction_journal_entry_hash": self.expert_prediction_journal_entry_hash,
            },
            "expert": {
                "definition_ref": self.expert_definition_ref,
                "definition_hash": self.expert_definition_hash,
                "registry_hash": self.expert_registry_hash,
            },
            "outcome": {
                "outcome_id": self.outcome_id,
                "content_hash": self.outcome_content_hash,
                "journal_entry_hash": self.outcome_journal_entry_hash,
            },
            "question": {
                "question_ref": self.question_ref,
                "definition_hash": self.question_definition_hash,
                "registry_hash": self.question_registry_hash,
                "subject_id": self.subject_id,
                "answer_kind": self.answer_kind,
                "horizon_ns": self.horizon_ns,
            },
            "status": self.status,
            "scoring_policy_id": self.scoring_policy_id,
            "evaluated_at_ns": self.evaluated_at_ns,
            "metrics": dict(self.metrics),
            "authority": {
                "model_evaluation_only": True,
                "market_truth_mutation": False,
                "model_competence": False,
                "adaptive_weighting": False,
                "capital_confidence": False,
                "capital_decision": False,
                "risk_authorization": False,
                "external_execution": False,
            },
        }

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        value = self.body()
        value["integrity"] = {
            "algorithm": "sha256",
            "content_hash": self.content_hash(),
        }
        return value

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "QuestionBoundEvaluation":
        prediction = value.get("prediction")
        expert = value.get("expert")
        outcome = value.get("outcome")
        question = value.get("question")
        if not all(
            isinstance(item, Mapping)
            for item in (prediction, expert, outcome, question)
        ):
            raise QuestionEvaluationError(
                "question evaluation envelope is malformed"
            )
        metrics = value.get("metrics")
        if not isinstance(metrics, Mapping):
            raise QuestionEvaluationError("evaluation metrics are malformed")
        item = cls(
            schema_version=str(value.get("schema_version", "")),
            evaluation_id=str(value.get("evaluation_id", "")),
            prediction_id=str(prediction.get("prediction_id", "")),
            prediction_content_hash=str(prediction.get("content_hash", "")),
            expert_prediction_content_hash=str(
                prediction.get("expert_prediction_content_hash", "")
            ),
            expert_prediction_journal_entry_hash=str(
                prediction.get("expert_prediction_journal_entry_hash", "")
            ),
            expert_definition_ref=str(expert.get("definition_ref", "")),
            expert_definition_hash=str(expert.get("definition_hash", "")),
            expert_registry_hash=str(expert.get("registry_hash", "")),
            outcome_id=str(outcome.get("outcome_id", "")),
            outcome_content_hash=str(outcome.get("content_hash", "")),
            outcome_journal_entry_hash=str(
                outcome.get("journal_entry_hash", "")
            ),
            question_ref=str(question.get("question_ref", "")),
            question_definition_hash=str(
                question.get("definition_hash", "")
            ),
            question_registry_hash=str(question.get("registry_hash", "")),
            subject_id=str(question.get("subject_id", "")),
            answer_kind=str(question.get("answer_kind", "")),
            horizon_ns=int(question.get("horizon_ns", -1)),
            status=str(value.get("status", "")),
            scoring_policy_id=str(value.get("scoring_policy_id", "")),
            evaluated_at_ns=int(value.get("evaluated_at_ns", -1)),
            metrics=dict(metrics),
        )
        expected_authority = {
            "model_evaluation_only": True,
            "market_truth_mutation": False,
            "model_competence": False,
            "adaptive_weighting": False,
            "capital_confidence": False,
            "capital_decision": False,
            "risk_authorization": False,
            "external_execution": False,
        }
        authority = value.get("authority")
        if not isinstance(authority, Mapping) or dict(authority) != expected_authority:
            raise QuestionEvaluationError(
                "question evaluation authority boundary is invalid"
            )
        integrity = value.get("integrity")
        if (
            not isinstance(integrity, Mapping)
            or integrity.get("algorithm") != "sha256"
            or integrity.get("content_hash") != item.content_hash()
        ):
            raise QuestionEvaluationError(
                "question evaluation content hash mismatch"
            )
        return item


def _validate_lineage(
    expert_prediction: QuestionExpertPrediction,
    outcome: QuestionBoundOutcome,
) -> None:
    prediction = expert_prediction.prediction
    checks = (
        (
            outcome.prediction_id == prediction.prediction_id,
            "outcome prediction_id mismatch",
        ),
        (
            outcome.prediction_content_hash == prediction.content_hash(),
            "outcome prediction content hash mismatch",
        ),
        (
            outcome.question_ref == prediction.question_ref,
            "outcome question ref mismatch",
        ),
        (
            outcome.question_definition_hash
            == prediction.question_definition_hash,
            "outcome question definition hash mismatch",
        ),
        (
            outcome.question_registry_hash == prediction.question_registry_hash,
            "outcome question registry hash mismatch",
        ),
        (
            outcome.subject_id == prediction.subject_id,
            "outcome subject mismatch",
        ),
        (
            outcome.answer_kind == prediction.answer_kind,
            "outcome answer kind mismatch",
        ),
        (
            outcome.outcome_metric_id == prediction.outcome_metric_id,
            "outcome metric mismatch",
        ),
        (
            outcome.resolver_policy_id == prediction.resolver_policy_id,
            "outcome resolver policy mismatch",
        ),
        (
            outcome.cutoff_at_ns == prediction.cutoff_at_ns,
            "outcome cutoff mismatch",
        ),
        (
            outcome.target_resolves_at_ns == prediction.resolves_at_ns,
            "outcome target horizon mismatch",
        ),
        (
            outcome.max_resolution_lag_ns
            == prediction.max_resolution_lag_ns,
            "outcome resolution lag mismatch",
        ),
    )
    failed = [message for passed, message in checks if not passed]
    if failed:
        raise QuestionEvaluationError("; ".join(failed))


def build_question_evaluation(
    *,
    expert_prediction: QuestionExpertPrediction,
    expert_prediction_journal_entry_hash: str,
    outcome: QuestionBoundOutcome,
    outcome_journal_entry_hash: str,
    evaluated_at_ns: int,
) -> QuestionBoundEvaluation:
    _digest(
        expert_prediction_journal_entry_hash,
        "expert_prediction_journal_entry_hash",
    )
    _digest(outcome_journal_entry_hash, "outcome_journal_entry_hash")
    evaluated = int(evaluated_at_ns)
    if evaluated < outcome.decided_at_ns:
        raise QuestionEvaluationError(
            "evaluation cannot predate mechanical outcome"
        )
    _validate_lineage(expert_prediction, outcome)

    prediction = expert_prediction.prediction
    if outcome.status == "UNRESOLVABLE":
        status = "NOT_SCORABLE_UNRESOLVABLE"
        scoring_policy_id = UNRESOLVABLE_SCORING_POLICY_ID
        metrics: Mapping[str, Any] = {}
    else:
        if outcome.realized_answer is None:
            raise QuestionEvaluationError(
                "resolved outcome lacks realized answer"
            )
        scoring_policy_id = SCORING_POLICY_BY_ANSWER_KIND.get(
            prediction.answer_kind, ""
        )
        if not scoring_policy_id:
            raise QuestionEvaluationError(
                "answer kind has no generic scoring policy"
            )
        metrics = _score(
            prediction.answer_kind,
            prediction.answer,
            outcome.realized_answer,
        )
        status = "SCORED"

    material = {
        "expert_prediction_hash": expert_prediction.content_hash(),
        "expert_prediction_journal_entry_hash": expert_prediction_journal_entry_hash,
        "outcome_hash": outcome.content_hash(),
        "outcome_journal_entry_hash": outcome_journal_entry_hash,
        "scoring_policy_id": scoring_policy_id,
    }
    evaluation_id = "QEVAL-%s" % hashlib.sha256(
        canonical_hash(material).encode("utf-8")
    ).hexdigest()[:32]

    return QuestionBoundEvaluation(
        evaluation_id=evaluation_id,
        prediction_id=prediction.prediction_id,
        prediction_content_hash=prediction.content_hash(),
        expert_prediction_content_hash=expert_prediction.content_hash(),
        expert_prediction_journal_entry_hash=str(
            expert_prediction_journal_entry_hash
        ),
        expert_definition_ref=expert_prediction.expert_definition_ref,
        expert_definition_hash=expert_prediction.expert_definition_hash,
        expert_registry_hash=expert_prediction.expert_registry_hash,
        outcome_id=outcome.outcome_id,
        outcome_content_hash=outcome.content_hash(),
        outcome_journal_entry_hash=str(outcome_journal_entry_hash),
        question_ref=prediction.question_ref,
        question_definition_hash=prediction.question_definition_hash,
        question_registry_hash=prediction.question_registry_hash,
        subject_id=prediction.subject_id,
        answer_kind=prediction.answer_kind,
        horizon_ns=prediction.horizon_ns,
        status=status,
        scoring_policy_id=scoring_policy_id,
        evaluated_at_ns=evaluated,
        metrics=metrics,
    )
