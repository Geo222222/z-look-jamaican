from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..operations import canonical_hash
from .adapters import (
    BINANCE_SPOT_PROVIDER,
    KRAKEN_PROVIDER,
    ProviderRecord,
    adapt_binance_spot,
    adapt_kraken_v2,
)
from .contracts import CanonicalObservation
from .instruments import InstrumentIdentityError, default_instrument_registry
from .store import CanonicalBatchStore


PUBLIC_SOURCE_CAPTURE_SCHEMA_VERSION = "1.0"
KRAKEN_PUBLIC_ENDPOINT = "wss://ws.kraken.com/v2"
BINANCE_SPOT_PUBLIC_ENDPOINT = "wss://stream.binance.com:9443/stream"


class PublicSourceCaptureError(RuntimeError):
    pass


def _safe_id(value: str, field: str) -> str:
    text = str(value)
    if not text or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in text):
        raise PublicSourceCaptureError("%s must be non-empty and file-safe" % field)
    return text


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = (json.dumps(dict(value), indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_bytes(path, encoded)


def _deterministic_gzip(raw: bytes) -> bytes:
    compressed = bytearray(gzip.compress(raw, compresslevel=9, mtime=0))
    if len(compressed) < 10 or compressed[0:2] != b"\x1f\x8b":
        raise PublicSourceCaptureError("gzip encoder returned an invalid header")
    compressed[9] = 255
    return bytes(compressed)


@dataclass(frozen=True)
class PublicSourceSpec:
    source_id: str
    provider: str
    endpoint: str
    provider_symbol: str
    canonical_instrument_id: str
    subscription_messages: Tuple[Mapping[str, Any], ...]
    market_event_types: Tuple[str, ...]
    book_snapshot_semantics: str
    sequence_semantics: str
    schema_version: str = PUBLIC_SOURCE_CAPTURE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PUBLIC_SOURCE_CAPTURE_SCHEMA_VERSION:
            raise PublicSourceCaptureError("unsupported public-source spec schema")
        _safe_id(self.source_id, "source_id")
        if not self.provider or not self.provider_symbol or not self.canonical_instrument_id:
            raise PublicSourceCaptureError("public-source identity is required")
        if not self.endpoint.startswith("wss://"):
            raise PublicSourceCaptureError("public source endpoint must use wss")
        if not self.subscription_messages:
            raise PublicSourceCaptureError("public source requires subscriptions")
        if not self.market_event_types:
            raise PublicSourceCaptureError("public source requires declared market event types")
        if not self.book_snapshot_semantics or not self.sequence_semantics:
            raise PublicSourceCaptureError("public source semantics are required")

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "provider": self.provider,
            "endpoint": self.endpoint,
            "provider_symbol": self.provider_symbol,
            "canonical_instrument_id": self.canonical_instrument_id,
            "subscription_messages": [dict(item) for item in self.subscription_messages],
            "market_event_types": list(self.market_event_types),
            "book_snapshot_semantics": self.book_snapshot_semantics,
            "sequence_semantics": self.sequence_semantics,
            "network_policy": "PUBLIC_READ_ONLY",
            "authentication_allowed": False,
            "orders_allowed": False,
            "wallets_allowed": False,
            "capital_effect": "NONE",
        }

    def content_hash(self) -> str:
        return canonical_hash(self.body())


def kraken_spot_source(provider_symbol: str = "BTC/USD") -> PublicSourceSpec:
    registry = default_instrument_registry()
    try:
        instrument = registry.resolve(KRAKEN_PROVIDER, provider_symbol)
    except InstrumentIdentityError as exc:
        raise PublicSourceCaptureError(str(exc)) from exc
    return PublicSourceSpec(
        source_id="KRAKEN-SPOT-%s" % provider_symbol.replace("/", "-"),
        provider=KRAKEN_PROVIDER,
        endpoint=KRAKEN_PUBLIC_ENDPOINT,
        provider_symbol=provider_symbol,
        canonical_instrument_id=instrument.canonical_id,
        subscription_messages=(
            {
                "method": "subscribe",
                "params": {"channel": "trade", "symbol": [provider_symbol], "snapshot": False},
                "req_id": 1,
            },
            {
                "method": "subscribe",
                "params": {"channel": "book", "symbol": [provider_symbol], "depth": 10, "snapshot": True},
                "req_id": 2,
            },
        ),
        market_event_types=("TRADE", "BOOK_SNAPSHOT", "BOOK_DELTA"),
        book_snapshot_semantics="PROVIDER_WEBSOCKET_SNAPSHOT_THEN_ORDERED_UPDATES_WITH_CHECKSUM",
        sequence_semantics="TRADE_ID_PER_BOOK_AND_BOOK_CHECKSUM_EVIDENCE_NOT_CONNECTION_GLOBAL",
    )


def binance_spot_source(provider_symbol: str = "BTCUSDT") -> PublicSourceSpec:
    registry = default_instrument_registry()
    try:
        instrument = registry.resolve(BINANCE_SPOT_PROVIDER, provider_symbol)
    except InstrumentIdentityError as exc:
        raise PublicSourceCaptureError(str(exc)) from exc
    stream_symbol = provider_symbol.lower()
    return PublicSourceSpec(
        source_id="BINANCE-SPOT-%s" % provider_symbol,
        provider=BINANCE_SPOT_PROVIDER,
        endpoint=BINANCE_SPOT_PUBLIC_ENDPOINT,
        provider_symbol=provider_symbol,
        canonical_instrument_id=instrument.canonical_id,
        subscription_messages=(
            {
                "method": "SUBSCRIBE",
                "params": ["%s@trade" % stream_symbol, "%s@depth@100ms" % stream_symbol],
                "id": "zlj-public-source-v1",
            },
        ),
        market_event_types=("TRADE", "BOOK_DELTA"),
        book_snapshot_semantics="DELTA_ONLY_NO_QUALIFIED_SNAPSHOT_IN_MS2",
        sequence_semantics="INSTRUMENT_UPDATE_ID_RANGE_U_TO_u",
    )


class RawPublicSourceJournal:
    """Append-only, hash-chained receipt journal written before canonicalization."""

    def __init__(self, root: Path, stream_id: str, spec: PublicSourceSpec) -> None:
        self.root = root.resolve()
        self.stream_id = _safe_id(stream_id, "stream_id")
        self.spec = spec
        self.path = self.root / "runtime/public_market_sources" / (self.stream_id + ".jsonl")
        entries = self.entries()
        self._next_index = len(entries)
        self._previous_hash = None if not entries else str(entries[-1]["entry_hash"])

    @property
    def immutable_data_path(self) -> Path:
        return self.root / "artifacts/market_data/provider_streams" / (self.stream_id + ".jsonl.gz")

    @property
    def immutable_manifest_path(self) -> Path:
        return self.root / "artifacts/market_data/provider_streams" / (self.stream_id + ".manifest.json")

    def immutable_event_ref(self, index: int) -> str:
        return "%s#entry-%d" % (self.immutable_data_path.relative_to(self.root).as_posix(), int(index))

    def append(self, message: Mapping[str, Any], received_at_ns: int) -> Mapping[str, Any]:
        if not isinstance(message, Mapping):
            raise PublicSourceCaptureError("public source message must be a mapping")
        received = int(received_at_ns)
        if received < 0:
            raise PublicSourceCaptureError("received_at_ns must be non-negative")
        body = {
            "schema_version": 1,
            "stream_id": self.stream_id,
            "source_spec_hash": self.spec.content_hash(),
            "local_receive_index": self._next_index,
            "received_at_ns": received,
            "message_hash": canonical_hash(message),
            "message": dict(message),
            "previous_entry_hash": self._previous_hash,
        }
        entry = dict(body)
        entry["entry_hash"] = canonical_hash(body)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._previous_hash = str(entry["entry_hash"])
        self._next_index += 1
        return entry

    def entries(self) -> List[Mapping[str, Any]]:
        if not self.path.is_file():
            return []
        entries: List[Mapping[str, Any]] = []
        previous = None
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PublicSourceCaptureError("raw source journal line %d is invalid JSON" % line_number) from exc
            body = {key: value for key, value in entry.items() if key != "entry_hash"}
            if entry.get("entry_hash") != canonical_hash(body):
                raise PublicSourceCaptureError("raw source journal entry hash mismatch at line %d" % line_number)
            if entry.get("previous_entry_hash") != previous:
                raise PublicSourceCaptureError("raw source journal chain mismatch at line %d" % line_number)
            if entry.get("source_spec_hash") != self.spec.content_hash():
                raise PublicSourceCaptureError("raw source journal spec hash mismatch")
            if int(entry.get("local_receive_index", -1)) != len(entries):
                raise PublicSourceCaptureError("raw source journal receive index is not contiguous")
            if canonical_hash(entry.get("message", {})) != entry.get("message_hash"):
                raise PublicSourceCaptureError("raw source message hash mismatch at line %d" % line_number)
            previous = str(entry["entry_hash"])
            entries.append(entry)
        return entries

    def finalize(self) -> Mapping[str, Any]:
        entries = self.entries()
        if not entries:
            raise PublicSourceCaptureError("cannot finalize an empty public source journal")
        raw = self.path.read_bytes()
        compressed = _deterministic_gzip(raw)
        journal_sha = hashlib.sha256(raw).hexdigest()
        compressed_sha = hashlib.sha256(compressed).hexdigest()
        manifest_body = {
            "schema_version": 1,
            "capture_contract_version": PUBLIC_SOURCE_CAPTURE_SCHEMA_VERSION,
            "stream_id": self.stream_id,
            "source_spec": self.spec.body(),
            "source_spec_hash": self.spec.content_hash(),
            "raw_journal_sha256": journal_sha,
            "compressed_sha256": compressed_sha,
            "compressed_path": self.immutable_data_path.relative_to(self.root).as_posix(),
            "entry_count": len(entries),
            "first_received_at_ns": int(entries[0]["received_at_ns"]),
            "last_received_at_ns": int(entries[-1]["received_at_ns"]),
            "last_entry_hash": str(entries[-1]["entry_hash"]),
            "qualification_claim": "CAPTURED_NOT_SOURCE_QUALIFIED",
        }
        manifest = dict(manifest_body)
        manifest["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(manifest_body)}
        if self.immutable_data_path.exists() or self.immutable_manifest_path.exists():
            if not (self.immutable_data_path.exists() and self.immutable_manifest_path.exists()):
                raise PublicSourceCaptureError("partial immutable source bundle already exists")
            existing = json.loads(self.immutable_manifest_path.read_text(encoding="utf-8"))
            if existing != manifest or self.immutable_data_path.read_bytes() != compressed:
                raise PublicSourceCaptureError("immutable public source bundle identity conflict")
            return existing
        _atomic_bytes(self.immutable_data_path, compressed)
        _atomic_json(self.immutable_manifest_path, manifest)
        return manifest


def canonicalize_public_record(spec: PublicSourceSpec, record: ProviderRecord) -> Tuple[CanonicalObservation, ...]:
    if record.provider != spec.provider:
        raise PublicSourceCaptureError("provider record differs from public-source spec")
    if spec.provider == KRAKEN_PROVIDER:
        return adapt_kraken_v2(record)
    if spec.provider == BINANCE_SPOT_PROVIDER:
        return adapt_binance_spot(record, default_symbol=spec.provider_symbol)
    raise PublicSourceCaptureError("unsupported public source provider")


async def capture_public_source_window(
    root: Path,
    spec: PublicSourceSpec,
    *,
    stream_id: str,
    capture_seconds: int,
    maximum_messages: int,
    maximum_uncompressed_bytes: int,
    message_idle_timeout_seconds: int,
    connect_factory: Optional[Callable[..., Any]] = None,
    clock_ns: Callable[[], int] = time.time_ns,
) -> Mapping[str, Any]:
    """Capture one bounded unauthenticated source window, raw evidence first.

    This function performs no account authentication and exposes no order,
    wallet, signing, transfer, or capital surface. It produces immutable raw
    provider evidence plus a derived canonical-observation batch. The returned
    claim remains CAPTURED_NOT_SOURCE_QUALIFIED until a separate prospective
    source-qualification procedure earns more.
    """
    root = root.resolve()
    stream_id = _safe_id(stream_id, "stream_id")
    if min(int(capture_seconds), int(maximum_messages), int(maximum_uncompressed_bytes), int(message_idle_timeout_seconds)) <= 0:
        raise PublicSourceCaptureError("capture bounds must be positive")
    journal = RawPublicSourceJournal(root, stream_id, spec)
    if journal.entries():
        raise PublicSourceCaptureError("public source capture refuses to reuse a non-empty stream id")

    if connect_factory is None:
        import websockets
        connect_factory = websockets.connect

    observations: List[CanonicalObservation] = []
    raw_message_count = 0
    total_bytes = 0
    loop = asyncio.get_running_loop()
    deadline = loop.time() + int(capture_seconds)

    async with connect_factory(
        spec.endpoint,
        open_timeout=20,
        max_size=int(maximum_uncompressed_bytes),
        ping_interval=20,
        ping_timeout=20,
    ) as socket:
        for subscription in spec.subscription_messages:
            await socket.send(json.dumps(dict(subscription), sort_keys=True, separators=(",", ":")))
        while raw_message_count < int(maximum_messages) and total_bytes < int(maximum_uncompressed_bytes) and loop.time() < deadline:
            remaining = deadline - loop.time()
            try:
                raw = await asyncio.wait_for(
                    socket.recv(),
                    timeout=min(int(message_idle_timeout_seconds), max(0.1, remaining)),
                )
            except asyncio.TimeoutError as exc:
                if loop.time() < deadline:
                    raise PublicSourceCaptureError("public source stream idle timeout") from exc
                break
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            raw_bytes = str(raw).encode("utf-8")
            total_bytes += len(raw_bytes)
            if total_bytes > int(maximum_uncompressed_bytes):
                raise PublicSourceCaptureError("public source stream exceeded byte bound")
            try:
                message = json.loads(str(raw))
            except json.JSONDecodeError as exc:
                raise PublicSourceCaptureError("public source returned invalid JSON") from exc
            if not isinstance(message, Mapping):
                raise PublicSourceCaptureError("public source message must be a JSON object")
            received_at_ns = int(clock_ns())
            entry = journal.append(message, received_at_ns)
            record = ProviderRecord(
                provider=spec.provider,
                stream_id=stream_id,
                received_at_ns=received_at_ns,
                message=message,
                message_hash=str(entry["message_hash"]),
                raw_ref=journal.immutable_event_ref(int(entry["local_receive_index"])),
            )
            observations.extend(canonicalize_public_record(spec, record))
            raw_message_count += 1

    raw_manifest = journal.finalize()
    if not observations:
        raise PublicSourceCaptureError("capture produced no canonical market observations")
    canonical_manifest = CanonicalBatchStore(root).persist_batch(
        batch_id="CANON-%s" % stream_id,
        observations=tuple(observations),
        source_ref=journal.immutable_manifest_path.relative_to(root).as_posix(),
        source_sha256=str(raw_manifest["raw_journal_sha256"]),
    )
    event_counts = Counter(item.event_type for item in observations)
    return {
        "schema_version": 1,
        "stream_id": stream_id,
        "source_id": spec.source_id,
        "source_spec_hash": spec.content_hash(),
        "provider": spec.provider,
        "provider_symbol": spec.provider_symbol,
        "canonical_instrument_id": spec.canonical_instrument_id,
        "raw_message_count": raw_message_count,
        "canonical_observation_count": len(observations),
        "event_counts": dict(sorted(event_counts.items())),
        "raw_manifest_path": journal.immutable_manifest_path.relative_to(root).as_posix(),
        "raw_journal_sha256": raw_manifest["raw_journal_sha256"],
        "canonical_batch_id": canonical_manifest["batch_id"],
        "canonical_manifest_hash": canonical_manifest["integrity"]["content_hash"],
        "book_snapshot_semantics": spec.book_snapshot_semantics,
        "sequence_semantics": spec.sequence_semantics,
        "qualification_claim": "CAPTURED_NOT_SOURCE_QUALIFIED",
        "authority": {
            "capital_decision": False,
            "risk_authorization": False,
            "external_execution": False,
            "network_access": "PUBLIC_READ_ONLY",
            "authentication": False,
        },
    }
