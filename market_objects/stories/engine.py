"""Deterministic, competing-hypothesis market story selection."""

from typing import Any, Mapping, Sequence

from .market_story import market_story


def _state(objects: Sequence[Mapping[str, Any]], object_type: str) -> str:
    item = next((value for value in objects if value.get("object_type") == object_type), None)
    return str(item["payload"]["state"]) if item else "UNAVAILABLE"


def determine_market_story(
    *, object_id: str, subject: Mapping[str, Any], effective_at: str, created_at: str,
    objects: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Compose a hypothesis while preserving every input as a separately typed object."""
    allowed = [item for item in objects if item.get("layer") in {"STRUCTURE", "PERCEPTION", "CONTEXT", "STATE", "TRANSITION"}]
    structure = next((item for item in allowed if item.get("object_type") == "PRICE_STRUCTURE"), None)
    transitions = [item for item in allowed if item.get("object_type") == "STATE_TRANSITION"]
    trend, volatility = _state(allowed, "TREND_STATE"), _state(allowed, "VOLATILITY_STATE")
    momentum, participation = _state(allowed, "MOMENTUM_STATE"), _state(allowed, "PARTICIPATION_STATE")
    risk = _state(allowed, "RISK_STATE")
    range_status = structure["payload"]["range"]["status"] if structure else "UNAVAILABLE"
    vol_expansion = any(item["payload"].get("to") == "EXPANDING" for item in transitions)
    contradictions = []

    if range_status in {"BROKEN_UP", "BROKEN_DOWN"} and trend in {"UP", "DOWN"} and volatility == "EXPANDING" and vol_expansion:
        story, confidence = "EARLY_TREND_EXPANSION", 0.78
        expected = ["DIRECTIONAL_FOLLOW_THROUGH", "BREAKOUT_RETEST", "FAILED_BREAKOUT"]
        alternatives = [{"story": "FAILED_BREAKOUT", "confidence": 0.28}, {"story": "LATE_STAGE_EXHAUSTION", "confidence": 0.18}]
        invalidation = [{"condition": "PRICE_REENTERS_PRIOR_RANGE"}, {"condition": "VOLATILITY_TRANSITION_REVERSES"}, {"condition": "TREND_STATE_REVERSES"}]
    elif risk == "ELEVATED" and trend in {"UP", "DOWN"} and volatility == "EXPANDING":
        story, confidence = "LATE_STAGE_EXHAUSTION_CANDIDATE", 0.67
        expected = ["MOMENTUM_DECAY", "REVERSAL", "CONTINUED_EXTENSION"]
        alternatives = [{"story": "TREND_CONTINUATION", "confidence": 0.40}]
        invalidation = [{"condition": "EXTENSION_NORMALIZES_WITHOUT_STRUCTURE_BREAK"}, {"condition": "PARTICIPATION_REACCELERATES_WITH_TREND"}]
    elif trend in {"RANGE_OR_MIXED", "COMPRESSION"} and volatility in {"LOW", "COMPRESSED"}:
        story, confidence = "BALANCED_COMPRESSION", 0.72
        expected = ["RANGE_CONTINUES", "VOLATILITY_EXPANSION"]
        alternatives = [{"story": "QUIET_ACCUMULATION_OR_DISTRIBUTION", "confidence": 0.30}]
        invalidation = [{"condition": "RANGE_BREAK_CONFIRMED"}, {"condition": "VOLATILITY_EXPANDS"}]
    else:
        story, confidence = "MIXED_UNRESOLVED", 0.50
        expected = ["MORE_EVIDENCE_REQUIRED"]
        alternatives = [{"story": "TREND_FORMATION", "confidence": 0.25}, {"story": "RANGE_FORMATION", "confidence": 0.25}]
        invalidation = [{"condition": "A_COHERENT_STATE_AND_TRANSITION_CONFIGURATION_EMERGES"}]

    if (trend == "UP" and momentum == "NEGATIVE") or (trend == "DOWN" and momentum == "POSITIVE"):
        contradictions.extend(item for item in allowed if item.get("object_type") == "MOMENTUM_STATE")
        confidence -= 0.12
    if participation in {"BELOW_NORMAL", "UNAVAILABLE"} and story == "EARLY_TREND_EXPANSION":
        contradictions.extend(item for item in allowed if item.get("object_type") == "PARTICIPATION_STATE")
        confidence -= 0.10

    return market_story(
        object_id=object_id, subject=subject, effective_at=effective_at, created_at=created_at,
        supporting_objects=allowed, primary={"story": story, "confidence": round(max(0.0, confidence), 4),
        "interpretation": "Deterministic hypothesis selected from typed state, structure, context, and transition objects."},
        alternatives=alternatives, contradiction_refs=contradictions, expected_next_states=expected,
        invalidation_conditions=invalidation,
    )
