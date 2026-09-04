"""Chart-image evidence: a provenance record, never canonical price truth."""

from typing import Any, Mapping

from ..core import build_object


def chart_image_evidence(
    *, object_id: str, instrument: str, exchange: str, asset: str, timeframe: str,
    timestamp: str, image_id: str, image_sha256: str, renderer: str, created_at: str,
) -> Mapping[str, Any]:
    if not image_id or len(image_sha256) != 64:
        raise ValueError("chart image requires an id and sha256")
    return build_object(
        object_id=object_id, object_type="CHART_IMAGE_EVIDENCE", truth_class="OBSERVED_EVIDENCE",
        subject={"instrument": instrument, "exchange": exchange, "asset": asset, "timeframe": timeframe},
        effective_at=timestamp, created_at=created_at, source_time_range={"start": timestamp, "end": timestamp},
        input_refs=[], method={"name": "CHART_IMAGE_PROVENANCE", "version": "1.0.0", "deterministic": True},
        quality={"status": "VALID", "image_sha256": image_sha256},
        payload={"image_id": image_id, "image_sha256": image_sha256, "renderer": renderer,
                 "authority": "SECONDARY_PERCEPTION_INPUT", "canonical_price_truth": False},
    )
