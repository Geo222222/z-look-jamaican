from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..context.contracts import MarketContextFrame
from ..experts.school import active_question_definitions
from ..operations import canonical_hash
from .policy import (
    BenjaminPublicationPolicyError,
    build_benjamin_publication_policy_v1,
    validate_benjamin_publication_policy,
)
from .publication import (
    FORBIDDEN_INSTRUCTION_FIELDS,
    INTERNAL_PUBLICATION_TYPE,
    IntelligencePublicationError,
    validate_intelligence_publication,
)


GATE_SCHEMA_VERSION = "1.0"
HANDOFF_SCHEMA_VERSION = "1.0"
GATE_TYPE = "BENJAMIN_PUBLICATION_QUALIFICATION"
HANDOFF_TYPE = "BENJAMIN_QUALIFIED_INTELLIGENCE"

GATE_AUTHORITY = {
    "evaluates_publication_eligibility": True,
    "economic_decision": False,
    "capital_allocation": False,
    "risk_authorization": False,
    "external_execution": False,
    "provider_order_creation": False,
}

HANDOFF_AUTHORITY = {
    "may_be_consumed_by": "BENJAMIN",
    "economic_decision_remains_with": "BENJAMIN",
    "risk_authorization_remains_with": "WATCHMAN",
    "execution_remains_with": "THE_HAND",
    "capital_allocation": False,
    "portfolio_instruction": False,
    "risk_authorization": False,
    "external_execution": False,
    "provider_order_creation": False,
}

REASON_INSUFFICIENT_TOTAL_SAMPLE_SUPPORT = "INSUFFICIENT_TOTAL_SAMPLE_SUPPORT"
REASON_INSUFFICIENT_CONTEXTUAL_SAMPLE_SUPPORT = "INSUFFICIENT_CONTEXTUAL_SAMPLE_SUPPORT"
REASON_STALE_COMPETENCE = "STALE_COMPETENCE"
REASON_STALE_CONTEXT = "STALE_CONTEXT"
REASON_CONTEXT_NOT_QUALIFIED = "CONTEXT_NOT_QUALIFIED"
REASON_EXCESSIVE_DISAGREEMENT = "EXCESSIVE_DISAGREEMENT"
REASON_EXCESSIVE_EXPERT_DOMINANCE = "EXCESSIVE_EXPERT_DOMINANCE"
REASON_INSUFFICIENT_EVIDENCE_INDEPENDENCE = "INSUFFICIENT_EVIDENCE_INDEPENDENCE"
REASON_ASSEMBLY_CONTEXT_MISMATCH = "ASSEMBLY_CONTEXT_MISMATCH"
REASON_COMPETENCE_PROVENANCE_MISMATCH = "COMPETENCE_PROVENANCE_MISMATCH"
REASON_DATA_QUALITY_NOT_VALID = "DATA_QUALITY_NOT_VALID"
REASON_INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
REASON_MISSING_REQUIRED_EVIDENCE = "MISSING_REQUIRED_EVIDENCE"
REASON_FUTURE_INFORMATION_LEAKAGE = "FUTURE_INFORMATION_LEAKAGE"
REASON_INSUFFICIENT_EXPERT_COUNT = "INSUFFICIENT_EXPERT_COUNT"
REASON_INSUFFICIENT_PER_EXPERT_SAMPLES = "INSUFFICIENT_PER_EXPERT_SAMPLES"
REASON_INSUFFICIENT_ASSEMBLY_CONFIDENCE = "INSUFFICIENT_ASSEMBLY_CONFIDENCE"


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


