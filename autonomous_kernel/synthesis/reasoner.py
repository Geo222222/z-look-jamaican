"""Deterministic across-question market-state compiler.

Consumes same-question assembly envelopes. Never rewrites assembled answers.
Never publishes internal intelligence or Benjamin handoff.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..operations import canonical_hash
from ..questions.catalog import question_catalog_v1
from ..questions.certification import resolver_ready_refs_v1_qualified
from ..questions.evolution import (
    REVERSAL_QUESTION_V1_1_REF,
    REVERSAL_QUESTION_V1_REF,
    reversal_question_v1_2,
)
from .contracts import (
    CONTRADICTION_KINDS,
    DIMENSION_WEIGHTS,
    QUESTION_FAMILIES,
    STALE_HORIZON_MULTIPLE,
    SYNTHESIS_AUTHORITY,
    SYNTHESIS_POLICY_ID,
    SYNTHESIS_POLICY_VERSION,
    SYNTHESIS_SCHEMA_VERSION,
    MarketSynthesisError,
    seal,
)


def _definitions() -> Dict[str, Any]:
    definitions = {item.question_ref: item for item in question_catalog_v1()}
    material = reversal_question_v1_2()
    definitions[material.question_ref] = material
    return definitions


def _ready_refs() -> Mapping[str, str]:
    return resolver_ready_refs_v1_qualified()


def _timescale_category(timescales: Sequence[str]) -> str:
    if "MICRO" in timescales:
        return "MICRO"
    if "SHORT" in timescales:
        return "SHORT"
    if "SESSION" in timescales:
        return "MEDIUM"
    if "MACRO_STRUCTURAL" in timescales:
        return "MACRO"
    return "OTHER"


def _truthy(value: Any) -> bool:
    if value in (True, 1, "1", 1.0):
        return True
    if value in (False, 0, "0", 0.0, None, ""):
        return False
    if isinstance(value, str) and value.upper() in {"TRUE", "YES"}:
        return True
    return False


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _verify_integrity(assembly: Mapping[str, Any]) -> str:
    integrity = assembly.get("integrity")
    if not isinstance(integrity, Mapping):
        raise MarketSynthesisError("question assembly must be integrity-bound")
    expected = canonical_hash({key: value for key, value in assembly.items() if key != "integrity"})
    observed = str(integrity.get("content_hash") or "")
    if observed != expected:
        raise MarketSynthesisError("tampered assembly hash fails closed")
    return observed


def interpret_fact(family: str, assembled_answer: Any) -> Mapping[str, Any]:
    if family == "DIRECTION":
        probability = _number(assembled_answer)
        if probability is None:
            raise MarketSynthesisError("Direction assembled answer must be numeric")
        if probability >= 0.55:
            polarity = "UPWARD"
        elif probability <= 0.45:
            polarity = "DOWNWARD"
        else:
            polarity = "BALANCED"
        return {"assembled_answer": probability, "polarity": polarity, "probability": probability}
    if family == "MAGNITUDE":
        bps = _number(assembled_answer)
        if bps is None:
            raise MarketSynthesisError("Magnitude assembled answer must be numeric")
        if abs(bps) < 2.0:
            impulse = "WEAK"
        elif abs(bps) >= 8.0:
            impulse = "STRONG"
        else:
            impulse = "MODERATE"
        sign = "POSITIVE" if bps > 0 else ("NEGATIVE" if bps < 0 else "NEAR_ZERO")
        return {"assembled_answer": bps, "impulse": impulse, "sign": sign}
    if family == "VOLATILITY":
        if isinstance(assembled_answer, str):
            label = assembled_answer.upper()
            level = "ELEVATED" if label in {"HIGH", "ELEVATED"} else ("QUIET" if label in {"LOW", "QUIET"} else "MODERATE")
            return {"assembled_answer": assembled_answer, "level": level}
        bps = _number(assembled_answer)
        if bps is None:
            raise MarketSynthesisError("Volatility assembled answer must be numeric or labeled")
        if bps >= 10.0:
            level = "ELEVATED"
        elif bps >= 5.0:
            level = "MODERATE"
        else:
            level = "QUIET"
        return {"assembled_answer": bps, "level": level}
    if family == "FRAGILITY":
        if isinstance(assembled_answer, str):
            label = assembled_answer.upper()
            level = "HIGH" if label in {"HIGH", "ELEVATED"} else ("CONTAINED" if label in {"LOW", "CONTAINED"} else "MODERATE")
            return {"assembled_answer": assembled_answer, "level": level}
        bps = _number(assembled_answer)
        if bps is None:
            raise MarketSynthesisError("Fragility assembled answer must be numeric or labeled")
        if bps >= 8.0:
            level = "HIGH"
        elif bps >= 3.0:
            level = "MODERATE"
        else:
            level = "CONTAINED"
        return {"assembled_answer": bps, "level": level}
    if family == "LIQUIDITY":
        deteriorating = _truthy(assembled_answer) if not isinstance(assembled_answer, str) else assembled_answer.upper() in {"DETERIORATING", "TRUE", "1"}
        if isinstance(assembled_answer, str) and assembled_answer.upper() in {"STABLE", "FALSE", "0"}:
            deteriorating = False
        return {"assembled_answer": assembled_answer, "condition": "DETERIORATING" if deteriorating else "STABLE"}
    if family == "BASIS":
        bps = _number(assembled_answer)
        if bps is None:
            raise MarketSynthesisError("Basis assembled answer must be numeric")
        if bps > 0.5:
            motion = "WIDENING"
        elif bps < -0.5:
            motion = "TIGHTENING"
        else:
            motion = "STABLE"
        return {"assembled_answer": bps, "motion": motion}
    if family == "RELATIVE_VALUE":
        bps = _number(assembled_answer)
        if bps is None:
            raise MarketSynthesisError("Relative-value assembled answer must be numeric")
        if bps > 0.5:
            motion = "CONVERGING"
        elif bps < -0.5:
            motion = "DIVERGING"
        else:
            motion = "STABLE"
        return {"assembled_answer": bps, "motion": motion}
    if family == "REGIME":
        label = str(assembled_answer).upper()
        if label in {"RISK_ON", "UP", "POSITIVE", "DIRECTIONAL", "UPWARD"}:
            stance = "CONSTRUCTIVE"
        elif label in {"RISK_OFF", "DOWN", "NEGATIVE", "DOWNWARD", "DEFENSIVE"}:
            stance = "DEFENSIVE"
        else:
            stance = "MIXED"
        return {"assembled_answer": assembled_answer, "label": label, "stance": stance}
    if family == "PERSISTENCE":
        persisting = _truthy(assembled_answer)
        return {"assembled_answer": assembled_answer, "persisting": persisting}
    if family == "REVERSAL":
        reversing = _truthy(assembled_answer)
        return {"assembled_answer": assembled_answer, "reversing": reversing}
    raise MarketSynthesisError("unsupported question family")


def _missing_state(family: str, status: str = "NOT_ASSEMBLED") -> Mapping[str, Any]:
    return {
        "family": family,
        "status": status,
        "assembled_answer": None,
        "display": "unavailable",
        "fact": None,
    }


def _finding(kind: str, summary: str, families: Sequence[str]) -> Mapping[str, Any]:
    if kind not in CONTRADICTION_KINDS:
        raise MarketSynthesisError("unknown contradiction kind")
    return {"kind": kind, "summary": summary, "families": list(families)}


def normalize_question_assembly(assembly: Mapping[str, Any], *, synthesis_known_at_ns: int) -> Mapping[str, Any]:
    if not isinstance(assembly, Mapping):
        raise MarketSynthesisError("raw model claims cannot bypass same-question assembly")
    required = (
        "assembly_id",
        "question_ref",
        "question_definition_hash",
        "subject_id",
        "horizon_ns",
        "cutoff_at_ns",
        "known_at_ns",
        "assembled_answer",
        "integrity",
    )
    for field in required:
        if field not in assembly:
            raise MarketSynthesisError("raw model claims cannot bypass same-question assembly")
    content_hash = _verify_integrity(assembly)
    question_ref = str(assembly["question_ref"])
    if question_ref in {REVERSAL_QUESTION_V1_REF, REVERSAL_QUESTION_V1_1_REF}:
        raise MarketSynthesisError("historical reversal versions cannot enter active synthesis")
    if "EXECUTION_SUITABILITY" in question_ref:
        raise MarketSynthesisError("deferred EXECUTION_SUITABILITY cannot enter synthesis")
    ready = _ready_refs()
    if question_ref not in ready:
        raise MarketSynthesisError("question ref is not an active resolver-ready family")
    definitions = _definitions()
    definition = definitions.get(question_ref)
    if definition is None:
        raise MarketSynthesisError("unknown question definition")
    if str(assembly["question_definition_hash"]) != definition.content_hash():
        raise MarketSynthesisError("wrong question version is rejected")
    known_at = int(assembly["known_at_ns"])
    if known_at > int(synthesis_known_at_ns):
        raise MarketSynthesisError("future assembly cannot enter earlier synthesis")
    horizon_ns = int(assembly["horizon_ns"])
    if horizon_ns != int(definition.horizon_ns):
        raise MarketSynthesisError("question horizon does not match frozen definition")
    timescales = [item.value for item in definition.required_timescales]
    cutoff = int(assembly["cutoff_at_ns"])
    resolves_at = cutoff + horizon_ns
    age = int(synthesis_known_at_ns) - known_at
    freshness = age
    stale = age > STALE_HORIZON_MULTIPLE * horizon_ns
    family = definition.family.value
    evidence_independence = assembly.get("evidence_independence") if isinstance(assembly.get("evidence_independence"), Mapping) else {}
    evidence_refs = []
    for key in ("evidence_refs", "source_evidence_refs", "contributing_claim_hashes"):
        value = assembly.get(key)
        if isinstance(value, (list, tuple)):
            evidence_refs.extend(str(item) for item in value)
    grouped = evidence_independence.get("evidence_groups") if isinstance(evidence_independence.get("evidence_groups"), (list, tuple)) else ()
    for group in grouped:
        if isinstance(group, (list, tuple)):
            evidence_refs.extend(str(item) for item in group)
        elif isinstance(group, str):
            evidence_refs.append(group)
    status = "STALE" if stale else "PRESENT"
    fact = interpret_fact(family, assembly.get("assembled_answer"))
    return {
        "family": family,
        "status": status,
        "question_ref": question_ref,
        "question_definition_hash": definition.content_hash(),
        "subject_id": str(assembly["subject_id"]),
        "economic_root": definition.scope.value,
        "horizon_ns": horizon_ns,
        "cutoff_at_ns": cutoff,
        "known_at_ns": known_at,
        "resolves_at_ns": resolves_at,
        "freshness_ns": freshness,
        "age_at_synthesis_ns": age,
        "timescales": timescales,
        "timescale_category": _timescale_category(timescales),
        "assembly_id": str(assembly["assembly_id"]),
        "assembly_hash": content_hash,
        "competence_hash": assembly.get("competence_memory_hash") or assembly.get("competence_hash"),
        "context_hash": assembly.get("context_hash"),
        "evidence_class": assembly.get("status") or assembly.get("evidence_class") or "RESEARCH_ONLY",
        "evidence_refs": sorted(set(evidence_refs)),
        "assembled_answer": assembly.get("assembled_answer"),
        "fact": dict(fact),
        "z9_status": assembly.get("z9_status"),
        "prospective_use": assembly.get("prospective_use"),
        "internal_intelligence_publication": assembly.get("internal_intelligence_publication"),
        "benjamin_publication": assembly.get("benjamin_publication"),
    }


def _state_from_input(item: Mapping[str, Any]) -> Mapping[str, Any]:
    if item["status"] not in {"PRESENT", "STALE"}:
        return _missing_state(str(item["family"]), str(item["status"]))
    return {
        "family": item["family"],
        "status": item["status"],
        "assembled_answer": item["assembled_answer"],
        "display": item["fact"],
        "fact": item["fact"],
        "horizon_ns": item["horizon_ns"],
        "timescale_category": item["timescale_category"],
        "question_ref": item["question_ref"],
        "assembly_hash": item["assembly_hash"],
    }


def synthesize_market_state(
    assemblies: Sequence[Mapping[str, Any]],
    *,
    known_at_ns: int,
    subject_id: str,
    context_status: str = "UNAVAILABLE",
    context_hash: Optional[str] = None,
    cutoff_at_ns: Optional[int] = None,
) -> Mapping[str, Any]:
    known_at = int(known_at_ns)
    if known_at < 0:
        raise MarketSynthesisError("known_at_ns must be non-negative")
    normalized: List[Mapping[str, Any]] = []
    for assembly in assemblies:
        normalized.append(normalize_question_assembly(assembly, synthesis_known_at_ns=known_at))
    subjects = {item["subject_id"] for item in normalized}
    if subjects and subjects != {str(subject_id)}:
        raise MarketSynthesisError("incompatible subjects cannot mix")
    by_family: Dict[str, Mapping[str, Any]] = {}
    grouped: Dict[str, List[Mapping[str, Any]]] = {}
    for item in normalized:
        grouped.setdefault(str(item["family"]), []).append(item)
    for family, items in grouped.items():
        refs = {row["question_ref"] for row in items}
        if len(refs) > 1:
            raise MarketSynthesisError("duplicate family identities conflict")
        ranked = sorted(items, key=lambda row: (int(row["known_at_ns"]), int(row["cutoff_at_ns"]), row["assembly_hash"]))
        latest_known = int(ranked[-1]["known_at_ns"])
        latest_cutoff = int(ranked[-1]["cutoff_at_ns"])
        contenders = [
            row
            for row in ranked
            if int(row["known_at_ns"]) == latest_known and int(row["cutoff_at_ns"]) == latest_cutoff
        ]
        hashes = {row["assembly_hash"] for row in contenders}
        if len(hashes) > 1:
            raise MarketSynthesisError("duplicate family identities conflict")
        by_family[family] = contenders[0]
    question_inputs = []
    for family in QUESTION_FAMILIES:
        if family in by_family:
            question_inputs.append(dict(by_family[family]))
        else:
            question_inputs.append(
                {
                    "family": family,
                    "status": "NOT_ASSEMBLED",
                    "question_ref": None,
                    "question_definition_hash": None,
                    "subject_id": str(subject_id),
                    "assembled_answer": None,
                    "fact": None,
                    "display": "unavailable",
                }
            )
    facts = {family: by_family[family] for family in by_family}
    inferences: List[Mapping[str, Any]] = []
    findings: List[Mapping[str, Any]] = []

    direction = facts.get("DIRECTION")
    magnitude = facts.get("MAGNITUDE")
    if direction and magnitude and direction["status"] == "PRESENT" and magnitude["status"] == "PRESENT":
        polarity = direction["fact"]["polarity"]
        impulse = magnitude["fact"]["impulse"]
        sign = magnitude["fact"]["sign"]
        aligned = (polarity == "UPWARD" and sign == "POSITIVE") or (polarity == "DOWNWARD" and sign == "NEGATIVE")
        if aligned and impulse == "WEAK":
            inferences.append({"kind": "DIRECTION_MAGNITUDE", "summary": "directional pressure exists but impulse is weak"})
        elif aligned and impulse == "STRONG":
            inferences.append({"kind": "DIRECTION_MAGNITUDE", "summary": "directional pressure is accompanied by a strong impulse"})
        elif aligned:
            inferences.append({"kind": "DIRECTION_MAGNITUDE", "summary": "directional pressure and magnitude sign agree"})
        elif polarity != "BALANCED" and sign != "NEAR_ZERO":
            findings.append(_finding("DIRECT_CONTRADICTION", "Direction polarity disagrees with Magnitude sign", ("DIRECTION", "MAGNITUDE")))

    reversal = facts.get("REVERSAL")
    if direction and reversal and reversal["status"] in {"PRESENT", "STALE"} and reversal["fact"]["reversing"]:
        findings.append(_finding("REGIME_TRANSITION", "Direction testimony coexists with reversal risk", ("DIRECTION", "REVERSAL")))
        inferences.append({"kind": "DIRECTION_REVERSAL", "summary": "current directional testimony may be transitional"})

    volatility = facts.get("VOLATILITY")
    fragility = facts.get("FRAGILITY")
    if (
        volatility
        and fragility
        and volatility["status"] in {"PRESENT", "STALE"}
        and fragility["status"] in {"PRESENT", "STALE"}
        and volatility["fact"]["level"] == "ELEVATED"
        and fragility["fact"]["level"] == "HIGH"
    ):
        inferences.append({"kind": "VOLATILITY_FRAGILITY", "summary": "elevated volatility and high fragility imply elevated instability"})

    liquidity = facts.get("LIQUIDITY")
    if direction and liquidity and liquidity["status"] in {"PRESENT", "STALE"} and liquidity["fact"]["condition"] == "DETERIORATING":
        probability = float(direction["fact"]["probability"])
        if abs(probability - 0.5) >= 0.15:
            findings.append(_finding("FRAGILITY_WARNING", "Directional conviction should be treated cautiously because liquidity is deteriorating", ("DIRECTION", "LIQUIDITY")))
            inferences.append({"kind": "LIQUIDITY_DIRECTION", "summary": "synthesis confidence is reduced; Direction answer is unchanged"})

    basis = facts.get("BASIS")
    relative_value = facts.get("RELATIVE_VALUE")
    if (
        basis
        and relative_value
        and basis["status"] in {"PRESENT", "STALE"}
        and relative_value["status"] in {"PRESENT", "STALE"}
        and basis["fact"]["motion"] == "WIDENING"
        and relative_value["fact"]["motion"] == "DIVERGING"
    ):
        findings.append(_finding("STRUCTURAL_DIVERGENCE", "Basis widening and relative-value divergence indicate structural dislocation", ("BASIS", "RELATIVE_VALUE")))
        inferences.append({"kind": "BASIS_RELATIVE_VALUE", "summary": "structural dislocation warning"})
    elif (
        basis
        and relative_value
        and basis["status"] in {"PRESENT", "STALE"}
        and relative_value["status"] in {"PRESENT", "STALE"}
        and basis["fact"]["motion"] == "TIGHTENING"
        and relative_value["fact"]["motion"] == "CONVERGING"
    ):
        inferences.append({"kind": "BASIS_RELATIVE_VALUE", "summary": "basis tightening and relative-value convergence support normalization"})

    regime = facts.get("REGIME")
    persistence = facts.get("PERSISTENCE")
    if regime and persistence and persistence["status"] in {"PRESENT", "STALE"} and not persistence["fact"]["persisting"]:
        findings.append(_finding("REGIME_TRANSITION", "Regime classification has low persistence", ("REGIME", "PERSISTENCE")))
        inferences.append({"kind": "REGIME_PERSISTENCE", "summary": "a low-persistence regime is not a stable regime"})

    if direction and regime and direction["status"] in {"PRESENT", "STALE"} and regime["status"] in {"PRESENT", "STALE"}:
        polarity = direction["fact"]["polarity"]
        stance = regime["fact"]["stance"]
        dir_cat = direction["timescale_category"]
        reg_cat = regime["timescale_category"]
        opposed = (polarity == "UPWARD" and stance == "DEFENSIVE") or (polarity == "DOWNWARD" and stance == "CONSTRUCTIVE")
        if opposed and dir_cat != reg_cat:
            findings.append(_finding("HORIZON_TENSION", "Short-horizon direction opposes broader regime", ("DIRECTION", "REGIME")))
            inferences.append({"kind": "REGIME_DIRECTION", "summary": "horizon tension; not a same-timescale contradiction"})
        elif opposed:
            findings.append(_finding("DIRECT_CONTRADICTION", "Direction and regime disagree on the same timescale", ("DIRECTION", "REGIME")))

    for item in question_inputs:
        if item.get("status") == "STALE":
            findings.append(_finding("STALE_INPUT", "%s assembly is stale relative to its horizon" % item["family"], (item["family"],)))

    present = [item for item in question_inputs if item.get("status") in {"PRESENT", "STALE"}]
    groups = []
    for item in present:
        groups.append(tuple(item.get("evidence_refs") or ()))
    distinct_groups = len(set(groups)) if groups else 0
    if present and distinct_groups <= 1 and len(present) > 1:
        findings.append(_finding("LOW_INDEPENDENCE", "Multiple question families share one underlying evidence group", tuple(item["family"] for item in present)))
    independence_score = 0.0 if not present else distinct_groups / float(len(present))
    missing = [item["family"] for item in question_inputs if item.get("status") not in {"PRESENT", "STALE"}]
    if missing:
        findings.append(_finding("MISSING_SUPPORT", "Intended market-state dimensions are not assembled", tuple(missing)))

    completeness = 0.0
    for family in QUESTION_FAMILIES:
        weight = DIMENSION_WEIGHTS[family]
        item = by_family.get(family)
        if item is None:
            continue
        if item["status"] == "PRESENT":
            completeness += weight
        elif item["status"] == "STALE":
            completeness += weight * 0.5
    completeness = round(min(1.0, completeness), 6)
    complete = completeness >= 0.999 and not missing

    coherence = 1.0
    for finding in findings:
        kind = finding["kind"]
        if kind == "DIRECT_CONTRADICTION":
            coherence -= 0.35
        elif kind == "STRUCTURAL_DIVERGENCE":
            coherence -= 0.20
        elif kind == "REGIME_TRANSITION":
            coherence -= 0.15
        elif kind == "HORIZON_TENSION":
            coherence -= 0.08
        elif kind == "FRAGILITY_WARNING":
            coherence -= 0.18
        elif kind == "STALE_INPUT":
            coherence -= 0.12
        elif kind == "LOW_INDEPENDENCE":
            coherence -= 0.10
        elif kind == "MISSING_SUPPORT":
            coherence -= 0.05 * min(4, len(finding["families"]))
    coherence = max(0.0, min(1.0, coherence))
    confidence = round(max(0.0, min(1.0, 0.35 * completeness + 0.40 * coherence + 0.25 * independence_score)), 6)

    z9_status = str(context_status or "UNAVAILABLE")
    if z9_status == "DEGRADED":
        z9_status = "DEGRADED"
    blocking = []
    if z9_status != "QUALIFIED":
        blocking.append("Z9_NOT_QUALIFIED")
    if not complete:
        blocking.append("SYNTHESIS_INCOMPLETE")
    blocking.append("INSUFFICIENT_CONTEXTUAL_SUPPORT")
    blocking.append("PROSPECTIVE_SYNTHESIS_NOT_QUALIFIED")
    if z9_status != "QUALIFIED" and "DEGRADED" in z9_status:
        blocking.append("DEGRADED_CONTEXT_CANNOT_QUALIFY")
    synthesis_status = "PARTIAL" if not complete else "RESEARCH_ONLY"
    if complete and z9_status != "QUALIFIED":
        synthesis_status = "RESEARCH_ONLY"

    dimension_states = {family: _state_from_input(next(item for item in question_inputs if item["family"] == family)) for family in QUESTION_FAMILIES}
    cutoff = int(cutoff_at_ns) if cutoff_at_ns is not None else (max((item["cutoff_at_ns"] for item in present), default=known_at))
    identity_body = {
        "policy_id": SYNTHESIS_POLICY_ID,
        "policy_version": SYNTHESIS_POLICY_VERSION,
        "subject_id": str(subject_id),
        "cutoff_at_ns": cutoff,
        "known_at_ns": known_at,
        "assembly_hashes": sorted(item["assembly_hash"] for item in present),
        "context_hash": context_hash,
    }
    synthesis_id = "MSYN-%s" % canonical_hash(identity_body)[:32]
    body = {
        "schema_version": SYNTHESIS_SCHEMA_VERSION,
        "synthesis_id": synthesis_id,
        "policy_id": SYNTHESIS_POLICY_ID,
        "policy_version": SYNTHESIS_POLICY_VERSION,
        "subject_id": str(subject_id),
        "cutoff_at_ns": cutoff,
        "known_at_ns": known_at,
        "question_inputs": question_inputs,
        "direction_state": dimension_states["DIRECTION"],
        "magnitude_state": dimension_states["MAGNITUDE"],
        "volatility_state": dimension_states["VOLATILITY"],
        "fragility_state": dimension_states["FRAGILITY"],
        "liquidity_state": dimension_states["LIQUIDITY"],
        "basis_state": dimension_states["BASIS"],
        "relative_value_state": dimension_states["RELATIVE_VALUE"],
        "regime_state": dimension_states["REGIME"],
        "persistence_state": dimension_states["PERSISTENCE"],
        "reversal_state": dimension_states["REVERSAL"],
        "facts": {family: (facts[family]["fact"] if family in facts else None) for family in QUESTION_FAMILIES},
        "inferences": inferences,
        "agreement": [item["summary"] for item in inferences if "agree" in item["summary"] or "accompanied" in item["summary"] or "normalization" in item["summary"]],
        "contradictions": [item for item in findings if item["kind"] in {"DIRECT_CONTRADICTION", "HORIZON_TENSION", "STRUCTURAL_DIVERGENCE", "REGIME_TRANSITION", "FRAGILITY_WARNING"}],
        "findings": findings,
        "missing_dimensions": missing,
        "available_dimensions": [item["family"] for item in present],
        "support": {
            "present_count": len(present),
            "intended_count": len(QUESTION_FAMILIES),
            "completeness": completeness,
            "complete": complete,
        },
        "confidence": {
            "synthesis_confidence": confidence,
            "directional_probability": None if not direction else direction["fact"]["probability"],
            "completeness": completeness,
            "coherence": round(coherence, 6),
            "independence": round(independence_score, 6),
            "meaning": "confidence that the market-state story is sufficiently supported and coherent",
        },
        "freshness": {
            "oldest_age_ns": None if not present else max(int(item["age_at_synthesis_ns"]) for item in present),
            "stale_families": [item["family"] for item in present if item["status"] == "STALE"],
        },
        "context_status": z9_status,
        "context_hash": context_hash,
        "evidence_independence_summary": {
            "source_evidence_families": sorted({ref for item in present for ref in (item.get("evidence_refs") or ())}),
            "distinct_underlying_evidence_groups": distinct_groups,
            "present_question_count": len(present),
            "independence_score": round(independence_score, 6),
            "dependence_warning": distinct_groups <= 1 and len(present) > 1,
        },
        "synthesis_status": synthesis_status,
        "prospective_qualification": "BLOCKED",
        "internal_intelligence_publication": "NOT_PUBLISHED",
        "benjamin_publication": "NOT_ELIGIBLE",
        "blocking_reasons": blocking,
        "authority": dict(SYNTHESIS_AUTHORITY),
        "artifact_class": "MARKET_SYNTHESIS",
    }
    return seal(body)
