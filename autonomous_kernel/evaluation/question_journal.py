from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..operations import canonical_hash
from ..prediction.question_bound import QuestionBoundPrediction, QuestionPredictionError
from ..prediction.question_journal import QuestionPredictionJournal, validate_question_prediction_journal
from ..store import writer_lock
from .question_outcome import QuestionBoundOutcome, QuestionOutcomeError


class QuestionOutcomeJournalError(RuntimeError):
    pass


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(value), handle, indent=2, sort_keys=True); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _entry_body(sequence: int, outcome: QuestionBoundOutcome, previous_hash: str) -> Dict[str, Any]:
    return {"schema_version": 1, "sequence": int(sequence), "outcome": outcome.to_wire(), "previous_hash": previous_hash}


def _entry_wire(sequence: int, outcome: QuestionBoundOutcome, previous_hash: str) -> Dict[str, Any]:
    body = _entry_body(sequence, outcome, previous_hash); value = dict(body); value["entry_hash"] = canonical_hash(body); return value


def _parse_entry(value: Mapping[str, Any]) -> Tuple[QuestionBoundOutcome, str]:
    body = {key: item for key, item in value.items() if key != "entry_hash"}; expected = canonical_hash(body)
    if value.get("entry_hash") != expected: raise QuestionOutcomeJournalError("question outcome journal entry hash mismatch")
    try: outcome = QuestionBoundOutcome.from_wire(value.get("outcome", {}))
    except (QuestionOutcomeError, ValueError, TypeError) as exc: raise QuestionOutcomeJournalError("journal contains invalid question outcome: %s" % exc) from exc
    return outcome, expected


def _prediction_lineage_error(root: Path, outcome: QuestionBoundOutcome) -> Optional[str]:
    errors = validate_question_prediction_journal(root)
    if errors: return "question prediction journal invalid: " + "; ".join(errors)
    matches = [entry for entry in QuestionPredictionJournal(root).entries() if entry.get("prediction", {}).get("prediction_id") == outcome.prediction_id]
    if len(matches) != 1: return "outcome must cite exactly one journaled question prediction"
    entry = matches[0]
    try: prediction = QuestionBoundPrediction.from_wire(entry.get("prediction", {}))
    except (QuestionPredictionError, ValueError, TypeError) as exc: return "cited question prediction is invalid: %s" % exc
    checks = (
        (outcome.prediction_content_hash == prediction.content_hash(), "prediction content hash mismatch"),
        (outcome.prediction_journal_entry_hash == entry.get("entry_hash"), "prediction journal entry hash mismatch"),
        (outcome.question_ref == prediction.question_ref, "question ref mismatch"),
        (outcome.question_definition_hash == prediction.question_definition_hash, "question definition hash mismatch"),
        (outcome.question_registry_hash == prediction.question_registry_hash, "question registry hash mismatch"),
        (outcome.subject_id == prediction.subject_id, "prediction subject mismatch"),
        (outcome.answer_kind == prediction.answer_kind, "answer kind mismatch"),
        (outcome.outcome_metric_id == prediction.outcome_metric_id, "outcome metric mismatch"),
        (outcome.resolver_policy_id == prediction.resolver_policy_id, "resolver policy mismatch"),
        (outcome.cutoff_at_ns == prediction.cutoff_at_ns, "cutoff mismatch"),
        (outcome.target_resolves_at_ns == prediction.resolves_at_ns, "resolution target mismatch"),
        (outcome.max_resolution_lag_ns == prediction.max_resolution_lag_ns, "resolution lag mismatch"),
    )
    failed = [message for passed, message in checks if not passed]; return None if not failed else "; ".join(failed)


