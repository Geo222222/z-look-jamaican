"""Order-book snapshot evidence without interpretation."""

from decimal import Decimal
from typing import Any, Mapping, Sequence

from ..core import build_object


def _levels(values: Sequence[Sequence[Any]], side: str) -> list[list[str]]:
    result = []
    previous = None
    for price_raw, size_raw in values:
        price, size = Decimal(str(price_raw)), Decimal(str(size_raw))
        if price <= 0 or size < 0 or not price.is_finite() or not size.is_finite():
            raise ValueError(f"invalid {side} level")
        if previous is not None and ((side == "bid" and price > previous) or (side == "ask" and price < previous)):
            raise ValueError(f"{side} levels are not sorted")
        result.append([str(price), str(size)])
        previous = price
    return result


def order_book_observation(
    *, object_id: str, instrument: str, exchange: str, asset: str, timestamp: str,
    bids: Sequence[Sequence[Any]], asks: Sequence[Sequence[Any]], sequence: Any,
    source_record_id: str, source_sha256: str, created_at: str,
) -> Mapping[str, Any]:
    normalized_bids, normalized_asks = _levels(bids, "bid"), _levels(asks, "ask")
    if not normalized_bids or not normalized_asks:
        raise ValueError("book requires bids and asks")
    if Decimal(normalized_bids[0][0]) >= Decimal(normalized_asks[0][0]):
        raise ValueError("book must not be crossed or locked")
    return build_object(
        object_id=object_id, object_type="ORDER_BOOK_OBSERVATION", truth_class="OBSERVED_EVIDENCE",
        subject={"instrument": instrument, "exchange": exchange, "asset": asset}, effective_at=timestamp,
        created_at=created_at, source_time_range={"start": timestamp, "end": timestamp}, input_refs=[],
        method={"name": "SOURCE_ORDER_BOOK_PRESERVATION", "version": "1.0.0", "deterministic": True},
        quality={"status": "VALID", "source_record_id": source_record_id, "source_sha256": source_sha256, "provider_sequence": sequence},
        payload={"bids": normalized_bids, "asks": normalized_asks, "sequence": sequence, "source_record_id": source_record_id},
    )
