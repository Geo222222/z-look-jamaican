"""A world snapshot indexes market objects; it does not contain their payloads."""

from typing import Any, Dict, Mapping, Sequence

from .core import MarketObjectRef, build_object


CATEGORY_BY_LAYER = {
    "EVIDENCE": "evidence_refs", "MEASUREMENT": "measurement_refs", "DERIVED_MATH": "derived_math_refs",
    "STRUCTURE": "structure_refs", "PERCEPTION": "perception_refs", "CONTEXT": "context_refs",
    "STATE": "state_refs", "TRANSITION": "transition_refs", "STORY": "story_refs",
    "STRATEGY_APPLICABILITY": "strategy_match_refs", "OPPORTUNITY": "opportunity_refs",
}


def world_snapshot(
    *, object_id: str, instrument: str, exchange: str, asset: str, as_of: str,
    created_at: str, objects: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    categories: Dict[str, list[str]] = {value: [] for value in CATEGORY_BY_LAYER.values()}
    refs = []
    for item in objects:
        category = CATEGORY_BY_LAYER.get(item.get("layer"))
        if category is None:
            raise ValueError(f"world snapshot cannot index layer {item.get('layer')}")
        categories[category].append(f"market://{item['object_id']}")
        refs.append(MarketObjectRef.to(item["object_id"], f"INDEXES_{item['layer']}", expected_object_type=item["object_type"]))
    return build_object(
        object_id=object_id, object_type="MARKET_WORLD_SNAPSHOT", truth_class="REFERENCE_INDEX",
        subject={"instrument": instrument, "exchange": exchange, "asset": asset}, effective_at=as_of,
        created_at=created_at, source_time_range={"start": min((item["source_time_range"].get("start", as_of) for item in objects), default=as_of), "end": as_of},
        input_refs=refs, method={"name": "REFERENCE_ONLY_WORLD_INDEX", "version": "1.0.0", "deterministic": True},
        quality={"status": "VALID", "indexed_object_count": len(objects)},
        payload={"snapshot_id": object_id, "instrument": instrument, "as_of": as_of, **categories, "contains_embedded_market_values": False, "execution_authority": False},
    )
