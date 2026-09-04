from __future__ import annotations

from .catalog import COMMON_ALLOWED, COMMON_FORBIDDEN, SECOND
from .contracts import (
    AnswerKind,
    OutcomeDefinition,
    QuestionContractError,
    QuestionDefinition,
    QuestionFamily,
    QuestionRegistryEntry,
    QuestionRegistrySnapshot,
    QuestionScope,
    build_question_registry_snapshot,
)
from ..experience.contracts import ExperienceTimescale


REVERSAL_QUESTION_V1_REF = "ECONOMIC_ROOT_REVERSAL_60S@1.0.0"
REVERSAL_QUESTION_V1_1_REF = "ECONOMIC_ROOT_REVERSAL_60S@1.1.0"
REVERSAL_ROOT_PATH_RESOLVER_POLICY_ID = "PREDICTION_BOUND_ROOT_PATH_AND_FORWARD_MIDPOINT_V1"
REVERSAL_ROOT_PATH_RESOLVER_IMPLEMENTATION_REF = "autonomous_kernel.evaluation.reversal_resolver.root_path_v1"


def reversal_question_v1_1() -> QuestionDefinition:
    """Return the first reversal definition that requires causal trailing-path memory.

    v1.0.0 is intentionally left untouched. This new version makes the exact
    prediction-time Economic Root Path a required input so the trailing minute
    cannot be reconstructed after the prediction is known.
    """
    return QuestionDefinition(
        question_id="ECONOMIC_ROOT_REVERSAL_60S",
        version="1.1.0",
        family=QuestionFamily.REVERSAL,
        scope=QuestionScope.ECONOMIC_ROOT,
        asks="Will the next sixty-second midpoint return reverse the sign of the exact prediction-bound trailing sixty-second Economic Root Path return?",
        horizon_ns=60 * SECOND,
        outcome=OutcomeDefinition(
            metric_id="RETURN_SIGN_REVERSAL_60S_V2",
            answer_kind=AnswerKind.BINARY,
            target_expression="1 if sign(root_path_return_bps(T-60s,T)) != sign(same_instrument_return_bps(T,T+60s)) and both returns are non-zero else 0",
            resolver_policy_id=REVERSAL_ROOT_PATH_RESOLVER_POLICY_ID,
            max_resolution_lag_ns=5 * SECOND,
            resolution_evidence_families=("ECONOMIC_ROOT_PATH", "SPOT_MICROSTRUCTURE"),
        ),
        required_timescales=(ExperienceTimescale.SHORT,),
        required_artifact_types=("MARKET_EXPERIENCE", "ECONOMIC_ROOT_PATH"),
        required_feature_families=("SPOT_MICROSTRUCTURE", "ECONOMIC_ROOT_PATH"),
        allowed_feature_families=COMMON_ALLOWED + ("ECONOMIC_ROOT_PATH",),
        forbidden_feature_families=COMMON_FORBIDDEN,
        parameters={
            "trailing_window_ns": 60 * SECOND,
            "trailing_grid_interval_ns": 10 * SECOND,
            "trailing_path_status": "QUALIFIED",
            "trailing_path_type": "ECONOMIC_ROOT_PATH",
            "instrument_policy": "EXACT_PREDICTION_BOUND_SPOT_INSTRUMENT",
            "zero_return_policy": "EITHER_ZERO_MEANS_NO_REVERSAL",
        },
    )


def build_reversal_v1_1_registry(
    base: QuestionRegistrySnapshot,
    *,
    version: str,
    known_at_ns: int,
    effective_at_ns: int,
) -> QuestionRegistrySnapshot:
    """Append resolver-ready reversal v1.1 without rewriting historical v1.0.

    The old v1.0 reversal definition remains DEFINED in the evolved snapshot.
    That is deliberate evidence: it existed, but no resolver was retroactively
    earned for it. The new v1.1 definition is a distinct content-addressed
    question with an explicit Economic Root Path prerequisite.
    """
    if not str(version).strip():
        raise QuestionContractError("reversal registry version is required")
    if known_at_ns < base.known_at_ns or effective_at_ns < known_at_ns:
        raise QuestionContractError("reversal registry timing is invalid")
    if base.registry_id != "ZLJ-MARKET-QUESTIONS":
        raise QuestionContractError("reversal evolution requires the canonical market-question registry")

    old = [entry for entry in base.entries if entry.definition.question_ref == REVERSAL_QUESTION_V1_REF]
    if len(old) != 1:
        raise QuestionContractError("reversal v1.0 must exist exactly once before v1.1 evolution")
    if old[0].lifecycle_state != "DEFINED" or old[0].resolver_implementation_ref is not None:
        raise QuestionContractError("reversal v1.0 must remain unearned and DEFINED")
    if any(entry.definition.question_ref == REVERSAL_QUESTION_V1_1_REF for entry in base.entries):
        raise QuestionContractError("reversal v1.1 already exists in registry")

    new_definition = reversal_question_v1_1()
    entries = tuple(base.entries) + (
        QuestionRegistryEntry(
            definition=new_definition,
            lifecycle_state="RESOLVER_READY",
            registered_at_ns=int(known_at_ns),
            effective_at_ns=int(effective_at_ns),
            resolver_implementation_ref=REVERSAL_ROOT_PATH_RESOLVER_IMPLEMENTATION_REF,
        ),
    )
    return build_question_registry_snapshot(
        registry_id=base.registry_id,
        version=str(version),
        entries=entries,
        known_at_ns=int(known_at_ns),
        effective_at_ns=int(effective_at_ns),
    )
