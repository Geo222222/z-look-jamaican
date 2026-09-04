"""Match a story's referenced objects to strategy prerequisites."""

from typing import Any, Dict, List, Mapping, Sequence

from ..core import MarketObjectRef, build_object
from .registry import validate_registry


_MISSING = object()


def _path(document: Mapping[str, Any], path: str) -> Any:
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _condition(condition: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    matching_type = [item for item in candidates if item.get("object_type") == condition["object_type"]]
    expected, operator = condition.get("value"), condition["operator"]
    results = []
    for item in matching_type:
        observed = _path(item, condition["path"])
        available = observed is not _MISSING and observed is not None and observed not in ("UNKNOWN", "UNAVAILABLE", "NOT_EARNED")
        matched = False
        if operator == "EXISTS": matched = observed is not _MISSING
        elif operator == "TRUTHY": matched = available and bool(observed)
        elif available:
            try:
                if operator == "EQ": matched = observed == expected
                elif operator == "NE": matched = observed != expected
                elif operator == "IN": matched = observed in expected
                elif operator == "NOT_IN": matched = observed not in expected
                elif operator == "GT": matched = float(observed) > float(expected)
                elif operator == "GTE": matched = float(observed) >= float(expected)
                elif operator == "LT": matched = float(observed) < float(expected)
                elif operator == "LTE": matched = float(observed) <= float(expected)
            except (TypeError, ValueError):
                matched = False
        results.append({"object_ref": f"market://{item['object_id']}", "observed": None if observed is _MISSING else observed, "available": available, "matched": matched})
    matched_result = next((item for item in results if item["matched"]), None)
    return {"condition_id": condition["condition_id"], "description": condition.get("description"), "object_type": condition["object_type"], "path": condition["path"], "operator": operator, "expected": expected, "matched": matched_result is not None, "matched_object_ref": matched_result["object_ref"] if matched_result else None, "candidate_results": results, "weight": float(condition.get("weight", 1.0))}


def _binding_matches(binding: Mapping[str, Any], story: Mapping[str, Any]) -> bool:
    scope = binding.get("scope", {})
    subject = story["subject"]
    if scope.get("exchange") not in {None, "ANY", subject.get("exchange")}:
        return False
    if "ANY" not in scope.get("assets", ["ANY"]) and subject.get("asset") not in scope.get("assets", []):
        return False
    if "ANY" not in scope.get("instruments", ["ANY"]) and subject.get("instrument") not in scope.get("instruments", []):
        return False
    return True


def assess_applicability(
    *, object_id: str, strategy: Mapping[str, Any], story: Mapping[str, Any],
    referenced_objects: Sequence[Mapping[str, Any]], created_at: str,
) -> Mapping[str, Any]:
    if story.get("object_type") != "MARKET_STORY":
        raise ValueError("strategy applicability requires a MARKET_STORY")
    conditions = strategy["conditions"]
    required = [_condition(item, referenced_objects) for item in conditions.get("required", [])]
    supporting = [_condition(item, referenced_objects) for item in conditions.get("supporting", [])]
    contraindications = [_condition(item, referenced_objects) for item in conditions.get("contraindications", [])]
    triggers = [_condition(item, referenced_objects) for item in conditions.get("triggers", [])]
    required_passed = all(item["matched"] for item in required)
    matched_weight = sum(item["weight"] for item in supporting if item["matched"])
    total_weight = sum(item["weight"] for item in supporting)
    support_score = matched_weight / total_weight if total_weight else 0.0
    contraindication_hits = [item for item in contraindications if item["matched"]]
    trigger_count = sum(item["matched"] for item in triggers)
    trigger_required = int(strategy.get("minimum_trigger_matches", 1))
    evidence = [item for item in strategy.get("evidence_bindings", []) if _binding_matches(item, story)]
    rejected = [item for item in evidence if item.get("outcome") == "REJECTED" and item.get("applies_to_all_horizons") is True]
    if rejected:
        applicability, status = "REJECTED", "RESEARCH_REJECTED_IN_SCOPE"
    elif not required_passed:
        applicability, status = "BLOCKED", "MISSING_PREREQUISITES"
    elif contraindication_hits:
        applicability, status = "LOW", "DANGEROUS_CONTEXT"
    elif support_score >= float(strategy.get("minimum_support_score", 0.5)) and trigger_count >= trigger_required:
        applicability, status = "HIGH", "WATCH"
    else:
        applicability, status = "MEDIUM", "CONDITIONAL"
    refs = [MarketObjectRef.to(story["object_id"], "ASSESSES_STORY", expected_object_type="MARKET_STORY")]
    for item in referenced_objects:
        refs.append(MarketObjectRef.to(item["object_id"], "EVALUATES_CONDITION", expected_object_type=item["object_type"]))
    matched = [item for item in required + supporting + triggers if item["matched"]]
    missing = [item for item in required if not item["matched"]]
    return build_object(
        object_id=object_id, object_type="STRATEGY_APPLICABILITY", truth_class="APPLICABILITY_ASSESSMENT",
        subject=story["subject"], effective_at=story["effective_at"], created_at=created_at,
        source_time_range=story["source_time_range"], input_refs=refs,
        method={"name": "DETERMINISTIC_STRATEGY_CONDITION_MATCHER", "version": "1.0.0", "deterministic": True, "strategy_registry_version": strategy.get("registry_version")},
        quality={"status": "VALID", "conditions_evaluated": len(required) + len(supporting) + len(contraindications) + len(triggers)},
        payload={"strategy_id": strategy["strategy_id"], "archetype": strategy["archetype"], "story_ref": f"market://{story['object_id']}", "applicability": applicability, "applicability_score": round(support_score, 4), "status": status, "matched_conditions": len(matched), "required_conditions": len(required), "matched_condition_ids": [item["condition_id"] for item in matched], "missing_conditions": [item["condition_id"] for item in missing], "contraindication_hits": [item["condition_id"] for item in contraindication_hits], "trigger_matches": trigger_count, "condition_results": {"required": required, "supporting": supporting, "contraindications": contraindications, "triggers": triggers}, "scoped_evidence": evidence, "economic_qualification": strategy["economics"]["qualification"], "opportunity_created": False, "execution_authority": False},
    )


def scan_registry(
    *, registry: Mapping[str, Any], story: Mapping[str, Any], referenced_objects: Sequence[Mapping[str, Any]],
    created_at: str, object_id_prefix: str,
) -> List[Mapping[str, Any]]:
    errors = validate_registry(registry)
    if errors:
        raise ValueError("; ".join(errors))
    results = []
    for strategy in registry["strategies"]:
        strategy_copy = dict(strategy)
        strategy_copy["registry_version"] = registry["registry_version"]
        results.append(assess_applicability(object_id=f"{object_id_prefix}-{strategy['strategy_id']}", strategy=strategy_copy, story=story, referenced_objects=referenced_objects, created_at=created_at))
    return results
