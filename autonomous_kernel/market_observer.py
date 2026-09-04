"""Deterministic, zero-capital controller for recurring public market observation."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional
from uuid import uuid4

from .market_data import _atomic_json
from .microstream import StreamJournal
from .microstructure_features import public_microstructure_distributions
from .operations import canonical_hash


SUPPORTED_PROVIDER = "coinbase_advanced_trade_public_websocket"
SUPPORTED_ENDPOINT = "wss://advanced-trade-ws.coinbase.com"
SUPPORTED_INSTRUMENT = "BTC-USD"
REQUIRED_CHANNELS = ("level2", "market_trades", "heartbeats")


class ObserverBusyError(RuntimeError):
    """Raised when another observer process holds a fresh lease."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _load_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class ObserverConfig:
    observer_id: str
    provider: str
    endpoint: str
    instrument: str
    channels: tuple[str, ...]
    cadence_seconds: int
    minimum_window_separation_seconds: int
    capture_seconds: int
    message_idle_timeout_seconds: int
    maximum_messages: int
    maximum_uncompressed_bytes: int
    maximum_source_clock_ahead_seconds: int
    distribution_percentiles: tuple[int, ...]
    lease_timeout_seconds: int
    maximum_consecutive_failures_before_degraded: int
    maximum_window_history: int

    @classmethod
    def load(cls, root: Path, path: Optional[Path] = None) -> "ObserverConfig":
        raw = _load_json(path or (root / "config/market_observer.json"))
        if raw.get("network_policy") != "PUBLIC_READ_ONLY":
            raise ValueError("market observer network policy must remain PUBLIC_READ_ONLY")
        if raw.get("authentication_allowed") is not False:
            raise ValueError("market observer authentication must remain disabled")
        if raw.get("orders_allowed") is not False or raw.get("wallets_allowed") is not False:
            raise ValueError("market observer orders and wallets must remain disabled")
        if str(raw.get("capital_used_usd")) != "0.00":
            raise ValueError("market observer capital_used_usd must remain 0.00")
        if raw.get("provider") != SUPPORTED_PROVIDER or raw.get("endpoint") != SUPPORTED_ENDPOINT:
            raise ValueError("observer v1 is restricted to the qualified public Coinbase feed")
        if raw.get("instrument") != SUPPORTED_INSTRUMENT:
            raise ValueError("observer v1 is restricted to the qualified BTC-USD instrument")
        if int(raw.get("maximum_source_clock_ahead_seconds", -1)) != 1:
            raise ValueError("observer v1 must preserve the qualified one-second clock-skew tolerance")

        channels = tuple(str(item) for item in raw.get("channels", []))
        if not set(REQUIRED_CHANNELS).issubset(channels):
            raise ValueError("observer channels must include level2, market_trades, and heartbeats")
        percentiles = tuple(int(item) for item in raw.get("distribution_percentiles", []))
        if not percentiles or any(item <= 0 or item > 100 for item in percentiles):
            raise ValueError("observer distribution percentiles must be within 1..100")

        config = cls(
            observer_id=str(raw["observer_id"]),
            provider=str(raw["provider"]),
            endpoint=str(raw["endpoint"]),
            instrument=str(raw["instrument"]),
            channels=channels,
            cadence_seconds=int(raw["cadence_seconds"]),
            minimum_window_separation_seconds=int(raw["minimum_window_separation_seconds"]),
            capture_seconds=int(raw["capture_seconds"]),
            message_idle_timeout_seconds=int(raw["message_idle_timeout_seconds"]),
            maximum_messages=int(raw["maximum_messages"]),
            maximum_uncompressed_bytes=int(raw["maximum_uncompressed_bytes"]),
            maximum_source_clock_ahead_seconds=int(raw["maximum_source_clock_ahead_seconds"]),
            distribution_percentiles=percentiles,
            lease_timeout_seconds=int(raw["lease_timeout_seconds"]),
            maximum_consecutive_failures_before_degraded=int(raw["maximum_consecutive_failures_before_degraded"]),
            maximum_window_history=int(raw["maximum_window_history"]),
        )
        if min(
            config.cadence_seconds,
            config.minimum_window_separation_seconds,
            config.capture_seconds,
            config.message_idle_timeout_seconds,
            config.maximum_messages,
            config.maximum_uncompressed_bytes,
            config.lease_timeout_seconds,
            config.maximum_consecutive_failures_before_degraded,
            config.maximum_window_history,
        ) <= 0:
            raise ValueError("observer numeric bounds must be positive")
        if config.lease_timeout_seconds <= config.capture_seconds + config.message_idle_timeout_seconds:
            raise ValueError("observer lease timeout must exceed capture plus idle timeout")
        return config


