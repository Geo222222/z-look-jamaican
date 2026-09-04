from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..context.contracts import MarketContextFrame
from ..operations import canonical_hash
from ..research.features import extract_context_features
from .publication import build_intelligence_publication, validate_intelligence_publication


BENJAMIN_GATE_SCHEMA_VERSION = "1.0"
BENJAMIN_HANDOFF_SCHEMA_VERSION = "1.0"

BENJAMIN_GATE_AUTHORITY = {
    "evaluates_publication_eligibility": True,
    "economic_decision": False,
    "capital_allocation": False,
    "risk_authorization": False,
    "external_execution": False,
}

DEFAULT_BENJAMIN_PUBLICATION_POLICY = {
    "minimum_expert_count": 2,
    "minimum_samples_per_expert": 5,
    "minimum_total_scored_samples": 20,
    "minimum_contextual_sample_support": 0.30,
    "minimum_assembly_confidence": 0.55,
    "maximum_disagreement": 0.35,
    "maximum_single_expert_weight": 0.75,
    "maximum_context_age_ns": 30_000_000_000,
    "maximum_competence_age_ns": 21_600_000_000_000,
}


class BenjaminPublicationGateError(ValueError):
    pass


def _digest(value: Any, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise BenjaminPublicationGateError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise BenjaminPublicationGateError("%s must be SHA-256 hex" % field) from exc
    return text


def _seal(body: Mapping[str, Any]) -> Dict[str, Any]:
    value = dict(body)
    value["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
    return value


def _validate_hash_envelope(value: Mapping[str, Any], field: str) -> str:
    integrity = value.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("algorithm") != "sha256":
        raise BenjaminPublicationGateError("%s integrity is missing" % field)
    digest = _digest(integrity.get("content_hash"), "%s content_hash" % field)
    body = {key: item for key, item in value.items() if key != "integrity"}
    if canonical_hash(body) != digest:
        raise BenjaminPublicationGateError("%s content hash mismatch" % field)
    return digest


def _policy(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    policy = dict(DEFAULT_BENJAMIN_PUBLICATION_POLICY)
    if value:
        unknown = set(value).difference(policy)
        if unknown:
            raise BenjaminPublicationGateError("unknown publication policy fields: %s" % ", ".join(sorted(unknown)))
        policy.update(value)
    integer_fields = (
        "minimum_expert_count",
        "minimum_samples_per_expert",
        "minimum_total_scored_samples",
        "maximum_context_age_ns",
        "maximum_competence_age_ns",
    )
    probability_fields = (
        "minimum_contextual_sample_support",
        "minimum_assembly_confidence",
        "maximum_disagreement",
        "maximum_single_expert_weight",
    )
    for field in integer_fields:
        if isinstance(policy[field], bool) or int(policy[field]) < 0:
            raise BenjaminPublicationGateError("publication policy %s must be non-negative integer" % field)
        policy[field] = int(policy[field])
    if policy["minimum_expert_count"] < 1 or policy["minimum_samples_per_expert"] < 1 or policy["minimum_total_scored_samples"] < 1:
        raise BenjaminPublicationGateError("publication policy sample/expert minima must be positive")
    for field in probability_fields:
        number = float(policy[field])
        if not 0.0 <= number <= 1.0:
            raise BenjaminPublicationGateError("publication policy %s must be in [0,1]" % field)
        policy[field] = number
    return policy


def _canonical_context_matches(assembly: Mapping[str, Any], context: MarketContextFrame) -> bool:
    canonical = extract_context_features(context)["features"]
    current = assembly.get("current_context")
    if not isinstance(current, Mapping) or not current:
        return False
    for key, value in current.items():
        if key not in canonical or canonical[key] != value:
            return False
    return True


def assess_benjamin_publication_eligibility(
    assembly: Mapping[str, Any],
    competence_memory: Mapping[str, Any],
    market_context: MarketContextFrame,
    *,
    evaluated_at_ns: int,
    policy: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Any]:
    """Fail-closed empirical gate for the ZLJ -> Benjamin intelligence bridge."""
    if not isinstance(assembly, Mapping) or not isinstance(competence_memory, Mapping):
        raise BenjaminPublicationGateError("assembly and competence memory are required")
    assembly_hash = _validate_hash_envelope(assembly, "assembly")
    competence_hash = _validate_hash_envelope(competence_memory, "competence memory")
    context_hash = market_context.content_hash()
    settings = _policy(policy)
    now = int(evaluated_at_ns)
    if now < 0:
        raise BenjaminPublicationGateError("evaluated_at_ns must be non-negative")

    contributions = assembly.get("expert_contributions")
    if not isinstance(contributions, Sequence) or isinstance(contributions, (str, bytes)):
        contributions = ()
    question_ref = str(assembly.get("question_ref", ""))
    competence_entries = {
        (str(item.get("expert_ref", "")), str(item.get("question_ref", ""))): item
        for item in competence_memory.get("entries", ())
        if isinstance(item, Mapping)
    }
    sample_counts = []
    contribution_support = []
    contribution_weights = []
    missing_competence = []
    for contribution in contributions:
        if not isinstance(contribution, Mapping):
            missing_competence.append("MALFORMED_CONTRIBUTION")
            continue
        expert_ref = str(contribution.get("expert_ref", ""))
        entry = competence_entries.get((expert_ref, question_ref))
        if entry is None:
            missing_competence.append(expert_ref or "UNKNOWN_EXPERT")
            continue
        sample_counts.append(int(entry.get("sample_count", 0) or 0))
        contextual = contribution.get("contextual_competence")
        if isinstance(contextual, Mapping):
            contribution_support.append(float(contextual.get("sample_support", 0.0) or 0.0))
        else:
            contribution_support.append(0.0)
        contribution_weights.append(float(contribution.get("weight", 0.0) or 0.0))

    expert_count = int(assembly.get("expert_count", len(contributions)) or 0)
    total_samples = sum(sample_counts)
    minimum_samples = min(sample_counts) if sample_counts else 0
    minimum_support = min(contribution_support) if contribution_support else 0.0
    maximum_weight = max(contribution_weights) if contribution_weights else 1.0
    confidence = float(assembly.get("assembly_confidence", 0.0) or 0.0)
    disagreement = float(assembly.get("disagreement", 1.0) or 0.0)
    context_age = now - int(market_context.known_at_ns)
    competence_known = int(competence_memory.get("known_at_ns", -1))
    competence_age = now - competence_known if competence_known >= 0 else -1

    checks = {
        "assembly_question_present": bool(question_ref),
        "expert_count_sufficient": expert_count >= settings["minimum_expert_count"],
        "contribution_count_matches": expert_count == len(contributions),
        "all_contributions_have_competence": not missing_competence and len(sample_counts) == expert_count,
        "samples_per_expert_sufficient": minimum_samples >= settings["minimum_samples_per_expert"],
        "total_scored_samples_sufficient": total_samples >= settings["minimum_total_scored_samples"],
        "contextual_sample_support_sufficient": minimum_support >= settings["minimum_contextual_sample_support"],
        "assembly_confidence_sufficient": confidence >= settings["minimum_assembly_confidence"],
        "disagreement_bounded": disagreement <= settings["maximum_disagreement"],
        "single_expert_weight_bounded": maximum_weight <= settings["maximum_single_expert_weight"],
        "market_context_qualified": market_context.status == "QUALIFIED",
        "market_context_known_before_gate": int(market_context.known_at_ns) <= now,
        "market_context_fresh": 0 <= context_age <= settings["maximum_context_age_ns"],
        "assembly_context_matches_exact_z9": _canonical_context_matches(assembly, market_context) if market_context.status == "QUALIFIED" else False,
        "competence_known_before_gate": 0 <= competence_known <= now,
        "competence_fresh": 0 <= competence_age <= settings["maximum_competence_age_ns"],
        "assembly_competence_hash_consistent": all(
            isinstance(item, Mapping) and str(item.get("claim_hash", ""))
            for item in contributions
        ),
    }
    reasons = [key for key, passed in sorted(checks.items()) if not passed]
    body = {
        "schema_version": BENJAMIN_GATE_SCHEMA_VERSION,
        "gate_type": "ZLJ_BENJAMIN_PUBLICATION_ELIGIBILITY",
        "question_ref": question_ref,
        "evaluated_at_ns": now,
        "status": "ELIGIBLE" if not reasons else "BLOCKED",
        "checks": checks,
        "blocking_reasons": reasons,
        "diagnostics": {
            "expert_count": expert_count,
            "minimum_samples_per_contributing_expert": minimum_samples,
            "total_scored_samples": total_samples,
            "minimum_contextual_sample_support": minimum_support,
            "maximum_single_expert_weight": maximum_weight,
            "assembly_confidence": confidence,
            "disagreement": disagreement,
            "market_context_age_ns": context_age,
            "competence_age_ns": competence_age,
            "missing_competence_experts": missing_competence,
        },
        "policy": settings,
        "provenance": {
            "assembly_hash": assembly_hash,
            "competence_memory_hash": competence_hash,
            "market_context_hash": context_hash,
            "market_context_id": market_context.context_id,
        },
        "authority": dict(BENJAMIN_GATE_AUTHORITY),
    }
    return _seal(body)


def validate_benjamin_publication_eligibility(receipt: Mapping[str, Any]) -> None:
    if receipt.get("schema_version") != BENJAMIN_GATE_SCHEMA_VERSION or receipt.get("gate_type") != "ZLJ_BENJAMIN_PUBLICATION_ELIGIBILITY":
        raise BenjaminPublicationGateError("Benjamin publication gate schema/type invalid")
    checks = receipt.get("checks")
    if not isinstance(checks, Mapping) or not checks or any(not isinstance(value, bool) for value in checks.values()):
        raise BenjaminPublicationGateError("Benjamin publication gate checks invalid")
    expected_reasons = [key for key, passed in sorted(checks.items()) if not passed]
    if receipt.get("blocking_reasons") != expected_reasons:
        raise BenjaminPublicationGateError("Benjamin publication gate blocking reasons inconsistent")
    expected_status = "ELIGIBLE" if not expected_reasons else "BLOCKED"
    if receipt.get("status") != expected_status:
        raise BenjaminPublicationGateError("Benjamin publication gate status inconsistent")
    if receipt.get("authority") != BENJAMIN_GATE_AUTHORITY:
        raise BenjaminPublicationGateError("Benjamin publication gate authority changed")
    provenance = receipt.get("provenance")
    if not isinstance(provenance, Mapping):
        raise BenjaminPublicationGateError("Benjamin publication provenance missing")
    for field in ("assembly_hash", "competence_memory_hash", "market_context_hash"):
        _digest(provenance.get(field), field)
    _policy(receipt.get("policy") if isinstance(receipt.get("policy"), Mapping) else None)
    _validate_hash_envelope(receipt, "Benjamin publication gate")


def build_benjamin_handoff(
    assembly: Mapping[str, Any],
    competence_memory: Mapping[str, Any],
    market_context: MarketContextFrame,
    *,
    published_at_ns: int,
    evidence_refs: Sequence[str],
    policy: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Any]:
    gate = assess_benjamin_publication_eligibility(
        assembly,
        competence_memory,
        market_context,
        evaluated_at_ns=published_at_ns,
        policy=policy,
    )
    validate_benjamin_publication_eligibility(gate)
    if gate["status"] != "ELIGIBLE":
        raise BenjaminPublicationGateError("Benjamin handoff blocked: %s" % ", ".join(gate["blocking_reasons"]))
    publication = build_intelligence_publication(
        assembly,
        published_at_ns=published_at_ns,
        evidence_refs=evidence_refs,
        competence_memory_hash=gate["provenance"]["competence_memory_hash"],
        market_context_hash=gate["provenance"]["market_context_hash"],
    )
    validate_intelligence_publication(publication)
    body = {
        "schema_version": BENJAMIN_HANDOFF_SCHEMA_VERSION,
        "handoff_type": "ZLJ_INTELLIGENCE_TO_BENJAMIN",
        "published_at_ns": int(published_at_ns),
        "question_ref": assembly["question_ref"],
        "eligibility": gate,
        "intelligence": publication,
        "consumer_boundary": {
            "may_be_consumed_by": ["BENJAMIN"],
            "economic_decision_remains_with": "BENJAMIN",
            "risk_authorization_remains_with": "WATCHMAN",
            "execution_remains_with": "THE_HAND",
        },
        "authority": dict(BENJAMIN_GATE_AUTHORITY),
    }
    return _seal(body)


def validate_benjamin_handoff(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != BENJAMIN_HANDOFF_SCHEMA_VERSION or value.get("handoff_type") != "ZLJ_INTELLIGENCE_TO_BENJAMIN":
        raise BenjaminPublicationGateError("Benjamin handoff schema/type invalid")
    eligibility = value.get("eligibility")
    intelligence = value.get("intelligence")
    if not isinstance(eligibility, Mapping) or not isinstance(intelligence, Mapping):
        raise BenjaminPublicationGateError("Benjamin handoff envelope malformed")
    validate_benjamin_publication_eligibility(eligibility)
    if eligibility.get("status") != "ELIGIBLE":
        raise BenjaminPublicationGateError("Benjamin handoff cannot contain blocked eligibility")
    validate_intelligence_publication(intelligence)
    if value.get("question_ref") != intelligence.get("question_ref") or value.get("question_ref") != eligibility.get("question_ref"):
        raise BenjaminPublicationGateError("Benjamin handoff question identity mismatch")
    provenance = intelligence.get("provenance")
    gate_provenance = eligibility.get("provenance")
    if not isinstance(provenance, Mapping) or not isinstance(gate_provenance, Mapping):
        raise BenjaminPublicationGateError("Benjamin handoff provenance malformed")
    for field in ("assembly_hash", "competence_memory_hash", "market_context_hash"):
        if provenance.get(field) != gate_provenance.get(field):
            raise BenjaminPublicationGateError("Benjamin handoff %s differs from gate" % field)
    if value.get("authority") != BENJAMIN_GATE_AUTHORITY:
        raise BenjaminPublicationGateError("Benjamin handoff authority changed")
    boundary = value.get("consumer_boundary")
    if not isinstance(boundary, Mapping) or boundary.get("may_be_consumed_by") != ["BENJAMIN"]:
        raise BenjaminPublicationGateError("Benjamin handoff consumer boundary invalid")
    _validate_hash_envelope(value, "Benjamin handoff")
