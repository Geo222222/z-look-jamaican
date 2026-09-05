"""Z4 candidate models plus the Z5 governed model lifecycle.

Models consume Z2 representations and emit Z3 predictions. Model definitions
remain immutable CANDIDATE artifacts; Z5 owns qualification state separately so
model code cannot self-certify, assemble itself, authorize capital, or execute.
"""

from .baselines import (
    BaselineModelError,
    BookImbalanceLinearModel,
    NullPriorModel,
    ReportedFlowLinearModel,
    baseline_model_set,
    run_baseline_models,
)
from .liquidity_baselines import (
    BookDepletionStressModel,
    LiquidityNullPriorModel,
    SpreadDepthPressureModel,
    liquidity_baseline_model_set,
)
from .contracts import ModelDefinition, ModelDefinitionError
from .qualification import (
    QUALIFICATION_AUTHORITY,
    ModelQualificationError,
    QualificationEvidenceStore,
    apply_transition_proposal,
    build_evaluation_receipt,
    build_transition_proposal,
    validate_evaluation_receipt,
    validate_transition_proposal,
)
from .qualification_status import qualification_evidence_status, validate_qualification_evidence_store
from .registry import ModelRegistry, ModelRegistryError, validate_model_registry

__all__ = [
    "BaselineModelError",
    "BookImbalanceLinearModel",
    "ModelDefinition",
    "ModelDefinitionError",
    "ModelRegistry",
    "ModelRegistryError",
    "ModelQualificationError",
    "QualificationEvidenceStore",
    "QUALIFICATION_AUTHORITY",
    "NullPriorModel",
    "ReportedFlowLinearModel",
    "apply_transition_proposal",
    "baseline_model_set",
    "BookDepletionStressModel",
    "LiquidityNullPriorModel",
    "SpreadDepthPressureModel",
    "liquidity_baseline_model_set",
    "build_evaluation_receipt",
    "build_transition_proposal",
    "qualification_evidence_status",
    "run_baseline_models",
    "validate_evaluation_receipt",
    "validate_model_registry",
    "validate_qualification_evidence_store",
    "validate_transition_proposal",
]
