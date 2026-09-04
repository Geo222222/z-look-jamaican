from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from ..operations import canonical_hash
from ..store import writer_lock
from .contracts import AssemblyContractError, AssemblyReceipt


class AssemblyJournalError(RuntimeError):
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


def _entry_body(sequence: int, receipt: AssemblyReceipt, previous_hash: str) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "sequence": int(sequence),
        "receipt": receipt.to_wire(),
        "previous_hash": previous_hash,
    }


def _entry_wire(sequence: int, receipt: AssemblyReceipt, previous_hash: str) -> Dict[str, Any]:
    body = _entry_body(sequence, receipt, previous_hash)
    value = dict(body)
    value["entry_hash"] = canonical_hash(body)
    return value


def _parse_entry(value: Mapping[str, Any]) -> Tuple[AssemblyReceipt, str]:
    body = {key: item for key, item in value.items() if key != "entry_hash"}
    expected = canonical_hash(body)
    if value.get("entry_hash") != expected:
        raise AssemblyJournalError("assembly journal entry hash mismatch")
    try:
        receipt = AssemblyReceipt.from_wire(value.get("receipt", {}))
    except (AssemblyContractError, ValueError, TypeError) as exc:
        raise AssemblyJournalError("assembly journal contains invalid receipt: %s" % exc) from exc
    return receipt, expected


class AssemblyJournal:
    """Durable explanation of how each assembled prediction was weighted."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.path = self.root / "memory/assemblies.jsonl"
        self.state_path = self.root / "state/assembly_journal.json"

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
                raise AssemblyJournalError("assembly journal line %d is invalid JSON" % line_number) from exc
            if not isinstance(value, dict):
                raise AssemblyJournalError("assembly journal line %d must be an object" % line_number)
            output.append(value)
        return tuple(output)

    def append(self, receipt: AssemblyReceipt) -> Mapping[str, Any]:
        with writer_lock(self.root):
            records = self.entries()
            errors = _validate_records(records)
            if errors:
                raise AssemblyJournalError("existing assembly journal is invalid: " + "; ".join(errors))
            for record in records:
                existing, _ = _parse_entry(record)
                if existing.assembled_prediction_id != receipt.assembled_prediction_id:
                    continue
                if existing.to_wire() != receipt.to_wire():
                    raise AssemblyJournalError("assembled prediction already has a different assembly receipt")
                self._write_state(records)
                return record
            previous = str(records[-1]["entry_hash"]) if records else "GENESIS"
            entry = _entry_wire(len(records), receipt, previous)
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
            "assembly_contract_version": "1.0",
            "authority": "append-only adaptive weighting evidence; never execution authority",
            "entry_count": len(records),
            "last_sequence": None if not records else int(records[-1]["sequence"]),
            "last_entry_hash": None if not records else str(records[-1]["entry_hash"]),
        }
        _atomic_json(self.state_path, state)

    def rebuild_state(self) -> Mapping[str, Any]:
        records = self.entries()
        errors = _validate_records(records)
        if errors:
            raise AssemblyJournalError("assembly journal cannot rebuild state: " + "; ".join(errors))
        self._write_state(records)
        return json.loads(self.state_path.read_text(encoding="utf-8"))


def _validate_records(records: Tuple[Mapping[str, Any], ...]) -> List[str]:
    errors: List[str] = []
    previous = "GENESIS"
    seen_predictions = set()
    seen_receipts = set()
    for index, value in enumerate(records):
        try:
            receipt, entry_hash = _parse_entry(value)
        except AssemblyJournalError as exc:
            errors.append("sequence %d: %s" % (index, exc))
            continue
        if value.get("schema_version") != 1 or value.get("sequence") != index:
            errors.append("sequence %d: schema/sequence mismatch" % index)
        if value.get("previous_hash") != previous:
            errors.append("sequence %d: previous_hash mismatch" % index)
        if receipt.assembled_prediction_id in seen_predictions:
            errors.append("sequence %d: duplicate assembled prediction" % index)
        if receipt.receipt_id in seen_receipts:
            errors.append("sequence %d: duplicate assembly receipt" % index)
        seen_predictions.add(receipt.assembled_prediction_id)
        seen_receipts.add(receipt.receipt_id)
        previous = entry_hash
    return errors


def validate_assembly_journal(root: Path) -> List[str]:
    journal = AssemblyJournal(root)
    full_kernel = (root / "state/current_state.json").is_file()
    errors: List[str] = []
    if full_kernel and not journal.path.is_file():
        errors.append("missing required journal: memory/assemblies.jsonl")
    try:
        records = journal.entries()
    except AssemblyJournalError as exc:
        return errors + [str(exc)]
    errors.extend(_validate_records(records))
    if not journal.state_path.is_file():
        if full_kernel:
            errors.append("missing required state file: state/assembly_journal.json")
        return errors
    try:
        state = json.loads(journal.state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append("state/assembly_journal.json unreadable: %s" % exc)
        return errors
    if state.get("schema_version") != 1 or state.get("assembly_contract_version") != "1.0":
        errors.append("assembly journal state schema invalid")
    if state.get("entry_count") != len(records):
        errors.append("assembly journal state count mismatch")
    expected_sequence = None if not records else len(records) - 1
    expected_hash = None if not records else records[-1].get("entry_hash")
    if state.get("last_sequence") != expected_sequence:
        errors.append("assembly journal state last_sequence mismatch")
    if state.get("last_entry_hash") != expected_hash:
        errors.append("assembly journal state last_entry_hash mismatch")
    return errors
