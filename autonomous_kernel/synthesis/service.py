"""Load journaled question assemblies and persist market synthesis."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ..context.status import market_context_status
from ..intelligence.runtime import IntelligenceRuntime, validate_event_chain
from ..learning.direction_loop import SUBJECT_ID
from .contracts import MarketSynthesisError
from .reasoner import synthesize_market_state
from .renderer import render_market_story


def _z9_view(root: Path) -> Mapping[str, Any]:
    status = market_context_status(root)
    latest = status.get("latest") if isinstance(status, Mapping) else None
    latest = latest if isinstance(latest, Mapping) else {}
    return {
        "status": str(latest.get("status") or "UNAVAILABLE"),
        "content_hash": latest.get("content_hash") or latest.get("context_hash"),
        "context_id": latest.get("context_id"),
    }


def assemblies_known_by(root: Path, *, known_at_ns: int) -> Sequence[Mapping[str, Any]]:
    runtime = IntelligenceRuntime(Path(root).resolve())
    errors = validate_event_chain(runtime.events())
    if errors:
        raise MarketSynthesisError("expert intelligence journal invalid: " + "; ".join(errors))
    output = []
    for event in runtime.events():
        if event.get("event_type") != "EXPERT_ASSEMBLY_RECORDED":
            continue
        if int(event.get("occurred_at_ns") or 0) > int(known_at_ns):
            continue
        assembly = (event.get("payload") or {}).get("assembly")
        if not isinstance(assembly, Mapping):
            continue
        if int(assembly.get("known_at_ns") or 0) > int(known_at_ns):
            continue
        output.append(assembly)
    return tuple(output)


def synthesize_from_runtime(
    root: Path,
    *,
    known_at_ns: int,
    subject_id: str = SUBJECT_ID,
    cutoff_at_ns: Optional[int] = None,
) -> Mapping[str, Any]:
    z9 = _z9_view(root)
    synthesis = synthesize_market_state(
        assemblies_known_by(root, known_at_ns=known_at_ns),
        known_at_ns=int(known_at_ns),
        subject_id=subject_id,
        context_status=str(z9["status"]),
        context_hash=None if not z9.get("content_hash") else str(z9["content_hash"]),
        cutoff_at_ns=cutoff_at_ns,
    )
    sealed = dict(synthesis)
    sealed["story"] = render_market_story(synthesis)
    from .contracts import seal

    return seal({key: value for key, value in sealed.items() if key != "integrity"})


def synthesize_and_record(
    root: Path,
    *,
    known_at_ns: int,
    subject_id: str = SUBJECT_ID,
    cutoff_at_ns: Optional[int] = None,
) -> Mapping[str, Any]:
    envelope = synthesize_from_runtime(root, known_at_ns=known_at_ns, subject_id=subject_id, cutoff_at_ns=cutoff_at_ns)
    runtime = IntelligenceRuntime(Path(root).resolve())
    runtime.record_synthesis(envelope, occurred_at_ns=int(known_at_ns))
    return envelope


def market_synthesis_projection(root: Path) -> Mapping[str, Any]:
    root = Path(root).resolve()
    runtime = IntelligenceRuntime(root)
    syntheses = [item for item in (runtime.state().get("syntheses") or []) if isinstance(item, Mapping)]
    latest = syntheses[-1] if syntheses else None
    exists = latest is not None
    return {
        "exists": exists,
        "status": "ABSENT" if not exists else str(latest.get("synthesis_status") or "PARTIAL"),
        "count": len(syntheses),
        "artifact_class": "MARKET_SYNTHESIS",
        "prospective_qualification": "BLOCKED" if exists else "NONE",
        "internal_intelligence_publication": "NOT_PUBLISHED",
        "benjamin_publication": "NOT_ELIGIBLE",
        "latest": None
        if latest is None
        else {
            "synthesis_id": latest.get("synthesis_id"),
            "subject_id": latest.get("subject_id"),
            "cutoff_at_ns": latest.get("cutoff_at_ns"),
            "known_at_ns": latest.get("known_at_ns"),
            "available_dimensions": latest.get("available_dimensions"),
            "missing_dimensions": latest.get("missing_dimensions"),
            "completeness": (latest.get("support") or {}).get("completeness"),
            "complete": (latest.get("support") or {}).get("complete"),
            "synthesis_confidence": (latest.get("confidence") or {}).get("synthesis_confidence"),
            "directional_probability": (latest.get("confidence") or {}).get("directional_probability"),
            "contradictions": latest.get("contradictions"),
            "context_status": latest.get("context_status"),
            "evidence_independence_summary": latest.get("evidence_independence_summary"),
            "prospective_qualification": latest.get("prospective_qualification"),
            "internal_intelligence_publication": latest.get("internal_intelligence_publication"),
            "benjamin_publication": latest.get("benjamin_publication"),
            "blocking_reasons": latest.get("blocking_reasons"),
            "story": latest.get("story"),
            "direction_state": latest.get("direction_state"),
            "magnitude_state": latest.get("magnitude_state"),
            "volatility_state": latest.get("volatility_state"),
            "fragility_state": latest.get("fragility_state"),
            "liquidity_state": latest.get("liquidity_state"),
            "basis_state": latest.get("basis_state"),
            "relative_value_state": latest.get("relative_value_state"),
            "regime_state": latest.get("regime_state"),
            "persistence_state": latest.get("persistence_state"),
            "reversal_state": latest.get("reversal_state"),
            "content_hash": (latest.get("integrity") or {}).get("content_hash"),
            "policy_id": latest.get("policy_id"),
            "policy_version": latest.get("policy_version"),
        },
        "authority": {
            "synthesizes_market_evidence": True,
            "sets_adaptive_weights": False,
            "capital_decision": False,
            "benjamin_eligible": False,
            "internal_intelligence_published": False,
        },
    }
