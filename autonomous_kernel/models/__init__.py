"""Z4 legacy models plus forward question-bound expert contracts.

Legacy Z4 models remain untouched for frozen evidence. QuestionExpertDefinition
is the forward scientific identity for experts that answer exact Question
Definitions; its existence never implies competence, capital authority, risk
authorization, or execution authority.
"""

from .baselines import (
    BaselineModelError,
    BookImbalanceLinearModel,
    NullPriorModel,
    ReportedFlowLinearModel,
    baseline_model_set,
    run_baseline_models,
)
from .contracts import ModelDefinition, ModelDefinitionError
from .expert_evidence import material_question_expert_registry_evidence
from .question_experts import (
    EXPERT_LIFECYCLE_STATES,
    EXPERT_TRAINING_MODES,
    QUESTION_EXPERT_REGISTRY_SCHEMA_VERSION,
    QUESTION_EXPERT_SCHEMA_VERSION,
    ExpertQuestionBinding,
    QuestionExpertDefinition,
    QuestionExpertError,
    QuestionExpertRegistryEntry,
    QuestionExpertRegistrySnapshot,
    bind_question,
    build_question_expert_registry_snapshot,
    validate_expert_question_compatibility,
)
from .registry import ModelRegistry, ModelRegistryError, validate_model_registry

__all__ = [
    "EXPERT_LIFECYCLE_STATES",
    "EXPERT_TRAINING_MODES",
    "QUESTION_EXPERT_REGISTRY_SCHEMA_VERSION",
    "QUESTION_EXPERT_SCHEMA_VERSION",
    "BaselineModelError",
    "BookImbalanceLinearModel",
    "ExpertQuestionBinding",
    "ModelDefinition",
    "ModelDefinitionError",
    "ModelRegistry",
    "ModelRegistryError",
    "NullPriorModel",
    "QuestionExpertDefinition",
    "QuestionExpertError",
    "QuestionExpertRegistryEntry",
    "QuestionExpertRegistrySnapshot",
    "ReportedFlowLinearModel",
    "baseline_model_set",
    "bind_question",
    "build_question_expert_registry_snapshot",
    "material_question_expert_registry_evidence",
    "run_baseline_models",
    "validate_expert_question_compatibility",
    "validate_model_registry",
]
