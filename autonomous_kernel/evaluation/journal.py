from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from ..operations import canonical_hash
from ..prediction.contracts import Prediction, PredictionContractError
from ..prediction.journal import PredictionJournal, validate_prediction_journal
from ..store import writer_lock
from .contracts import OutcomeContractError, PredictionOutcome


class OutcomeJournalError(RuntimeError):
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


def _entry_body(sequence: int, outcome: PredictionOutcome, previous_hash: str) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "sequence": int(sequence),
        "outcome": outcome.to_wire(),
        "previous_hash": previous_hash,
    }


def _entry_wire(sequence: int, outcome: PredictionOutcome, previous_hash: str) -> Dict[str, Any]:
    body = _entry_body(sequence, outcome, previous_hash)
    value = dict(body)
    value["entry_hash"] = canonical_hash(body)
    return value


def _parse_entry(value: Mapping[str, Any]) -> Tuple[PredictionOutcome, str]:
    body = {key: item for key, item in value.items() if key != "entry_hash"}
    expected = canonical_hash(body)
    if value.get("entry_hash") != expected:
        raise OutcomeJournalError("outcome journal entry hash mismatch")
    try:
        outcome = PredictionOutcome.from_wire(value.get("outcome", {}))
    except (OutcomeContractError, ValueError, TypeError) as exc:
        raise OutcomeJournalError("outcome journal contains invalid outcome: %s" % exc) from exc
    return outcome, expected


def _prediction_lineage_error(root: Path, outcome: PredictionOutcome) -> str | None:
    prediction_errors = validate_prediction_journal(root)
    if prediction_errors:
        return "prediction journal invalid: " + "; ".join(prediction_errors)
    matches = [
        entry
        for entry in PredictionJournal(root).entries()
        if entry.get("prediction", {}).get("prediction_id") == outcome.prediction_id
    ]
    if len(matches) != 1:
        return "outcome must cite exactly one journaled prediction"
    entry = matches[0]
    try:
        prediction = Prediction.from_wire(entry.get("prediction", {}))
    except (PredictionContractError, ValueError, TypeError) as exc:
        return "cited prediction is invalid: %s" % exc
    checks = (
        (outcome.prediction_content_hash == prediction.content_hash(), "prediction content hash mismatch"),
        (outcome.prediction_journal_entry_hash == entry.get("entry_hash"), "prediction journal entry hash mismatch"),
        (outcome.evidence_class == prediction.evidence_class, "prediction evidence class mismatch"),
        (outcome.target_metric == prediction.target_metric, "prediction target metric mismatch"),
        (outcome.model_refs == prediction.model_refs, "prediction model lineage mismatch"),
        (outcome.target_resolves_at_ns == prediction.resolves_at_ns, "prediction resolution time mismatch"),
        (outcome.reference_price == prediction.reference_price, "prediction reference price mismatch"),
        (outcome.reference_price_source == prediction.reference_price_source, "prediction reference source mismatch"),
    )
    failed = [message for passed, message in checks if not passed]
    return None if not failed else "; ".join(failed)


