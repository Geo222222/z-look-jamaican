"""Z4 candidate model factory.

Models consume Z2 representations and emit Z3 predictions. They do not own
qualification, assembly, capital decisions, or execution authority.
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

__all__ = [
    "BaselineModelError",
    "BookImbalanceLinearModel",
    "ModelDefinition",
    "ModelDefinitionError",
    "NullPriorModel",
    "ReportedFlowLinearModel",
    "baseline_model_set",
    "run_baseline_models",
]
