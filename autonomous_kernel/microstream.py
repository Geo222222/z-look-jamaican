"""Crash-resumable public microstructure journals and deterministic replay."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from .market_data import MarketDataStore, _atomic_json
from .market_data_quality import classify_market_data
from .operations import canonical_hash


CHANNEL_ALIASES = {"l2_data": "level2"}
def logical_channel(provider_channel: str) -> str:
    return CHANNEL_ALIASES.get(provider_channel, provider_channel)


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _iso_epoch(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def _quantiles(values: list[Decimal], percentiles: list[int]) -> Mapping[str, str]:
    ordered = sorted(values)
    if not ordered:
        return {}
    result = {}
    for percentile in percentiles:
        index = max(0, ((len(ordered) * percentile + 99) // 100) - 1)
        result[str(percentile)] = str(ordered[min(index, len(ordered) - 1)])
    return result


def replay_records(records: list[Mapping[str, Any]], percentiles: list[int] | None = None) -> Mapping[str, Any]:
    percentiles = percentiles or [50, 90, 99, 100]
    bids: dict[str, str] = {}
    asks: dict[str, str] = {}
    last_sequences: dict[str, int] = {}
    last_global_sequence: int | None = None
    identities: dict[str, str] = {}
    gaps: list[Mapping[str, int | str]] = []
    out_of_order: list[Mapping[str, int | str]] = []
    duplicates = 0
    channels: set[str] = set()
    level2_snapshots = level2_updates = trade_messages = heartbeat_messages = 0
    spreads: list[Decimal] = []
    signed_clock_skews: list[Decimal] = []
    latest_provider_at: str | None = None
    for record in records:
        message = record["message"]
        provider_channel = str(message.get("channel", ""))
        channel = logical_channel(provider_channel)
        sequence = int(message.get("sequence_num", -1))
        identity = str(sequence)
        digest = str(record["message_hash"])
        if identity in identities:
            if identities[identity] != digest:
                raise RuntimeError(f"conflicting duplicate stream identity {identity}")
            duplicates += 1
            continue
        identities[identity] = digest
        channels.add(channel)
        if last_global_sequence is not None and sequence > last_global_sequence + 1:
            gaps.append({"scope": "connection", "after": last_global_sequence, "before": sequence, "missing": sequence - last_global_sequence - 1})
        elif last_global_sequence is not None and sequence < last_global_sequence:
            out_of_order.append({"scope": "connection", "previous": last_global_sequence, "observed": sequence})
        last_global_sequence = max(sequence, last_global_sequence if last_global_sequence is not None else sequence)
        previous_channel = last_sequences.get(channel)
        last_sequences[channel] = max(sequence, previous_channel if previous_channel is not None else sequence)
        timestamp = str(message.get("timestamp", ""))
        if timestamp:
            provider_epoch = Decimal(str(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()))
            receive_epoch = Decimal(int(record["received_at_ns"])) / Decimal("1000000000")
            signed_clock_skews.append(provider_epoch - receive_epoch)
        if timestamp and (latest_provider_at is None or timestamp > latest_provider_at):
            latest_provider_at = timestamp
        if channel == "level2":
            for event in message.get("events", []):
                event_type = event.get("type")
                if event_type == "snapshot":
                    bids, asks = {}, {}
                    level2_snapshots += 1
                elif event_type == "update":
                    if not bids and not asks:
                        raise RuntimeError("level2 update precedes snapshot")
                    level2_updates += 1
                for update in event.get("updates", []):
                    side, price, quantity = str(update["side"]), str(update["price_level"]), Decimal(str(update["new_quantity"]))
                    if quantity < 0:
                        raise RuntimeError("negative book quantity")
                    book = bids if side == "bid" else asks if side == "offer" else None
                    if book is None:
                        raise RuntimeError("unknown book side")
                    if quantity == 0:
                        book.pop(price, None)
                    else:
                        book[price] = str(quantity)
                if bids and asks:
                    best_bid = max(Decimal(price) for price in bids)
                    best_ask = min(Decimal(price) for price in asks)
                    if best_bid >= best_ask:
                        raise RuntimeError("replayed book crossed or locked")
                    midpoint = (best_bid + best_ask) / Decimal("2")
                    spreads.append((best_ask - best_bid) / midpoint * Decimal("10000"))
        elif channel == "market_trades":
            trade_messages += 1
        elif channel == "heartbeats":
            heartbeat_messages += 1
    final_book = {"bids": sorted(bids.items(), key=lambda item: Decimal(item[0]), reverse=True), "asks": sorted(asks.items(), key=lambda item: Decimal(item[0]))}
    return {"schema_version": 1, "record_count": len(records), "unique_message_count": len(identities), "duplicate_count": duplicates, "channels": sorted(channels), "sequence_scope": "CONNECTION_GLOBAL", "last_global_sequence": last_global_sequence, "last_sequences_observed_by_channel": last_sequences, "gaps": gaps, "out_of_order": out_of_order, "level2_snapshot_count": level2_snapshots, "level2_update_count": level2_updates, "market_trade_message_count": trade_messages, "heartbeat_message_count": heartbeat_messages, "latest_provider_at": latest_provider_at, "signed_provider_minus_receive_seconds_percentiles": _quantiles(signed_clock_skews, percentiles), "spread_bps_percentiles": _quantiles(spreads, percentiles), "spread_sample_count": len(spreads), "final_book_hash": canonical_hash(final_book), "final_book": final_book}


class StreamJournal:
    def __init__(self, root: Path, stream_id: str):
        self.root = root.resolve()
        self.stream_id = stream_id
        self.path = self.root / "runtime/market_stream" / f"{stream_id}.jsonl"
        self._identities: dict[str, str] | None = None

    def _identity_index(self) -> dict[str, str]:
        if self._identities is None:
            self._identities = {}
            for record in self.records():
                identity = str(record["message"]["sequence_num"])
                digest = str(record["message_hash"])
                if identity in self._identities and self._identities[identity] != digest:
                    raise RuntimeError(f"conflicting duplicate stream identity {identity}")
                self._identities[identity] = digest
        return self._identities

    def records(self) -> list[Mapping[str, Any]]:
        if not self.path.is_file():
            return []
        records = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"corrupt stream journal line {line_number}") from exc
            if canonical_hash(record["message"]) != record.get("message_hash"):
                raise RuntimeError(f"stream journal hash mismatch line {line_number}")
            records.append(record)
        return records

    def ingest(self, message: Mapping[str, Any], received_at_ns: int) -> bool:
        provider_channel = str(message.get("channel", ""))
        if not provider_channel:
            raise ValueError("stream message lacks channel provenance")
        channel = logical_channel(provider_channel)
        if not isinstance(message.get("sequence_num"), int) or not message.get("timestamp"):
            raise ValueError("stream message lacks sequence/timestamp provenance")
        digest = canonical_hash(message)
        identity = str(message["sequence_num"])
        identities = self._identity_index()
        if identity in identities:
            if identities[identity] != digest:
                raise RuntimeError(f"conflicting duplicate stream identity {identity}")
            return False
        record = {"schema_version": 1, "stream_id": self.stream_id, "received_at_ns": int(received_at_ns), "message_hash": digest, "provider_channel": provider_channel, "logical_channel": channel, "message": dict(message)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        identities[identity] = digest
        return True

    def finalize(self, percentiles: list[int] | None = None) -> Mapping[str, Any]:
        records = self.records()
        summary = replay_records(records, percentiles)
        raw = self.path.read_bytes()
        compressed = gzip.compress(raw, mtime=0)
        bundle_path = self.root / "artifacts/market_data/streams" / f"{self.stream_id}.jsonl.gz"
        _atomic_bytes(bundle_path, compressed)
        compact_summary = {key: value for key, value in summary.items() if key != "final_book"}
        manifest = {"schema_version": 1, "stream_id": self.stream_id, "journal_sha256": hashlib.sha256(raw).hexdigest(), "compressed_sha256": hashlib.sha256(compressed).hexdigest(), "compressed_path": bundle_path.relative_to(self.root).as_posix(), "summary": compact_summary, "integrity": {"algorithm": "sha256"}}
        manifest["integrity"]["content_hash"] = canonical_hash({key: value for key, value in manifest.items() if key != "integrity"})
        manifest_path = self.root / "artifacts/market_data/streams" / f"{self.stream_id}.manifest.json"
        _atomic_json(manifest_path, manifest)
        latest_at = summary.get("latest_provider_at")
        if not latest_at:
            raise RuntimeError("stream has no provider timestamp")
        source_at = _iso_epoch(str(latest_at))
        observed_at = max(int(record["received_at_ns"]) // 1_000_000_000 for record in records)
        quality = classify_market_data(provider="coinbase_advanced_trade_public_websocket", source_event_at=source_at, received_at=observed_at, observed_at=observed_at, max_event_age_seconds=30, max_transport_age_seconds=30, max_clock_skew_seconds=1).to_dict()
        observation_id = f"OBS-{self.stream_id}"
        raw_section = {"provider": "coinbase_advanced_trade_public_websocket", "instrument": "BTC-USD", "channel": "microstructure_stream", "provider_payload": {"manifest_path": manifest_path.relative_to(self.root).as_posix(), "compressed_path": bundle_path.relative_to(self.root).as_posix()}, "source_event_at": source_at, "received_at": observed_at, "raw_stream_sha256": manifest["journal_sha256"], "compressed_sha256": manifest["compressed_sha256"]}
        normalized = {"schema_version": 1, "type": "microstructure_stream_summary", "instrument": "BTC-USD", "raw_observation_id": observation_id, "stream_id": self.stream_id, "summary": {key: value for key, value in summary.items() if key != "final_book"}, "truth_class": "OBSERVED_PUBLIC_MARKET_DATA"}
        content = {"raw": raw_section, "normalized": normalized, "quality": quality}
        observation = {"schema_version": 1, "observation_id": observation_id, "observed_at": observed_at, "raw": raw_section, "normalized": normalized, "quality": quality, "integrity": {"algorithm": "sha256", "content_hash": canonical_hash(content)}}
        MarketDataStore(self.root).persist(observation)
        return {"manifest": manifest, "observation": observation}


def validate_stream_bundles(root: Path) -> list[str]:
    errors = []
    for manifest_path in (root / "artifacts/market_data/streams").glob("*.manifest.json") if (root / "artifacts/market_data/streams").is_dir() else []:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected = canonical_hash({key: value for key, value in manifest.items() if key != "integrity"})
            if expected != manifest.get("integrity", {}).get("content_hash"):
                errors.append(f"{manifest_path}: manifest integrity mismatch")
                continue
            compressed_path = root / manifest["compressed_path"]
            compressed = compressed_path.read_bytes()
            if hashlib.sha256(compressed).hexdigest() != manifest["compressed_sha256"]:
                errors.append(f"{compressed_path}: compressed stream hash mismatch")
                continue
            raw = gzip.decompress(compressed)
            if hashlib.sha256(raw).hexdigest() != manifest["journal_sha256"]:
                errors.append(f"{compressed_path}: journal hash mismatch")
                continue
            records = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
            rebuilt = replay_records(records)
            if rebuilt["final_book_hash"] != manifest["summary"]["final_book_hash"] or rebuilt["unique_message_count"] != manifest["summary"]["unique_message_count"]:
                errors.append(f"{compressed_path}: deterministic replay mismatch")
        except (OSError, KeyError, ValueError, RuntimeError, json.JSONDecodeError, gzip.BadGzipFile) as exc:
            errors.append(f"{manifest_path}: stream bundle unreadable: {exc}")
    return errors
