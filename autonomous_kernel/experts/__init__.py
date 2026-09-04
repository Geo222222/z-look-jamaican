"""Question-bound expert contracts for ZLJ Expert School.

Experts answer frozen Question Registry examinations. They do not define truth,
claim competence, assemble weights, make economic decisions, authorize risk, or
execute externally.
"""

from .contracts import (
    EXPERT_CONTRACT_SCHEMA_VERSION,
    EXPERT_CLAIM_SCHEMA_VERSION,
    ExpertContractError,
    build_expert_contract,
    build_expert_claim,
    validate_expert_contract,
    validate_expert_claim,
)

__all__ = (
    "EXPERT_CONTRACT_SCHEMA_VERSION",
    "EXPERT_CLAIM_SCHEMA_VERSION",
    "ExpertContractError",
    "build_expert_contract",
    "build_expert_claim",
    "validate_expert_contract",
    "validate_expert_claim",
)
