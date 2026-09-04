"""Open-interest source evidence."""

from decimal import Decimal
from typing import Any, Mapping

from ..core import build_object


def open_interest_observation(
    *, object_id: str, instrument: str, exchange: str, asset: str, timestamp: str,
    open_interest: Any, unit: str, source_record_id: str, source_sha256: str, created_at: str,
) -> Mapping[str, Any]:
    value = Decimal(str(open_interest))
    if not value.is_finite() or value < 0 or not unit:
        raise ValueError("invalid open-interest observation")
    return build_object(
        object_id=object_id, object_type="OPEN_INTEREST_OBSERVATION", truth_class="OBSERVED_EVIDENCE",
        subject={"instrument": instrument, "exchange": exchange, "asset": asset},
        effective_at=timestamp, created_at=created_at, source_time_range={"start": timestamp, "end": timestamp},
        input_refs=[], method={"name": "SOURCE_OPEN_INTEREST_PRESERVATION", "version": "1.0.0", "deterministic": True},
        quality={"status": "VALID", "source_record_id": source_record_id, "source_sha256": source_sha256},
        payload={"open_interest": str(value), "unit": unit, "source_record_id": source_record_id},
    )