class OutcomeJournal:
    """Append-only final outcomes; each prediction can receive exactly one outcome."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.path = self.root / "memory/outcomes.jsonl"
        self.state_path = self.root / "state/outcome_journal.json"

    def entries(self) -> Tuple[Mapping[str, Any], ...]:
        if not self.path.is_file():
            return ()
        output: List[Mapping[str, Any]] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OutcomeJournalError("outcome journal line %d is invalid JSON" % line_number) from exc
            if not isinstance(value, dict):
                raise OutcomeJournalError("outcome journal line %d must be an object" % line_number)
            output.append(value)
        return tuple(output)

    def append(self, outcome: PredictionOutcome) -> Mapping[str, Any]:
        lineage_error = _prediction_lineage_error(self.root, outcome)
        if lineage_error:
            raise OutcomeJournalError("outcome prediction lineage invalid: " + lineage_error)
        with writer_lock(self.root):
            records = self.entries()
            errors = _validate_records(records)
            if errors:
                raise OutcomeJournalError("existing outcome journal is invalid: " + "; ".join(errors))
            for record in records:
                existing, _ = _parse_entry(record)
                if existing.prediction_id != outcome.prediction_id:
                    continue
                if existing.to_wire() != outcome.to_wire():
                    raise OutcomeJournalError("prediction already has a different final outcome")
                self._write_state(records)
                return record
            previous = str(records[-1]["entry_hash"]) if records else "GENESIS"
            entry = _entry_wire(len(records), outcome, previous)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._write_state(tuple(records) + (entry,))
            return entry

    def _write_state(self, records: Tuple[Mapping[str, Any], ...]) -> None:
        state = {
            "schema_version": 1,
            "outcome_contract_version": "1.0",
            "authority": "append-only independently resolved prediction outcomes",
            "entry_count": len(records),
            "last_sequence": None if not records else int(records[-1]["sequence"]),
            "last_entry_hash": None if not records else str(records[-1]["entry_hash"]),
        }
        _atomic_json(self.state_path, state)

    def rebuild_state(self) -> Mapping[str, Any]:
        records = self.entries()
        errors = _validate_records(records)
        if errors:
            raise OutcomeJournalError("outcome journal cannot rebuild state: " + "; ".join(errors))
        self._write_state(records)
        return json.loads(self.state_path.read_text(encoding="utf-8"))


def _validate_records(records: Tuple[Mapping[str, Any], ...]) -> List[str]:
    errors: List[str] = []
    previous = "GENESIS"
    seen_predictions = set()
    for index, value in enumerate(records):
        try:
            outcome, entry_hash = _parse_entry(value)
        except OutcomeJournalError as exc:
            errors.append("sequence %d: %s" % (index, exc))
            continue
        if value.get("schema_version") != 1 or value.get("sequence") != index:
            errors.append("sequence %d: schema/sequence mismatch" % index)
        if value.get("previous_hash") != previous:
            errors.append("sequence %d: previous_hash mismatch" % index)
        if outcome.prediction_id in seen_predictions:
            errors.append("sequence %d: duplicate prediction outcome" % index)
        seen_predictions.add(outcome.prediction_id)
        previous = entry_hash
    return errors


def validate_outcome_journal(root: Path) -> List[str]:
    journal = OutcomeJournal(root)
    full_kernel = (root / "state/current_state.json").is_file()
    errors: List[str] = []
    if full_kernel and not journal.path.is_file():
        errors.append("missing required journal: memory/outcomes.jsonl")
    try:
        records = journal.entries()
    except OutcomeJournalError as exc:
        return errors + [str(exc)]
    errors.extend(_validate_records(records))
    if not errors:
        for index, record in enumerate(records):
            try:
                outcome, _ = _parse_entry(record)
            except OutcomeJournalError:
                continue
            lineage_error = _prediction_lineage_error(root, outcome)
            if lineage_error:
                errors.append("sequence %d: outcome prediction lineage invalid: %s" % (index, lineage_error))
    if not journal.state_path.is_file():
        if full_kernel:
            errors.append("missing required state file: state/outcome_journal.json")
        return errors
    try:
        state = json.loads(journal.state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append("state/outcome_journal.json unreadable: %s" % exc)
        return errors
    if state.get("schema_version") != 1 or state.get("outcome_contract_version") != "1.0":
        errors.append("outcome journal state schema invalid")
    if state.get("entry_count") != len(records):
        errors.append("outcome journal state count mismatch")
    expected_sequence = None if not records else len(records) - 1
    expected_hash = None if not records else records[-1].get("entry_hash")
    if state.get("last_sequence") != expected_sequence:
        errors.append("outcome journal state last_sequence mismatch")
    if state.get("last_entry_hash") != expected_hash:
        errors.append("outcome journal state last_entry_hash mismatch")
    return errors
