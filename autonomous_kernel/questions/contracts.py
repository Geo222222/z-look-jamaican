from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..experience.contracts import ExperienceTimescale
from ..operations import canonical_hash


QUESTION_SCHEMA_VERSION = "1.0"
QUESTION_REGISTRY_SCHEMA_VERSION = "1.0"
EVIDENCE_CUTOFF_POLICY = "KNOWN_AT_OR_BEFORE_QUESTION_CUTOFF"
QUESTION_LIFECYCLE_STATES = {"DEFINED", "RESOLVER_READY", "QUALIFIED", "RETIRED"}


class QuestionContractError(ValueError):
    pass


class QuestionFamily(str, Enum):
    DIRECTION = "DIRECTION"
    MAGNITUDE = "MAGNITUDE"
    VOLATILITY = "VOLATILITY"
    LIQUIDITY = "LIQUIDITY"
    FRAGILITY = "FRAGILITY"
    BASIS = "BASIS"
    REGIME = "REGIME"
    PERSISTENCE = "PERSISTENCE"
    REVERSAL = "REVERSAL"
    EXECUTION_SUITABILITY = "EXECUTION_SUITABILITY"
    RELATIVE_VALUE = "RELATIVE_VALUE"


class QuestionScope(str, Enum):
    INSTRUMENT = "INSTRUMENT"
    ECONOMIC_ROOT = "ECONOMIC_ROOT"
    RELATIONSHIP = "RELATIONSHIP"
    MARKET_WIDE = "MARKET_WIDE"


class AnswerKind(str, Enum):
    BINARY = "BINARY"
    CONTINUOUS = "CONTINUOUS"
    CATEGORICAL = "CATEGORICAL"
    DISTRIBUTION = "DISTRIBUTION"


def _strings(values: Sequence[str], field: str, *, allow_empty: bool = False) -> Tuple[str, ...]:
    result = tuple(str(value) for value in values)
    if (not result and not allow_empty) or any(not value for value in result):
        raise QuestionContractError("%s must contain non-empty values" % field)
    if len(set(result)) != len(result):
        raise QuestionContractError("%s must contain unique values" % field)
    return result


@dataclass(frozen=True)
class OutcomeDefinition:
    metric_id: str
    answer_kind: AnswerKind
    target_expression: str
    resolver_policy_id: str
    max_resolution_lag_ns: int
    resolution_evidence_families: Tuple[str, ...]

    def __post_init__(self) -> None:
        for field in ("metric_id", "target_expression", "resolver_policy_id"):
            if not str(getattr(self, field)).strip():
                raise QuestionContractError("outcome %s is required" % field)
        if self.max_resolution_lag_ns < 0:
            raise QuestionContractError("max_resolution_lag_ns must be non-negative")
        _strings(self.resolution_evidence_families, "resolution_evidence_families")

    def to_wire(self) -> Dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "answer_kind": self.answer_kind.value,
            "target_expression": self.target_expression,
            "resolver_policy_id": self.resolver_policy_id,
            "max_resolution_lag_ns": self.max_resolution_lag_ns,
            "resolution_evidence_families": list(self.resolution_evidence_families),
        }

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "OutcomeDefinition":
        return cls(
            metric_id=str(value.get("metric_id", "")),
            answer_kind=AnswerKind(str(value.get("answer_kind", ""))),
            target_expression=str(value.get("target_expression", "")),
            resolver_policy_id=str(value.get("resolver_policy_id", "")),
            max_resolution_lag_ns=int(value.get("max_resolution_lag_ns", -1)),
            resolution_evidence_families=tuple(
                str(item) for item in value.get("resolution_evidence_families", [])
            ),
        )


