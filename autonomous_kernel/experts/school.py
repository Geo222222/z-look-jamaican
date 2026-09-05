from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..operations import canonical_hash
from ..questions.catalog import question_catalog_v1
from ..questions.certification import resolver_ready_refs_v1_qualified
from ..questions.evolution import reversal_question_v1_2
from .contracts import EXPERT_AUTHORITY, build_expert_contract, validate_expert_claim


EXPERT_SCHOOL_SCHEMA_VERSION = "1.0"
COMPETENCE_SCHEMA_VERSION = "1.0"
ASSEMBLY_SCHEMA_VERSION = "1.0"
ADAPTIVE_WEIGHT_POLICY_ID = "EXPERT_SCHOOL_ADAPTIVE_WEIGHT_POLICY_V1"
ADAPTIVE_WEIGHT_POLICY_VERSION = "1.0"
NEUTRAL_COMPETENCE = 0.5
OVERLAP_PENALTY_FLOOR = 0.25
OVERLAP_PENALTY_SCALE = 0.5

SPECIES_BY_FAMILY = {
    "DIRECTION": ("NAIVE_PERSISTENCE", "LINEAR_LOGISTIC", "TREE_BOOSTING", "MICROSTRUCTURE", "TEMPORAL"),
    "MAGNITUDE": ("CONDITIONAL_MEAN", "LINEAR_REGRESSION", "TREE_BOOSTING", "TEMPORAL"),
    "VOLATILITY": ("HISTORICAL_REALIZED", "CONDITIONAL_VOLATILITY", "TREE_BOOSTING", "TEMPORAL"),
    "FRAGILITY": ("VOLATILITY_FRAGILITY", "LIQUIDITY_FRAGILITY", "DERIVATIVE_STRUCTURE", "CROWDING_LIQUIDATION"),
    "LIQUIDITY": ("SPREAD", "DEPTH", "ORDER_BOOK_PRESSURE", "VENUE_LIQUIDITY"),
    "BASIS": ("SPOT_FUTURES_BASIS", "TERM_STRUCTURE", "DERIVATIVE_STRUCTURE"),
    "RELATIVE_VALUE": ("CROSS_VENUE_RELATIVE_VALUE", "CONVERGENCE", "DERIVATIVE_STRUCTURE"),
    "REGIME": ("STATISTICAL_STATE", "TREE_CLASSIFIER", "TEMPORAL_REGIME"),
    "PERSISTENCE": ("STATE_PERSISTENCE", "TREND_PERSISTENCE", "REGIME_DURATION"),
    "REVERSAL": ("MOMENTUM_EXHAUSTION", "MICROSTRUCTURE_REVERSAL", "MEAN_REVERSION", "REGIME_TRANSITION"),
}


class ExpertSchoolError(ValueError):
    pass


def active_question_definitions() -> Dict[str, Any]:
    definitions = {item.question_ref: item for item in question_catalog_v1()}
    material_reversal = reversal_question_v1_2()
    definitions[material_reversal.question_ref] = material_reversal
    return {ref: definitions[ref] for ref in resolver_ready_refs_v1_qualified()}


