"""Provider-neutral immutable market observations with raw/derived separation."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from .market_data_quality import classify_market_data
from .operations import canonical_hash


def _atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(document), handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def build_candle_observation(
    *, observation_id: str, provider: str, instrument: str, interval_seconds: int,
    candle_start_at: int, received_at: int, observed_at: int,
    open_price: str, high_price: str, low_price: str, close_price: str, volume: str,
    max_event_age_seconds: int, max_transport_age_seconds: int,
) -> Mapping[str, Any]:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    source_event_at = int(candle_start_at) + int(interval_seconds)
    raw = {
        "provider": provider, "instrument": instrument, "channel": "candles",
        "provider_payload": [int(candle_start_at), low_price, high_price, open_price, close_price, volume],
        "source_event_at": source_event_at, "received_at": int(received_at),
    }
    quality = classify_market_data(
        provider=provider, source_event_at=source_event_at, received_at=int(received_at),
        observed_at=int(observed_at), max_event_age_seconds=int(max_event_age_seconds),
        max_transport_age_seconds=int(max_transport_age_seconds),
    ).to_dict()
    normalized = {
        "schema_version": 1, "type": "candle", "instrument": instrument,
        "interval_seconds": int(interval_seconds), "start_at": int(candle_start_at),
        "end_at": source_event_at, "open": str(open_price), "high": str(high_price),
        "low": str(low_price), "close": str(close_price), "volume": str(volume),
        "raw_observation_id": observation_id,
    }
    content = {"raw": raw, "normalized": normalized, "quality": quality}
    return {
        "schema_version": 1, "observation_id": observation_id, "observed_at": int(observed_at),
        "raw": raw, "normalized": normalized, "quality": quality,
        "integrity": {"algorithm": "sha256", "content_hash": canonical_hash(content)},
    }


def _depth_walk(levels: list[list[Any]], quantity: str, reference_price: Decimal) -> Mapping[str, str | bool]:
    remaining = Decimal(quantity)
    total_quantity = Decimal("0")
    total_notional = Decimal("0")
    for level in levels:
        price, available = Decimal(str(level[0])), Decimal(str(level[1]))
        taken = min(remaining, available)
        total_quantity += taken
        total_notional += taken * price
        remaining -= taken
        if remaining == 0:
            break
    if total_quantity == 0:
        return {"requested_quantity": quantity, "filled_quantity": "0", "sufficient_depth": False, "vwap": "0", "slippage_bps": "0"}
    vwap = total_notional / total_quantity
    slippage = abs(vwap - reference_price) / reference_price * Decimal("10000")
    return {"requested_quantity": quantity, "filled_quantity": str(total_quantity), "sufficient_depth": remaining == 0, "vwap": str(vwap), "slippage_bps": str(slippage)}


def build_microstructure_observation(
    *, observation_id: str, provider: str, instrument: str, payloads: Mapping[str, Any],
    payload_hashes: Mapping[str, str], request_started_at: Mapping[str, int],
    received_at: Mapping[str, int], request_duration_ms: Mapping[str, int], observed_at: int, max_event_age_seconds: int,
    max_transport_age_seconds: int, test_quantities: list[str], depth_bands_bps: list[str],
) -> Mapping[str, Any]:
    product, book, ticker, trades = (payloads[name] for name in ("product_rules", "level2_book", "ticker", "recent_trades"))
    bids = sorted(book.get("bids", []), key=lambda item: Decimal(str(item[0])), reverse=True)
    asks = sorted(book.get("asks", []), key=lambda item: Decimal(str(item[0])))
    if not bids or not asks:
        raise ValueError("microstructure book requires bids and asks")
    best_bid, best_ask = Decimal(str(bids[0][0])), Decimal(str(asks[0][0]))
    if best_bid >= best_ask:
        raise ValueError("microstructure book is crossed or locked")
    midpoint = (best_bid + best_ask) / Decimal("2")
    spread_bps = (best_ask - best_bid) / midpoint * Decimal("10000")
    source_iso = str(book.get("time", ""))
    if not source_iso:
        raise ValueError("book provider timestamp is required")
    from datetime import datetime
    source_event_at = int(datetime.fromisoformat(source_iso.replace("Z", "+00:00")).timestamp())
    final_received_at = max(int(value) for value in received_at.values())
    quality = classify_market_data(provider=provider, source_event_at=source_event_at, received_at=final_received_at, observed_at=int(observed_at), max_event_age_seconds=int(max_event_age_seconds), max_transport_age_seconds=int(max_transport_age_seconds)).to_dict()
    depth = {quantity: {"buy": _depth_walk(asks, quantity, best_ask), "sell": _depth_walk(bids, quantity, best_bid)} for quantity in test_quantities}
    band_depth: dict[str, Mapping[str, str]] = {}
    for band in depth_bands_bps:
        fraction = Decimal(band) / Decimal("10000")
        bid_floor, ask_ceiling = best_bid * (Decimal("1") - fraction), best_ask * (Decimal("1") + fraction)
        band_depth[band] = {
            "bid_base_quantity": str(sum((Decimal(str(level[1])) for level in bids if Decimal(str(level[0])) >= bid_floor), Decimal("0"))),
            "ask_base_quantity": str(sum((Decimal(str(level[1])) for level in asks if Decimal(str(level[0])) <= ask_ceiling), Decimal("0"))),
        }
    raw = {"provider": provider, "instrument": instrument, "channel": "microstructure_snapshot", "provider_payload": dict(payloads), "payload_sha256": dict(payload_hashes), "request_started_at": dict(request_started_at), "received_at_by_surface": dict(received_at), "request_duration_ms": dict(request_duration_ms), "source_event_at": source_event_at, "received_at": final_received_at}
    normalized = {
        "schema_version": 1, "type": "microstructure_snapshot", "instrument": instrument,
        "raw_observation_id": observation_id, "book_sequence": int(book["sequence"]),
        "book_time": source_iso, "best_bid": str(best_bid), "best_ask": str(best_ask),
        "midpoint": str(midpoint), "quoted_spread_bps": str(spread_bps),
        "depth_walks": depth, "depth_bands_bps": band_depth,
        "product_rules": {key: product.get(key) for key in ("id", "base_increment", "quote_increment", "min_market_funds", "status", "post_only", "limit_only", "cancel_only", "trading_disabled", "auction_mode")},
        "ticker": {key: ticker.get(key) for key in ("trade_id", "price", "size", "time", "bid", "ask", "volume")},
        "recent_trade_count": len(trades), "recent_trade_id_min": min((int(item["trade_id"]) for item in trades), default=None), "recent_trade_id_max": max((int(item["trade_id"]) for item in trades), default=None),
        "market_data_http_duration_ms": dict(request_duration_ms),
        "unavailable_for_qualification": ["account_tier_fee_bps", "order_round_trip_latency", "rejection_probability", "partial_fill_probability", "actual_fill_truth"],
        "truth_class": "OBSERVED_PUBLIC_MARKET_DATA",
    }
    content = {"raw": raw, "normalized": normalized, "quality": quality}
    return {"schema_version": 1, "observation_id": observation_id, "observed_at": int(observed_at), "raw": raw, "normalized": normalized, "quality": quality, "integrity": {"algorithm": "sha256", "content_hash": canonical_hash(content)}}


def validate_observation(document: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    observation_id = str(document.get("observation_id", ""))
    if document.get("schema_version") != 1 or not observation_id:
        errors.append("observation schema/id invalid")
    content = {"raw": document.get("raw"), "normalized": document.get("normalized"), "quality": document.get("quality")}
    if canonical_hash(content) != document.get("integrity", {}).get("content_hash"):
        errors.append("observation content hash mismatch")
    normalized = document.get("normalized", {})
    if normalized.get("raw_observation_id") != observation_id:
        errors.append("normalized record has broken raw lineage")
    quality = document.get("quality", {})
    if quality.get("status") != "VALID" and quality.get("action_permitted") is not False:
        errors.append("non-valid data cannot permit action")
    return errors


class MarketDataStore:
    """Immutable observation bundles plus a rebuildable authoritative index."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.directory = self.root / "artifacts/market_data/observations"
        self.index_path = self.root / "state/market_data.json"

    def persist(self, observation: Mapping[str, Any]) -> Mapping[str, Any]:
        errors = validate_observation(observation)
        if errors:
            raise ValueError("; ".join(errors))
        observation_id = str(observation["observation_id"])
        if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in observation_id):
            raise ValueError("unsafe observation_id")
        path = self.directory / f"{observation_id}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("integrity", {}).get("content_hash") != observation.get("integrity", {}).get("content_hash"):
                raise RuntimeError("observation ID conflict")
            return existing
        _atomic_json(path, observation)
        self.rebuild_index()
        return observation

    def rebuild_index(self) -> Mapping[str, Any]:
        items = []
        if self.directory.is_dir():
            for path in sorted(self.directory.glob("*.json")):
                document = json.loads(path.read_text(encoding="utf-8"))
                errors = validate_observation(document)
                if errors:
                    raise RuntimeError(f"{path.name}: {'; '.join(errors)}")
                items.append({
                    "observation_id": document["observation_id"],
                    "path": path.relative_to(self.root).as_posix(),
                    "provider": document["raw"]["provider"],
                    "instrument": document["normalized"]["instrument"],
                    "channel": document["raw"]["channel"],
                    "source_event_at": document["raw"]["source_event_at"],
                    "observed_at": document["observed_at"],
                    "quality_status": document["quality"]["status"],
                    "content_hash": document["integrity"]["content_hash"],
                    "provider_sequence": document["normalized"].get("book_sequence"),
                    "sequence_gap_state": "NOT_APPLICABLE_SNAPSHOT_ONLY" if document["normalized"].get("type") == "microstructure_snapshot" else "UNSUPPORTED_BY_CHANNEL",
                })
        index = {"schema_version": 1, "authority": "immutable bundles listed here; index is deterministically rebuildable", "items": items}
        _atomic_json(self.index_path, index)
        return index


