from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..operations import canonical_hash
from ..store import writer_lock
from .question_bound import QuestionBoundPrediction, QuestionPredictionError


class QuestionPredictionJournalError(RuntimeError):
    pass


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(value), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _entry_body(sequence: int, prediction: QuestionBoundPrediction, journaled_at_ns: int, previous_hash: str) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "sequence": int(sequence),
        "prediction": prediction.to_wire(),
        "journaled_at_ns": int(journaled_at_ns),
        "previous_hash": previous_hash,
    }


def _entry_wire(sequence: int, prediction: QuestionBoundPrediction, journaled_at_ns: int, previous_hash: str) -> Dict[str, Any]:
    body = _entry_body(sequence, prediction, journaled_at_ns, previous_hash)
    value = dict(body)
    value["entry_hash"] = canonical_hash(body)
    return value


def _parse_entry(value: Mapping[str, Any]) -> Tuple[QuestionBoundPrediction, str]:
    body = {key: item for key, item in value.items() if key != "entry_hash"}
    expected = canonical_hash(body)
    if value.get("entry_hash") != expected:
        raise QuestionPredictionJournalError("question prediction journal entry hash mismatch")
    try:
        prediction = QuestionBoundPrediction.from_wire(value.get("prediction", {}))
    except (QuestionPredictionError, ValueError, TypeError) as exc:
        raise QuestionPredictionJournalError("journal contains invalid question prediction: %s" % exc) from exc
    return prediction, expected


class QuestionPredictionJournal:
    """Append-only record proving question-bound model claims existed before resolution."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.path = self.root / "memory/question_predictions.jsonl"
        self.state_path = self.root / "state/question_prediction_journal.json"

    def entries(self) -> Tuple[Mapping[str, Any], ...]:
        if not self.path.is_file():
            return ()
        records: List[Mapping[str, Any]] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise QuestionPredictionJournalError("question prediction journal line %d is invalid JSON" % line_number) from exc
            if not isinstance(value, dict):
                raise QuestionPredictionJournalError("question prediction journal line %d must be an object" % line_number)
            records.append(value)
        return tuple(records)

    def append(self, prediction: QuestionBoundPrediction, *, journaled_at_ns: int) -> Mapping[str, Any]:
        journaled = int(journaled_at_ns)
        if journaled < prediction.created_at_ns:
            raise QuestionPredictionJournalError("prediction cannot be journaled before created_at_ns")
        if prediction.mode == "PROSPECTIVE_SHADOW" and journaled >= prediction.resolves_at_ns:
            raise QuestionPredictionJournalError("prospective prediction must be durably journaled before resolution horizon")
        with writer_lock(self.root):
            records = self.entries()
            errors = _validate_records(records)
            if errors:
                raise QuestionPredictionJournalError("existing question prediction journal is invalid: " + "; ".join(errors))
            for record in records:
                existing, _ = _parse_entry(record)
                if existing.prediction_id != prediction.prediction_id:
                    continue
                if existing.to_wire() != prediction.to_wire():
                    raise QuestionPredictionJournalError("prediction_id already exists with different content")
                return record
            previous_hash = str(records[-1]["entry_hash"]) if records else "GENESIS"
            entry = _entry_wire(len(records), prediction, journaled, previous_hash)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._write_state(len(records) + 1, entry)
            return entry

    def _write_state(self, count: int, last_entry: Optional[Mapping[str, Any]]) -> None:
        state = {
            "schema_version": 1,
            "question_prediction_contract_version": "1.0",
            "authority": "append-only question-bound claims; outcomes resolve separately",
            "entry_count": int(count),
            "last_sequence": None if last_entry is None else int(last_entry["sequence"]),
            "last_entry_hash": None if last_entry is None else str(last_entry["entry_hash"]),
        }
        _atomic_json(self.state_path, state)

    def rebuild_state(self) -> Mapping[str, Any]:
        records = self.entries()
        errors = _validate_records(records)
        if errors:
            raise QuestionPredictionJournalError("journal cannot rebuild state: " + "; ".join(errors))
        self._write_state(len(records), records[-1] if records else None)
        return json.loads(self.state_path.read_text(encoding="utf-8"))


def _validate_records(records: Tuple[Mapping[str, Any], ...]) -> List[str]:
    errors: List[str] = []
    previous = "GENESIS"
    seen = set()
    for index, value in enumerate(records):
        try:
            prediction, entry_hash = _parse_entry(value)
        except QuestionPredictionJournalError as exc:
            errors.append("sequence %d: %s" % (index, exc))
            continue
        if value.get("schema_version") != 1:
            errors.append("sequence %d: unsupported journal schema" % index)
        if value.get("sequence") != index:
            errors.append("sequence %d: sequence mismatch" % index)
        if value.get("previous_hash") != previous:
            errors.append("sequence %d: previous_hash mismatch" % index)
        journaled = value.get("journaled_at_ns")
        if not isinstance(journaled, int) or journaled < prediction.created_at_ns:
            errors.append("sequence %d: journal timing invalid" % index)
        elif prediction.mode == "PROSPECTIVE_SHADOW" and journaled >= prediction.resolves_at_ns:
            errors.append("sequence %d: prospective prediction was journaled after resolution" % index)
        if prediction.prediction_id in seen:
            errors.append("sequence %d: duplicate prediction_id" % index)
        seen.add(prediction.prediction_id)
        previous = entry_hash
    return errors


def validate_question_prediction_journal(root: Path) -> List[str]:
    journal = QuestionPredictionJournal(root)
    try:
        records = journal.entries()
    except QuestionPredictionJournalError as exc:
        return [str(exc)]
    errors = _validate_records(records)
    if not journal.state_path.is_file():
        return errors
    try:
        state = json.loads(journal.state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append("question prediction journal state unreadable: %s" % exc)
        return errors
    if state.get("schema_version") != 1 or state.get("question_prediction_contract_version") != "1.0":
        errors.append("question prediction journal state schema invalid")
    if state.get("entry_count") != len(records):
        errors.append("question prediction journal state count mismatch")
    expected_sequence = None if not records else len(records) - 1
    expected_hash = None if not records else records[-1].get("entry_hash")
    if state.get("last_sequence") != expected_sequence:
        errors.append("question prediction journal state last_sequence mismatch")
    if state.get("last_entry_hash") != expected_hash:
        errors.append("question prediction journal state last_entry_hash mismatch")
    return errors
