from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Sequence, Tuple

from ..experience.contracts import ExperienceTimescale
from ..operations import canonical_hash
from ..questions.contracts import AnswerKind, QuestionDefinition, QuestionRegistrySnapshot

QUESTION_PREDICTION_SCHEMA_VERSION = "1.0"
QUESTION_PREDICTION_MODES = {"PROSPECTIVE_SHADOW", "HISTORICAL_REPLAY"}
QUESTION_PREDICTION_EVIDENCE_CLASSES = {"FORWARD_EVALUABLE", "RESEARCH_ONLY"}
ARTIFACT_STATUSES = {"QUALIFIED", "DEGRADED"}

class QuestionPredictionError(ValueError):
    pass

def _digest(value: str, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise QuestionPredictionError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise QuestionPredictionError("%s must be hexadecimal" % field) from exc
    return text

def _decimal_text(value: Any, field: str) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise QuestionPredictionError("%s must be decimal-compatible" % field) from exc
    if not number.is_finite():
        raise QuestionPredictionError("%s must be finite" % field)
    return format(number, "f")

def _unique_strings(values: Sequence[str], field: str, *, allow_empty: bool = False) -> Tuple[str, ...]:
    result = tuple(str(value) for value in values)
    if (not result and not allow_empty) or any(not value for value in result):
        raise QuestionPredictionError("%s must contain non-empty values" % field)
    if len(set(result)) != len(result):
        raise QuestionPredictionError("%s must contain unique values" % field)
    return result

def normalize_question_answer(answer_kind: AnswerKind, answer: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(answer, Mapping):
        raise QuestionPredictionError("answer must be a mapping")
    value = answer.get("value")
    normalized: Dict[str, Any] = {}
    if answer_kind is AnswerKind.BINARY:
        if value not in (0, 1):
            raise QuestionPredictionError("binary answer value must be 0 or 1")
        normalized["value"] = int(value)
        if answer.get("probability_1") is not None:
            probability = Decimal(_decimal_text(answer.get("probability_1"), "probability_1"))
            if probability < 0 or probability > 1:
                raise QuestionPredictionError("probability_1 must be between 0 and 1")
            normalized["probability_1"] = format(probability, "f")
    elif answer_kind is AnswerKind.CONTINUOUS:
        normalized["value"] = _decimal_text(value, "continuous answer value")
        low, high = answer.get("interval_low"), answer.get("interval_high")
        if (low is None) != (high is None):
            raise QuestionPredictionError("continuous interval requires low and high")
        if low is not None:
            low_text = _decimal_text(low, "interval_low")
            high_text = _decimal_text(high, "interval_high")
            if Decimal(low_text) > Decimal(high_text):
                raise QuestionPredictionError("interval_low cannot exceed interval_high")
            normalized.update({"interval_low": low_text, "interval_high": high_text})
    elif answer_kind is AnswerKind.CATEGORICAL:
        text = str(value).strip()
        if not text:
            raise QuestionPredictionError("categorical answer value is required")
        normalized["value"] = text
        probabilities = answer.get("probabilities")
        if probabilities is not None:
            if not isinstance(probabilities, Mapping) or not probabilities:
                raise QuestionPredictionError("categorical probabilities must be a non-empty mapping")
            parsed: Dict[str, str] = {}
            total = Decimal("0")
            for key, raw in probabilities.items():
                label = str(key).strip()
                if not label:
                    raise QuestionPredictionError("categorical probability label is required")
                probability = Decimal(_decimal_text(raw, "categorical probability"))
                if probability < 0 or probability > 1:
                    raise QuestionPredictionError("categorical probability must be between 0 and 1")
                parsed[label] = format(probability, "f")
                total += probability
            if total > Decimal("1.0000000001"):
                raise QuestionPredictionError("categorical probabilities cannot sum above 1")
            normalized["probabilities"] = dict(sorted(parsed.items()))
    elif answer_kind is AnswerKind.DISTRIBUTION:
        summary = answer.get("summary")
        if not isinstance(summary, Mapping) or not summary:
            raise QuestionPredictionError("distribution answer requires summary")
        normalized = {"value": None, "summary": dict(summary)}
    else:
        raise QuestionPredictionError("unsupported answer kind")
    return normalized

@dataclass(frozen=True)
class PredictionArtifactRef:
    artifact_type: str
    artifact_id: str
    content_hash: str
    known_at_ns: int
    status: str
    timescales: Tuple[ExperienceTimescale, ...]
    feature_families: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.artifact_type or not self.artifact_id:
            raise QuestionPredictionError("artifact identity is required")
        _digest(self.content_hash, "artifact content_hash")
        if self.known_at_ns < 0:
            raise QuestionPredictionError("artifact known_at_ns must be non-negative")
        if self.status not in ARTIFACT_STATUSES:
            raise QuestionPredictionError("artifact status is invalid")
        if len(set(self.timescales)) != len(self.timescales):
            raise QuestionPredictionError("artifact timescales must be unique")
        _unique_strings(self.feature_families, "artifact feature_families", allow_empty=True)

    def to_wire(self) -> Dict[str, Any]:
        return {"artifact_type": self.artifact_type, "artifact_id": self.artifact_id, "content_hash": self.content_hash, "known_at_ns": self.known_at_ns, "status": self.status, "timescales": [item.value for item in self.timescales], "feature_families": list(self.feature_families)}

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "PredictionArtifactRef":
        return cls(artifact_type=str(value.get("artifact_type", "")), artifact_id=str(value.get("artifact_id", "")), content_hash=str(value.get("content_hash", "")), known_at_ns=int(value.get("known_at_ns", -1)), status=str(value.get("status", "")), timescales=tuple(ExperienceTimescale(str(item)) for item in value.get("timescales", [])), feature_families=tuple(str(item) for item in value.get("feature_families", [])))

@dataclass(frozen=True)
class QuestionBoundPrediction:
    prediction_id: str
    mode: str
    evidence_class: str
    question_ref: str
    question_definition_hash: str
    question_registry_id: str
    question_registry_version: str
    question_registry_hash: str
    question_family: str
    question_scope: str
    answer_kind: str
    cutoff_at_ns: int
    created_at_ns: int
    horizon_ns: int
    resolves_at_ns: int
    outcome_metric_id: str
    resolver_policy_id: str
    max_resolution_lag_ns: int
    answer: Mapping[str, Any]
    model_refs: Tuple[str, ...]
    artifact_refs: Tuple[PredictionArtifactRef, ...]
    schema_version: str = QUESTION_PREDICTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != QUESTION_PREDICTION_SCHEMA_VERSION:
            raise QuestionPredictionError("unsupported question-bound prediction schema")
        if not self.prediction_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in self.prediction_id):
            raise QuestionPredictionError("prediction_id must be non-empty and file-safe")
        if self.mode not in QUESTION_PREDICTION_MODES or self.evidence_class not in QUESTION_PREDICTION_EVIDENCE_CLASSES:
            raise QuestionPredictionError("prediction mode/evidence_class is invalid")
        for field in ("question_ref", "question_registry_id", "question_registry_version", "question_family", "question_scope", "answer_kind", "outcome_metric_id", "resolver_policy_id"):
            if not str(getattr(self, field)).strip():
                raise QuestionPredictionError("%s is required" % field)
        _digest(self.question_definition_hash, "question_definition_hash")
        _digest(self.question_registry_hash, "question_registry_hash")
        if self.cutoff_at_ns < 0 or self.created_at_ns < 0 or self.horizon_ns <= 0 or self.max_resolution_lag_ns < 0:
            raise QuestionPredictionError("prediction timing is invalid")
        if self.resolves_at_ns != self.cutoff_at_ns + self.horizon_ns:
            raise QuestionPredictionError("resolves_at_ns must equal cutoff_at_ns + horizon_ns")
        if self.mode == "PROSPECTIVE_SHADOW":
            if self.evidence_class != "FORWARD_EVALUABLE":
                raise QuestionPredictionError("prospective prediction must be FORWARD_EVALUABLE")
            if self.created_at_ns >= self.resolves_at_ns:
                raise QuestionPredictionError("prospective prediction must be created before resolution horizon")
            if any(ref.status != "QUALIFIED" for ref in self.artifact_refs):
                raise QuestionPredictionError("prospective prediction requires qualified artifact refs")
        elif self.evidence_class != "RESEARCH_ONLY":
            raise QuestionPredictionError("historical replay must be RESEARCH_ONLY")
        normalize_question_answer(AnswerKind(self.answer_kind), self.answer)
        _unique_strings(self.model_refs, "model_refs")
        if not self.artifact_refs:
            raise QuestionPredictionError("question-bound prediction requires artifact refs")
        ids = [ref.artifact_id for ref in self.artifact_refs]
        if len(ids) != len(set(ids)):
            raise QuestionPredictionError("artifact refs must have unique artifact_id values")
        if any(ref.known_at_ns > self.cutoff_at_ns for ref in self.artifact_refs):
            raise QuestionPredictionError("post-cutoff artifact rejected")
        if self.created_at_ns < max(ref.known_at_ns for ref in self.artifact_refs):
            raise QuestionPredictionError("prediction cannot be created before its evidence is knowable")

    def body(self) -> Dict[str, Any]:
        return {"schema_version": self.schema_version, "prediction_id": self.prediction_id, "mode": self.mode, "evidence_class": self.evidence_class, "question": {"question_ref": self.question_ref, "definition_hash": self.question_definition_hash, "family": self.question_family, "scope": self.question_scope, "answer_kind": self.answer_kind, "outcome_metric_id": self.outcome_metric_id, "resolver_policy_id": self.resolver_policy_id, "max_resolution_lag_ns": self.max_resolution_lag_ns}, "registry": {"registry_id": self.question_registry_id, "version": self.question_registry_version, "content_hash": self.question_registry_hash}, "timing": {"cutoff_at_ns": self.cutoff_at_ns, "created_at_ns": self.created_at_ns, "horizon_ns": self.horizon_ns, "resolves_at_ns": self.resolves_at_ns}, "answer": dict(self.answer), "model_refs": list(self.model_refs), "artifact_refs": [ref.to_wire() for ref in sorted(self.artifact_refs, key=lambda item: (item.artifact_type, item.artifact_id))], "authority": {"capital_decision": False, "risk_authorization": False, "external_execution": False}}

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        value = self.body(); value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}; return value

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "QuestionBoundPrediction":
        question, registry, timing = value.get("question"), value.get("registry"), value.get("timing")
        refs_raw = value.get("artifact_refs")
        if not isinstance(question, Mapping) or not isinstance(registry, Mapping) or not isinstance(timing, Mapping):
            raise QuestionPredictionError("question-bound prediction envelope is malformed")
        if not isinstance(refs_raw, Sequence) or isinstance(refs_raw, (str, bytes)):
            raise QuestionPredictionError("artifact_refs must be an array")
        answer = value.get("answer")
        if not isinstance(answer, Mapping):
            raise QuestionPredictionError("prediction answer is malformed")
        refs = tuple(PredictionArtifactRef.from_wire(item) for item in refs_raw if isinstance(item, Mapping))
        if len(refs) != len(refs_raw):
            raise QuestionPredictionError("artifact ref is malformed")
        item = cls(schema_version=str(value.get("schema_version", "")), prediction_id=str(value.get("prediction_id", "")), mode=str(value.get("mode", "")), evidence_class=str(value.get("evidence_class", "")), question_ref=str(question.get("question_ref", "")), question_definition_hash=str(question.get("definition_hash", "")), question_registry_id=str(registry.get("registry_id", "")), question_registry_version=str(registry.get("version", "")), question_registry_hash=str(registry.get("content_hash", "")), question_family=str(question.get("family", "")), question_scope=str(question.get("scope", "")), answer_kind=str(question.get("answer_kind", "")), cutoff_at_ns=int(timing.get("cutoff_at_ns", -1)), created_at_ns=int(timing.get("created_at_ns", -1)), horizon_ns=int(timing.get("horizon_ns", -1)), resolves_at_ns=int(timing.get("resolves_at_ns", -1)), outcome_metric_id=str(question.get("outcome_metric_id", "")), resolver_policy_id=str(question.get("resolver_policy_id", "")), max_resolution_lag_ns=int(question.get("max_resolution_lag_ns", -1)), answer=dict(answer), model_refs=tuple(str(ref) for ref in value.get("model_refs", [])), artifact_refs=refs)
        authority = value.get("authority")
        if not isinstance(authority, Mapping) or any(authority.get(key) is not False for key in ("capital_decision", "risk_authorization", "external_execution")):
            raise QuestionPredictionError("prediction authority boundary is invalid")
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("content_hash") != item.content_hash():
            raise QuestionPredictionError("question-bound prediction content hash mismatch")
        return item

