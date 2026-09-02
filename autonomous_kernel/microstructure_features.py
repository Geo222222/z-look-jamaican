"""Public-observable microstructure feature extraction from qualified journals."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from .microstream import logical_channel


def _quantiles(values: list[Decimal], percentiles: Sequence[int]) -> Mapping[str, str]:
    ordered = sorted(values)
    if not ordered:
        return {}
    result: dict[str, str] = {}
    for percentile in percentiles:
        index = max(0, ((len(ordered) * int(percentile) + 99) // 100) - 1)
        result[str(percentile)] = str(ordered[min(index, len(ordered) - 1)])
    return result


def _buy_slippage_bps(
    asks: Mapping[str, str], midpoint: Decimal, quote_notional: Decimal
) -> Decimal | None:
    remaining_quote = quote_notional
    base_acquired = Decimal("0")
    quote_spent = Decimal("0")
    for price_text, quantity_text in sorted(
        asks.items(), key=lambda item: Decimal(item[0])
    ):
        price = Decimal(price_text)
        quantity = Decimal(quantity_text)
        level_quote = price * quantity
        take_quote = min(remaining_quote, level_quote)
        if take_quote > 0:
            base_acquired += take_quote / price
            quote_spent += take_quote
            remaining_quote -= take_quote
        if remaining_quote <= 0:
            break
    if remaining_quote > 0 or base_acquired <= 0:
        return None
    vwap = quote_spent / base_acquired
    return (vwap - midpoint) / midpoint * Decimal("10000")


def _sell_slippage_bps(
    bids: Mapping[str, str], midpoint: Decimal, quote_notional: Decimal
) -> Decimal | None:
    base_target = quote_notional / midpoint
    remaining_base = base_target
    base_sold = Decimal("0")
    quote_received = Decimal("0")
    for price_text, quantity_text in sorted(
        bids.items(), key=lambda item: Decimal(item[0]), reverse=True
    ):
        price = Decimal(price_text)
        quantity = Decimal(quantity_text)
        take_base = min(remaining_base, quantity)
        if take_base > 0:
            base_sold += take_base
            quote_received += take_base * price
            remaining_base -= take_base
        if remaining_base <= 0:
            break
    if remaining_base > 0 or base_sold <= 0:
        return None
    vwap = quote_received / base_sold
    return (midpoint - vwap) / midpoint * Decimal("10000")


def public_microstructure_distributions(
    records: Sequence[Mapping[str, Any]],
    percentiles: Sequence[int] = (50, 90, 99, 100),
    depth_band_bps: int = 10,
    quote_probe_notionals: Sequence[Decimal] = (Decimal("100"), Decimal("1000")),
) -> Mapping[str, Any]:
    """Extract depth/imbalance/book-impact proxies without inferring actual fills."""
    bids: dict[str, str] = {}
    asks: dict[str, str] = {}
    bid_depth: list[Decimal] = []
    ask_depth: list[Decimal] = []
    total_depth: list[Decimal] = []
    imbalance: list[Decimal] = []
    buy_slippage: dict[str, list[Decimal]] = {
        str(value): [] for value in quote_probe_notionals
    }
    sell_slippage: dict[str, list[Decimal]] = {
        str(value): [] for value in quote_probe_notionals
    }

    band = Decimal(depth_band_bps) / Decimal("10000")
    for record in records:
        message = record["message"]
        if logical_channel(str(message.get("channel", ""))) != "level2":
            continue
        for event in message.get("events", []):
            event_type = event.get("type")
            if event_type == "snapshot":
                bids, asks = {}, {}
            elif event_type == "update" and not bids and not asks:
                raise RuntimeError("level2 update precedes snapshot during feature replay")
            for update in event.get("updates", []):
                side = str(update["side"])
                price = str(update["price_level"])
                quantity = Decimal(str(update["new_quantity"]))
                if quantity < 0:
                    raise RuntimeError("negative book quantity during feature replay")
                book = bids if side == "bid" else asks if side == "offer" else None
                if book is None:
                    raise RuntimeError("unknown book side during feature replay")
                if quantity == 0:
                    book.pop(price, None)
                else:
                    book[price] = str(quantity)

            if not bids or not asks:
                continue
            best_bid = max(Decimal(price) for price in bids)
            best_ask = min(Decimal(price) for price in asks)
            if best_bid >= best_ask:
                raise RuntimeError("feature replay book crossed or locked")
            midpoint = (best_bid + best_ask) / Decimal("2")
            bid_floor = midpoint * (Decimal("1") - band)
            ask_ceiling = midpoint * (Decimal("1") + band)
            bid_value = sum(
                (Decimal(quantity) for price, quantity in bids.items() if Decimal(price) >= bid_floor),
                Decimal("0"),
            )
            ask_value = sum(
                (Decimal(quantity) for price, quantity in asks.items() if Decimal(price) <= ask_ceiling),
                Decimal("0"),
            )
            total = bid_value + ask_value
            bid_depth.append(bid_value)
            ask_depth.append(ask_value)
            total_depth.append(total)
            if total > 0:
                imbalance.append((bid_value - ask_value) / total)

            for notional in quote_probe_notionals:
                buy = _buy_slippage_bps(asks, midpoint, notional)
                sell = _sell_slippage_bps(bids, midpoint, notional)
                if buy is not None:
                    buy_slippage[str(notional)].append(buy)
                if sell is not None:
                    sell_slippage[str(notional)].append(sell)

    return {
        "schema_version": 1,
        "depth_band_bps": int(depth_band_bps),
        "depth_sample_count": len(total_depth),
        "bid_depth_10bps_base_percentiles": _quantiles(bid_depth, percentiles),
        "ask_depth_10bps_base_percentiles": _quantiles(ask_depth, percentiles),
        "total_depth_10bps_base_percentiles": _quantiles(total_depth, percentiles),
        "book_imbalance_10bps_percentiles": _quantiles(imbalance, percentiles),
        "book_impact_proxy": {
            "truth_class": "PUBLIC_ORDER_BOOK_PROXY_NOT_ACTUAL_FILL",
            "quote_currency": "USD",
            "buy_slippage_bps_by_quote_notional": {
                key: _quantiles(values, percentiles) for key, values in buy_slippage.items()
            },
            "sell_slippage_bps_by_quote_notional": {
                key: _quantiles(values, percentiles) for key, values in sell_slippage.items()
            },
        },
    }