def _contains_forbidden_fields(value: Any) -> Optional[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in {"authority", "denies", "consumer_boundary"}:
                continue
            lowered = str(key).lower()
            if lowered in FORBIDDEN_INSTRUCTION_FIELDS:
                return str(key)
            nested = _contains_forbidden_fields(item)
            if nested:
                return nested
    elif isinstance(value, (list, tuple)):
        for item in value:
            nested = _contains_forbidden_fields(item)
            if nested:
                return nested
    return None


def _mean_pairwise_jaccard(claims: Sequence[Mapping[str, Any]]) -> float:
    sets = [set(str(item) for item in claim.get("evidence_refs") or ()) for claim in claims]
    pairs = 0
    total = 0.0
    for index, left in enumerate(sets):
        for right in sets[index + 1 :]:
            union = left | right
            if not union:
                continue
            total += len(left & right) / float(len(union))
            pairs += 1
    return 0.0 if pairs == 0 else total / float(pairs)


def _z9_school_context(context: MarketContextFrame) -> Dict[str, Any]:
    regimes = context.state.get("regimes") if isinstance(context.state.get("regimes"), Mapping) else {}
    projected: Dict[str, Any] = {}
    if regimes.get("direction") is not None:
        projected["regime"] = str(regimes["direction"])
    for key in ("liquidity", "volatility", "correlation"):
        if regimes.get(key) is not None:
            projected[key] = str(regimes[key])
    return projected


def _policy_or_default(policy: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    if policy is None:
        return build_benjamin_publication_policy_v1()
    try:
        validate_benjamin_publication_policy(policy)
    except BenjaminPublicationPolicyError as exc:
        raise BenjaminPublicationGateError(str(exc)) from exc
    return policy


def qualification_identity(
    *,
    publication_hash: str,
    assembly_hash: str,
    competence_hash: str,
    context_hash: str,
    policy_hash: str,
    qualification_cutoff_ns: int,
) -> str:
    return canonical_hash({
        "publication_hash": publication_hash,
        "assembly_hash": assembly_hash,
        "competence_hash": competence_hash,
        "context_hash": context_hash,
        "policy_hash": policy_hash,
        "qualification_cutoff_ns": int(qualification_cutoff_ns),
    })


def assess_benjamin_publication_qualification(
    publication: Mapping[str, Any],
    assembly: Mapping[str, Any],
    competence_memory: Mapping[str, Any],
    market_context: MarketContextFrame,
    *,
    qualification_cutoff_ns: int,
    claims: Optional[Sequence[Mapping[str, Any]]] = None,
    data_quality: Optional[Mapping[str, Any]] = None,
    policy: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Any]:
    """Fail-closed epistemic gate. Does not decide, size, authorize, or execute."""
    reasons: List[str] = []
    diagnostics: Dict[str, Any] = {}
    cutoff = int(qualification_cutoff_ns)
    if cutoff < 0:
        raise BenjaminPublicationGateError("qualification_cutoff_ns must be non-negative")

    publication_hash = None
    assembly_hash = None
    competence_hash = None
    policy_hash = None
    context_hash = None
    settings: Dict[str, Any] = {}
    try:
        sealed_policy = _policy_or_default(policy)
        policy_hash = _validate_hash_envelope(sealed_policy, "policy")
        settings = dict(sealed_policy["thresholds"])
        validate_intelligence_publication(publication)
        if publication.get("publication_type") != INTERNAL_PUBLICATION_TYPE:
            reasons.append(REASON_INTEGRITY_FAILURE)
        if "BENJAMIN" in (publication.get("consumer_boundary") or {}).get("may_be_consumed_by") or []:
            reasons.append(REASON_INTEGRITY_FAILURE)
        publication_hash = _validate_hash_envelope(publication, "publication")
        assembly_hash = _validate_hash_envelope(assembly, "assembly")
        competence_hash = _validate_hash_envelope(competence_memory, "competence memory")
        context_hash = market_context.content_hash()
    except (BenjaminPublicationGateError, IntelligencePublicationError, BenjaminPublicationPolicyError) as exc:
        diagnostics["integrity_error"] = str(exc)
        reasons.append(REASON_INTEGRITY_FAILURE)
        sealed_policy = policy if isinstance(policy, Mapping) else build_benjamin_publication_policy_v1()
        settings = dict(sealed_policy.get("thresholds") or {}) if isinstance(sealed_policy.get("thresholds"), Mapping) else dict(settings)
        policy_hash = str((sealed_policy.get("integrity") or {}).get("content_hash") or "0" * 64)

    forbidden = _contains_forbidden_fields(publication) or _contains_forbidden_fields(assembly)
    if forbidden:
        reasons.append(REASON_INTEGRITY_FAILURE)
        diagnostics["forbidden_field"] = forbidden

    if data_quality is None or not isinstance(data_quality, Mapping) or not data_quality.get("state"):
        reasons.append(REASON_MISSING_REQUIRED_EVIDENCE)
    elif str(data_quality.get("state")) != str(settings.get("required_data_quality_state", "VALID")):
        reasons.append(REASON_DATA_QUALITY_NOT_VALID)

    claim_list: Tuple[Mapping[str, Any], ...] = tuple(claims or ())
    if not claim_list:
        reasons.append(REASON_MISSING_REQUIRED_EVIDENCE)

    contributions = assembly.get("expert_contributions") if isinstance(assembly, Mapping) else None
    if not isinstance(contributions, Sequence) or isinstance(contributions, (str, bytes)):
        contributions = ()
        reasons.append(REASON_MISSING_REQUIRED_EVIDENCE)

    question_ref = str((publication or {}).get("question_ref") or assembly.get("question_ref") or "")
    question_hash = str((publication or {}).get("question_definition_hash") or "")
    horizon_ns = publication.get("horizon_ns") if isinstance(publication, Mapping) else None
    definitions = active_question_definitions()
    catalog = definitions.get(question_ref)
    if catalog is None:
        reasons.append(REASON_MISSING_REQUIRED_EVIDENCE)
    else:
        if question_hash and question_hash != catalog.content_hash():
            reasons.append(REASON_INTEGRITY_FAILURE)
        if horizon_ns is not None and int(horizon_ns) != int(catalog.horizon_ns):
            reasons.append(REASON_INTEGRITY_FAILURE)
        if not question_hash:
            reasons.append(REASON_MISSING_REQUIRED_EVIDENCE)
            question_hash = catalog.content_hash()
        if horizon_ns is None:
            reasons.append(REASON_MISSING_REQUIRED_EVIDENCE)
            horizon_ns = catalog.horizon_ns

    provenance = publication.get("provenance") if isinstance(publication, Mapping) else None
    if not isinstance(provenance, Mapping):
        reasons.append(REASON_MISSING_REQUIRED_EVIDENCE)
        provenance = {}
    if assembly_hash and provenance.get("assembly_hash") != assembly_hash:
        reasons.append(REASON_COMPETENCE_PROVENANCE_MISMATCH)
    if competence_hash and provenance.get("competence_memory_hash") != competence_hash:
        reasons.append(REASON_COMPETENCE_PROVENANCE_MISMATCH)
    if context_hash and provenance.get("market_context_hash") != context_hash:
        reasons.append(REASON_ASSEMBLY_CONTEXT_MISMATCH)

    published_at = int((publication or {}).get("published_at_ns") or -1)
    competence_known = int((competence_memory or {}).get("known_at_ns") or -1)
    context_known = int(market_context.known_at_ns)
    if published_at > cutoff or competence_known > cutoff or context_known > cutoff:
        reasons.append(REASON_FUTURE_INFORMATION_LEAKAGE)
    if published_at >= 0 and competence_known >= 0 and not (competence_known <= published_at <= cutoff):
        if competence_known > published_at:
            reasons.append(REASON_FUTURE_INFORMATION_LEAKAGE)

    if market_context.status != settings.get("required_context_status", "QUALIFIED"):
        reasons.append(REASON_CONTEXT_NOT_QUALIFIED)
    context_age = cutoff - context_known
    if context_known < 0 or context_age < 0 or context_age > int(settings.get("maximum_context_age_ns") or 0):
        reasons.append(REASON_STALE_CONTEXT)
    competence_age = cutoff - competence_known
    if competence_known < 0 or competence_age < 0 or competence_age > int(settings.get("maximum_competence_age_ns") or 0):
        reasons.append(REASON_STALE_COMPETENCE)

    projected = _z9_school_context(market_context)
    current = assembly.get("current_context") if isinstance(assembly, Mapping) else None
    if not isinstance(current, Mapping) or not current:
        reasons.append(REASON_ASSEMBLY_CONTEXT_MISMATCH)
    else:
        for key, value in current.items():
            if key not in projected or projected[key] != value:
                reasons.append(REASON_ASSEMBLY_CONTEXT_MISMATCH)
                break

    competence_entries = {
        (str(item.get("expert_ref", "")), str(item.get("question_ref", ""))): item
        for item in (competence_memory.get("entries") or ())
        if isinstance(item, Mapping)
    }
    sample_counts: List[int] = []
    supports: List[float] = []
    weights: List[float] = []
    claim_by_hash = {
        str(claim.get("integrity", {}).get("content_hash")): claim
        for claim in claim_list
        if isinstance(claim, Mapping)
    }
    for contribution in contributions:
        if not isinstance(contribution, Mapping):
            reasons.append(REASON_MISSING_REQUIRED_EVIDENCE)
            continue
        expert_ref = str(contribution.get("expert_ref", ""))
        entry = competence_entries.get((expert_ref, question_ref))
        if entry is None:
            reasons.append(REASON_COMPETENCE_PROVENANCE_MISMATCH)
            continue
        last_resolved = int(entry.get("last_resolved_at_ns") or -1)
        if last_resolved > cutoff:
            reasons.append(REASON_FUTURE_INFORMATION_LEAKAGE)
        sample_counts.append(int(entry.get("sample_count", 0) or 0))
        contextual = contribution.get("contextual_competence")
        if isinstance(contextual, Mapping):
            supports.append(float(contextual.get("sample_support", 0.0) or 0.0))
        else:
            supports.append(0.0)
            reasons.append(REASON_MISSING_REQUIRED_EVIDENCE)
        weights.append(float(contribution.get("weight", 0.0) or 0.0))
        claim_hash = str(contribution.get("claim_hash", ""))
        claim = claim_by_hash.get(claim_hash)
        if claim is None:
            reasons.append(REASON_MISSING_REQUIRED_EVIDENCE)
        elif str(claim.get("expert_ref")) != expert_ref or str(claim.get("question_ref")) != question_ref:
            reasons.append(REASON_COMPETENCE_PROVENANCE_MISMATCH)
        elif int(claim.get("cutoff_ns") or -1) > cutoff:
            reasons.append(REASON_FUTURE_INFORMATION_LEAKAGE)

    expert_count = int(assembly.get("expert_count", len(contributions)) or 0)
    total_samples = sum(sample_counts)
    min_samples = min(sample_counts) if sample_counts else 0
    min_support = min(supports) if supports else 0.0
    max_weight = max(weights) if weights else 1.0
    confidence = float(assembly.get("assembly_confidence", 0.0) or 0.0)
    disagreement = float(assembly.get("disagreement", 1.0) or 0.0)
    overlap = _mean_pairwise_jaccard(claim_list) if len(claim_list) >= 2 else 1.0 if claim_list else 1.0

    if expert_count < int(settings.get("minimum_expert_count") or 0) or expert_count != len(tuple(contributions)):
        reasons.append(REASON_INSUFFICIENT_EXPERT_COUNT)
    if total_samples < int(settings.get("minimum_total_scored_samples") or 0):
        reasons.append(REASON_INSUFFICIENT_TOTAL_SAMPLE_SUPPORT)
    if min_samples < int(settings.get("minimum_samples_per_expert") or 0):
        reasons.append(REASON_INSUFFICIENT_PER_EXPERT_SAMPLES)
    if min_support < float(settings.get("minimum_contextual_sample_support") or 0.0):
        reasons.append(REASON_INSUFFICIENT_CONTEXTUAL_SAMPLE_SUPPORT)
    if confidence < float(settings.get("minimum_assembly_confidence") or 0.0):
        reasons.append(REASON_INSUFFICIENT_ASSEMBLY_CONFIDENCE)
    if disagreement > float(settings.get("maximum_disagreement") or 0.0):
        reasons.append(REASON_EXCESSIVE_DISAGREEMENT)
    if max_weight > float(settings.get("maximum_single_expert_weight") or 0.0):
        reasons.append(REASON_EXCESSIVE_EXPERT_DOMINANCE)
    if overlap > float(settings.get("maximum_mean_pairwise_evidence_jaccard") or 0.0):
        reasons.append(REASON_INSUFFICIENT_EVIDENCE_INDEPENDENCE)

    unique_reasons = []
    for reason in reasons:
        if reason not in unique_reasons:
            unique_reasons.append(reason)
    unique_reasons.sort()
    status = "ELIGIBLE" if not unique_reasons else "BLOCKED"
    identity = qualification_identity(
        publication_hash=publication_hash or "0" * 64,
        assembly_hash=assembly_hash or "0" * 64,
        competence_hash=competence_hash or "0" * 64,
        context_hash=context_hash or "0" * 64,
        policy_hash=policy_hash or "0" * 64,
        qualification_cutoff_ns=cutoff,
    )
    diagnostics.update({
        "expert_count": expert_count,
        "total_scored_samples": total_samples,
        "minimum_samples_per_contributing_expert": min_samples,
        "minimum_contextual_sample_support": min_support,
        "maximum_single_expert_weight": max_weight,
        "assembly_confidence": confidence,
        "disagreement": disagreement,
        "mean_pairwise_evidence_jaccard": overlap,
        "market_context_age_ns": context_age,
        "competence_age_ns": competence_age,
        "data_quality_state": None if data_quality is None else data_quality.get("state"),
    })
    body = {
        "schema_version": GATE_SCHEMA_VERSION,
        "result_type": GATE_TYPE,
        "qualification_id": "BPQ-" + identity,
        "status": status,
        "blocking_reasons": unique_reasons,
        "qualification_cutoff_ns": cutoff,
        "known_at_ns": cutoff,
        "question_ref": question_ref,
        "question_definition_hash": question_hash,
        "horizon_ns": int(horizon_ns or 0),
        "diagnostics": diagnostics,
        "policy": {
            "policy_id": sealed_policy.get("policy_id"),
            "policy_version": sealed_policy.get("policy_version"),
            "policy_hash": policy_hash,
        },
        "provenance": {
            "publication_hash": publication_hash,
            "assembly_hash": assembly_hash,
            "competence_memory_hash": competence_hash,
            "market_context_hash": context_hash,
            "market_context_id": market_context.context_id,
        },
        "authority": dict(GATE_AUTHORITY),
    }
    receipt = _seal(body)
    validate_benjamin_publication_qualification(receipt)
    return receipt


def validate_benjamin_publication_qualification(receipt: Mapping[str, Any]) -> None:
    if receipt.get("schema_version") != GATE_SCHEMA_VERSION or receipt.get("result_type") != GATE_TYPE:
        raise BenjaminPublicationGateError("qualification result schema/type invalid")
    if receipt.get("status") not in {"ELIGIBLE", "BLOCKED"}:
        raise BenjaminPublicationGateError("qualification status invalid")
    reasons = receipt.get("blocking_reasons")
    if not isinstance(reasons, list) or any(not isinstance(item, str) or not item for item in reasons):
        raise BenjaminPublicationGateError("blocking_reasons invalid")
    if sorted(reasons) != reasons:
        raise BenjaminPublicationGateError("blocking_reasons must be sorted")
    if (receipt.get("status") == "ELIGIBLE") != (not reasons):
        raise BenjaminPublicationGateError("qualification status inconsistent with reasons")
    if receipt.get("authority") != GATE_AUTHORITY:
        raise BenjaminPublicationGateError("qualification authority changed")
    forbidden = _contains_forbidden_fields({key: value for key, value in receipt.items() if key not in {"diagnostics", "authority"}})
    if forbidden:
        raise BenjaminPublicationGateError("qualification result cannot contain %s" % forbidden)
    _validate_hash_envelope(receipt, "qualification result")


def build_benjamin_handoff(
    publication: Mapping[str, Any],
    qualification: Mapping[str, Any],
    assembly: Mapping[str, Any],
    competence_memory: Mapping[str, Any],
    market_context: MarketContextFrame,
    claims: Sequence[Mapping[str, Any]],
    *,
    created_at_ns: int,
) -> Mapping[str, Any]:
    validate_benjamin_publication_qualification(qualification)
    if qualification.get("status") != "ELIGIBLE":
        raise BenjaminPublicationGateError("blocked publication cannot create Benjamin handoff")
    validate_intelligence_publication(publication)
    publication_hash = _validate_hash_envelope(publication, "publication")
    assembly_hash = _validate_hash_envelope(assembly, "assembly")
    competence_hash = _validate_hash_envelope(competence_memory, "competence memory")
    if publication_hash != qualification["provenance"]["publication_hash"]:
        raise BenjaminPublicationGateError("handoff publication hash does not match qualification")
    if assembly_hash != qualification["provenance"]["assembly_hash"]:
        raise BenjaminPublicationGateError("handoff assembly hash does not match qualification")
    forbidden = _contains_forbidden_fields(publication)
    if forbidden:
        raise BenjaminPublicationGateError("handoff cannot contain %s" % forbidden)
    expert_refs = [str(item.get("expert_ref")) for item in assembly.get("expert_contributions") or ()]
    evidence_refs = []
    for claim in claims:
        evidence_refs.extend(str(item) for item in claim.get("evidence_refs") or ())
    unique_evidence = []
    for ref in list(publication.get("provenance", {}).get("evidence_refs") or []) + evidence_refs:
        if ref not in unique_evidence:
            unique_evidence.append(ref)
    created = int(created_at_ns)
    if created < int(qualification["qualification_cutoff_ns"]):
        raise BenjaminPublicationGateError("handoff created_at cannot precede qualification cutoff")
    if created < int(publication.get("published_at_ns") or 0):
        raise BenjaminPublicationGateError("handoff created_at cannot precede internal publication")
    body = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "handoff_type": HANDOFF_TYPE,
        "handoff_id": "BHO-" + qualification["qualification_id"],
        "created_at_ns": created,
        "known_at_ns": int(qualification["known_at_ns"]),
        "question_ref": publication["question_ref"],
        "question_definition_hash": publication["question_definition_hash"],
        "horizon_ns": int(publication["horizon_ns"]),
        "internal_publication_id": publication.get("publication_id") or publication_hash,
        "internal_publication_hash": publication_hash,
        "assembly_hash": assembly_hash,
        "qualification_result_id": qualification["qualification_id"],
        "qualification_result_hash": qualification["integrity"]["content_hash"],
        "qualification_policy_id": qualification["policy"]["policy_id"],
        "qualification_policy_hash": qualification["policy"]["policy_hash"],
        "market_context_id": market_context.context_id,
        "market_context_hash": market_context.content_hash(),
        "competence_memory_hash": competence_hash,
        "contributing_expert_refs": expert_refs,
        "evidence_refs": unique_evidence,
        "authority": dict(HANDOFF_AUTHORITY),
        "denies": {
            "capital_allocation": True,
            "portfolio_instruction": True,
            "risk_authorization": True,
            "external_execution": True,
            "provider_order_creation": True,
        },
    }
    handoff = _seal(body)
    validate_benjamin_handoff(handoff)
    return handoff


def validate_benjamin_handoff(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != HANDOFF_SCHEMA_VERSION or value.get("handoff_type") != HANDOFF_TYPE:
        raise BenjaminPublicationGateError("Benjamin handoff schema/type invalid")
    if value.get("handoff_type") == INTERNAL_PUBLICATION_TYPE:
        raise BenjaminPublicationGateError("internal publication cannot masquerade as Benjamin handoff")
    authority = value.get("authority")
    if authority != HANDOFF_AUTHORITY:
        raise BenjaminPublicationGateError("Benjamin handoff authority changed")
    if authority.get("may_be_consumed_by") != "BENJAMIN":
        raise BenjaminPublicationGateError("Benjamin handoff consumer boundary invalid")
    denies = value.get("denies")
    if not isinstance(denies, Mapping) or not all(denies.get(key) is True for key in (
        "capital_allocation", "portfolio_instruction", "risk_authorization", "external_execution", "provider_order_creation"
    )):
        raise BenjaminPublicationGateError("Benjamin handoff must deny capital, risk, and execution")
    forbidden = _contains_forbidden_fields({key: item for key, item in value.items() if key not in {"authority", "denies"}})
    if forbidden:
        raise BenjaminPublicationGateError("Benjamin handoff cannot contain %s" % forbidden)
    for field in (
        "internal_publication_hash",
        "assembly_hash",
        "qualification_result_hash",
        "qualification_policy_hash",
        "market_context_hash",
        "competence_memory_hash",
        "question_definition_hash",
    ):
        _digest(value.get(field), field)
    _validate_hash_envelope(value, "Benjamin handoff")
