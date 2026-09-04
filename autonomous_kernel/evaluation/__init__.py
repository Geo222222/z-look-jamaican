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
from .liquidity_resolver import (
    LIQUIDITY_RESOLVER_IMPLEMENTATION_REF,
    LIQUIDITY_RESOLVER_POLICY_ID,
    resolve_liquidity_question,
)
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
from .question_resolvers import (
    MIDPOINT_RESOLVER_IMPLEMENTATION_REF,
    MIDPOINT_RESOLVER_POLICY_ID,
    QuestionOutcomePendingError,
    QuestionResolverError,
    resolve_midpoint_question,
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
    "LIQUIDITY_RESOLVER_IMPLEMENTATION_REF",
    "LIQUIDITY_RESOLVER_POLICY_ID",
    "MAX_RESOLUTION_LAG_NS_V1",
    "MIDPOINT_RESOLVER_IMPLEMENTATION_REF",
    "MIDPOINT_RESOLVER_POLICY_ID",
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
    "QuestionOutcomePendingError",
    "QuestionResolverError",
    "RESOLUTION_POLICY_ID",
    "ResolutionEvidenceRef",
    "build_competence_profiles",
    "build_question_outcome_id",
    "resolve_liquidity_question",
    "resolve_midpoint_question",
    "resolve_prediction",
    "select_resolution_frame",
    "validate_outcome_journal",
    "validate_question_outcome_journal",
]
