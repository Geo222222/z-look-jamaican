"""Question-bound expert contracts and Expert School intelligence lifecycle."""

from .adapters import (
    ExpertAdapterError,
    implemented_baseline_expert_contracts,
    operational_expert_inventory,
    question_prediction_to_expert_claim,
)
from .contracts import (
    EXPERT_CONTRACT_SCHEMA_VERSION,
    EXPERT_CLAIM_SCHEMA_VERSION,
    ExpertContractError,
    build_expert_contract,
    build_expert_claim,
    validate_expert_contract,
    validate_expert_claim,
)
from .school import (
    ADAPTIVE_WEIGHT_POLICY_ID,
    ADAPTIVE_WEIGHT_POLICY_VERSION,
    EXPERT_SCHOOL_SCHEMA_VERSION,
    ExpertSchoolError,
    active_question_definitions,
    assemble_expert_claims,
    build_baseline_expert_school,
    build_competence_memory,
    contextual_competence,
    score_expert_claim,
)

__all__ = (
    "EXPERT_CONTRACT_SCHEMA_VERSION",
    "EXPERT_CLAIM_SCHEMA_VERSION",
    "EXPERT_SCHOOL_SCHEMA_VERSION",
    "ADAPTIVE_WEIGHT_POLICY_ID",
    "ADAPTIVE_WEIGHT_POLICY_VERSION",
    "ExpertAdapterError",
    "ExpertContractError",
    "ExpertSchoolError",
    "build_expert_contract",
    "build_expert_claim",
    "validate_expert_contract",
    "validate_expert_claim",
    "implemented_baseline_expert_contracts",
    "operational_expert_inventory",
    "question_prediction_to_expert_claim",
    "active_question_definitions",
    "build_baseline_expert_school",
    "score_expert_claim",
    "build_competence_memory",
    "contextual_competence",
    "assemble_expert_claims",
)
