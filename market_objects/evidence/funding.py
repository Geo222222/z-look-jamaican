"""Perpetual funding evidence."""

from decimal import Decimal
from typing import Any, Mapping

from ..core import build_object


def funding_observation(
    *, object_id: str, instrument: str, exchange: str, asset: str, timestamp: str,
    funding_rate: Any, interval_hours: int, source_record_id: str, source_sha256: str, created_at: str,
) -> Mapping[str, Any]:
    rate = Decimal(str(funding_rate))
    if not rate.is_finite() or int(interval_hours) <= 0:
        raise ValueError("invalid funding observation")
    return build_object(
        object_id=object_id, object_type="FUNDING_OBSERVATION", truth_class="OBSERVED_EVIDENCE",
        subject={"instrument": instrument, "exchange": exchange, "asset": asset}, effective_at=timestamp,
        created_at=created_at, source_time_range={"start": timestamp, "end": timestamp}, input_refs=[],
        method={"name": "SOURCE_FUNDING_PRESERVATION", "version": "1.0.0", "deterministic": True},
        quality={"status": "VALID", "source_record_id": source_record_id, "source_sha256": source_sha256},
        payload={"funding_rate": str(rate), "interval_hours": int(interval_hours), "source_record_id": source_record_id},
    )
