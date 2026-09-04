"""Trade-tick or bounded trade-batch evidence."""

from decimal import Decimal
from typing import Any, Mapping, Sequence

from ..core import build_object


def trade_observation(
    *, object_id: str, instrument: str, exchange: str, asset: str, timestamp: str,
    trades: Sequence[Mapping[str, Any]], source_record_id: str, source_sha256: str, created_at: str,
) -> Mapping[str, Any]:
    normalized = []
    for index, trade in enumerate(trades):
        price = Decimal(str(trade["price"]))
        size = Decimal(str(trade["size"]))
        side = str(trade["aggressor_side"]).upper()
        if not price.is_finite() or price <= 0 or not size.is_finite() or size < 0:
            raise ValueError(f"trade {index} has invalid price/size")
        if side not in {"BUY", "SELL", "UNKNOWN"}:
            raise ValueError(f"trade {index} has invalid aggressor_side")
        normalized.append({"trade_id": trade.get("trade_id"), "timestamp": trade["timestamp"], "price": str(price), "size": str(size), "aggressor_side": side})
    return build_object(
        object_id=object_id, object_type="TRADE_OBSERVATION", truth_class="OBSERVED_EVIDENCE",
        subject={"instrument": instrument, "exchange": exchange, "asset": asset}, effective_at=timestamp,
        created_at=created_at, source_time_range={"start": normalized[0]["timestamp"] if normalized else timestamp, "end": normalized[-1]["timestamp"] if normalized else timestamp},
        input_refs=[], method={"name": "SOURCE_TRADE_BATCH_PRESERVATION", "version": "1.0.0", "deterministic": True},
        quality={"status": "VALID", "source_record_id": source_record_id, "source_sha256": source_sha256},
        payload={"trade_count": len(normalized), "trades": normalized, "source_record_id": source_record_id},
    )