def build_baseline_expert_school() -> Mapping[str, Any]:
    """Construct the Phase-10 candidate population without claiming competence.

    The factory creates governed expert identities only. Training, fitting, replay,
    walk-forward and shadow evidence are separate concerns and cannot be inferred
    from a contract's existence.
    """
    experts: List[Mapping[str, Any]] = []
    for question_ref, question in sorted(active_question_definitions().items()):
        family = question.family.value
        for species in SPECIES_BY_FAMILY.get(family, ("GENERIC_BASELINE",)):
            expert_id = "%s_%s_EXPERT" % (question.question_id, species)
            implementation_ref = "zlj.experts.%s.%s:v1" % (family.lower(), species.lower())
            implementation_hash = canonical_hash({
                "implementation_ref": implementation_ref,
                "question_ref": question_ref,
                "species": species,
                "factory_schema": EXPERT_SCHOOL_SCHEMA_VERSION,
            })
            experts.append(build_expert_contract(
                expert_id=expert_id,
                version="1.0.0",
                species=species,
                implementation_ref=implementation_ref,
                implementation_hash=implementation_hash,
                model_refs=(),
                question_refs=(question_ref,),
                required_artifact_types=question.required_artifact_types,
                allowed_feature_families=question.required_feature_families,
                parameters={"factory": "BASELINE_EXPERT_FACTORY_V1"},
            ))
    body = {
        "schema_version": EXPERT_SCHOOL_SCHEMA_VERSION,
        "lifecycle_state": "CANDIDATE_POPULATION",
        "experts": experts,
        "expert_count": len(experts),
        "authority": dict(EXPERT_AUTHORITY),
        "claims_competence": False,
        "sets_adaptive_weights": False,
    }
    body["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash({k: v for k, v in body.items() if k != "integrity"})}
    return body


def score_expert_claim(contract: Mapping[str, Any], claim: Mapping[str, Any], resolved_answer: Any, *, resolved_at_ns: int, context: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
    """Phase 11: grade one immutable claim against resolver-produced truth."""
    validate_expert_claim(contract, claim)
    if int(resolved_at_ns) < int(claim["cutoff_ns"]) + int(claim["horizon_ns"]):
        raise ExpertSchoolError("outcome cannot be scored before the question horizon")
    kind = claim["claim_kind"]
    predicted = claim["answer"]
    if kind == "PROBABILITY":
        actual = 1.0 if bool(resolved_answer) else 0.0
        p = float(predicted)
        brier = (p - actual) ** 2
        score = 1.0 - brier
        metrics = {"brier": brier, "accuracy": 1.0 if (p >= 0.5) == bool(actual) else 0.0}
    elif kind == "POINT_ESTIMATE":
        actual = float(resolved_answer)
        error = float(predicted) - actual
        score = 1.0 / (1.0 + abs(error))
        metrics = {"absolute_error": abs(error), "squared_error": error * error}
    else:
        if not isinstance(predicted, Mapping):
            raise ExpertSchoolError("distribution claim is malformed")
        label = str(resolved_answer)
        probability = float(predicted.get(label, 0.0))
        score = probability
        metrics = {"assigned_probability_to_realized_label": probability, "log_loss": -math.log(max(probability, 1e-15))}
    body = {
        "schema_version": "1.0",
        "expert_ref": claim["expert_ref"],
        "question_ref": claim["question_ref"],
        "claim_hash": claim["integrity"]["content_hash"],
        "cutoff_ns": int(claim["cutoff_ns"]),
        "resolved_at_ns": int(resolved_at_ns),
        "score": float(score),
        "metrics": metrics,
        "context": dict(context or {}),
        "authority": dict(EXPERT_AUTHORITY),
    }
    body["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash({k: v for k, v in body.items() if k != "integrity"})}
    return body


def build_competence_memory(records: Sequence[Mapping[str, Any]], *, now_ns: int, recent_half_life_ns: int = 3_600_000_000_000) -> Mapping[str, Any]:
    """Phase 12: reconstruct earned expert/question competence from scored history."""
    if int(now_ns) < 0 or int(recent_half_life_ns) <= 0:
        raise ExpertSchoolError("invalid competence clock")
    groups: Dict[Tuple[str, str], List[Mapping[str, Any]]] = {}
    for record in records:
        key = (str(record["expert_ref"]), str(record["question_ref"]))
        groups.setdefault(key, []).append(record)
    entries: List[Mapping[str, Any]] = []
    for (expert_ref, question_ref), items in sorted(groups.items()):
        scores = [float(item["score"]) for item in items]
        weighted_sum = 0.0
        weight_total = 0.0
        contexts: Dict[str, Dict[str, List[float]]] = {}
        for item, score in zip(items, scores):
            age = max(0, int(now_ns) - int(item["resolved_at_ns"]))
            weight = math.exp(-math.log(2.0) * age / float(recent_half_life_ns))
            weighted_sum += weight * score
            weight_total += weight
            for dimension, value in dict(item.get("context") or {}).items():
                contexts.setdefault(str(dimension), {}).setdefault(str(value), []).append(score)
        context_scores = {
            dimension: {value: sum(vals) / len(vals) for value, vals in sorted(values.items())}
            for dimension, values in sorted(contexts.items())
        }
        entries.append({
            "expert_ref": expert_ref,
            "question_ref": question_ref,
            "sample_count": len(items),
            "mean_score": sum(scores) / len(scores),
            "recent_score": weighted_sum / weight_total if weight_total else 0.0,
            "context_scores": context_scores,
            "last_resolved_at_ns": max(int(item["resolved_at_ns"]) for item in items),
        })
    body = {
        "schema_version": COMPETENCE_SCHEMA_VERSION,
        "known_at_ns": int(now_ns),
        "entries": entries,
        "entry_count": len(entries),
        "authority": {**dict(EXPERT_AUTHORITY), "claims_competence": True, "sets_adaptive_weights": False},
    }
    body["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash({k: v for k, v in body.items() if k != "integrity"})}
    return body


def contextual_competence(entry: Mapping[str, Any], current_context: Mapping[str, Any]) -> Mapping[str, Any]:
    """Phase 13: estimate how relevant earned competence is to conditions now."""
    global_score = float(entry.get("mean_score", 0.0))
    recent_score = float(entry.get("recent_score", global_score))
    matched: List[float] = []
    used: List[str] = []
    context_scores = entry.get("context_scores") or {}
    if isinstance(context_scores, Mapping):
        for dimension, current_value in current_context.items():
            values = context_scores.get(str(dimension))
            if isinstance(values, Mapping) and str(current_value) in values:
                matched.append(float(values[str(current_value)]))
                used.append(str(dimension))
    contextual = sum(matched) / len(matched) if matched else global_score
    support = min(1.0, math.log1p(int(entry.get("sample_count", 0))) / math.log(101.0))
    earned = (0.35 * global_score) + (0.30 * recent_score) + (0.35 * contextual)
    shrunken = support * earned + (1.0 - support) * NEUTRAL_COMPETENCE
    return {
        "expert_ref": entry["expert_ref"],
        "question_ref": entry["question_ref"],
        "global_competence": global_score,
        "recent_competence": recent_score,
        "contextual_match": contextual,
        "earned_competence": earned,
        "contextual_score": max(0.0, min(1.0, shrunken)),
        "sample_support": support,
        "sample_count": int(entry.get("sample_count", 0)),
        "matched_context_dimensions": used,
    }


def _evidence_overlap_ratio(left: Iterable[str], right: Iterable[str]) -> float:
    first = set(str(item) for item in left)
    second = set(str(item) for item in right)
    if not first or not second:
        return 0.0
    return len(first & second) / float(min(len(first), len(second)))


def _overlap_penalty(claim: Mapping[str, Any], claims: Sequence[Mapping[str, Any]]) -> Tuple[float, float]:
    evidence = set(str(value) for value in claim.get("evidence_refs", ()))
    if not evidence or len(claims) < 2:
        return 1.0, 0.0
    overlaps = []
    for other in claims:
        if other is claim:
            continue
        overlaps.append(_evidence_overlap_ratio(evidence, other.get("evidence_refs", ())))
    mean_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0
    return max(OVERLAP_PENALTY_FLOOR, 1.0 - OVERLAP_PENALTY_SCALE * mean_overlap), mean_overlap


def _verify_competence_memory(competence_memory: Mapping[str, Any], *, assembly_at_ns: Optional[int] = None) -> None:
    if not isinstance(competence_memory, Mapping):
        raise ExpertSchoolError("competence memory is required")
    integrity = competence_memory.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("algorithm") != "sha256":
        raise ExpertSchoolError("competence memory lacks integrity")
    body = {key: value for key, value in competence_memory.items() if key != "integrity"}
    if integrity.get("content_hash") != canonical_hash(body):
        raise ExpertSchoolError("competence memory content hash mismatch")
    known_at = competence_memory.get("known_at_ns")
    if assembly_at_ns is not None and known_at is not None and int(known_at) > int(assembly_at_ns):
        raise ExpertSchoolError("future competence cannot enter earlier assembly")


def assemble_expert_claims(
    claims: Sequence[Mapping[str, Any]],
    competence_memory: Mapping[str, Any],
    current_context: Mapping[str, Any],
    *,
    assembly_at_ns: Optional[int] = None,
) -> Mapping[str, Any]:
    """Phase 14: adapt influence to earned contextual competence and evidence independence."""
    if not claims:
        raise ExpertSchoolError("assembly requires expert claims")
    _verify_competence_memory(competence_memory, assembly_at_ns=assembly_at_ns)
    ordered = tuple(sorted(claims, key=lambda item: (str(item.get("expert_ref")), str((item.get("integrity") or {}).get("content_hash")))))
    expert_refs = [str(claim["expert_ref"]) for claim in ordered]
    if len(set(expert_refs)) != len(expert_refs):
        raise ExpertSchoolError("duplicate expert testimony cannot enter the same assembly")
    question_refs = {str(claim["question_ref"]) for claim in ordered}
    if len(question_refs) != 1:
        raise ExpertSchoolError("assembly can only combine claims for one exact question")
    question_ref = next(iter(question_refs))
    for field in ("question_definition_hash", "horizon_ns", "cutoff_ns"):
        values = {claim.get(field) for claim in ordered}
        if len(values) != 1:
            raise ExpertSchoolError("assembly requires identical %s" % field)
    question_definition_hash = ordered[0].get("question_definition_hash")
    horizon_ns = ordered[0].get("horizon_ns")
    cutoff_ns = ordered[0].get("cutoff_ns")
    kind = str(ordered[0]["claim_kind"])
    if any(str(claim["claim_kind"]) != kind for claim in ordered):
        raise ExpertSchoolError("claim kinds disagree")
    entries = {
        (str(item["expert_ref"]), str(item["question_ref"])): item
        for item in competence_memory.get("entries", ())
    }
    raw: List[Tuple[Mapping[str, Any], float, Mapping[str, Any], float, float]] = []
    for claim in ordered:
        entry = entries.get((str(claim["expert_ref"]), question_ref))
        if entry is None:
            context_comp = {
                "contextual_score": NEUTRAL_COMPETENCE,
                "sample_support": 0.0,
                "sample_count": 0,
                "global_competence": NEUTRAL_COMPETENCE,
                "recent_competence": NEUTRAL_COMPETENCE,
                "contextual_match": NEUTRAL_COMPETENCE,
                "earned_competence": NEUTRAL_COMPETENCE,
                "matched_context_dimensions": [],
            }
        else:
            context_comp = contextual_competence(entry, current_context)
        base = max(1e-9, float(context_comp["contextual_score"]))
        overlap_penalty, mean_overlap = _overlap_penalty(claim, ordered)
        raw.append((claim, base * overlap_penalty, context_comp, overlap_penalty, mean_overlap))
    total = sum(item[1] for item in raw)
    if total <= 0:
        raise ExpertSchoolError("adaptive weight total must be positive")
    weights = [item[1] / total for item in raw]
    if kind in {"PROBABILITY", "POINT_ESTIMATE"}:
        estimate: Any = sum(weight * float(item[0]["answer"]) for item, weight in zip(raw, weights))
    else:
        labels = sorted({str(label) for claim in ordered for label in claim["answer"].keys()})
        estimate = {label: sum(weight * float(item[0]["answer"].get(label, 0.0)) for item, weight in zip(raw, weights)) for label in labels}
    numeric_answers = []
    if kind in {"PROBABILITY", "POINT_ESTIMATE"}:
        numeric_answers = [float(claim["answer"]) for claim in ordered]
    disagreement = (max(numeric_answers) - min(numeric_answers)) if numeric_answers else 1.0 - max(estimate.values())
    contribution = []
    mean_overlap_values = []
    for (claim, pre_norm, context_comp, overlap_penalty, mean_overlap), weight in zip(raw, weights):
        mean_overlap_values.append(mean_overlap)
        contribution.append({
            "expert_ref": claim["expert_ref"],
            "claim_hash": claim["integrity"]["content_hash"],
            "weight": weight,
            "base_competence": float(context_comp.get("global_competence", NEUTRAL_COMPETENCE)),
            "sample_support_adjustment": float(context_comp.get("sample_support", 0.0)),
            "freshness_adjustment": float(context_comp.get("recent_competence", NEUTRAL_COMPETENCE)),
            "contextual_adjustment": float(context_comp.get("contextual_match", NEUTRAL_COMPETENCE)),
            "evidence_overlap_penalty": float(overlap_penalty),
            "pre_normalization_score": float(pre_norm),
            "normalized_weight": float(weight),
            "sample_count": int(context_comp.get("sample_count", 0)),
            "contextual_competence": context_comp,
        })
    pairwise = []
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            pairwise.append({
                "left_expert_ref": left["expert_ref"],
                "right_expert_ref": right["expert_ref"],
                "overlap_ratio": _evidence_overlap_ratio(left.get("evidence_refs", ()), right.get("evidence_refs", ())),
            })
    body = {
        "schema_version": ASSEMBLY_SCHEMA_VERSION,
        "question_ref": question_ref,
        "question_definition_hash": question_definition_hash,
        "horizon_ns": horizon_ns,
        "cutoff_ns": cutoff_ns,
        "claim_kind": kind,
        "assembled_estimate": estimate,
        "expert_contributions": contribution,
        "expert_count": len(contribution),
        "disagreement": float(disagreement),
        "assembly_confidence": max(0.0, min(1.0, 1.0 - float(disagreement))),
        "current_context": dict(current_context),
        "weight_policy_id": ADAPTIVE_WEIGHT_POLICY_ID,
        "weight_policy_version": ADAPTIVE_WEIGHT_POLICY_VERSION,
        "evidence_independence": {
            "mean_overlap_ratio": (sum(mean_overlap_values) / len(mean_overlap_values)) if mean_overlap_values else 0.0,
            "pairwise": pairwise,
            "penalty_floor": OVERLAP_PENALTY_FLOOR,
            "penalty_scale": OVERLAP_PENALTY_SCALE,
            "metric": "OVERLAP_COEFFICIENT_MIN_SET",
        },
        "competence_memory_hash": (competence_memory.get("integrity") or {}).get("content_hash"),
        "competence_known_at_ns": competence_memory.get("known_at_ns"),
        "authority": {**dict(EXPERT_AUTHORITY), "claims_competence": True, "sets_adaptive_weights": True},
    }
    body["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash({k: v for k, v in body.items() if k != "integrity"})}
    return body
