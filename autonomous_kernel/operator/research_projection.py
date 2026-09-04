from __future__ import annotations

from typing import Any, Mapping

from ..research.contracts import RESEARCH_AUTHORITY
from ..research.features import FEATURE_SCHEMA_VERSION


def research_qualification_projection() -> Mapping[str, Any]:
    """Read-only construction status for the pre-training research plane."""
    return {
        "status": "PRE_TRAINING_INFRASTRUCTURE",
        "training": "NOT_RUN",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "construction": {
            "point_in_time_feature_plane": "BUILT",
            "dataset_manifest_contract": "BUILT",
            "walk_forward_evaluation_plan": "BUILT",
            "experiment_preregistration": "BUILT",
            "model_artifact_lineage": "BUILT",
            "promotion_evidence_assessment": "BUILT",
            "context_feature_binding": "BUILT",
            "scheduled_training": "NOT_BUILT",
            "trained_expert_population": "NOT_EARNED",
        },
        "guarantees": {
            "feature_known_at_or_before_cutoff": True,
            "label_known_strictly_after_cutoff": True,
            "random_shuffle_primary_split_forbidden": True,
            "walk_forward_time_order_required": True,
            "training_label_embargo_supported": True,
            "experiment_identity_frozen_before_training": True,
            "model_artifact_dataset_lineage_required": True,
            "promotion_assessment_does_not_mutate_lifecycle": True,
        },
        "authority": dict(RESEARCH_AUTHORITY),
    }
