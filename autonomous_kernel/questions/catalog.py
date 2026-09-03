from __future__ import annotations

from typing import Tuple

from ..experience.contracts import ExperienceTimescale
from .contracts import (
    AnswerKind,
    OutcomeDefinition,
    QuestionDefinition,
    QuestionFamily,
    QuestionRegistryEntry,
    QuestionRegistrySnapshot,
    QuestionScope,
    build_question_registry_snapshot,
)


SECOND = 1_000_000_000
MINUTE = 60 * SECOND

COMMON_ALLOWED = (
    "SPOT_MICROSTRUCTURE",
    "DERIVATIVE_MICROSTRUCTURE",
    "DERIVATIVE_POSITIONING",
    "DERIVATIVE_FINANCING",
    "DERIVATIVE_LIQUIDATIONS",
    "MARK_INDEX_DIVERGENCE",
    "TERM_STRUCTURE",
    "MARKET_WIDE_CONTEXT",
    "MARKET_WIDE_TRAJECTORY",
    "ECONOMIC_RELATIONSHIP_STATE",
)
COMMON_FORBIDDEN = (
    "FUTURE_OUTCOME",
    "POST_CUTOFF_MARKET_DATA",
    "BENJAMIN_CAPITAL_STATE",
    "HAND_EXECUTION_RESULT",
)


def _outcome(metric_id: str, answer_kind: AnswerKind, target_expression: str, resolver_policy_id: str, lag_ns: int, families: Tuple[str, ...]) -> OutcomeDefinition:
    return OutcomeDefinition(
        metric_id=metric_id,
        answer_kind=answer_kind,
        target_expression=target_expression,
        resolver_policy_id=resolver_policy_id,
        max_resolution_lag_ns=lag_ns,
        resolution_evidence_families=families,
    )


