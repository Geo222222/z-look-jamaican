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
from .question_evaluation import (
    QUESTION_EVALUATION_SCHEMA_VERSION,
    SCORING_POLICY_BY_ANSWER_KIND,
    UNRESOLVABLE_SCORING_POLICY_ID,
    QuestionBoundEvaluation,
    QuestionEvaluationError,
    build_question_evaluation,
)
from .question_evaluation_journal import (
    QuestionEvaluationJournal,
    QuestionEvaluationJournalError,
    validate_question_evaluation_journal,
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
from .question_path_resolvers import (
    FIXED_GRID_RESOLVER_IMPLEMENTATION_REF,
    FIXED_GRID_RESOLVER_POLICY_ID,
    resolve_fixed_grid_question,
)
from .question_resolvers import (
    MIDPOINT_RESOLVER_IMPLEMENTATION_REF,
    MIDPOINT_RESOLVER_POLICY_ID,
    QuestionOutcomePendingError,
    QuestionResolverError,
    resolve_midpoint_question,
)
from .regime_resolver import (
    REGIME_ENDPOINT_IMPLEMENTATION_REF,
    REGIME_ENDPOINT_POLICY_ID,
    REGIME_PERSISTENCE_IMPLEMENTATION_REF,
    REGIME_PERSISTENCE_POLICY_ID,
    RegimeContractDiscontinuityError,
    resolve_market_regime_question,
    resolve_regime_persistence_question,
)
from .relationship_resolver import (
    RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF,
    RELATIONSHIP_RESOLVER_POLICY_ID,
    resolve_relationship_question,
)
from .resolver import (
    MAX_RESOLUTION_LAG_NS_V1,
    OutcomePendingError,
    OutcomeResolutionError,
    resolve_prediction,
    select_resolution_frame,
)
from .reversal_resolver import (
    REVERSAL_ROOT_PATH_RESOLVER_IMPLEMENTATION_REF,
    REVERSAL_ROOT_PATH_RESOLVER_POLICY_ID,
    ReversalResolverError,
    resolve_reversal_question,
)

__all__ = [
    "COMPETENCE_SCHEMA_VERSION",
    "FIXED_GRID_RESOLVER_IMPLEMENTATION_REF",
    "FIXED_GRID_RESOLVER_POLICY_ID",
    "LIQUIDITY_RESOLVER_IMPLEMENTATION_REF",
    "LIQUIDITY_RESOLVER_POLICY_ID",
    "MAX_RESOLUTION_LAG_NS_V1",
    "MIDPOINT_RESOLVER_IMPLEMENTATION_REF",
    "MIDPOINT_RESOLVER_POLICY_ID",
    "QUESTION_EVALUATION_SCHEMA_VERSION",
    "REGIME_ENDPOINT_IMPLEMENTATION_REF",
    "REGIME_ENDPOINT_POLICY_ID",
    "REGIME_PERSISTENCE_IMPLEMENTATION_REF",
    "REGIME_PERSISTENCE_POLICY_ID",
    "RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF",
    "RELATIONSHIP_RESOLVER_POLICY_ID",
    "REVERSAL_ROOT_PATH_RESOLVER_IMPLEMENTATION_REF",
    "REVERSAL_ROOT_PATH_RESOLVER_POLICY_ID",
    "RESOLUTION_POLICY_ID",
    "SAMPLE_STRENGTH_PRIOR_COUNT",
    "SCORING_POLICY_BY_ANSWER_KIND",
    "UNRESOLVABLE_SCORING_POLICY_ID",
    "CompetenceError",
    "CompetenceProfile",
    "OutcomeContractError",
    "OutcomeJournal",
    "OutcomeJournalError",
    "OutcomePendingError",
    "OutcomeResolutionError",
    "PredictionOutcome",
    "QuestionBoundEvaluation",
    "QuestionBoundOutcome",
    "QuestionEvaluationError",
    "QuestionEvaluationJournal",
    "QuestionEvaluationJournalError",
    "QuestionOutcomeError",
    "QuestionOutcomeJournal",
    "QuestionOutcomeJournalError",
    "QuestionOutcomePendingError",
    "QuestionResolverError",
    "RegimeContractDiscontinuityError",
    "ResolutionEvidenceRef",
    "ReversalResolverError",
    "build_competence_profiles",
    "build_question_evaluation",
    "build_question_outcome_id",
    "resolve_fixed_grid_question",
    "resolve_liquidity_question",
    "resolve_market_regime_question",
    "resolve_midpoint_question",
    "resolve_prediction",
    "resolve_regime_persistence_question",
    "resolve_relationship_question",
    "resolve_reversal_question",
    "select_resolution_frame",
    "validate_outcome_journal",
    "validate_question_evaluation_journal",
    "validate_question_outcome_journal",
]
