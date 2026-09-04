from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence, Tuple

from ..models.question_experts import (
    QuestionExpertDefinition,
    QuestionExpertError,
    QuestionExpertRegistrySnapshot,
    validate_expert_question_compatibility,
)
from ..operations import canonical_hash
from ..questions.contracts import QuestionDefinition, QuestionRegistrySnapshot
from .question_bound import (
    PredictionArtifactRef,
    QuestionBoundPrediction,
    QuestionPredictionError,
    build_question_bound_prediction,
)

QUESTION_EXPERT_PREDICTION_SCHEMA_VERSION = "1.0"


class QuestionExpertPredictionError(ValueError):
    pass


def _digest(value: str, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise QuestionExpertPredictionError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise QuestionExpertPredictionError("%s must be hexadecimal" % field) from exc
    return text


def _unique_strings(values: Sequence[str], field: str) -> Tuple[str, ...]:
    result = tuple(str(value) for value in values)
    if not result or any(not value for value in result):
        raise QuestionExpertPredictionError("%s must contain non-empty values" % field)
    if len(set(result)) != len(result):
        raise QuestionExpertPredictionError("%s must contain unique values" % field)
    return result


@dataclass(frozen=True)
class QuestionExpertPrediction:
    prediction: QuestionBoundPrediction
    expert_registry_id: str
    expert_registry_version: str
    expert_registry_hash: str
    expert_definition_ref: str
    expert_definition_hash: str
    expert_lifecycle_state: str
    qualification_evidence_refs: Tuple[str, ...]
    schema_version: str = QUESTION_EXPERT_PREDICTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != QUESTION_EXPERT_PREDICTION_SCHEMA_VERSION:
            raise QuestionExpertPredictionError("unsupported question-expert prediction schema")
        for field in (
            "expert_registry_id",
            "expert_registry_version",
            "expert_definition_ref",
            "expert_lifecycle_state",
        ):
            if not str(getattr(self, field)).strip():
                raise QuestionExpertPredictionError("%s is required" % field)
        _digest(self.expert_registry_hash, "expert_registry_hash")
        _digest(self.expert_definition_hash, "expert_definition_hash")
        if self.expert_definition_ref.rsplit("#", 1)[-1] != self.expert_definition_hash:
            raise QuestionExpertPredictionError("expert definition ref/hash mismatch")
        if self.expert_lifecycle_state != "SHADOW_QUALIFIED":
            raise QuestionExpertPredictionError(
                "prospective expert prediction requires SHADOW_QUALIFIED expert"
            )
        refs = _unique_strings(
            self.qualification_evidence_refs, "qualification_evidence_refs"
        )
        if self.prediction.mode != "PROSPECTIVE_SHADOW":
            raise QuestionExpertPredictionError(
                "question-expert prediction must be prospective shadow"
            )
        if self.prediction.evidence_class != "FORWARD_EVALUABLE":
            raise QuestionExpertPredictionError(
                "question-expert prediction must be forward evaluable"
            )
        if self.prediction.model_refs != (self.expert_definition_ref,):
            raise QuestionExpertPredictionError(
                "prediction model_refs must bind exactly one expert definition"
            )
        if not refs:
            raise QuestionExpertPredictionError(
                "shadow-qualified expert requires qualification evidence"
            )

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "prediction": self.prediction.to_wire(),
            "expert_registry": {
                "registry_id": self.expert_registry_id,
                "version": self.expert_registry_version,
                "content_hash": self.expert_registry_hash,
            },
            "expert": {
                "definition_ref": self.expert_definition_ref,
                "definition_hash": self.expert_definition_hash,
                "lifecycle_state": self.expert_lifecycle_state,
                "qualification_evidence_refs": list(self.qualification_evidence_refs),
            },
            "authority": {
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
    def from_wire(cls, value: Mapping[str, Any]) -> "QuestionExpertPrediction":
        prediction_raw = value.get("prediction")
        registry = value.get("expert_registry")
        expert = value.get("expert")
        if (
            not isinstance(prediction_raw, Mapping)
            or not isinstance(registry, Mapping)
            or not isinstance(expert, Mapping)
        ):
            raise QuestionExpertPredictionError(
                "question-expert prediction envelope is malformed"
            )
        try:
            prediction = QuestionBoundPrediction.from_wire(prediction_raw)
        except QuestionPredictionError as exc:
            raise QuestionExpertPredictionError(str(exc)) from exc
        item = cls(
            schema_version=str(value.get("schema_version", "")),
            prediction=prediction,
            expert_registry_id=str(registry.get("registry_id", "")),
            expert_registry_version=str(registry.get("version", "")),
            expert_registry_hash=str(registry.get("content_hash", "")),
            expert_definition_ref=str(expert.get("definition_ref", "")),
            expert_definition_hash=str(expert.get("definition_hash", "")),
            expert_lifecycle_state=str(expert.get("lifecycle_state", "")),
            qualification_evidence_refs=tuple(
                str(ref) for ref in expert.get("qualification_evidence_refs", [])
            ),
        )
        authority = value.get("authority")
        expected_authority = {
            "model_competence": False,
            "adaptive_weighting": False,
            "capital_confidence": False,
            "capital_decision": False,
            "risk_authorization": False,
            "external_execution": False,
        }
        if not isinstance(authority, Mapping) or dict(authority) != expected_authority:
            raise QuestionExpertPredictionError(
                "question-expert prediction authority boundary is invalid"
            )
        integrity = value.get("integrity")
        if (
            not isinstance(integrity, Mapping)
            or integrity.get("algorithm") != "sha256"
            or integrity.get("content_hash") != item.content_hash()
        ):
            raise QuestionExpertPredictionError(
                "question-expert prediction content hash mismatch"
            )
        return item


def _select_expert(
    expert_registry: QuestionExpertRegistrySnapshot,
    expert_definition_ref: str,
) -> Tuple[QuestionExpertDefinition, str, int, int, Tuple[str, ...]]:
    matches = [
        entry
        for entry in expert_registry.entries
        if entry.definition.definition_ref == str(expert_definition_ref)
    ]
    if len(matches) != 1:
        raise QuestionExpertPredictionError(
            "expert definition is not uniquely present in expert registry"
        )
    entry = matches[0]
    return (
        entry.definition,
        entry.lifecycle_state,
        entry.registered_at_ns,
        entry.effective_at_ns,
        tuple(entry.qualification_evidence_refs),
    )


def _validate_expert_evidence(
    expert: QuestionExpertDefinition,
    subject_id: str,
    artifact_refs: Sequence[PredictionArtifactRef],
) -> None:
    refs = tuple(artifact_refs)
    artifact_types = {ref.artifact_type for ref in refs}
    features = {family for ref in refs for family in ref.feature_families}
    timescales = {timescale for ref in refs for timescale in ref.timescales}
    subjects = {subject for ref in refs for subject in ref.subject_ids}

    if not set(expert.required_artifact_types).issubset(artifact_types):
        raise QuestionExpertPredictionError(
            "expert-required artifact type is missing"
        )
    if not set(expert.required_feature_families).issubset(features):
        raise QuestionExpertPredictionError(
            "expert-required feature family is missing"
        )
    if not set(expert.required_timescales).issubset(timescales):
        raise QuestionExpertPredictionError(
            "expert-required timescale is missing"
        )
    if not features.issubset(set(expert.allowed_feature_families)):
        raise QuestionExpertPredictionError(
            "artifact includes feature family outside expert allowlist"
        )
    if subject_id not in subjects:
        raise QuestionExpertPredictionError(
            "prediction subject is not bound by expert input evidence"
        )
    if expert.supported_subject_ids and subject_id not in set(
        expert.supported_subject_ids
    ):
        raise QuestionExpertPredictionError(
            "expert does not support prediction subject"
        )


def _validate_training_cutoff(
    expert: QuestionExpertDefinition,
    *,
    cutoff_at_ns: int,
    registered_at_ns: int,
) -> None:
    if expert.training_mode == "NONE":
        return
    assert expert.training_data_cutoff_ns is not None
    assert expert.training_completed_at_ns is not None
    if expert.training_data_cutoff_ns > cutoff_at_ns:
        raise QuestionExpertPredictionError(
            "expert training data cutoff is after prediction cutoff"
        )
    if expert.training_completed_at_ns > cutoff_at_ns:
        raise QuestionExpertPredictionError(
            "expert training completed after prediction cutoff"
        )
    if expert.training_completed_at_ns > registered_at_ns:
        raise QuestionExpertPredictionError(
            "expert was registered before training completed"
        )


def build_prospective_question_expert_prediction(
    *,
    question_registry: QuestionRegistrySnapshot,
    question: QuestionDefinition,
    expert_registry: QuestionExpertRegistrySnapshot,
    expert_definition_ref: str,
    subject_id: str,
    cutoff_at_ns: int,
    created_at_ns: int,
    answer: Mapping[str, Any],
    artifact_refs: Sequence[PredictionArtifactRef],
) -> QuestionExpertPrediction:
    if not str(subject_id).strip():
        raise QuestionExpertPredictionError("subject_id is required")
    if (
        expert_registry.known_at_ns > cutoff_at_ns
        or expert_registry.effective_at_ns > cutoff_at_ns
    ):
        raise QuestionExpertPredictionError(
            "expert registry was not knowable/effective at prospective cutoff"
        )

    (
        expert,
        lifecycle_state,
        registered_at_ns,
        effective_at_ns,
        qualification_evidence_refs,
    ) = _select_expert(expert_registry, expert_definition_ref)

    if lifecycle_state != "SHADOW_QUALIFIED":
        raise QuestionExpertPredictionError(
            "prospective prediction requires SHADOW_QUALIFIED expert"
        )
    if registered_at_ns > cutoff_at_ns or effective_at_ns > cutoff_at_ns:
        raise QuestionExpertPredictionError(
            "expert was not registered/effective at prospective cutoff"
        )
    if not qualification_evidence_refs:
        raise QuestionExpertPredictionError(
            "shadow-qualified expert lacks qualification evidence"
        )

    try:
        validate_expert_question_compatibility(expert, question)
    except QuestionExpertError as exc:
        raise QuestionExpertPredictionError(str(exc)) from exc

    _validate_expert_evidence(expert, str(subject_id), artifact_refs)
    _validate_training_cutoff(
        expert,
        cutoff_at_ns=int(cutoff_at_ns),
        registered_at_ns=registered_at_ns,
    )

    try:
        prediction = build_question_bound_prediction(
            registry=question_registry,
            question=question,
            subject_id=str(subject_id),
            mode="PROSPECTIVE_SHADOW",
            evidence_class="FORWARD_EVALUABLE",
            cutoff_at_ns=int(cutoff_at_ns),
            created_at_ns=int(created_at_ns),
            answer=answer,
            model_refs=(expert.definition_ref,),
            artifact_refs=artifact_refs,
        )
    except QuestionPredictionError as exc:
        raise QuestionExpertPredictionError(str(exc)) from exc

    return QuestionExpertPrediction(
        prediction=prediction,
        expert_registry_id=expert_registry.registry_id,
        expert_registry_version=expert_registry.version,
        expert_registry_hash=expert_registry.content_hash(),
        expert_definition_ref=expert.definition_ref,
        expert_definition_hash=expert.content_hash(),
        expert_lifecycle_state=lifecycle_state,
        qualification_evidence_refs=qualification_evidence_refs,
    )
