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
    shrunken = support * earned + (1.0 - support) * 0.5
    return {
        "expert_ref": entry["expert_ref"],
        "question_ref": entry["question_ref"],
        "contextual_score": max(0.0, min(1.0, shrunken)),
        "sample_support": support,
        "matched_context_dimensions": used,
    }


def assemble_expert_claims(claims: Sequence[Mapping[str, Any]], competence_memory: Mapping[str, Any], current_context: Mapping[str, Any]) -> Mapping[str, Any]:
    """Phase 14: adapt influence to earned contextual competence and evidence independence."""
    if not claims:
        raise ExpertSchoolError("assembly requires expert claims")
    question_refs = {str(claim["question_ref"]) for claim in claims}
    if len(question_refs) != 1:
        raise ExpertSchoolError("assembly can only combine claims for one exact question")
    question_ref = next(iter(question_refs))
    entries = {
        (str(item["expert_ref"]), str(item["question_ref"])): item
        for item in competence_memory.get("entries", ())
    }
    raw: List[Tuple[Mapping[str, Any], float, Mapping[str, Any]]] = []
    for claim in claims:
        entry = entries.get((str(claim["expert_ref"]), question_ref))
        if entry is None:
            context_comp = {"contextual_score": 0.5, "sample_support": 0.0, "matched_context_dimensions": []}
        else:
            context_comp = contextual_competence(entry, current_context)
        base = max(1e-9, float(context_comp["contextual_score"]))
        evidence = set(str(v) for v in claim.get("evidence_refs", ()))
        overlap_penalty = 1.0
        if evidence:
            overlaps = []
            for other in claims:
                if other is claim:
                    continue
                other_evidence = set(str(v) for v in other.get("evidence_refs", ()))
                union = evidence | other_evidence
                if union:
                    overlaps.append(len(evidence & other_evidence) / float(len(union)))
            if overlaps:
                overlap_penalty = max(0.25, 1.0 - 0.5 * (sum(overlaps) / len(overlaps)))
        raw.append((claim, base * overlap_penalty, context_comp))
    total = sum(item[1] for item in raw)
    weights = [item[1] / total for item in raw]
    kind = str(claims[0]["claim_kind"])
    if any(str(claim["claim_kind"]) != kind for claim in claims):
        raise ExpertSchoolError("claim kinds disagree")
    if kind in {"PROBABILITY", "POINT_ESTIMATE"}:
        estimate: Any = sum(weight * float(item[0]["answer"]) for item, weight in zip(raw, weights))
    else:
        labels = sorted({str(label) for claim in claims for label in claim["answer"].keys()})
        estimate = {label: sum(weight * float(item[0]["answer"].get(label, 0.0)) for item, weight in zip(raw, weights)) for label in labels}
    numeric_answers = []
    if kind in {"PROBABILITY", "POINT_ESTIMATE"}:
        numeric_answers = [float(claim["answer"]) for claim in claims]
    disagreement = (max(numeric_answers) - min(numeric_answers)) if numeric_answers else 1.0 - max(estimate.values())
    contribution = []
    for (claim, _, context_comp), weight in zip(raw, weights):
        contribution.append({
            "expert_ref": claim["expert_ref"],
            "claim_hash": claim["integrity"]["content_hash"],
            "weight": weight,
            "contextual_competence": context_comp,
        })
    body = {
        "schema_version": ASSEMBLY_SCHEMA_VERSION,
        "question_ref": question_ref,
        "claim_kind": kind,
        "assembled_estimate": estimate,
        "expert_contributions": contribution,
        "expert_count": len(contribution),
        "disagreement": float(disagreement),
        "assembly_confidence": max(0.0, min(1.0, 1.0 - float(disagreement))),
        "current_context": dict(current_context),
        "authority": {**dict(EXPERT_AUTHORITY), "claims_competence": True, "sets_adaptive_weights": True},
    }
    body["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash({k: v for k, v in body.items() if k != "integrity"})}
    return body
