"""Provider-neutral immutable market observations with raw/derived separation."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
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
    return errors
