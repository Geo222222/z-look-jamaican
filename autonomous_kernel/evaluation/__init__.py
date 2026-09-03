"""Outcome resolution and model-evaluation primitives.

Z6 resolves journaled Z3 claims against future Z2 state. Z7 derives segmented
competence from those immutable claims and outcomes. Evaluation never grants
capital, execution, or model self-promotion.
"""

from .competence import (
    COMPETENCE_SCHEMA_VERSION,
    SAMPLE_STRENGTH_PRIOR_COUNT,
    CompetenceError,
    CompetenceProfile,
    build_competence_profiles,
)
from .contracts import OutcomeContractError, PredictionOutcome, RESOLUTION_POLICY_ID
from .journal import OutcomeJournal, OutcomeJournalError, validate_outcome_journal
from .resolver import (
    MAX_RESOLUTION_LAG_NS_V1,
    OutcomePendingError,
    OutcomeResolutionError,
    resolve_prediction,
    select_resolution_frame,
)

__all__ = [
    "COMPETENCE_SCHEMA_VERSION",
    "MAX_RESOLUTION_LAG_NS_V1",
    "SAMPLE_STRENGTH_PRIOR_COUNT",
    "CompetenceError",
    "CompetenceProfile",
    "OutcomeContractError",
    "OutcomeJournal",
    "OutcomeJournalError",
    "OutcomePendingError",
    "OutcomeResolutionError",
    "PredictionOutcome",
    "RESOLUTION_POLICY_ID",
    "build_competence_profiles",
    "resolve_prediction",
    "select_resolution_frame",
    "validate_outcome_journal",
]
