"""Secondary chart-image perception with explicit non-canonical authority."""

from typing import Any, Mapping, Sequence

from ..core import MarketObjectRef, build_object


def chart_perception(
    *, object_id: str, chart_image_evidence: Mapping[str, Any], created_at: str,
    model_id: str, model_version: str, perceived_features: Mapping[str, Any],
    corroborating_structure_refs: Sequence[Mapping[str, Any]] = (),
) -> Mapping[str, Any]:
    if chart_image_evidence.get("object_type") != "CHART_IMAGE_EVIDENCE":
        raise ValueError("chart perception requires chart-image evidence")
    refs = [MarketObjectRef.to(chart_image_evidence["object_id"], "PERCEIVES", expected_object_type="CHART_IMAGE_EVIDENCE")]
    for structure in corroborating_structure_refs:
        if structure.get("layer") != "STRUCTURE":
            raise ValueError("corroborating references must be structure objects")
        refs.append(MarketObjectRef.to(structure["object_id"], "COMPARES_WITH_STRUCTURE", expected_object_type=structure["object_type"]))
    return build_object(
        object_id=object_id, object_type="CHART_PERCEPTION", truth_class="SECONDARY_PERCEPTION",
        subject=chart_image_evidence["subject"], effective_at=chart_image_evidence["effective_at"], created_at=created_at,
        source_time_range=chart_image_evidence["source_time_range"], input_refs=refs,
        method={"name": model_id, "version": model_version, "deterministic": False},
        quality={"status": "VALID", "authority": "SECONDARY_PERCEPTION", "canonical_price_truth": False},
        payload={"image_ref": f"market://{chart_image_evidence['object_id']}", "perceived_features": dict(perceived_features), "corroborating_structure_refs": [f"market://{item['object_id']}" for item in corroborating_structure_refs], "authority": "SECONDARY_PERCEPTION", "must_not_override_underlying_market_data": True},
    )