@dataclass(frozen=True)
class QuestionDefinition:
    question_id: str
    version: str
    family: QuestionFamily
    scope: QuestionScope
    asks: str
    horizon_ns: int
    outcome: OutcomeDefinition
    required_timescales: Tuple[ExperienceTimescale, ...]
    required_artifact_types: Tuple[str, ...]
    required_feature_families: Tuple[str, ...]
    allowed_feature_families: Tuple[str, ...]
    forbidden_feature_families: Tuple[str, ...]
    parameters: Mapping[str, Any]
    evidence_cutoff_policy: str = EVIDENCE_CUTOFF_POLICY
    schema_version: str = QUESTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != QUESTION_SCHEMA_VERSION:
            raise QuestionContractError("unsupported question schema")
        for field in ("question_id", "version", "asks"):
            if not str(getattr(self, field)).strip():
                raise QuestionContractError("question %s is required" % field)
        if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in self.question_id):
            raise QuestionContractError("question_id must be file-safe")
        if self.horizon_ns <= 0:
            raise QuestionContractError("question horizon_ns must be positive")
        if self.evidence_cutoff_policy != EVIDENCE_CUTOFF_POLICY:
            raise QuestionContractError("question evidence cutoff policy is invalid")
        if not self.required_timescales or len(set(self.required_timescales)) != len(self.required_timescales):
            raise QuestionContractError("required_timescales must contain unique values")
        required_artifacts = set(_strings(self.required_artifact_types, "required_artifact_types"))
        required = set(_strings(self.required_feature_families, "required_feature_families", allow_empty=True))
        allowed = set(_strings(self.allowed_feature_families, "allowed_feature_families"))
        forbidden = set(_strings(self.forbidden_feature_families, "forbidden_feature_families", allow_empty=True))
        if not required.issubset(allowed):
            raise QuestionContractError("required feature families must be allowed")
        if required.intersection(forbidden) or allowed.intersection(forbidden):
            raise QuestionContractError("allowed/required feature families cannot also be forbidden")
        if not required_artifacts:
            raise QuestionContractError("question requires at least one artifact type")
        if not isinstance(self.parameters, Mapping):
            raise QuestionContractError("question parameters must be a mapping")

    @property
    def question_ref(self) -> str:
        return "%s@%s" % (self.question_id, self.version)

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "question_id": self.question_id,
            "version": self.version,
            "family": self.family.value,
            "scope": self.scope.value,
            "asks": self.asks,
            "horizon_ns": self.horizon_ns,
            "outcome": self.outcome.to_wire(),
            "evidence_policy": {
                "cutoff_policy": self.evidence_cutoff_policy,
                "required_timescales": [item.value for item in self.required_timescales],
                "required_artifact_types": list(self.required_artifact_types),
                "required_feature_families": list(self.required_feature_families),
                "allowed_feature_families": list(self.allowed_feature_families),
                "forbidden_feature_families": list(self.forbidden_feature_families),
            },
            "parameters": dict(self.parameters),
            "authority": {
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
    def from_wire(cls, value: Mapping[str, Any]) -> "QuestionDefinition":
        outcome = value.get("outcome")
        evidence = value.get("evidence_policy")
        if not isinstance(outcome, Mapping) or not isinstance(evidence, Mapping):
            raise QuestionContractError("question envelope is malformed")
        item = cls(
            schema_version=str(value.get("schema_version", "")),
            question_id=str(value.get("question_id", "")),
            version=str(value.get("version", "")),
            family=QuestionFamily(str(value.get("family", ""))),
            scope=QuestionScope(str(value.get("scope", ""))),
            asks=str(value.get("asks", "")),
            horizon_ns=int(value.get("horizon_ns", -1)),
            outcome=OutcomeDefinition.from_wire(outcome),
            required_timescales=tuple(
                ExperienceTimescale(str(item)) for item in evidence.get("required_timescales", [])
            ),
            required_artifact_types=tuple(
                str(item) for item in evidence.get("required_artifact_types", [])
            ),
            required_feature_families=tuple(
                str(item) for item in evidence.get("required_feature_families", [])
            ),
            allowed_feature_families=tuple(
                str(item) for item in evidence.get("allowed_feature_families", [])
            ),
            forbidden_feature_families=tuple(
                str(item) for item in evidence.get("forbidden_feature_families", [])
            ),
            parameters=value.get("parameters") if isinstance(value.get("parameters"), Mapping) else {},
            evidence_cutoff_policy=str(evidence.get("cutoff_policy", "")),
        )
        authority = value.get("authority")
        if not isinstance(authority, Mapping) or any(
            authority.get(key) is not False
            for key in ("capital_decision", "risk_authorization", "external_execution")
        ):
            raise QuestionContractError("question authority boundary is invalid")
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("content_hash") != item.content_hash():
            raise QuestionContractError("question content hash mismatch")
        return item


@dataclass(frozen=True)
class QuestionRegistryEntry:
    definition: QuestionDefinition
    lifecycle_state: str
    registered_at_ns: int
    effective_at_ns: int
    resolver_implementation_ref: Optional[str] = None
    qualification_evidence_refs: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.lifecycle_state not in QUESTION_LIFECYCLE_STATES:
            raise QuestionContractError("invalid question lifecycle state")
        if self.registered_at_ns < 0 or self.effective_at_ns < self.registered_at_ns:
            raise QuestionContractError("question registry timing is invalid")
        evidence = _strings(self.qualification_evidence_refs, "qualification_evidence_refs", allow_empty=True)
        if self.lifecycle_state == "DEFINED":
            if self.resolver_implementation_ref is not None or evidence:
                raise QuestionContractError("DEFINED question cannot claim resolver or qualification evidence")
        elif self.lifecycle_state == "RESOLVER_READY":
            if not self.resolver_implementation_ref or evidence:
                raise QuestionContractError("RESOLVER_READY requires resolver implementation but no qualification claim")
        elif self.lifecycle_state == "QUALIFIED":
            if not self.resolver_implementation_ref or not evidence:
                raise QuestionContractError("QUALIFIED question requires resolver implementation and evidence")

    def to_wire(self) -> Dict[str, Any]:
        return {
            "question_ref": self.definition.question_ref,
            "definition_hash": self.definition.content_hash(),
            "definition": self.definition.to_wire(),
            "lifecycle_state": self.lifecycle_state,
            "registered_at_ns": self.registered_at_ns,
            "effective_at_ns": self.effective_at_ns,
            "resolver_implementation_ref": self.resolver_implementation_ref,
            "qualification_evidence_refs": list(self.qualification_evidence_refs),
        }

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "QuestionRegistryEntry":
        definition_raw = value.get("definition")
        if not isinstance(definition_raw, Mapping):
            raise QuestionContractError("question registry entry definition is malformed")
        definition = QuestionDefinition.from_wire(definition_raw)
        if value.get("question_ref") != definition.question_ref:
            raise QuestionContractError("question registry entry question_ref mismatch")
        if value.get("definition_hash") != definition.content_hash():
            raise QuestionContractError("question registry entry definition hash mismatch")
        return cls(
            definition=definition,
            lifecycle_state=str(value.get("lifecycle_state", "")),
            registered_at_ns=int(value.get("registered_at_ns", -1)),
            effective_at_ns=int(value.get("effective_at_ns", -1)),
            resolver_implementation_ref=(
                None
                if value.get("resolver_implementation_ref") is None
                else str(value.get("resolver_implementation_ref"))
            ),
            qualification_evidence_refs=tuple(
                str(item) for item in value.get("qualification_evidence_refs", [])
            ),
        )


@dataclass(frozen=True)
class QuestionRegistrySnapshot:
    registry_id: str
    version: str
    known_at_ns: int
    effective_at_ns: int
    entries: Tuple[QuestionRegistryEntry, ...]
    schema_version: str = QUESTION_REGISTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != QUESTION_REGISTRY_SCHEMA_VERSION:
            raise QuestionContractError("unsupported question registry schema")
        if not self.registry_id or not self.version:
            raise QuestionContractError("question registry identity is required")
        if self.known_at_ns < 0 or self.effective_at_ns < self.known_at_ns:
            raise QuestionContractError("question registry timing is invalid")
        if not self.entries:
            raise QuestionContractError("question registry requires entries")
        refs = [entry.definition.question_ref for entry in self.entries]
        if len(refs) != len(set(refs)):
            raise QuestionContractError("question registry refs must be unique")
        if any(
            entry.registered_at_ns > self.known_at_ns or entry.effective_at_ns > self.effective_at_ns
            for entry in self.entries
        ):
            raise QuestionContractError("registry snapshot cannot predate an entry")

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "registry_id": self.registry_id,
            "version": self.version,
            "known_at_ns": self.known_at_ns,
            "effective_at_ns": self.effective_at_ns,
            "entries": [
                entry.to_wire()
                for entry in sorted(self.entries, key=lambda item: item.definition.question_ref)
            ],
            "authority": {
                "defines_learning_targets": True,
                "selects_model": False,
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
    def from_wire(cls, value: Mapping[str, Any]) -> "QuestionRegistrySnapshot":
        entries_raw = value.get("entries")
        if not isinstance(entries_raw, Sequence) or isinstance(entries_raw, (str, bytes)):
            raise QuestionContractError("question registry entries must be an array")
        item = cls(
            schema_version=str(value.get("schema_version", "")),
            registry_id=str(value.get("registry_id", "")),
            version=str(value.get("version", "")),
            known_at_ns=int(value.get("known_at_ns", -1)),
            effective_at_ns=int(value.get("effective_at_ns", -1)),
            entries=tuple(
                QuestionRegistryEntry.from_wire(entry)
                for entry in entries_raw
                if isinstance(entry, Mapping)
            ),
        )
        if len(item.entries) != len(entries_raw):
            raise QuestionContractError("question registry entry is malformed")
        authority = value.get("authority")
        if not isinstance(authority, Mapping):
            raise QuestionContractError("question registry authority boundary is invalid")
        if authority.get("defines_learning_targets") is not True or any(
            authority.get(key) is not False
            for key in ("selects_model", "capital_decision", "risk_authorization", "external_execution")
        ):
            raise QuestionContractError("question registry authority boundary is invalid")
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("content_hash") != item.content_hash():
            raise QuestionContractError("question registry content hash mismatch")
        return item


def build_question_registry_snapshot(
    *,
    registry_id: str,
    version: str,
    entries: Sequence[QuestionRegistryEntry],
    known_at_ns: int,
    effective_at_ns: int,
) -> QuestionRegistrySnapshot:
    return QuestionRegistrySnapshot(
        registry_id=registry_id,
        version=version,
        known_at_ns=known_at_ns,
        effective_at_ns=effective_at_ns,
        entries=tuple(entries),
    )