class QuestionOutcomeJournal:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(); self.path = self.root / "memory/question_outcomes.jsonl"; self.state_path = self.root / "state/question_outcome_journal.json"

    def entries(self) -> Tuple[Mapping[str, Any], ...]:
        if not self.path.is_file(): return ()
        output: List[Mapping[str, Any]] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip(): continue
            try: value = json.loads(line)
            except json.JSONDecodeError as exc: raise QuestionOutcomeJournalError("question outcome journal line %d is invalid JSON" % line_number) from exc
            if not isinstance(value, dict): raise QuestionOutcomeJournalError("question outcome journal line %d must be an object" % line_number)
            output.append(value)
        return tuple(output)

    def append(self, outcome: QuestionBoundOutcome) -> Mapping[str, Any]:
        lineage_error = _prediction_lineage_error(self.root, outcome)
        if lineage_error: raise QuestionOutcomeJournalError("outcome prediction lineage invalid: " + lineage_error)
        with writer_lock(self.root):
            records = self.entries(); errors = _validate_records(records)
            if errors: raise QuestionOutcomeJournalError("existing question outcome journal is invalid: " + "; ".join(errors))
            for record in records:
                existing, _ = _parse_entry(record)
                if existing.prediction_id != outcome.prediction_id: continue
                if existing.to_wire() != outcome.to_wire(): raise QuestionOutcomeJournalError("prediction already has a different final question outcome")
                self._write_state(records); return record
            previous = str(records[-1]["entry_hash"]) if records else "GENESIS"; entry = _entry_wire(len(records), outcome, previous)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"); handle.flush(); os.fsync(handle.fileno())
            self._write_state(tuple(records) + (entry,)); return entry

    def _write_state(self, records: Tuple[Mapping[str, Any], ...]) -> None:
        _atomic_json(self.state_path, {"schema_version": 1, "question_outcome_contract_version": "1.1", "authority": "append-only market outcomes; evaluation scores are separate", "entry_count": len(records), "last_sequence": None if not records else int(records[-1]["sequence"]), "last_entry_hash": None if not records else str(records[-1]["entry_hash"])})

    def rebuild_state(self) -> Mapping[str, Any]:
        records = self.entries(); errors = _validate_records(records)
        if errors: raise QuestionOutcomeJournalError("question outcome journal cannot rebuild state: " + "; ".join(errors))
        self._write_state(records); return json.loads(self.state_path.read_text(encoding="utf-8"))


def _validate_records(records: Tuple[Mapping[str, Any], ...]) -> List[str]:
    errors: List[str] = []; previous = "GENESIS"; seen = set()
    for index, value in enumerate(records):
        try: outcome, entry_hash = _parse_entry(value)
        except QuestionOutcomeJournalError as exc: errors.append("sequence %d: %s" % (index, exc)); continue
        if value.get("schema_version") != 1 or value.get("sequence") != index: errors.append("sequence %d: schema/sequence mismatch" % index)
        if value.get("previous_hash") != previous: errors.append("sequence %d: previous_hash mismatch" % index)
        if outcome.prediction_id in seen: errors.append("sequence %d: duplicate prediction outcome" % index)
        seen.add(outcome.prediction_id); previous = entry_hash
    return errors


def validate_question_outcome_journal(root: Path) -> List[str]:
    journal = QuestionOutcomeJournal(root)
    try: records = journal.entries()
    except QuestionOutcomeJournalError as exc: return [str(exc)]
    errors = _validate_records(records)
    if not errors:
        for index, record in enumerate(records):
            try: outcome, _ = _parse_entry(record)
            except QuestionOutcomeJournalError: continue
            lineage_error = _prediction_lineage_error(root, outcome)
            if lineage_error: errors.append("sequence %d: outcome prediction lineage invalid: %s" % (index, lineage_error))
    if not journal.state_path.is_file(): return errors
    try: state = json.loads(journal.state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc: errors.append("question outcome journal state unreadable: %s" % exc); return errors
    if state.get("schema_version") != 1 or state.get("question_outcome_contract_version") != "1.1": errors.append("question outcome journal state schema invalid")
    if state.get("entry_count") != len(records): errors.append("question outcome journal state count mismatch")
    expected_sequence = None if not records else len(records) - 1; expected_hash = None if not records else records[-1].get("entry_hash")
    if state.get("last_sequence") != expected_sequence: errors.append("question outcome journal state last_sequence mismatch")
    if state.get("last_entry_hash") != expected_hash: errors.append("question outcome journal state last_entry_hash mismatch")
    return errors