def question_catalog_v1() -> Tuple[QuestionDefinition, ...]:
    """First preregistered question semantics.

    These are definitions only. A question appearing here does not imply its
    outcome resolver or any model answering it has been implemented/qualified.
    """

    return (
        QuestionDefinition(
            question_id="ECONOMIC_ROOT_DIRECTION_10S",
            version="1.0.0",
            family=QuestionFamily.DIRECTION,
            scope=QuestionScope.ECONOMIC_ROOT,
            asks="Will the qualified aggregate midpoint be higher ten seconds after cutoff T?",
            horizon_ns=10 * SECOND,
            outcome=_outcome(
                "AGGREGATE_MIDPOINT_DIRECTION_10S_V1",
                AnswerKind.BINARY,
                "1 if aggregate_midpoint(T+10s) > aggregate_midpoint(T) else 0",
                "FIRST_QUALIFIED_AGGREGATE_MIDPOINT_AT_OR_AFTER_TARGET_V1",
                2 * SECOND,
                ("SPOT_MICROSTRUCTURE",),
            ),
            required_timescales=(ExperienceTimescale.MICRO,),
            required_artifact_types=("MARKET_EXPERIENCE",),
            required_feature_families=("SPOT_MICROSTRUCTURE",),
            allowed_feature_families=COMMON_ALLOWED,
            forbidden_feature_families=COMMON_FORBIDDEN,
            parameters={"direction_zero_policy": "ZERO_RETURN_IS_NOT_POSITIVE"},
        ),
        QuestionDefinition(
            question_id="ECONOMIC_ROOT_MAGNITUDE_30S",
            version="1.0.0",
            family=QuestionFamily.MAGNITUDE,
            scope=QuestionScope.ECONOMIC_ROOT,
            asks="What signed aggregate-midpoint return in basis points will occur over the next thirty seconds?",
            horizon_ns=30 * SECOND,
            outcome=_outcome(
                "AGGREGATE_MIDPOINT_RETURN_BPS_30S_V1",
                AnswerKind.CONTINUOUS,
                "10000 * (aggregate_midpoint(T+30s) / aggregate_midpoint(T) - 1)",
                "FIRST_QUALIFIED_AGGREGATE_MIDPOINT_AT_OR_AFTER_TARGET_V1",
                2 * SECOND,
                ("SPOT_MICROSTRUCTURE",),
            ),
            required_timescales=(ExperienceTimescale.MICRO, ExperienceTimescale.SHORT),
            required_artifact_types=("MARKET_EXPERIENCE", "MARKET_WIDE_EXPERIENCE"),
            required_feature_families=("SPOT_MICROSTRUCTURE", "MARKET_WIDE_CONTEXT"),
            allowed_feature_families=COMMON_ALLOWED,
            forbidden_feature_families=COMMON_FORBIDDEN,
            parameters={},
        ),
        QuestionDefinition(
            question_id="ECONOMIC_ROOT_VOLATILITY_60S",
            version="1.0.0",
            family=QuestionFamily.VOLATILITY,
            scope=QuestionScope.ECONOMIC_ROOT,
            asks="What realized aggregate-midpoint volatility will occur during the next sixty seconds?",
            horizon_ns=60 * SECOND,
            outcome=_outcome(
                "REALIZED_AGGREGATE_MIDPOINT_VOLATILITY_BPS_60S_V1",
                AnswerKind.CONTINUOUS,
                "population_stdev(qualified 5-second aggregate-midpoint returns over (T,T+60s])",
                "QUALIFIED_FIXED_GRID_AGGREGATE_MIDPOINT_PATH_V1",
                5 * SECOND,
                ("SPOT_MICROSTRUCTURE",),
            ),
            required_timescales=(ExperienceTimescale.SHORT,),
            required_artifact_types=("MARKET_EXPERIENCE", "MARKET_WIDE_EXPERIENCE"),
            required_feature_families=("SPOT_MICROSTRUCTURE", "MARKET_WIDE_CONTEXT"),
            allowed_feature_families=COMMON_ALLOWED,
            forbidden_feature_families=COMMON_FORBIDDEN,
            parameters={"resolution_grid_ns": 5 * SECOND},
        ),
        QuestionDefinition(
            question_id="ECONOMIC_ROOT_LIQUIDITY_DETERIORATION_30S",
            version="1.0.0",
            family=QuestionFamily.LIQUIDITY,
            scope=QuestionScope.ECONOMIC_ROOT,
            asks="Will executable liquidity deteriorate over the next thirty seconds?",
            horizon_ns=30 * SECOND,
            outcome=_outcome(
                "SPREAD_UP_AND_DEPTH10_DOWN_30S_V1",
                AnswerKind.BINARY,
                "1 if spread_bps(T+30s) > spread_bps(T) and depth_10bps(T+30s) < depth_10bps(T) else 0",
                "FIRST_QUALIFIED_BOOK_STATE_AT_OR_AFTER_TARGET_V1",
                2 * SECOND,
                ("SPOT_MICROSTRUCTURE",),
            ),
            required_timescales=(ExperienceTimescale.MICRO,),
            required_artifact_types=("MARKET_EXPERIENCE",),
            required_feature_families=("SPOT_MICROSTRUCTURE",),
            allowed_feature_families=COMMON_ALLOWED,
            forbidden_feature_families=COMMON_FORBIDDEN,
            parameters={"depth_band_bps": 10},
        ),
        QuestionDefinition(
            question_id="ECONOMIC_ROOT_FRAGILITY_MAE_60S",
            version="1.0.0",
            family=QuestionFamily.FRAGILITY,
            scope=QuestionScope.ECONOMIC_ROOT,
            asks="How large will the maximum adverse aggregate-midpoint excursion be during the next sixty seconds?",
            horizon_ns=60 * SECOND,
            outcome=_outcome(
                "MAX_ADVERSE_EXCURSION_BPS_60S_V1",
                AnswerKind.CONTINUOUS,
                "max(0, -min_t(10000 * (aggregate_midpoint(t)/aggregate_midpoint(T)-1))) for t in (T,T+60s]",
                "QUALIFIED_FIXED_GRID_AGGREGATE_MIDPOINT_PATH_V1",
                5 * SECOND,
                ("SPOT_MICROSTRUCTURE",),
            ),
            required_timescales=(ExperienceTimescale.MICRO, ExperienceTimescale.SHORT),
            required_artifact_types=("MARKET_EXPERIENCE", "MARKET_WIDE_EXPERIENCE"),
            required_feature_families=("SPOT_MICROSTRUCTURE", "MARKET_WIDE_CONTEXT"),
            allowed_feature_families=COMMON_ALLOWED,
            forbidden_feature_families=COMMON_FORBIDDEN,
            parameters={"resolution_grid_ns": 5 * SECOND},
        ),
        QuestionDefinition(
            question_id="SPOT_DERIVATIVE_BASIS_CHANGE_5M",
            version="1.0.0",
            family=QuestionFamily.BASIS,
            scope=QuestionScope.RELATIONSHIP,
            asks="How much will qualified spot-derivative basis change over the next five minutes?",
            horizon_ns=5 * MINUTE,
            outcome=_outcome(
                "SPOT_DERIVATIVE_BASIS_CHANGE_BPS_5M_V1",
                AnswerKind.CONTINUOUS,
                "basis_bps(T+5m) - basis_bps(T)",
                "FIRST_QUALIFIED_RELATIONSHIP_STATE_AT_OR_AFTER_TARGET_V1",
                10 * SECOND,
                ("ECONOMIC_RELATIONSHIP_STATE",),
            ),
            required_timescales=(ExperienceTimescale.SHORT, ExperienceTimescale.SESSION),
            required_artifact_types=("ECONOMIC_RELATIONSHIP_STATE", "MARKET_EXPERIENCE"),
            required_feature_families=("ECONOMIC_RELATIONSHIP_STATE",),
            allowed_feature_families=COMMON_ALLOWED,
            forbidden_feature_families=COMMON_FORBIDDEN,
            parameters={"quote_unit_policy": "DIRECT_BASIS_REQUIRES_COMPATIBLE_QUOTE_UNITS"},
        ),
        QuestionDefinition(
            question_id="MARKET_DIRECTION_REGIME_15M",
            version="1.0.0",
            family=QuestionFamily.REGIME,
            scope=QuestionScope.MARKET_WIDE,
            asks="What qualified market-wide direction regime will exist fifteen minutes after cutoff T?",
            horizon_ns=15 * MINUTE,
            outcome=_outcome(
                "MARKET_DIRECTION_REGIME_15M_V1",
                AnswerKind.CATEGORICAL,
                "Z9.market_context.regimes.direction at first qualified context at/after T+15m",
                "FIRST_QUALIFIED_MARKET_CONTEXT_AT_OR_AFTER_TARGET_V1",
                30 * SECOND,
                ("MARKET_WIDE_CONTEXT",),
            ),
            required_timescales=(ExperienceTimescale.SESSION, ExperienceTimescale.MACRO_STRUCTURAL),
            required_artifact_types=("MARKET_WIDE_EXPERIENCE",),
            required_feature_families=("MARKET_WIDE_CONTEXT", "MARKET_WIDE_TRAJECTORY"),
            allowed_feature_families=COMMON_ALLOWED,
            forbidden_feature_families=COMMON_FORBIDDEN,
            parameters={},
        ),
        QuestionDefinition(
            question_id="MARKET_REGIME_PERSISTENCE_5M",
            version="1.0.0",
            family=QuestionFamily.PERSISTENCE,
            scope=QuestionScope.MARKET_WIDE,
            asks="Will the current qualified market-wide direction regime persist through the next five minutes?",
            horizon_ns=5 * MINUTE,
            outcome=_outcome(
                "MARKET_DIRECTION_REGIME_PERSISTENCE_5M_V1",
                AnswerKind.BINARY,
                "1 if every qualified market context in (T,T+5m] has direction regime equal to regime(T) else 0",
                "QUALIFIED_MARKET_CONTEXT_INTERVAL_V1",
                30 * SECOND,
                ("MARKET_WIDE_CONTEXT",),
            ),
            required_timescales=(ExperienceTimescale.SESSION,),
            required_artifact_types=("MARKET_WIDE_EXPERIENCE",),
            required_feature_families=("MARKET_WIDE_CONTEXT", "MARKET_WIDE_TRAJECTORY"),
            allowed_feature_families=COMMON_ALLOWED,
            forbidden_feature_families=COMMON_FORBIDDEN,
            parameters={},
        ),
        QuestionDefinition(
            question_id="ECONOMIC_ROOT_REVERSAL_60S",
            version="1.0.0",
            family=QuestionFamily.REVERSAL,
            scope=QuestionScope.ECONOMIC_ROOT,
            asks="Will the next sixty-second aggregate-midpoint return reverse the sign of the trailing sixty-second return known at T?",
            horizon_ns=60 * SECOND,
            outcome=_outcome(
                "RETURN_SIGN_REVERSAL_60S_V1",
                AnswerKind.BINARY,
                "1 if sign(return_bps(T-60s,T)) != sign(return_bps(T,T+60s)) and both returns are non-zero else 0",
                "QUALIFIED_TRAILING_AND_FORWARD_AGGREGATE_MIDPOINT_V1",
                5 * SECOND,
                ("SPOT_MICROSTRUCTURE",),
            ),
            required_timescales=(ExperienceTimescale.SHORT,),
            required_artifact_types=("MARKET_EXPERIENCE", "MARKET_WIDE_EXPERIENCE"),
            required_feature_families=("SPOT_MICROSTRUCTURE", "MARKET_WIDE_CONTEXT"),
            allowed_feature_families=COMMON_ALLOWED,
            forbidden_feature_families=COMMON_FORBIDDEN,
            parameters={"trailing_window_ns": 60 * SECOND},
        ),
        QuestionDefinition(
            question_id="SPOT_DERIVATIVE_RELATIVE_VALUE_CONVERGENCE_5M",
            version="1.0.0",
            family=QuestionFamily.RELATIVE_VALUE,
            scope=QuestionScope.RELATIONSHIP,
            asks="How much will the absolute spot-derivative basis dislocation converge over the next five minutes?",
            horizon_ns=5 * MINUTE,
            outcome=_outcome(
                "ABSOLUTE_BASIS_CONVERGENCE_BPS_5M_V1",
                AnswerKind.CONTINUOUS,
                "abs(basis_bps(T)) - abs(basis_bps(T+5m)); positive means convergence",
                "FIRST_QUALIFIED_RELATIONSHIP_STATE_AT_OR_AFTER_TARGET_V1",
                10 * SECOND,
                ("ECONOMIC_RELATIONSHIP_STATE",),
            ),
            required_timescales=(ExperienceTimescale.SHORT, ExperienceTimescale.SESSION),
            required_artifact_types=("ECONOMIC_RELATIONSHIP_STATE", "MARKET_WIDE_EXPERIENCE"),
            required_feature_families=("ECONOMIC_RELATIONSHIP_STATE", "MARKET_WIDE_CONTEXT"),
            allowed_feature_families=COMMON_ALLOWED,
            forbidden_feature_families=COMMON_FORBIDDEN,
            parameters={"quote_unit_policy": "DIRECT_BASIS_REQUIRES_COMPATIBLE_QUOTE_UNITS"},
        ),
    )


def default_question_registry_v1(*, registered_at_ns: int, effective_at_ns: int) -> QuestionRegistrySnapshot:
    definitions = question_catalog_v1()
    entries = tuple(
        QuestionRegistryEntry(
            definition=definition,
            lifecycle_state="DEFINED",
            registered_at_ns=registered_at_ns,
            effective_at_ns=effective_at_ns,
        )
        for definition in definitions
    )
    return build_question_registry_snapshot(
        registry_id="ZLJ-MARKET-QUESTIONS",
        version="1.0.0",
        entries=entries,
        known_at_ns=registered_at_ns,
        effective_at_ns=effective_at_ns,
    )
