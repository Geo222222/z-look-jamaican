"""Economic, corporate, protocol, or scheduled-event evidence."""

from typing import Any, Mapping

from ..core import build_object


def event_observation(
    *, object_id: str, instrument: str, exchange: str, asset: str, timestamp: str,
    event_class: str, event_payload: Mapping[str, Any], source_record_id: str,
    source_sha256: str, created_at: str, quality_status: str = "VALID",
) -> Mapping[str, Any]:
    return build_object(
        object_id=object_id, object_type="EVENT_OBSERVATION", truth_class="OBSERVED_EVIDENCE",
        subject={"instrument": instrument, "exchange": exchange, "asset": asset}, effective_at=timestamp,
        created_at=created_at, source_time_range={"start": timestamp, "end": timestamp}, input_refs=[],
        method={"name": "SOURCE_EVENT_PRESERVATION", "version": "1.0.0", "deterministic": True},
        quality={"status": quality_status, "source_record_id": source_record_id, "source_sha256": source_sha256},
        payload={"event_class": event_class, "event": dict(event_payload), "source_record_id": source_record_id},
    )
