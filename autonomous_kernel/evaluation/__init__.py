"""Outcome resolution and later model-evaluation primitives.

Z6 resolves journaled Z3 claims against independently reconstructed future Z2
state. Evaluation never grants capital, execution, or model self-promotion.
"""

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
    "MAX_RESOLUTION_LAG_NS_V1",
    "OutcomeContractError",
    "OutcomeJournal",
    "OutcomeJournalError",
    "OutcomePendingError",
    "OutcomeResolutionError",
    "PredictionOutcome",
    "RESOLUTION_POLICY_ID",
    "resolve_prediction",
    "select_resolution_frame",
    "validate_outcome_journal",
]
