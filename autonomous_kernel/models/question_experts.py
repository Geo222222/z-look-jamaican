from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..experience.contracts import ExperienceTimescale
from ..operations import canonical_hash
from ..questions.contracts import QuestionDefinition


QUESTION_EXPERT_SCHEMA_VERSION = "1.0"
QUESTION_EXPERT_REGISTRY_SCHEMA_VERSION = "1.0"
EXPERT_LIFECYCLE_STATES = {"CANDIDATE", "REPLAY_QUALIFIED", "SHADOW_QUALIFIED", "RETIRED"}
EXPERT_TRAINING_MODES = {"NONE", "FROZEN_SUPERVISED"}


class QuestionExpertError(ValueError):
    pass


def _digest(value: str, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise QuestionExpertError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise QuestionExpertError("%s must be hexadecimal" % field) from exc
    return text


def _strings(values: Sequence[str], field: str, *, allow_empty: bool = False) -> Tuple[str, ...]:
    result = tuple(str(value) for value in values)
    if (not result and not allow_empty) or any(not value for value in result):
        raise QuestionExpertError("%s must contain non-empty values" % field)
    if len(set(result)) != len(result):
        raise QuestionExpertError("%s must contain unique values" % field)
    return result


@dataclass(frozen=True)
class ExpertQuestionBinding:
    question_ref: str
    question_definition_hash: str
    family: str
    scope: str
    answer_kind: str
    horizon_ns: int

    def __post_init__(self) -> None:
        for field in ("question_ref", "family", "scope", "answer_kind"):
            if not str(getattr(self, field)).strip():
                raise QuestionExpertError("expert question %s is required" % field)
        _digest(self.question_definition_hash, "question_definition_hash")
        if self.horizon_ns <= 0:
            raise QuestionExpertError("expert question horizon_ns must be positive")

    def to_wire(self) -> Dict[str, Any]:
        return {
            "question_ref": self.question_ref,
            "question_definition_hash": self.question_definition_hash,
            "family": self.family,
            "scope": self.scope,
            "answer_kind": self.answer_kind,
            "horizon_ns": int(self.horizon_ns),
        }

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "ExpertQuestionBinding":
        return cls(
            question_ref=str(value.get("question_ref", "")),
            question_definition_hash=str(value.get("question_definition_hash", "")),
            family=str(value.get("family", "")),
            scope=str(value.get("scope", "")),
            answer_kind=str(value.get("answer_kind", "")),
            horizon_ns=int(value.get("horizon_ns", -1)),
        )


def bind_question(question: QuestionDefinition) -> ExpertQuestionBinding:
    return ExpertQuestionBinding(
        question_ref=question.question_ref,
        question_definition_hash=question.content_hash(),
        family=question.family.value,
        scope=question.scope.value,
        answer_kind=question.outcome.answer_kind.value,
        horizon_ns=question.horizon_ns,
    )


@dataclass(frozen=True)
class QuestionExpertDefinition:
    expert_id: str
    version: str
    family: str
    implementation_ref: str
    implementation_version: str
    question_bindings: Tuple[ExpertQuestionBinding, ...]
    required_artifact_types: Tuple[str, ...]
    required_feature_families: Tuple[str, ...]
    allowed_feature_families: Tuple[str, ...]
    required_timescales: Tuple[ExperienceTimescale, ...]
    feature_schema_id: str
    feature_schema_version: str
    training_mode: str
    training_data_cutoff_ns: Optional[int]
    training_completed_at_ns: Optional[int]
    supported_subject_ids: Tuple[str, ...]
    parameters: Mapping[str, Any]
    schema_version: str = QUESTION_EXPERT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != QUESTION_EXPERT_SCHEMA_VERSION:
            raise QuestionExpertError("unsupported question expert schema")
        if not self.expert_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in self.expert_id):
            raise QuestionExpertError("expert_id must be non-empty and file-safe")
        for field in (
            "version",
            "family",
            "implementation_ref",
            "implementation_version",
            "feature_schema_id",
            "feature_schema_version",
        ):
            if not str(getattr(self, field)).strip():
                raise QuestionExpertError("%s is required" % field)
        if not self.question_bindings:
            raise QuestionExpertError("question expert requires at least one exact QuestionDefinition binding")
        refs = [binding.question_ref for binding in self.question_bindings]
        if len(refs) != len(set(refs)):
            raise QuestionExpertError("expert question bindings must be unique")
        _strings(self.required_artifact_types, "required_artifact_types")
        required_features = set(_strings(self.required_feature_families, "required_feature_families", allow_empty=True))
        allowed_features = set(_strings(self.allowed_feature_families, "allowed_feature_families", allow_empty=True))
        if not required_features.issubset(allowed_features):
            raise QuestionExpertError("required expert features must be allowed")
        if not self.required_timescales or len(set(self.required_timescales)) != len(self.required_timescales):
            raise QuestionExpertError("required_timescales must be non-empty and unique")
        _strings(self.supported_subject_ids, "supported_subject_ids", allow_empty=True)
        if self.training_mode not in EXPERT_TRAINING_MODES:
            raise QuestionExpertError("expert training_mode is invalid")
        if self.training_mode == "NONE":
            if self.training_data_cutoff_ns is not None or self.training_completed_at_ns is not None:
                raise QuestionExpertError("untrained expert cannot claim training timestamps")
        else:
            if self.training_data_cutoff_ns is None or self.training_completed_at_ns is None:
                raise QuestionExpertError("frozen supervised expert requires training cutoff/completion")
            if self.training_data_cutoff_ns < 0 or self.training_completed_at_ns < self.training_data_cutoff_ns:
                raise QuestionExpertError("expert training timing is invalid")
        if not isinstance(self.parameters, Mapping):
            raise QuestionExpertError("expert parameters must be a mapping")

    @property
    def definition_ref(self) -> str:
        return "%s@%s#%s" % (self.expert_id, self.version, self.content_hash())

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "expert_id": self.expert_id,
            "version": self.version,
            "family": self.family,
            "implementation": {
                "ref": self.implementation_ref,
                "version": self.implementation_version,
            },
            "question_bindings": [
                binding.to_wire() for binding in sorted(self.question_bindings, key=lambda item: item.question_ref)
            ],
            "input_contract": {
                "required_artifact_types": list(sorted(self.required_artifact_types)),
                "required_feature_families": list(sorted(self.required_feature_families)),
                "allowed_feature_families": list(sorted(self.allowed_feature_families)),
                "required_timescales": sorted(item.value for item in self.required_timescales),
                "feature_schema_id": self.feature_schema_id,
                "feature_schema_version": self.feature_schema_version,
                "supported_subject_ids": list(sorted(self.supported_subject_ids)),
            },
            "training": {
                "mode": self.training_mode,
                "data_cutoff_ns": self.training_data_cutoff_ns,
                "completed_at_ns": self.training_completed_at_ns,
            },
            "parameters": dict(self.parameters),
            "authority": {
                "market_observation": False,
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

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "QuestionExpertDefinition":
        implementation = value.get("implementation")
        input_contract = value.get("input_contract")
        training = value.get("training")
        bindings_raw = value.get("question_bindings")
        if not isinstance(implementation, Mapping) or not isinstance(input_contract, Mapping) or not isinstance(training, Mapping):
            raise QuestionExpertError("question expert envelope is malformed")
        if not isinstance(bindings_raw, Sequence) or isinstance(bindings_raw, (str, bytes)):
            raise QuestionExpertError("question_bindings must be an array")
        bindings = tuple(ExpertQuestionBinding.from_wire(item) for item in bindings_raw if isinstance(item, Mapping))
        if len(bindings) != len(bindings_raw):
            raise QuestionExpertError("question binding is malformed")
        item = cls(
            schema_version=str(value.get("schema_version", "")),
            expert_id=str(value.get("expert_id", "")),
            version=str(value.get("version", "")),
            family=str(value.get("family", "")),
            implementation_ref=str(implementation.get("ref", "")),
            implementation_version=str(implementation.get("version", "")),
            question_bindings=bindings,
            required_artifact_types=tuple(str(raw) for raw in input_contract.get("required_artifact_types", [])),
            required_feature_families=tuple(str(raw) for raw in input_contract.get("required_feature_families", [])),
            allowed_feature_families=tuple(str(raw) for raw in input_contract.get("allowed_feature_families", [])),
            required_timescales=tuple(ExperienceTimescale(str(raw)) for raw in input_contract.get("required_timescales", [])),
            feature_schema_id=str(input_contract.get("feature_schema_id", "")),
            feature_schema_version=str(input_contract.get("feature_schema_version", "")),
            training_mode=str(training.get("mode", "")),
            training_data_cutoff_ns=None if training.get("data_cutoff_ns") is None else int(training.get("data_cutoff_ns")),
            training_completed_at_ns=None if training.get("completed_at_ns") is None else int(training.get("completed_at_ns")),
            supported_subject_ids=tuple(str(raw) for raw in input_contract.get("supported_subject_ids", [])),
            parameters=value.get("parameters") if isinstance(value.get("parameters"), Mapping) else {},
        )
        authority = value.get("authority")
        if not isinstance(authority, Mapping) or any(authority.get(key) is not False for key in ("market_observation", "capital_decision", "risk_authorization", "external_execution")):
            raise QuestionExpertError("question expert authority boundary is invalid")
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("algorithm") != "sha256" or integrity.get("content_hash") != item.content_hash():
            raise QuestionExpertError("question expert content hash mismatch")
        return item


@dataclass(frozen=True)
class QuestionExpertRegistryEntry:
    definition: QuestionExpertDefinition
    lifecycle_state: str
    registered_at_ns: int
    effective_at_ns: int
    qualification_evidence_refs: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.lifecycle_state not in EXPERT_LIFECYCLE_STATES:
            raise QuestionExpertError("expert lifecycle_state is invalid")
        if self.registered_at_ns < 0 or self.effective_at_ns < self.registered_at_ns:
            raise QuestionExpertError("expert registry entry timing is invalid")
        refs = _strings(self.qualification_evidence_refs, "qualification_evidence_refs", allow_empty=True)
        if self.lifecycle_state == "CANDIDATE" and refs:
            raise QuestionExpertError("candidate expert cannot claim qualification evidence")
        if self.lifecycle_state in {"REPLAY_QUALIFIED", "SHADOW_QUALIFIED"} and not refs:
            raise QuestionExpertError("qualified expert requires qualification evidence")

    def to_wire(self) -> Dict[str, Any]:
        return {
            "definition": self.definition.to_wire(),
            "lifecycle_state": self.lifecycle_state,
            "registered_at_ns": self.registered_at_ns,
            "effective_at_ns": self.effective_at_ns,
            "qualification_evidence_refs": list(self.qualification_evidence_refs),
        }

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "QuestionExpertRegistryEntry":
        definition = value.get("definition")
        if not isinstance(definition, Mapping):
            raise QuestionExpertError("expert registry definition is malformed")
        return cls(
            definition=QuestionExpertDefinition.from_wire(definition),
            lifecycle_state=str(value.get("lifecycle_state", "")),
            registered_at_ns=int(value.get("registered_at_ns", -1)),
            effective_at_ns=int(value.get("effective_at_ns", -1)),
            qualification_evidence_refs=tuple(str(ref) for ref in value.get("qualification_evidence_refs", [])),
        )


@dataclass(frozen=True)
class QuestionExpertRegistrySnapshot:
    registry_id: str
    version: str
    entries: Tuple[QuestionExpertRegistryEntry, ...]
    known_at_ns: int
    effective_at_ns: int
    schema_version: str = QUESTION_EXPERT_REGISTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != QUESTION_EXPERT_REGISTRY_SCHEMA_VERSION:
            raise QuestionExpertError("unsupported question expert registry schema")
        if not self.registry_id or not self.version:
            raise QuestionExpertError("expert registry identity is required")
        if self.known_at_ns < 0 or self.effective_at_ns < self.known_at_ns:
            raise QuestionExpertError("expert registry timing is invalid")
        if not self.entries:
            raise QuestionExpertError("expert registry requires entries")
        refs = [entry.definition.definition_ref for entry in self.entries]
        if len(refs) != len(set(refs)):
            raise QuestionExpertError("expert registry definition refs must be unique")
        if any(entry.registered_at_ns > self.known_at_ns for entry in self.entries):
            raise QuestionExpertError("expert registry cannot predate entry registration")

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "registry_id": self.registry_id,
            "version": self.version,
            "known_at_ns": self.known_at_ns,
            "effective_at_ns": self.effective_at_ns,
            "entries": [
                entry.to_wire() for entry in sorted(self.entries, key=lambda item: item.definition.definition_ref)
            ],
            "authority": {
                "model_competence": False,
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

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "QuestionExpertRegistrySnapshot":
        entries_raw = value.get("entries")
        if not isinstance(entries_raw, Sequence) or isinstance(entries_raw, (str, bytes)):
            raise QuestionExpertError("expert registry entries must be an array")
        entries = tuple(QuestionExpertRegistryEntry.from_wire(item) for item in entries_raw if isinstance(item, Mapping))
        if len(entries) != len(entries_raw):
            raise QuestionExpertError("expert registry entry is malformed")
        item = cls(
            schema_version=str(value.get("schema_version", "")),
            registry_id=str(value.get("registry_id", "")),
            version=str(value.get("version", "")),
            entries=entries,
            known_at_ns=int(value.get("known_at_ns", -1)),
            effective_at_ns=int(value.get("effective_at_ns", -1)),
        )
        authority = value.get("authority")
        if not isinstance(authority, Mapping) or authority.get("model_competence") is not False or any(authority.get(key) is not False for key in ("capital_decision", "risk_authorization", "external_execution")):
            raise QuestionExpertError("expert registry authority boundary is invalid")
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("algorithm") != "sha256" or integrity.get("content_hash") != item.content_hash():
            raise QuestionExpertError("expert registry content hash mismatch")
        return item


def build_question_expert_registry_snapshot(
    *,
    registry_id: str,
    version: str,
    entries: Sequence[QuestionExpertRegistryEntry],
    known_at_ns: int,
    effective_at_ns: int,
) -> QuestionExpertRegistrySnapshot:
    return QuestionExpertRegistrySnapshot(
        registry_id=str(registry_id),
        version=str(version),
        entries=tuple(entries),
        known_at_ns=int(known_at_ns),
        effective_at_ns=int(effective_at_ns),
    )


def validate_expert_question_compatibility(
    expert: QuestionExpertDefinition,
    question: QuestionDefinition,
) -> None:
    bindings = [binding for binding in expert.question_bindings if binding.question_ref == question.question_ref]
    if len(bindings) != 1:
        raise QuestionExpertError("expert does not bind the exact question version")
    binding = bindings[0]
    if binding.question_definition_hash != question.content_hash():
        raise QuestionExpertError("expert question hash differs from QuestionDefinition")
    if binding.family != question.family.value or binding.scope != question.scope.value or binding.answer_kind != question.outcome.answer_kind.value or binding.horizon_ns != question.horizon_ns:
        raise QuestionExpertError("expert question semantics differ from QuestionDefinition")

    expert_required_features = set(expert.required_feature_families)
    expert_allowed_features = set(expert.allowed_feature_families)
    question_required_features = set(question.required_feature_families)
    question_allowed_features = set(question.allowed_feature_families)
    question_forbidden_features = set(question.forbidden_feature_families)
    if not question_required_features.issubset(expert_required_features):
        raise QuestionExpertError("expert omits a feature family required by the question")
    if not expert_required_features.issubset(question_allowed_features):
        raise QuestionExpertError("expert requires feature outside question allowlist")
    if not expert_allowed_features.issubset(question_allowed_features):
        raise QuestionExpertError("expert allows feature outside question allowlist")
    if expert_allowed_features.intersection(question_forbidden_features):
        raise QuestionExpertError("expert permits a feature forbidden by the question")
    if not set(question.required_artifact_types).issubset(set(expert.required_artifact_types)):
        raise QuestionExpertError("expert omits an artifact required by the question")
    if not set(question.required_timescales).issubset(set(expert.required_timescales)):
        raise QuestionExpertError("expert omits a timescale required by the question")
