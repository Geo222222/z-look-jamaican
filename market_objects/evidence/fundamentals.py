"""Typed fundamental facts preserved without valuation interpretation."""

from decimal import Decimal
from typing import Any, Mapping

from ..core import build_object


def fundamental_observation(
    *, object_id: str, instrument: str, exchange: str, asset: str, timestamp: str,
    metric: str, value: Any, unit: str, period: str, source_record_id: str,
    source_sha256: str, created_at: str,
) -> Mapping[str, Any]:
    number = Decimal(str(value))
    if not metric or not unit or not period or not number.is_finite():
        raise ValueError("invalid fundamental observation")
    return build_object(
        object_id=object_id, object_type="FUNDAMENTAL_OBSERVATION", truth_class="OBSERVED_EVIDENCE",
        subject={"instrument": instrument, "exchange": exchange, "asset": asset},
        effective_at=timestamp, created_at=created_at, source_time_range={"start": timestamp, "end": timestamp},
        input_refs=[], method={"name": "SOURCE_FUNDAMENTAL_PRESERVATION", "version": "1.0.0", "deterministic": True},
        quality={"status": "VALID", "source_record_id": source_record_id, "source_sha256": source_sha256},
        payload={"metric": metric, "value": str(number), "unit": unit, "period": period,
                 "source_record_id": source_record_id},
    )
