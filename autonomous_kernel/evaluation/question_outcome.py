from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..operations import canonical_hash
from ..questions.contracts import AnswerKind

QUESTION_OUTCOME_SCHEMA_VERSION = "1.1"
QUESTION_OUTCOME_STATUSES = {"RESOLVED", "UNRESOLVABLE"}
RESOLUTION_EVIDENCE_ROLES = {"BASELINE", "FORWARD"}


class QuestionOutcomeError(ValueError):
    pass


def _digest(value: str, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise QuestionOutcomeError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise QuestionOutcomeError("%s must be hexadecimal" % field) from exc
    return text


def _decimal_text(value: Any, field: str) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise QuestionOutcomeError("%s must be decimal-compatible" % field) from exc
    if not number.is_finite():
        raise QuestionOutcomeError("%s must be finite" % field)
    return format(number, "f")


def _validate_realized_answer(answer_kind: AnswerKind, answer: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(answer, Mapping):
        raise QuestionOutcomeError("realized answer must be a mapping")
    value = answer.get("value")
    if answer_kind is AnswerKind.BINARY:
        if value not in (0, 1):
            raise QuestionOutcomeError("binary realized value must be 0 or 1")
        return {"value": int(value)}
    if answer_kind is AnswerKind.CONTINUOUS:
        return {"value": _decimal_text(value, "continuous realized value")}
    if answer_kind is AnswerKind.CATEGORICAL:
        text = str(value).strip()
        if not text:
            raise QuestionOutcomeError("categorical realized value is required")
        return {"value": text}
    if answer_kind is AnswerKind.DISTRIBUTION:
        summary = answer.get("summary")
        if not isinstance(summary, Mapping) or not summary:
            raise QuestionOutcomeError("distribution realized answer requires summary")
        return {"value": None, "summary": dict(summary)}
    raise QuestionOutcomeError("unsupported answer kind")


@dataclass(frozen=True)
class ResolutionEvidenceRef:
    evidence_family: str
    artifact_type: str
    artifact_id: str
    content_hash: str
    known_at_ns: int
    role: str
    subject_ids: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.evidence_family or not self.artifact_type or not self.artifact_id:
            raise QuestionOutcomeError("resolution evidence identity is required")
        _digest(self.content_hash, "resolution evidence content_hash")
        if self.known_at_ns < 0:
            raise QuestionOutcomeError("resolution evidence known_at_ns must be non-negative")
        if self.role not in RESOLUTION_EVIDENCE_ROLES:
            raise QuestionOutcomeError("resolution evidence role is invalid")
        if not self.subject_ids or any(not item for item in self.subject_ids) or len(set(self.subject_ids)) != len(self.subject_ids):
            raise QuestionOutcomeError("resolution evidence subject_ids must be unique non-empty values")

    def to_wire(self) -> Dict[str, Any]:
        return {"evidence_family": self.evidence_family, "artifact_type": self.artifact_type, "artifact_id": self.artifact_id, "content_hash": self.content_hash, "known_at_ns": self.known_at_ns, "role": self.role, "subject_ids": list(self.subject_ids)}

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "ResolutionEvidenceRef":
        return cls(evidence_family=str(value.get("evidence_family", "")), artifact_type=str(value.get("artifact_type", "")), artifact_id=str(value.get("artifact_id", "")), content_hash=str(value.get("content_hash", "")), known_at_ns=int(value.get("known_at_ns", -1)), role=str(value.get("role", "")), subject_ids=tuple(str(item) for item in value.get("subject_ids", [])))


@dataclass(frozen=True)
class QuestionBoundOutcome:
    outcome_id: str
    prediction_id: str
    prediction_content_hash: str
    prediction_journal_entry_hash: str
    question_ref: str
    question_definition_hash: str
    question_registry_hash: str
    subject_id: str
    answer_kind: str
    outcome_metric_id: str
    resolver_policy_id: str
    resolver_implementation_ref: str
    status: str
    cutoff_at_ns: int
    target_resolves_at_ns: int
    max_resolution_lag_ns: int
    decided_at_ns: int
    realized_answer: Optional[Mapping[str, Any]]
    resolution_evidence: Tuple[ResolutionEvidenceRef, ...]
    schema_version: str = QUESTION_OUTCOME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != QUESTION_OUTCOME_SCHEMA_VERSION:
            raise QuestionOutcomeError("unsupported question outcome schema")
        if not self.outcome_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in self.outcome_id):
            raise QuestionOutcomeError("outcome_id must be non-empty and file-safe")
        for field in ("prediction_id", "question_ref", "subject_id", "answer_kind", "outcome_metric_id", "resolver_policy_id", "resolver_implementation_ref"):
            if not str(getattr(self, field)).strip():
                raise QuestionOutcomeError("%s is required" % field)
        _digest(self.prediction_content_hash, "prediction_content_hash")
        _digest(self.prediction_journal_entry_hash, "prediction_journal_entry_hash")
        _digest(self.question_definition_hash, "question_definition_hash")
        _digest(self.question_registry_hash, "question_registry_hash")
        if self.status not in QUESTION_OUTCOME_STATUSES:
            raise QuestionOutcomeError("outcome status is invalid")
        if self.cutoff_at_ns < 0 or self.target_resolves_at_ns <= self.cutoff_at_ns or self.max_resolution_lag_ns < 0 or self.decided_at_ns < 0:
            raise QuestionOutcomeError("outcome timing is invalid")
        ids = [item.artifact_id for item in self.resolution_evidence]
        if len(ids) != len(set(ids)):
            raise QuestionOutcomeError("resolution evidence artifact ids must be unique")
        upper = self.target_resolves_at_ns + self.max_resolution_lag_ns
        for ref in self.resolution_evidence:
            if ref.role == "BASELINE" and ref.known_at_ns > self.cutoff_at_ns:
                raise QuestionOutcomeError("baseline resolution evidence was not knowable at cutoff")
            if ref.role == "FORWARD" and (ref.known_at_ns <= self.cutoff_at_ns or ref.known_at_ns > upper):
                raise QuestionOutcomeError("forward resolution evidence lies outside allowed causal window")
        if self.status == "RESOLVED":
            if self.realized_answer is None or not self.resolution_evidence:
                raise QuestionOutcomeError("resolved outcome requires answer and evidence")
            _validate_realized_answer(AnswerKind(self.answer_kind), self.realized_answer)
            if not any(ref.role == "FORWARD" for ref in self.resolution_evidence):
                raise QuestionOutcomeError("resolved outcome requires forward evidence")
            if self.subject_id not in {subject for ref in self.resolution_evidence for subject in ref.subject_ids}:
                raise QuestionOutcomeError("resolved outcome evidence does not bind prediction subject")
            max_known = max(ref.known_at_ns for ref in self.resolution_evidence)
            if self.decided_at_ns < max_known:
                raise QuestionOutcomeError("outcome cannot be decided before its evidence is known")
        else:
            if self.realized_answer is not None or self.resolution_evidence:
                raise QuestionOutcomeError("unresolvable outcome cannot claim realized answer/evidence")
            if self.decided_at_ns <= upper:
                raise QuestionOutcomeError("unresolvable outcome cannot be final before resolution window closes")

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "outcome_id": self.outcome_id,
            "prediction": {"prediction_id": self.prediction_id, "content_hash": self.prediction_content_hash, "journal_entry_hash": self.prediction_journal_entry_hash},
            "question": {"question_ref": self.question_ref, "definition_hash": self.question_definition_hash, "registry_hash": self.question_registry_hash, "subject_id": self.subject_id, "answer_kind": self.answer_kind, "outcome_metric_id": self.outcome_metric_id, "resolver_policy_id": self.resolver_policy_id, "resolver_implementation_ref": self.resolver_implementation_ref},
            "timing": {"cutoff_at_ns": self.cutoff_at_ns, "target_resolves_at_ns": self.target_resolves_at_ns, "max_resolution_lag_ns": self.max_resolution_lag_ns, "decided_at_ns": self.decided_at_ns},
            "status": self.status,
            "realized_answer": None if self.realized_answer is None else dict(self.realized_answer),
            "resolution_evidence": [ref.to_wire() for ref in sorted(self.resolution_evidence, key=lambda item: (item.known_at_ns, item.artifact_type, item.artifact_id))],
            "authority": {"market_truth_only": True, "capital_decision": False, "risk_authorization": False, "external_execution": False},
        }

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        value = self.body(); value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}; return value

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "QuestionBoundOutcome":
        prediction, question, timing = value.get("prediction"), value.get("question"), value.get("timing")
        refs_raw = value.get("resolution_evidence", [])
        if not isinstance(prediction, Mapping) or not isinstance(question, Mapping) or not isinstance(timing, Mapping):
            raise QuestionOutcomeError("question outcome envelope is malformed")
        if not isinstance(refs_raw, Sequence) or isinstance(refs_raw, (str, bytes)):
            raise QuestionOutcomeError("resolution_evidence must be an array")
        answer = value.get("realized_answer")
        if answer is not None and not isinstance(answer, Mapping):
            raise QuestionOutcomeError("realized_answer is malformed")
        refs = tuple(ResolutionEvidenceRef.from_wire(item) for item in refs_raw if isinstance(item, Mapping))
        if len(refs) != len(refs_raw):
            raise QuestionOutcomeError("resolution evidence ref is malformed")
        item = cls(schema_version=str(value.get("schema_version", "")), outcome_id=str(value.get("outcome_id", "")), prediction_id=str(prediction.get("prediction_id", "")), prediction_content_hash=str(prediction.get("content_hash", "")), prediction_journal_entry_hash=str(prediction.get("journal_entry_hash", "")), question_ref=str(question.get("question_ref", "")), question_definition_hash=str(question.get("definition_hash", "")), question_registry_hash=str(question.get("registry_hash", "")), subject_id=str(question.get("subject_id", "")), answer_kind=str(question.get("answer_kind", "")), outcome_metric_id=str(question.get("outcome_metric_id", "")), resolver_policy_id=str(question.get("resolver_policy_id", "")), resolver_implementation_ref=str(question.get("resolver_implementation_ref", "")), status=str(value.get("status", "")), cutoff_at_ns=int(timing.get("cutoff_at_ns", -1)), target_resolves_at_ns=int(timing.get("target_resolves_at_ns", -1)), max_resolution_lag_ns=int(timing.get("max_resolution_lag_ns", -1)), decided_at_ns=int(timing.get("decided_at_ns", -1)), realized_answer=None if answer is None else dict(answer), resolution_evidence=refs)
        authority = value.get("authority")
        if not isinstance(authority, Mapping) or authority.get("market_truth_only") is not True or any(authority.get(key) is not False for key in ("capital_decision", "risk_authorization", "external_execution")):
            raise QuestionOutcomeError("outcome authority boundary is invalid")
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("content_hash") != item.content_hash():
            raise QuestionOutcomeError("question outcome content hash mismatch")
        return item


def build_question_outcome_id(prediction_id: str, resolver_policy_id: str, resolver_implementation_ref: str) -> str:
    material = "%s|%s|%s" % (prediction_id, resolver_policy_id, resolver_implementation_ref)
    return "QOUT-%s" % hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
