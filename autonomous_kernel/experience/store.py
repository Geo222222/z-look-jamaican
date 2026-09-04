from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from ..book_bridge import canonical_json
from .contracts import MarketExperienceFrame
from .material_evidence import MaterialEvidenceIntent


class ExperienceStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExperiencePersistReceipt:
    experience_id: str
    content_hash: str
    created_snapshot: bool
    appended_event: bool
    sequence: Optional[int]
    event_hash: Optional[str]


@dataclass(frozen=True)
class ExperienceJournalCommitment:
    journal_name: str
    start_sequence: int
    end_sequence: int
    event_count: int
    first_event_hash: str
    last_event_hash: str
    range_digest: str
    last_experience_id: str
    last_cutoff_at_ns: int
    known_at_ns: int

    def body(self) -> Dict[str, object]:
        return {
            "schema_version": "ZLJ.EXPERIENCE_JOURNAL_COMMITMENT.v1",
            "journal_name": self.journal_name,
            "range": {
                "start_sequence": self.start_sequence,
                "end_sequence": self.end_sequence,
                "event_count": self.event_count,
                "first_event_hash": self.first_event_hash,
                "last_event_hash": self.last_event_hash,
                "range_digest": self.range_digest,
            },
            "last_experience_id": self.last_experience_id,
            "last_cutoff_at_ns": self.last_cutoff_at_ns,
            "known_at_ns": self.known_at_ns,
        }

    def material_evidence(
        self,
        *,
        payload_ref: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_receipt_id: Optional[str] = None,
        evidence_receipt_ids: Sequence[str] = (),
    ) -> MaterialEvidenceIntent:
        return MaterialEvidenceIntent(
            event_type="ZLJ.EXPERIENCE_JOURNAL_COMMITMENT",
            evidence_class="ANALYTICAL",
            subject_id=f"{self.journal_name}:{self.start_sequence}-{self.end_sequence}",
            occurred_at_ns=self.last_cutoff_at_ns,
            known_at_ns=self.known_at_ns,
            payload=canonical_json(self.body()),
            payload_ref=payload_ref,
            correlation_id=correlation_id,
            causation_receipt_id=causation_receipt_id,
            evidence_receipt_ids=tuple(evidence_receipt_ids),
        )


