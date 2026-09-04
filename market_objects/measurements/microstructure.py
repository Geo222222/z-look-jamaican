"""Mechanical order-book/trade calculations; classifications belong later."""

from decimal import Decimal
from typing import Any, Mapping, Optional

from ..core import MarketObjectRef, build_object


def microstructure_calculation(
    *, object_id: str, order_book: Mapping[str, Any], created_at: str,
    trades: Optional[Mapping[str, Any]] = None, depth_band_bps: int = 10,
) -> Mapping[str, Any]:
    if order_book.get("object_type") != "ORDER_BOOK_OBSERVATION":
        raise ValueError("microstructure calculation requires order-book evidence")
    bids, asks = order_book["payload"]["bids"], order_book["payload"]["asks"]
    best_bid, best_ask = Decimal(bids[0][0]), Decimal(asks[0][0])
    midpoint = (best_bid + best_ask) / 2
    spread_bps = (best_ask - best_bid) / midpoint * Decimal("10000")
    fraction = Decimal(depth_band_bps) / Decimal("10000")
    bid_floor, ask_ceiling = best_bid * (1 - fraction), best_ask * (1 + fraction)
    bid_depth = sum((Decimal(size) * Decimal(price) for price, size in bids if Decimal(price) >= bid_floor), Decimal(0))
    ask_depth = sum((Decimal(size) * Decimal(price) for price, size in asks if Decimal(price) <= ask_ceiling), Decimal(0))
    imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth) if bid_depth + ask_depth else Decimal(0)
    aggressor_ratio = None
    refs = [MarketObjectRef.to(order_book["object_id"], "CALCULATED_FROM", expected_object_type="ORDER_BOOK_OBSERVATION")]
    trade_ref = None
    if trades is not None:
        if trades.get("object_type") != "TRADE_OBSERVATION":
            raise ValueError("trades input has wrong type")
        buy = sum((Decimal(item["size"]) for item in trades["payload"]["trades"] if item["aggressor_side"] == "BUY"), Decimal(0))
        sell = sum((Decimal(item["size"]) for item in trades["payload"]["trades"] if item["aggressor_side"] == "SELL"), Decimal(0))
        aggressor_ratio = buy / (buy + sell) if buy + sell else None
        refs.append(MarketObjectRef.to(trades["object_id"], "CALCULATED_FROM", expected_object_type="TRADE_OBSERVATION"))
        trade_ref = f"market://{trades['object_id']}"
    return build_object(
        object_id=object_id, object_type="MICROSTRUCTURE_CALCULATION", truth_class="DETERMINISTIC_CALCULATION",
        subject=order_book["subject"], effective_at=order_book["effective_at"], created_at=created_at,
        source_time_range=order_book["source_time_range"], input_refs=refs,
        method={"name": "BOOK_DEPTH_AND_AGGRESSOR_MATH", "version": "1.0.0", "deterministic": True, "parameters": {"depth_band_bps": depth_band_bps}},
        quality={"status": "VALID", "aggressor_ratio_available": aggressor_ratio is not None},
        payload={"order_book_ref": f"market://{order_book['object_id']}", "trade_ref": trade_ref, "values": {"midpoint": str(midpoint), "spread_bps": str(spread_bps), "bid_depth_band_quote": str(bid_depth), "ask_depth_band_quote": str(ask_depth), "book_imbalance": str(imbalance), "aggressor_ratio": str(aggressor_ratio) if aggressor_ratio is not None else None}},
    )
