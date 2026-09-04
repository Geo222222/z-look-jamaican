"""Immutable economic-question contracts for ZLJ learning."""

from .catalog import default_question_registry_v1, question_catalog_v1
from .contracts import (
    EVIDENCE_CUTOFF_POLICY,
    QUESTION_REGISTRY_SCHEMA_VERSION,
    QUESTION_SCHEMA_VERSION,
    AnswerKind,
    OutcomeDefinition,
    QuestionContractError,
    QuestionDefinition,
    QuestionFamily,
    QuestionRegistryEntry,
    QuestionRegistrySnapshot,
    QuestionScope,
    build_question_registry_snapshot,
)
from .evidence import material_question_registry_evidence

__all__ = [
    "EVIDENCE_CUTOFF_POLICY",
    "QUESTION_REGISTRY_SCHEMA_VERSION",
    "QUESTION_SCHEMA_VERSION",
    "AnswerKind",
    "OutcomeDefinition",
    "QuestionContractError",
    "QuestionDefinition",
    "QuestionFamily",
    "QuestionRegistryEntry",
    "QuestionRegistrySnapshot",
    "QuestionScope",
    "build_question_registry_snapshot",
    "default_question_registry_v1",
    "material_question_registry_evidence",
    "question_catalog_v1",
]