def validate_market_data_store(root: Path) -> list[str]:
    errors: list[str] = []
    index_path = root / "state/market_data.json"
    if not index_path.is_file():
        return ["missing required state file: state/market_data.json"]
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"state/market_data.json: unreadable JSON: {exc}"]
    if index.get("schema_version") != 1 or not isinstance(index.get("items"), list):
        errors.append("state/market_data.json: invalid schema")
        return errors
    for item in index["items"]:
        path = (root / str(item.get("path", ""))).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            errors.append("state/market_data.json: observation path escapes repository")
            continue
        if not path.is_file():
            errors.append(f"state/market_data.json: missing observation {item.get('observation_id')}")
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{item.get('path')}: unreadable observation: {exc}")
            continue
        errors.extend(f"{item.get('path')}: {error}" for error in validate_observation(document))
        if document.get("integrity", {}).get("content_hash") != item.get("content_hash"):
            errors.append(f"state/market_data.json: index hash mismatch for {item.get('observation_id')}")

    full_kernel = (root / "state/current_state.json").is_file()
    qualified_shadow_path = root / "state/qualified_market_shadow.json"
    if full_kernel and not qualified_shadow_path.is_file():
        errors.append("missing required successor shadow state: state/qualified_market_shadow.json")
    elif qualified_shadow_path.is_file():
        # Imported lazily to avoid an import cycle: qualified_shadow consumes the
        # market qualification contract, while canonical validation starts here.
        from .qualified_shadow import validate_qualified_shadow_state

        errors.extend(validate_qualified_shadow_state(root))

    if full_kernel:
        from .joined_shadow_observer import validate_joined_shadow_policy

        errors.extend(validate_joined_shadow_policy(root))
    return errors
