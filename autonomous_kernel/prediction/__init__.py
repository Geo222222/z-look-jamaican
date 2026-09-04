"""Prediction contracts and durable journals.

Legacy Z3 prediction contracts remain intact for frozen evidence. The
question-bound contracts are the forward learning architecture and bind each
claim to an immutable Question Registry definition and legal evidence set.
"""

from .contracts import Prediction, PredictionContractError
from .factory import PredictionFactoryError, create_prediction, representation_target_price
from .journal import PredictionJournal, PredictionJournalError, validate_prediction_journal
from .question_bound import (
    PredictionArtifactRef,
    QuestionBoundPrediction,
    QuestionPredictionError,
    build_question_bound_prediction,
    normalize_question_answer,
)
from .question_journal import (
    QuestionPredictionJournal,
    QuestionPredictionJournalError,
    validate_question_prediction_journal,
)

__all__ = [
    "Prediction",
    "PredictionArtifactRef",
    "PredictionContractError",
    "PredictionFactoryError",
    "PredictionJournal",
    "PredictionJournalError",
    "QuestionBoundPrediction",
    "QuestionPredictionError",
    "QuestionPredictionJournal",
    "QuestionPredictionJournalError",
    "build_question_bound_prediction",
    "create_prediction",
    "normalize_question_answer",
    "representation_target_price",
    "validate_prediction_journal",
    "validate_question_prediction_journal",
]
