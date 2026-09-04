from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence

from ..operations import canonical_hash


INTELLIGENCE_SCHEMA_VERSION = "1.0"
INTERNAL_PUBLICATION_TYPE = "ZLJ_INTERNAL_INTELLIGENCE"
INTELLIGENCE_AUTHORITY = {
    "perception_only": True,
    "economic_decision": False,
    "capital_allocation": False,
    "risk_authorization": False,
    "external_execution": False,
}
FORBIDDEN_INSTRUCTION_FIELDS = (
    "buy",
    "sell",
    "hold",
    "position_size",
    "portfolio_action",
    "portfolio_instruction",
    "capital_action",
    "capital_allocation",
    "risk_authorization",
    "execution_instruction",
    "provider_order",
)


class IntelligencePublicationError(ValueError):
    pass


def build_intelligence_publication(
    assembly: Mapping[str, Any],
    *,
    published_at_ns: int,
    evidence_refs: Sequence[str],
    competence_memory_hash: str,
    market_context_hash: str,
    question_definition_hash: str,
    horizon_ns: int,
) -> Mapping[str, Any]:
    """Publish question-bound ZLJ internal intelligence.

    This package is ZLJ_INTERNAL_INTELLIGENCE. Benjamin must not consume it
    until a separate qualification gate produces a Benjamin handoff envelope.
    """
    refs = tuple(str(value) for value in evidence_refs)
    if not refs or any(not value for value in refs) or len(set(refs)) != len(refs):
        raise IntelligencePublicationError("evidence_refs must be unique and non-empty")
    if int(published_at_ns) < 0 or int(horizon_ns) < 0:
        raise IntelligencePublicationError("published_at_ns and horizon_ns must be non-negative")
    for value, field in (
        (competence_memory_hash, "competence_memory_hash"),
        (market_context_hash, "market_context_hash"),
        (question_definition_hash, "question_definition_hash"),
    ):
        text = str(value).lower()
        if len(text) != 64:
            raise IntelligencePublicationError("%s must be SHA-256 hex" % field)
        try:
            int(text, 16)
        except ValueError as exc:
            raise IntelligencePublicationError("%s must be SHA-256 hex" % field) from exc
    if not isinstance(assembly, Mapping) or "question_ref" not in assembly or "assembled_estimate" not in assembly:
        raise IntelligencePublicationError("assembly is incomplete")

    contributions = assembly.get("expert_contributions") or ()
    body: Dict[str, Any] = {
        "schema_version": INTELLIGENCE_SCHEMA_VERSION,
        "publication_type": INTERNAL_PUBLICATION_TYPE,
        "published_at_ns": int(published_at_ns),
        "known_at_ns": int(published_at_ns),
        "question_ref": assembly["question_ref"],
        "question_definition_hash": str(question_definition_hash).lower(),
        "horizon_ns": int(horizon_ns),
        "claim_kind": assembly["claim_kind"],
        "assembled_estimate": assembly["assembled_estimate"],
        "assembly_confidence": float(assembly.get("assembly_confidence", 0.0)),
        "disagreement": float(assembly.get("disagreement", 1.0)),
        "current_context": dict(assembly.get("current_context") or {}),
        "expert_evidence": [dict(item) for item in contributions],
        "provenance": {
            "assembly_hash": assembly.get("integrity", {}).get("content_hash"),
            "competence_memory_hash": str(competence_memory_hash).lower(),
            "market_context_hash": str(market_context_hash).lower(),
            "evidence_refs": list(refs),
        },
        "authority": dict(INTELLIGENCE_AUTHORITY),
        "consumer_boundary": {
            "may_be_consumed_by": ["ZLJ"],
            "not_consumable_by": ["BENJAMIN", "WATCHMAN", "THE_HAND"],
            "does_not_instruct": ["BENJAMIN", "WATCHMAN", "THE_HAND"],
        },
    }
    sealed_body = dict(body)
    sealed_body["publication_id"] = "ZLJ-INT-" + canonical_hash(body)
    publication = dict(sealed_body)
    publication["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(sealed_body)}
    validate_intelligence_publication(publication)
    return publication


def validate_intelligence_publication(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise IntelligencePublicationError("intelligence publication schema is invalid")
    if value.get("handoff_type") == "BENJAMIN_QUALIFIED_INTELLIGENCE" or value.get("publication_type") == "BENJAMIN_QUALIFIED_INTELLIGENCE":
        raise IntelligencePublicationError("internal publication cannot masquerade as Benjamin handoff")
    if value.get("schema_version") != INTELLIGENCE_SCHEMA_VERSION:
        raise IntelligencePublicationError("intelligence publication schema is invalid")
    if value.get("publication_type") != INTERNAL_PUBLICATION_TYPE:
        raise IntelligencePublicationError("publication_type must be ZLJ_INTERNAL_INTELLIGENCE")
    if value.get("authority") != INTELLIGENCE_AUTHORITY:
        raise IntelligencePublicationError("intelligence authority boundary changed")
    boundary = value.get("consumer_boundary")
    if not isinstance(boundary, Mapping) or "BENJAMIN" in (boundary.get("may_be_consumed_by") or []):
        raise IntelligencePublicationError("internal intelligence is not Benjamin-consumable")
    for forbidden in FORBIDDEN_INSTRUCTION_FIELDS:
        if forbidden in value:
            raise IntelligencePublicationError("intelligence publication cannot contain %s" % forbidden)
    confidence = value.get("assembly_confidence")
    if not isinstance(confidence, (int, float) ) or isinstance(confidence, bool) or not 0.0 <= float(confidence) <= 1.0:
        raise IntelligencePublicationError("assembly_confidence must be in [0,1]")
    integrity = value.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("algorithm") != "sha256":
        raise IntelligencePublicationError("publication integrity is missing")
    body = {key: item for key, item in value.items() if key != "integrity"}
    if integrity.get("content_hash") != canonical_hash(body):
        raise IntelligencePublicationError("publication content hash mismatch")