class ObserverLease:
    """Filesystem lease preventing overlapping capture windows on one runtime."""

    def __init__(self, root: Path, timeout_seconds: int, now: datetime):
        self.timeout_seconds = int(timeout_seconds)
        self.now = now
        self.path = root / "runtime/market_observer/observer.lock"
        self.token = uuid4().hex
        self.acquired = False

    def _existing_is_stale(self) -> bool:
        try:
            acquired_at = _parse_iso(str(_load_json(self.path).get("acquired_at")))
        except (OSError, ValueError, json.JSONDecodeError):
            return True
        return acquired_at is None or (self.now - acquired_at).total_seconds() > self.timeout_seconds

    def acquire(self) -> "ObserverLease":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                if not self._existing_is_stale():
                    raise ObserverBusyError("market observer lease is already held")
                stale = self.path.with_name(
                    "observer.stale.%s.%s.lock" % (int(self.now.timestamp()), uuid4().hex)
                )
                try:
                    os.replace(self.path, stale)
                except FileNotFoundError:
                    continue
                continue

            payload = json.dumps(
                {
                    "schema_version": 1,
                    "token": self.token,
                    "pid": os.getpid(),
                    "acquired_at": _iso(self.now),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise
            self.acquired = True
            return self
        raise ObserverBusyError("market observer lease could not be acquired")

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            if _load_json(self.path).get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        self.acquired = False


def _state_path(root: Path) -> Path:
    return root / "state/market_observer.json"


def load_observer_state(root: Path, config: ObserverConfig) -> dict[str, Any]:
    path = _state_path(root)
    if path.is_file():
        state = dict(_load_json(path))
    else:
        state = {
            "schema_version": 1,
            "observer_id": config.observer_id,
            "status": "IDLE",
            "updated_at": None,
            "active_window": None,
            "last_attempt_at": None,
            "last_success_at": None,
            "next_eligible_at": None,
            "consecutive_failures": 0,
            "windows": [],
            "failures": [],
            "authority": "Public read-only market observation only.",
        }
    if state.get("observer_id") != config.observer_id:
        raise ValueError("market observer state/config identity mismatch")
    return state


def persist_observer_state(root: Path, state: Mapping[str, Any]) -> None:
    _atomic_json(_state_path(root), state)


def _due(state: Mapping[str, Any], config: ObserverConfig, now: datetime) -> bool:
    next_eligible = _parse_iso(state.get("next_eligible_at"))
    if next_eligible is not None:
        return now >= next_eligible
    last_success = _parse_iso(state.get("last_success_at"))
    if last_success is None:
        return True
    return (now - last_success).total_seconds() >= config.minimum_window_separation_seconds


def _window_identity(now: datetime) -> Mapping[str, str]:
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return {
        "window_id": "PUBLIC-MICROSTRUCTURE-WINDOW-%s" % stamp,
        "stream_id": "COINBASE-BTC-USD-OBS-%s" % stamp,
    }


def _preregister_window(
    root: Path, config: ObserverConfig, identity: Mapping[str, str], now: datetime
) -> str:
    path = (
        root
        / "artifacts/evidence/market/observer"
        / ("%s-preregistration.json" % identity["window_id"])
    )
    if path.exists():
        raise RuntimeError("observer preregistration already exists: %s" % path)
    frozen: dict[str, Any] = {
        "schema_version": 1,
        "window_id": identity["window_id"],
        "stream_id": identity["stream_id"],
        "frozen_at": _iso(now),
        "objective": "Collect one independent bounded public BTC-USD microstructure window for activity/spread/depth calibration.",
        "frozen_parameters": {
            "provider": config.provider,
            "endpoint": config.endpoint,
            "instrument": config.instrument,
            "channels": list(config.channels),
            "capture_seconds": config.capture_seconds,
            "message_idle_timeout_seconds": config.message_idle_timeout_seconds,
            "maximum_messages": config.maximum_messages,
            "maximum_uncompressed_bytes": config.maximum_uncompressed_bytes,
            "maximum_source_clock_ahead_seconds": config.maximum_source_clock_ahead_seconds,
            "distribution_percentiles": list(config.distribution_percentiles),
            "depth_band_bps": 10,
            "book_impact_quote_probe_usd": ["100", "1000"],
        },
        "pass_gate": "VALID market-data quality, required public channels, snapshot/update/trade/heartbeat evidence, no unexplained connection-global gap or out-of-order event, deterministic bundle validation, and zero external financial effect.",
        "failure_gate": "Any provenance, freshness, sequence, replay, resource, provider, or zero-effect invariant failure.",
        "scope_limit": "Public-observable microstructure evidence only; book-impact values are proxies, never actual-fill truth; no fee tier, order latency, rejection, partial-fill, strategy-edge, capital, or live-readiness inference.",
        "safety": {
            "authentication_allowed": False,
            "accounts_allowed": False,
            "orders_allowed": False,
            "wallets_or_signers_allowed": False,
            "capital_used_usd": "0.00",
        },
    }
    frozen["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(frozen)}
    _atomic_json(path, frozen)
    return path.relative_to(root).as_posix()


async def capture_public_window(
    root: Path, config: ObserverConfig, identity: Mapping[str, str]
) -> Mapping[str, Any]:
    """Capture one bounded public window with no credential or order surface."""
    import websockets

    journal = StreamJournal(root, identity["stream_id"])
    if journal.records():
        raise RuntimeError("observer refuses to reuse a stream identity with existing records")

    accepted = 0
    total_bytes = 0
    loop = asyncio.get_running_loop()
    deadline = loop.time() + config.capture_seconds
    async with websockets.connect(
        config.endpoint,
        open_timeout=20,
        max_size=config.maximum_uncompressed_bytes,
        ping_interval=20,
        ping_timeout=20,
    ) as socket:
        for channel in config.channels:
            await socket.send(
                json.dumps(
                    {
                        "type": "subscribe",
                        "product_ids": [config.instrument],
                        "channel": channel,
                    },
                    separators=(",", ":"),
                )
            )
        while (
            loop.time() < deadline
            and accepted < config.maximum_messages
            and total_bytes < config.maximum_uncompressed_bytes
        ):
            remaining = deadline - loop.time()
            try:
                raw = await asyncio.wait_for(
                    socket.recv(),
                    timeout=min(config.message_idle_timeout_seconds, max(0.1, remaining)),
                )
            except asyncio.TimeoutError as exc:
                if loop.time() < deadline:
                    raise RuntimeError("public observer stream idle timeout") from exc
                break
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            total_bytes += len(raw.encode("utf-8"))
            if total_bytes > config.maximum_uncompressed_bytes:
                raise RuntimeError("public observer stream exceeded byte bound")
            message = json.loads(raw)
            if message.get("type") == "error" or message.get("channel") == "errors":
                raise RuntimeError("public observer provider error: %s" % message)
            if journal.ingest(message, time.time_ns()):
                accepted += 1

    finalized = journal.finalize(list(config.distribution_percentiles))
    summary = finalized["manifest"]["summary"]
    if not set(REQUIRED_CHANNELS).issubset(summary.get("channels", [])):
        raise RuntimeError("public observer required channel missing")
    if summary.get("level2_snapshot_count", 0) < 1 or summary.get("level2_update_count", 0) < 1:
        raise RuntimeError("public observer requires level2 snapshot and update evidence")
    if summary.get("market_trade_message_count", 0) < 1 or summary.get("heartbeat_message_count", 0) < 1:
        raise RuntimeError("public observer requires trade and heartbeat evidence")
    if summary.get("gaps") or summary.get("out_of_order"):
        raise RuntimeError("public observer rejected sequence gap or out-of-order evidence")
    if finalized["observation"]["quality"]["status"] != "VALID":
        raise RuntimeError(
            "public observer market-data quality is %s"
            % finalized["observation"]["quality"]["status"]
        )

    features = public_microstructure_distributions(
        journal.records(), config.distribution_percentiles
    )
    if features.get("depth_sample_count", 0) < 1:
        raise RuntimeError("public observer produced no depth-distribution samples")
    return {
        "stream_id": identity["stream_id"],
        "accepted_messages": accepted,
        "uncompressed_network_bytes": total_bytes,
        "manifest": finalized["manifest"],
        "observation": finalized["observation"],
        "microstructure_features": features,
    }


def _decimal_metric(
    source: Mapping[str, Any], key: str, percentile: str = "50"
) -> Optional[str]:
    values = source.get(key) or {}
    value = values.get(percentile) if isinstance(values, Mapping) else None
    return None if value is None else str(Decimal(str(value)))


def summarize_public_features(
    stream_summary: Mapping[str, Any],
    microstructure: Mapping[str, Any],
    config: ObserverConfig,
) -> Mapping[str, Any]:
    return {
        "message_rate_per_second": str(
            Decimal(str(stream_summary.get("unique_message_count", 0)))
            / Decimal(config.capture_seconds)
        ),
        "level2_update_rate_per_second": str(
            Decimal(str(stream_summary.get("level2_update_count", 0)))
            / Decimal(config.capture_seconds)
        ),
        "trade_message_rate_per_second": str(
            Decimal(str(stream_summary.get("market_trade_message_count", 0)))
            / Decimal(config.capture_seconds)
        ),
        "spread_bps_p50": _decimal_metric(stream_summary, "spread_bps_percentiles", "50"),
        "spread_bps_p90": _decimal_metric(stream_summary, "spread_bps_percentiles", "90"),
        "total_depth_10bps_base_p50": _decimal_metric(
            microstructure, "total_depth_10bps_base_percentiles", "50"
        ),
        "book_imbalance_10bps_p50": _decimal_metric(
            microstructure, "book_imbalance_10bps_percentiles", "50"
        ),
        "depth_sample_count": int(microstructure.get("depth_sample_count", 0) or 0),
        "book_impact_proxy": microstructure.get("book_impact_proxy", {}),
        "truth_class": "OBSERVED_PUBLIC_MARKET_DATA",
        "actual_fill_truth_available": False,
        "economic_edge_inferred": False,
    }


def _persist_audit(root: Path, payload: Mapping[str, Any]) -> str:
    path = (
        root
        / "evidence/audits/market_observer"
        / ("%s.json" % str(payload["window_id"]))
    )
    _atomic_json(path, payload)
    return path.relative_to(root).as_posix()


def _recover_interrupted_window(
    state: dict[str, Any], now: datetime
) -> dict[str, Any]:
    active = state.get("active_window")
    if not active:
        return state
    failure = {
        "window_id": active.get("window_id"),
        "stream_id": active.get("stream_id"),
        "failed_at": _iso(now),
        "kind": "INTERRUPTED_PREVIOUS_PROCESS",
        "error": "Previous process ended while a window was active; partial evidence is preserved and the stream identity will not be reused.",
        "preregistration": active.get("preregistration"),
        "partial_journal_path": "runtime/market_stream/%s.jsonl"
        % active.get("stream_id"),
    }
    state.setdefault("failures", []).append(failure)
    state["active_window"] = None
    state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
    state["status"] = "DEGRADED"
    state["updated_at"] = _iso(now)
    return state


CaptureFunction = Callable[
    [Path, ObserverConfig, Mapping[str, str]],
    Awaitable[Mapping[str, Any]],
]


async def run_observer_once(
    root: Path,
    config_path: Optional[Path] = None,
    now: Optional[datetime] = None,
    capture_fn: Optional[CaptureFunction] = None,
) -> Mapping[str, Any]:
    root = root.resolve()
    current = (now or _utc_now()).astimezone(timezone.utc)
    config = ObserverConfig.load(root, config_path)
    capture = capture_fn or capture_public_window
    lease = ObserverLease(root, config.lease_timeout_seconds, current)
    try:
        lease.acquire()
    except ObserverBusyError as exc:
        return {"status": "BUSY", "observer_id": config.observer_id, "error": str(exc)}

    try:
        state = _recover_interrupted_window(load_observer_state(root, config), current)
        if not _due(state, config, current):
            if state.get("status") not in ("DEGRADED", "PAUSED"):
                state["status"] = "IDLE"
            state["updated_at"] = _iso(current)
            persist_observer_state(root, state)
            return {
                "status": "NOT_DUE",
                "observer_id": config.observer_id,
                "next_eligible_at": state.get("next_eligible_at"),
                "last_success_at": state.get("last_success_at"),
            }

        identity = _window_identity(current)
        preregistration = _preregister_window(root, config, identity, current)
        state["status"] = "CAPTURING"
        state["updated_at"] = _iso(current)
        state["last_attempt_at"] = _iso(current)
        state["active_window"] = {
            **identity,
            "started_at": _iso(current),
            "preregistration": preregistration,
        }
        persist_observer_state(root, state)

        try:
            captured = await capture(root, config, identity)
            stream_summary = captured["manifest"]["summary"]
            microstructure = captured.get("microstructure_features", {})
            features = summarize_public_features(stream_summary, microstructure, config)
            completed_at = (
                _utc_now()
                if now is None
                else current + timedelta(seconds=config.capture_seconds)
            )
            audit = {
                "schema_version": 1,
                "window_id": identity["window_id"],
                "stream_id": identity["stream_id"],
                "observer_id": config.observer_id,
                "completed_at": _iso(completed_at),
                "outcome": "VALID_PUBLIC_OBSERVATION_WINDOW",
                "preregistration": preregistration,
                "observation_id": captured["observation"]["observation_id"],
                "quality": captured["observation"]["quality"],
                "stream_summary_hash": canonical_hash(stream_summary),
                "microstructure_feature_hash": canonical_hash(microstructure),
                "microstructure_features": microstructure,
                "public_features": features,
                "safety": {
                    "authentication_used": False,
                    "account_or_order_surface_accessed": False,
                    "wallet_or_signer_accessed": False,
                    "capital_used_usd": "0.00",
                    "capability_promoted": False,
                },
            }
            audit_path = _persist_audit(root, audit)
            item = {
                "window_id": identity["window_id"],
                "stream_id": identity["stream_id"],
                "started_at": _iso(current),
                "completed_at": _iso(completed_at),
                "quality": "VALID",
                "observation_id": captured["observation"]["observation_id"],
                "manifest_path": captured["observation"]["raw"]["provider_payload"][
                    "manifest_path"
                ],
                "audit_path": audit_path,
                "public_features": features,
            }
            windows = list(state.get("windows", [])) + [item]
            state["windows"] = windows[-config.maximum_window_history :]
            state["failures"] = list(state.get("failures", []))[
                -config.maximum_window_history :
            ]
            state["status"] = "IDLE"
            state["active_window"] = None
            state["last_success_at"] = _iso(completed_at)
            state["next_eligible_at"] = _iso(
                current + timedelta(seconds=config.minimum_window_separation_seconds)
            )
            state["consecutive_failures"] = 0
            state["updated_at"] = _iso(completed_at)
            persist_observer_state(root, state)
            return {
                "status": "CAPTURED",
                "observer_id": config.observer_id,
                "window": item,
                "next_eligible_at": state["next_eligible_at"],
            }
        except Exception as exc:
            failed_at = _utc_now() if now is None else current
            failure = {
                "window_id": identity["window_id"],
                "stream_id": identity["stream_id"],
                "failed_at": _iso(failed_at),
                "kind": type(exc).__name__,
                "error": str(exc),
                "preregistration": preregistration,
                "partial_journal_path": "runtime/market_stream/%s.jsonl"
                % identity["stream_id"],
            }
            failures = list(state.get("failures", [])) + [failure]
            state["failures"] = failures[-config.maximum_window_history :]
            state["active_window"] = None
            state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
            state["status"] = (
                "DEGRADED"
                if state["consecutive_failures"]
                >= config.maximum_consecutive_failures_before_degraded
                else "IDLE"
            )
            state["next_eligible_at"] = _iso(
                current + timedelta(seconds=config.cadence_seconds)
            )
            state["updated_at"] = _iso(failed_at)
            persist_observer_state(root, state)
            _persist_audit(
                root,
                {
                    "schema_version": 1,
                    **failure,
                    "observer_id": config.observer_id,
                    "outcome": "REJECTED_PUBLIC_OBSERVATION_WINDOW",
                    "safety": {
                        "authentication_used": False,
                        "account_or_order_surface_accessed": False,
                        "wallet_or_signer_accessed": False,
                        "capital_used_usd": "0.00",
                        "capability_promoted": False,
                    },
                },
            )
            return {
                "status": "FAILED",
                "observer_id": config.observer_id,
                "failure": failure,
                "next_eligible_at": state["next_eligible_at"],
            }
    finally:
        lease.release()


async def run_observer_daemon(
    root: Path,
    config_path: Optional[Path] = None,
    max_cycles: Optional[int] = None,
) -> list[Mapping[str, Any]]:
    """Run recurring observation ticks; any supervisor may restart this process."""
    root = root.resolve()
    config = ObserverConfig.load(root, config_path)
    results: list[Mapping[str, Any]] = []
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        results.append(await run_observer_once(root, config_path=config_path))
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        await asyncio.sleep(config.cadence_seconds)
    return results