class MarketExperienceStore:
    """Immutable ZLJ experience snapshots plus a hash-chained local journal.

    The journal is ZLJ learning state, not The Book. Periodic compact commitments
    can be signed and delivered to The Book through `ExperienceJournalCommitment`.
    """

    JOURNAL_NAME = "ZLJ.MARKET_EXPERIENCE.v1"

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.snapshot_dir = self.root / "artifacts" / "market_experience" / "frames"
        self.journal_path = self.root / "memory" / "market_experiences.jsonl"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)

    def persist(self, frame: MarketExperienceFrame) -> ExperiencePersistReceipt:
        snapshot_path = self.snapshot_dir / f"{frame.experience_id}.json"
        snapshot_bytes = canonical_json(frame.to_wire())
        created_snapshot = False
        if snapshot_path.exists():
            existing = snapshot_path.read_bytes()
            if existing.endswith(b"\n"):
                existing = existing[:-1]
            if existing != snapshot_bytes:
                raise ExperienceStoreError("existing experience snapshot differs from immutable content")
        else:
            self._atomic_create(snapshot_path, snapshot_bytes + b"\n")
            created_snapshot = True

        existing_event = self._event_for_experience(frame.experience_id)
        if existing_event is not None:
            if existing_event.get("content_hash") != frame.content_hash():
                raise ExperienceStoreError("experience journal identity conflicts with stored content")
            return ExperiencePersistReceipt(
                experience_id=frame.experience_id,
                content_hash=frame.content_hash(),
                created_snapshot=created_snapshot,
                appended_event=False,
                sequence=int(existing_event["sequence"]),
                event_hash=str(existing_event["event_hash"]),
            )

        events = tuple(self._events())
        sequence = len(events) + 1
        previous_hash = "GENESIS" if not events else str(events[-1]["event_hash"])
        body: Dict[str, object] = {
            "schema_version": "ZLJ.MARKET_EXPERIENCE.EVENT.v1",
            "journal_name": self.JOURNAL_NAME,
            "sequence": sequence,
            "experience_id": frame.experience_id,
            "content_hash": frame.content_hash(),
            "economic_root_id": frame.economic_root_id,
            "cutoff_at_ns": frame.cutoff_at_ns,
            "known_at_ns": frame.known_at_ns,
            "status": frame.status,
            "graph_hash": frame.graph_hash,
            "context_hash": frame.context_hash,
            "previous_event_hash": previous_hash,
        }
        event_hash = hashlib.sha256(canonical_json(body)).hexdigest()
        event = dict(body)
        event["event_hash"] = event_hash
        with self.journal_path.open("ab") as handle:
            handle.write(canonical_json(event))
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        return ExperiencePersistReceipt(
            experience_id=frame.experience_id,
            content_hash=frame.content_hash(),
            created_snapshot=created_snapshot,
            appended_event=True,
            sequence=sequence,
            event_hash=event_hash,
        )

    def load(self, experience_id: str) -> MarketExperienceFrame:
        path = self.snapshot_dir / f"{experience_id}.json"
        if not path.is_file():
            raise KeyError(experience_id)
        value = json.loads(path.read_text(encoding="utf-8"))
        return MarketExperienceFrame.from_wire(value)

    def verify(self) -> Tuple[bool, Tuple[str, ...]]:
        errors = []
        expected_previous = "GENESIS"
        expected_sequence = 1
        for event in self._events():
            sequence = int(event.get("sequence", -1))
            if sequence != expected_sequence:
                errors.append(f"sequence {sequence}: expected {expected_sequence}")
            if event.get("previous_event_hash") != expected_previous:
                errors.append(f"sequence {sequence}: previous hash mismatch")
            body = dict(event)
            claimed_hash = str(body.pop("event_hash", ""))
            computed_hash = hashlib.sha256(canonical_json(body)).hexdigest()
            if claimed_hash != computed_hash:
                errors.append(f"sequence {sequence}: content hash mismatch")
            experience_id = str(event.get("experience_id", ""))
            snapshot_path = self.snapshot_dir / f"{experience_id}.json"
            if not snapshot_path.is_file():
                errors.append(f"sequence {sequence}: missing experience snapshot")
            else:
                try:
                    frame = MarketExperienceFrame.from_wire(json.loads(snapshot_path.read_text(encoding="utf-8")))
                    if frame.content_hash() != event.get("content_hash"):
                        errors.append(f"sequence {sequence}: snapshot content hash mismatch")
                except Exception as exc:
                    errors.append(f"sequence {sequence}: invalid snapshot: {type(exc).__name__}")
            expected_previous = computed_hash
            expected_sequence += 1
        return not errors, tuple(errors)

    def commitment(self, *, start_sequence: int = 1, end_sequence: Optional[int] = None) -> ExperienceJournalCommitment:
        events = tuple(self._events())
        if not events:
            raise ExperienceStoreError("cannot commit an empty experience journal")
        end = len(events) if end_sequence is None else int(end_sequence)
        start = int(start_sequence)
        if start <= 0 or end < start or end > len(events):
            raise ExperienceStoreError("invalid experience journal commitment range")
        selected = events[start - 1 : end]
        for offset, event in enumerate(selected, start=start):
            if int(event.get("sequence", -1)) != offset:
                raise ExperienceStoreError("journal sequence is not contiguous")
        hashes = [str(event["event_hash"]) for event in selected]
        range_digest = hashlib.sha256(canonical_json(hashes)).hexdigest()
        last = selected[-1]
        return ExperienceJournalCommitment(
            journal_name=self.JOURNAL_NAME,
            start_sequence=start,
            end_sequence=end,
            event_count=len(selected),
            first_event_hash=hashes[0],
            last_event_hash=hashes[-1],
            range_digest=range_digest,
            last_experience_id=str(last["experience_id"]),
            last_cutoff_at_ns=int(last["cutoff_at_ns"]),
            known_at_ns=max(int(event["known_at_ns"]) for event in selected),
        )

    def _event_for_experience(self, experience_id: str) -> Optional[Mapping[str, object]]:
        for event in self._events():
            if event.get("experience_id") == experience_id:
                return event
        return None

    def _events(self) -> Iterable[Dict[str, object]]:
        if not self.journal_path.is_file():
            return ()
        result = []
        with self.journal_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ExperienceStoreError("experience journal entry must be an object")
                    result.append(value)
        return tuple(result)

    @staticmethod
    def _atomic_create(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                raise FileExistsError(path)
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
