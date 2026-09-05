"""Deterministic human-readable renderer for a sealed market synthesis."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import MarketSynthesisError


def _clause_join(parts: Sequence[str]) -> str:
    items = [item for item in parts if item]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return "%s and %s" % (items[0], items[1])
    return "%s, and %s" % (", ".join(items[:-1]), items[-1])


def render_market_story(synthesis: Mapping[str, Any]) -> str:
    if not isinstance(synthesis, Mapping) or synthesis.get("artifact_class") != "MARKET_SYNTHESIS":
        raise MarketSynthesisError("renderer requires a sealed MARKET_SYNTHESIS artifact")
    direction = synthesis.get("direction_state") or {}
    liquidity = synthesis.get("liquidity_state") or {}
    volatility = synthesis.get("volatility_state") or {}
    regime = synthesis.get("regime_state") or {}
    missing = list(synthesis.get("missing_dimensions") or [])
    findings = list(synthesis.get("findings") or [])
    kinds = {item.get("kind") for item in findings if isinstance(item, Mapping)}

    sentences = []
    if direction.get("status") in {"PRESENT", "STALE"}:
        fact = direction.get("fact") or {}
        polarity = str(fact.get("polarity") or "BALANCED").lower()
        category = str(direction.get("timescale_category") or "MICRO").lower()
        if category == "medium":
            horizon_label = "session-horizon"
        elif category == "macro":
            horizon_label = "macro-horizon"
        elif category == "short":
            horizon_label = "brief-horizon"
        else:
            horizon_label = "micro-horizon"
        if polarity == "upward":
            lead = "%s direction is positive" % horizon_label.capitalize()
        elif polarity == "downward":
            lead = "%s direction is negative" % horizon_label.capitalize()
        else:
            lead = "%s direction is balanced" % horizon_label.capitalize()
        caveats = []
        if liquidity.get("status") in {"PRESENT", "STALE"} and (liquidity.get("fact") or {}).get("condition") == "DETERIORATING":
            caveats.append("liquidity is deteriorating")
        if volatility.get("status") in {"PRESENT", "STALE"} and (volatility.get("fact") or {}).get("level") == "ELEVATED":
            caveats.append("volatility is elevated")
        if "REGIME_TRANSITION" in kinds:
            caveats.append("reversal or regime persistence evidence indicates transition")
        if caveats:
            sentences.append("%s, but confidence is limited because %s." % (lead, _clause_join(caveats)))
        else:
            sentences.append("%s." % lead)
    else:
        sentences.append("Direction evidence is unavailable.")

    if "HORIZON_TENSION" in kinds:
        sentences.append("Broader regime testimony differs across horizon, which is a horizon tension rather than a same-timescale contradiction.")
    elif regime.get("status") not in {"PRESENT", "STALE"}:
        sentences.append("Broader regime evidence is unavailable.")

    if "STRUCTURAL_DIVERGENCE" in kinds:
        sentences.append("Basis and relative-value testimony indicate structural dislocation.")
    if "MISSING_SUPPORT" in kinds and missing:
        remaining = [name.lower().replace("_", " ") for name in missing if name != "REGIME"]
        if remaining and direction.get("status") in {"PRESENT", "STALE"}:
            sentences.append("Other intended dimensions remain unavailable.")
    completeness = (synthesis.get("support") or {}).get("complete")
    if not completeness:
        sentences.append("The synthesis is partial and does not claim complete market understanding.")
    return " ".join(sentences)
