"""Across-question market-state synthesis contracts.

This layer sits above same-question adaptive assembly. It does not weight
experts and does not publish Benjamin intelligence.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..operations import canonical_hash


SYNTHESIS_SCHEMA_VERSION = "1.0"
SYNTHESIS_POLICY_ID = "ACROSS_QUESTION_SYNTHESIS_POLICY_V1"
SYNTHESIS_POLICY_VERSION = "1.0"
SYNTHESIS_EVENT_TYPE = "MARKET_SYNTHESIS_PUBLISHED"

QUESTION_FAMILIES = (
    "DIRECTION",
    "MAGNITUDE",
    "VOLATILITY",
    "FRAGILITY",
    "LIQUIDITY",
    "BASIS",
    "RELATIVE_VALUE",
    "REGIME",
    "PERSISTENCE",
    "REVERSAL",
)

DIMENSION_WEIGHTS = {
    "DIRECTION": 0.18,
    "MAGNITUDE": 0.10,
    "VOLATILITY": 0.10,
    "FRAGILITY": 0.10,
    "LIQUIDITY": 0.12,
    "BASIS": 0.08,
    "RELATIVE_VALUE": 0.08,
    "REGIME": 0.10,
    "PERSISTENCE": 0.07,
    "REVERSAL": 0.07,
}

INPUT_STATUSES = {"PRESENT", "NOT_ASSEMBLED", "UNAVAILABLE", "INSUFFICIENT_SUPPORT", "STALE"}
SYNTHESIS_STATUSES = {"PARTIAL", "RESEARCH_ONLY", "INCOMPLETE"}
CONTRADICTION_KINDS = {
    "DIRECT_CONTRADICTION",
    "HORIZON_TENSION",
    "STRUCTURAL_DIVERGENCE",
    "MISSING_SUPPORT",
    "STALE_INPUT",
    "LOW_INDEPENDENCE",
    "LINEAGE_UNKNOWN",
    "REGIME_TRANSITION",
    "FRAGILITY_WARNING",
}

SYNTHESIS_AUTHORITY = {
    "defines_examination_truth": False,
    "claims_competence": False,
    "sets_adaptive_weights": False,
    "synthesizes_market_evidence": True,
    "capital_decision": False,
    "risk_authorization": False,
    "external_execution": False,
    "provider_order": False,
    "benjamin_decision_instruction": False,
}

FORBIDDEN_FIELD_NAMES = {
    "buy",
    "sell",
    "hold",
    "long",
    "short",
    "position_size",
    "allocation",
    "target_exposure",
    "order",
    "take_profit",
    "stop_loss",
    "leverage",
    "capital_action",
    "pnl",
    "p_and_l",
}
AUTHORITY_DENIAL_FIELDS = set(SYNTHESIS_AUTHORITY)
FORBIDDEN_TEXT_TOKENS = ("buy", "sell", "hold", "leverage", "pnl")
FORBIDDEN_TEXT_PHRASES = (
    "go long",
    "go short",
    "take profit",
    "stop loss",
    "position size",
    "target exposure",
    "capital action",
)

STALE_HORIZON_MULTIPLE = 3


class MarketSynthesisError(RuntimeError):
    pass


def _tokens(text: str) -> Sequence[str]:
    parts = []
    current = []
    for char in str(text).lower():
        if char.isalnum():
            current.append(char)
        elif current:
            parts.append("".join(current))
            current = []
    if current:
        parts.append("".join(current))
    return tuple(parts)


def assert_no_forbidden(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered not in AUTHORITY_DENIAL_FIELDS and lowered in FORBIDDEN_FIELD_NAMES:
                raise MarketSynthesisError("synthesis contains forbidden field %s" % key)
            assert_no_forbidden(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_no_forbidden(item)
    elif isinstance(value, str):
        lowered = value.lower()
        parts = _tokens(value)
        for token in FORBIDDEN_TEXT_TOKENS:
            if token in parts:
                raise MarketSynthesisError("synthesis text contains forbidden vocabulary")
        for phrase in FORBIDDEN_TEXT_PHRASES:
            if phrase in lowered:
                raise MarketSynthesisError("synthesis text contains forbidden vocabulary")


def seal(body: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = dict(body)
    payload.pop("integrity", None)
    payload["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(payload)}
    assert_no_forbidden(payload)
    return payload