def build_question_bound_prediction(*, registry: QuestionRegistrySnapshot, question: QuestionDefinition, mode: str, evidence_class: str, cutoff_at_ns: int, created_at_ns: int, answer: Mapping[str, Any], model_refs: Sequence[str], artifact_refs: Sequence[PredictionArtifactRef]) -> QuestionBoundPrediction:
    matching = [entry for entry in registry.entries if entry.definition.question_ref == question.question_ref]
    if len(matching) != 1:
        raise QuestionPredictionError("question is not uniquely present in registry")
    entry = matching[0]
    if entry.definition.content_hash() != question.content_hash():
        raise QuestionPredictionError("question definition differs from registry")
    if mode == "PROSPECTIVE_SHADOW":
        if entry.lifecycle_state not in {"RESOLVER_READY", "QUALIFIED"}:
            raise QuestionPredictionError("prospective prediction requires resolver-ready question")
        if registry.known_at_ns > cutoff_at_ns or entry.effective_at_ns > cutoff_at_ns:
            raise QuestionPredictionError("question registry was not knowable/effective at prospective cutoff")
    refs = tuple(artifact_refs)
    artifact_types = {ref.artifact_type for ref in refs}
    features = {family for ref in refs for family in ref.feature_families}
    timescales = {timescale for ref in refs for timescale in ref.timescales}
    if not set(question.required_artifact_types).issubset(artifact_types):
        raise QuestionPredictionError("required artifact type is missing")
    if not set(question.required_feature_families).issubset(features):
        raise QuestionPredictionError("required feature family is missing")
    if not set(question.required_timescales).issubset(timescales):
        raise QuestionPredictionError("required timescale is missing")
    if features.intersection(set(question.forbidden_feature_families)):
        raise QuestionPredictionError("forbidden feature family is present")
    if not features.issubset(set(question.allowed_feature_families)):
        raise QuestionPredictionError("artifact includes feature family outside question allowlist")
    if any(ref.known_at_ns > cutoff_at_ns for ref in refs):
        raise QuestionPredictionError("post-cutoff artifact rejected")
    normalized = normalize_question_answer(question.outcome.answer_kind, answer)
    models = _unique_strings(model_refs, "model_refs")
    material = {"registry_hash": registry.content_hash(), "question_hash": question.content_hash(), "cutoff_at_ns": int(cutoff_at_ns), "created_at_ns": int(created_at_ns), "mode": mode, "evidence_class": evidence_class, "answer": normalized, "model_refs": list(models), "artifact_refs": [ref.to_wire() for ref in sorted(refs, key=lambda item: (item.artifact_type, item.artifact_id))]}
    prediction_id = "QPRED-%s" % hashlib.sha256(canonical_hash(material).encode("utf-8")).hexdigest()[:32]
    return QuestionBoundPrediction(prediction_id=prediction_id, mode=mode, evidence_class=evidence_class, question_ref=question.question_ref, question_definition_hash=question.content_hash(), question_registry_id=registry.registry_id, question_registry_version=registry.version, question_registry_hash=registry.content_hash(), question_family=question.family.value, question_scope=question.scope.value, answer_kind=question.outcome.answer_kind.value, cutoff_at_ns=int(cutoff_at_ns), created_at_ns=int(created_at_ns), horizon_ns=question.horizon_ns, resolves_at_ns=int(cutoff_at_ns) + question.horizon_ns, outcome_metric_id=question.outcome.metric_id, resolver_policy_id=question.outcome.resolver_policy_id, max_resolution_lag_ns=question.outcome.max_resolution_lag_ns, answer=normalized, model_refs=models, artifact_refs=refs)
