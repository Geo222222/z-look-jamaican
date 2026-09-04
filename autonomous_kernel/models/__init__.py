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
from .contracts import ModelDefinition, ModelDefinitionError
from .registry import ModelRegistry, ModelRegistryError, validate_model_registry

__all__ = [
    "BaselineModelError",
    "BookImbalanceLinearModel",
    "ModelDefinition",
    "ModelDefinitionError",
    "ModelRegistry",
    "ModelRegistryError",
    "NullPriorModel",
    "ReportedFlowLinearModel",
    "baseline_model_set",
    "run_baseline_models",
    "validate_model_registry",
]
