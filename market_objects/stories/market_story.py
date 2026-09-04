"""Compose competing explanations from referenced lower-layer objects."""

from typing import Any, Mapping, Sequence

from ..core import MarketObjectRef, build_object


def market_story(
    *, object_id: str, subject: Mapping[str, Any], effective_at: str, created_at: str,
    supporting_objects: Sequence[Mapping[str, Any]], primary: Mapping[str, Any],
    alternatives: Sequence[Mapping[str, Any]], contradiction_refs: Sequence[Mapping[str, Any]],
    expected_next_states: Sequence[str], invalidation_conditions: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    if not supporting_objects:
        raise ValueError("market story requires supporting objects")
    allowed_layers = {"STRUCTURE", "PERCEPTION", "CONTEXT", "STATE", "TRANSITION"}
    if any(item.get("layer") not in allowed_layers for item in supporting_objects):
        raise ValueError("market story supports only structure/perception/context/state/transition objects")
    if not primary.get("story") or not 0 <= float(primary.get("confidence", -1)) <= 1:
        raise ValueError("primary story/confidence are required")
    if not invalidation_conditions:
        raise ValueError("market story requires invalidation conditions")
    contradiction_ids = {item["object_id"] for item in contradiction_refs}
    refs = [MarketObjectRef.to(item["object_id"], "CONTRADICTS_STORY" if item["object_id"] in contradiction_ids else "SUPPORTS_STORY", expected_object_type=item["object_type"]) for item in supporting_objects]
    supporting_ids = {item["object_id"] for item in supporting_objects}
    for item in contradiction_refs:
        if item["object_id"] not in supporting_ids:
            refs.append(MarketObjectRef.to(item["object_id"], "CONTRADICTS_STORY", expected_object_type=item["object_type"]))
    supporting_refs = [f"market://{item['object_id']}" for item in supporting_objects]
    contradicting = [f"market://{item['object_id']}" for item in contradiction_refs]
    return build_object(
        object_id=object_id, object_type="MARKET_STORY", truth_class="HYPOTHESIS_COMPOSITION",
        subject=subject, effective_at=effective_at, created_at=created_at,
        source_time_range={"start": min(item["source_time_range"].get("start", effective_at) for item in supporting_objects), "end": effective_at},
        input_refs=refs, method={"name": "COMPETING_HYPOTHESIS_COMPOSER", "version": "1.0.0", "deterministic": True},
        quality={"status": "VALID", "supporting_object_count": len(supporting_objects), "contradiction_count": len(contradiction_refs)},
        payload={"primary": {**dict(primary), "epistemic_type": "HYPOTHESIS"}, "alternatives": [{**dict(item), "epistemic_type": "HYPOTHESIS"} for item in alternatives], "supporting_refs": supporting_refs, "contradiction_refs": contradicting, "expected_next_states": list(expected_next_states), "invalidation_conditions": [dict(item) for item in invalidation_conditions], "facts_embedded": False, "measurements_embedded": False, "story_is_objective_truth": False},
    )
