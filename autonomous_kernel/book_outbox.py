from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol


PENDING = "PENDING"
ACKNOWLEDGED = "ACKNOWLEDGED"
QUARANTINED = "QUARANTINED"


class BookOutboxError(RuntimeError):
    pass


class OutboxConflict(BookOutboxError):
    pass


class PermanentBookDeliveryError(BookOutboxError):
    pass


@dataclass(frozen=True)
class BookAcceptance:
    receipt_id: str
    sequence: int
    entry_hash: str
    recorded_at: str
    accepted: bool
    duplicate_replay: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BookAcceptance":
        return cls(
            receipt_id=str(value["receipt_id"]),
            sequence=int(value["sequence"]),
            entry_hash=str(value["entry_hash"]),
            recorded_at=str(value["recorded_at"]),
            accepted=bool(value["accepted"]),
            duplicate_replay=bool(value.get("duplicate_replay", False)),
        )

    def wire(self) -> Dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "sequence": self.sequence,
            "entry_hash": self.entry_hash,
            "recorded_at": self.recorded_at,
            "accepted": self.accepted,
            "duplicate_replay": self.duplicate_replay,
        }


class BookTransport(Protocol):
    def append_idempotent(self, *, envelope: Mapping[str, Any], payload: bytes) -> Mapping[str, Any]: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(value), handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _read(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise BookOutboxError(f"{path}: outbox record must be an object")
    return value


class BookOutbox:
    """Durable producer outbox for signed ZLJ Book evidence.

    Records are persisted before delivery. A retry always uses the stored signed
    envelope and stored payload bytes. There is no retry limit that silently
    discards evidence.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.pending_dir = self.root / "pending"
        self.acknowledged_dir = self.root / "acknowledged"
        self.quarantined_dir = self.root / "quarantined"

    def _path(self, state: str, receipt_id: str) -> Path:
        directory = {
            PENDING: self.pending_dir,
            ACKNOWLEDGED: self.acknowledged_dir,
            QUARANTINED: self.quarantined_dir,
        }[state]
        return directory / f"{receipt_id}.json"

    def _find(self, receipt_id: str) -> Optional[Path]:
        for state in (PENDING, ACKNOWLEDGED, QUARANTINED):
            path = self._path(state, receipt_id)
            if path.is_file():
                return path
        return None

    def enqueue(self, *, envelope: Mapping[str, Any], payload: bytes) -> Dict[str, Any]:
        receipt_id = str(envelope.get("receipt_id", ""))
        if not receipt_id:
            raise BookOutboxError("signed envelope requires receipt_id")
        if envelope.get("producer") != "ZLJ" or not str(envelope.get("event_type", "")).startswith("ZLJ."):
            raise BookOutboxError("ZLJ outbox accepts only ZLJ.* producer evidence")
        if envelope.get("privacy_class") == "SECRET_REGULATED":
            raise BookOutboxError("raw SECRET_REGULATED payload bytes may not enter the Book outbox")
        payload_digest = _sha256(payload)
        if payload_digest != str(envelope.get("payload_digest", "")).lower():
            raise BookOutboxError("payload does not match signed envelope payload_digest")
        envelope_digest = _sha256(_canonical(dict(envelope)))

        existing_path = self._find(receipt_id)
        if existing_path is not None:
            existing = _read(existing_path)
            if (
                existing.get("envelope_digest") != envelope_digest
                or existing.get("payload_digest") != payload_digest
            ):
                raise OutboxConflict(f"receipt_id {receipt_id!r} already belongs to different evidence")
            return existing

        created_at = _utc_now()
        record: Dict[str, Any] = {
            "schema_version": 1,
            "receipt_id": receipt_id,
            "producer": "ZLJ",
            "state": PENDING,
            "envelope": dict(envelope),
            "envelope_digest": envelope_digest,
            "payload_b64": base64.b64encode(payload).decode("ascii"),
            "payload_digest": payload_digest,
            "attempt_count": 0,
            "created_at": created_at,
            "last_attempt_at": None,
            "last_error": None,
            "acknowledged_at": None,
            "book_receipt": None,
            "quarantine_reason": None,
        }
        _atomic_write(self._path(PENDING, receipt_id), record)
        return record

    def pending_receipt_ids(self) -> tuple[str, ...]:
        if not self.pending_dir.is_dir():
            return ()
        return tuple(sorted(path.stem for path in self.pending_dir.glob("*.json")))

    def deliver_one(self, receipt_id: str, transport: BookTransport) -> Dict[str, Any]:
        path = self._path(PENDING, receipt_id)
        if not path.is_file():
            existing = self._find(receipt_id)
            if existing is None:
                raise KeyError(receipt_id)
            return _read(existing)

        record = _read(path)
        envelope = record["envelope"]
        payload = base64.b64decode(str(record["payload_b64"]), validate=True)
        if _sha256(payload) != record["payload_digest"]:
            return self.quarantine(receipt_id, "stored payload digest mismatch")
        if _sha256(_canonical(envelope)) != record["envelope_digest"]:
            return self.quarantine(receipt_id, "stored envelope digest mismatch")

        record["attempt_count"] = int(record.get("attempt_count", 0)) + 1
        record["last_attempt_at"] = _utc_now()
        record["last_error"] = None
        _atomic_write(path, record)

        try:
            response = transport.append_idempotent(envelope=envelope, payload=payload)
            receipt = BookAcceptance.from_mapping(response)
        except PermanentBookDeliveryError as exc:
            return self.quarantine(receipt_id, str(exc))
        except Exception as exc:
            record = _read(path)
            record["last_error"] = f"{type(exc).__name__}: {exc}"
            _atomic_write(path, record)
            return record

        if not receipt.accepted or receipt.receipt_id != receipt_id:
            return self.quarantine(receipt_id, "Book returned an invalid acceptance receipt")

        record = _read(path)
        record["state"] = ACKNOWLEDGED
        record["acknowledged_at"] = _utc_now()
        record["book_receipt"] = receipt.wire()
        acknowledged_path = self._path(ACKNOWLEDGED, receipt_id)
        _atomic_write(acknowledged_path, record)
        path.unlink()
        return record

    def deliver_pending(self, transport: BookTransport) -> tuple[Dict[str, Any], ...]:
        return tuple(self.deliver_one(receipt_id, transport) for receipt_id in self.pending_receipt_ids())

    def quarantine(self, receipt_id: str, reason: str) -> Dict[str, Any]:
        if not reason:
            raise BookOutboxError("quarantine reason is required")
        pending_path = self._path(PENDING, receipt_id)
        if not pending_path.is_file():
            existing = self._find(receipt_id)
            if existing is None:
                raise KeyError(receipt_id)
            return _read(existing)
        record = _read(pending_path)
        record["state"] = QUARANTINED
        record["quarantine_reason"] = reason
        record["last_error"] = reason
        quarantine_path = self._path(QUARANTINED, receipt_id)
        _atomic_write(quarantine_path, record)
        pending_path.unlink()
        return record
