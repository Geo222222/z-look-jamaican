"""Session/calendar context kept separate from chart state."""

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from ..core import MarketObjectRef, build_object


def calendar_context(
    *, object_id: str, instrument: str, exchange: str, asset: str, as_of: str, created_at: str,
    event_evidence: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Any]:
    moment = datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(timezone.utc)
    hour = moment.hour
    if 13 <= hour < 21:
        session = "US_SESSION"
    elif 7 <= hour < 16:
        session = "EUROPE_SESSION"
    else:
        session = "ASIA_OR_OVERNIGHT"
    refs = []
    scheduled_event = "UNKNOWN"
    if event_evidence is not None:
        refs.append(MarketObjectRef.to(event_evidence["object_id"], "EVENT_CONTEXT", expected_object_type="EVENT_OBSERVATION"))
        scheduled_event = event_evidence["payload"]["event_class"]
    return build_object(
        object_id=object_id, object_type="MARKET_CONTEXT", truth_class="CALENDAR_CONTEXT",
        subject={"instrument": instrument, "exchange": exchange, "asset": asset}, effective_at=as_of,
        created_at=created_at, source_time_range={"start": as_of, "end": as_of}, input_refs=refs,
        method={"name": "UTC_MARKET_CALENDAR_CONTEXT", "version": "1.0.0", "deterministic": True},
        quality={"status": "VALID" if event_evidence is not None else "DEGRADED", "scheduled_event_evidence_available": event_evidence is not None},
        payload={"session": session, "day_of_week": moment.strftime("%A").upper(), "is_weekend": moment.weekday() >= 5, "hour_utc": hour, "scheduled_event": scheduled_event, "event_ref": f"market://{event_evidence['object_id']}" if event_evidence else None},
    )
