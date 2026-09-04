"""Outcome resolution and model-evaluation primitives.

Legacy Z6/Z7 contracts remain intact for frozen evidence. Question-bound
outcomes are the forward architecture: market truth is resolved separately
from model scoring, competence, capital judgment, and execution authority.
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
from .question_journal import (
    QuestionOutcomeJournal,
    QuestionOutcomeJournalError,
    validate_question_outcome_journal,
)
from .question_outcome import (
    QuestionBoundOutcome,
    QuestionOutcomeError,
    ResolutionEvidenceRef,
    build_question_outcome_id,
)
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
    "QuestionBoundOutcome",
    "QuestionOutcomeError",
    "QuestionOutcomeJournal",
    "QuestionOutcomeJournalError",
    "RESOLUTION_POLICY_ID",
    "ResolutionEvidenceRef",
    "build_competence_profiles",
    "build_question_outcome_id",
    "resolve_prediction",
    "select_resolution_frame",
    "validate_outcome_journal",
    "validate_question_outcome_journal",
]
