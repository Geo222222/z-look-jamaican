from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..book_bridge import canonical_json
from ..experience.material_evidence import MaterialEvidenceIntent


SUPPORTED_LEARNING_JOURNALS = {
    "ZLJ.QUESTION_PREDICTIONS.v1",
    "ZLJ.QUESTION_OUTCOMES.v1",
}


class LearningEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class LearningJournalCommitment:
    journal_name: str
    start_sequence: int
    end_sequence: int
    event_count: int
    first_entry_hash: str
    last_entry_hash: str
    range_digest: str
    known_at_ns: int
    last_subject_id: str

    def __post_init__(self) -> None:
        if self.journal_name not in SUPPORTED_LEARNING_JOURNALS:
            raise LearningEvidenceError("unsupported learning journal")
        if self.start_sequence < 0 or self.end_sequence < self.start_sequence:
            raise LearningEvidenceError("learning journal commitment range is invalid")
        if self.event_count != self.end_sequence - self.start_sequence + 1:
            raise LearningEvidenceError("learning journal event_count does not match range")
        if self.known_at_ns < 0 or not self.last_subject_id:
            raise LearningEvidenceError("learning journal commitment identity/timing is invalid")
        for value in (self.first_entry_hash, self.last_entry_hash, self.range_digest):
            if len(value) != 64:
                raise LearningEvidenceError("learning journal hashes must be SHA-256 hex")
            try:
                int(value, 16)
            except ValueError as exc:
                raise LearningEvidenceError("learning journal hash must be hexadecimal") from exc

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": "ZLJ.LEARNING_JOURNAL_COMMITMENT.v1",
            "journal_name": self.journal_name,
            "range": {
                "start_sequence": self.start_sequence,
                "end_sequence": self.end_sequence,
                "event_count": self.event_count,
                "first_entry_hash": self.first_entry_hash,
                "last_entry_hash": self.last_entry_hash,
                "range_digest": self.range_digest,
            },
            "known_at_ns": self.known_at_ns,
            "last_subject_id": self.last_subject_id,
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
            event_type="ZLJ.LEARNING_JOURNAL_COMMITMENT",
            evidence_class="ANALYTICAL",
            subject_id="%s:%d-%d" % (self.journal_name, self.start_sequence, self.end_sequence),
            occurred_at_ns=self.known_at_ns,
            known_at_ns=self.known_at_ns,
            payload=canonical_json(self.body()),
            payload_ref=payload_ref,
            correlation_id=correlation_id,
            causation_receipt_id=causation_receipt_id,
            evidence_receipt_ids=tuple(evidence_receipt_ids),
        )


def build_learning_journal_commitment(
    *,
    journal_name: str,
    records: Sequence[Mapping[str, Any]],
    start_sequence: int = 0,
    end_sequence: Optional[int] = None,
) -> LearningJournalCommitment:
    if journal_name not in SUPPORTED_LEARNING_JOURNALS:
        raise LearningEvidenceError("unsupported learning journal")
    items = tuple(records)
    if not items:
        raise LearningEvidenceError("cannot commit empty learning journal")
    end = len(items) - 1 if end_sequence is None else int(end_sequence)
    start = int(start_sequence)
    if start < 0 or end < start or end >= len(items):
        raise LearningEvidenceError("learning journal commitment range is invalid")
    selected = items[start : end + 1]
    hashes = []
    known_values = []
    subject_ids = []
    for expected, record in enumerate(selected, start=start):
        if int(record.get("sequence", -1)) != expected:
            raise LearningEvidenceError("learning journal sequence is not contiguous")
        entry_hash = str(record.get("entry_hash", ""))
        if len(entry_hash) != 64:
            raise LearningEvidenceError("learning journal entry hash is invalid")
        hashes.append(entry_hash)
        if journal_name == "ZLJ.QUESTION_PREDICTIONS.v1":
            payload = record.get("prediction")
            if not isinstance(payload, Mapping):
                raise LearningEvidenceError("prediction journal record is malformed")
            timing = payload.get("timing")
            if not isinstance(timing, Mapping):
                raise LearningEvidenceError("prediction timing is malformed")
            known_values.append(int(record.get("journaled_at_ns", -1)))
            subject_ids.append(str(payload.get("prediction_id", "")))
        else:
            payload = record.get("outcome")
            if not isinstance(payload, Mapping):
                raise LearningEvidenceError("outcome journal record is malformed")
            timing = payload.get("timing")
            if not isinstance(timing, Mapping):
                raise LearningEvidenceError("outcome timing is malformed")
            known_values.append(int(timing.get("decided_at_ns", -1)))
            subject_ids.append(str(payload.get("outcome_id", "")))
    if any(value < 0 for value in known_values) or any(not value for value in subject_ids):
        raise LearningEvidenceError("learning journal commitment record timing/identity is invalid")
    range_digest = hashlib.sha256(canonical_json(hashes)).hexdigest()
    return LearningJournalCommitment(
        journal_name=journal_name,
        start_sequence=start,
        end_sequence=end,
        event_count=len(selected),
        first_entry_hash=hashes[0],
        last_entry_hash=hashes[-1],
        range_digest=range_digest,
        known_at_ns=max(known_values),
        last_subject_id=subject_ids[-1],
    )
