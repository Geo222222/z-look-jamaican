"""Point-in-time research and model qualification contracts."""

from .contracts import (
    RESEARCH_AUTHORITY,
    ResearchContractError,
    assess_promotion_evidence,
    build_experiment_contract,
    build_model_artifact_lineage,
    build_point_in_time_dataset_manifest,
    build_walk_forward_plan,
    validate_dataset_manifest,
    validate_point_in_time_row,
    validate_walk_forward_plan,
)
from .features import (
    FEATURE_SCHEMA_VERSION,
    build_training_row,
    extract_context_features,
    extract_instrument_features,
)
from .falsification import (
    FALSIFICATION_AUTHORITY,
    FALSIFICATION_SCHEMA_VERSION,
    build_falsification_policy,
    evaluate_falsification,
)

__all__ = (
    "RESEARCH_AUTHORITY",
    "FEATURE_SCHEMA_VERSION",
    "FALSIFICATION_AUTHORITY",
    "FALSIFICATION_SCHEMA_VERSION",
    "ResearchContractError",
    "build_point_in_time_dataset_manifest",
    "validate_point_in_time_row",
    "validate_dataset_manifest",
    "build_walk_forward_plan",
    "validate_walk_forward_plan",
    "build_experiment_contract",
    "build_model_artifact_lineage",
    "assess_promotion_evidence",
    "extract_instrument_features",
    "extract_context_features",
    "build_training_row",
    "build_falsification_policy",
    "evaluate_falsification",
)
