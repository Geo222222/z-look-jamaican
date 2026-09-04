from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from ..operations import canonical_hash
from ..store import writer_lock
from .contextual import CONTEXTUAL_ASSEMBLY_SCHEMA_VERSION, ContextualAssemblyError, ContextualAssemblyReceipt


class ContextualAssemblyJournalError(RuntimeError):
    pass


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(value), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _entry_wire(sequence: int, receipt: ContextualAssemblyReceipt, previous_hash: str) -> Dict[str, Any]:
    body = {"schema_version": 1, "sequence": int(sequence), "receipt": receipt.to_wire(), "previous_hash": previous_hash}
    return dict(body, entry_hash=canonical_hash(body))


def _parse(value: Mapping[str, Any]) -> Tuple[ContextualAssemblyReceipt, str]:
    body = {key: item for key, item in value.items() if key != "entry_hash"}
    expected = canonical_hash(body)
    if value.get("entry_hash") != expected:
        raise ContextualAssemblyJournalError("contextual assembly journal entry hash mismatch")
    try:
        receipt = ContextualAssemblyReceipt.from_wire(value.get("receipt", {}))
    except (ContextualAssemblyError, ValueError, TypeError) as exc:
        raise ContextualAssemblyJournalError("contextual assembly receipt invalid: %s" % exc) from exc
    return receipt, expected


class ContextualAssemblyJournal:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(); self.path = self.root / "memory/contextual_assemblies.jsonl"; self.state_path = self.root / "state/contextual_assembly_journal.json"

    def entries(self) -> Tuple[Mapping[str, Any], ...]:
        if not self.path.is_file(): return ()
        output: List[Mapping[str, Any]] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip(): continue
            try: value = json.loads(line)
            except json.JSONDecodeError as exc: raise ContextualAssemblyJournalError("contextual journal line %d invalid JSON" % line_number) from exc
            if not isinstance(value, dict): raise ContextualAssemblyJournalError("contextual journal entries must be objects")
            output.append(value)
        return tuple(output)

    def append(self, receipt: ContextualAssemblyReceipt) -> Mapping[str, Any]:
        with writer_lock(self.root):
            records = self.entries(); errors = _validate_records(records)
            if errors: raise ContextualAssemblyJournalError("existing contextual journal invalid: " + "; ".join(errors))
            for record in records:
                existing, _ = _parse(record)
                if existing.final_prediction_id == receipt.final_prediction_id:
                    if existing.to_wire() != receipt.to_wire(): raise ContextualAssemblyJournalError("final prediction already has different contextual receipt")
                    self._write_state(records); return record
            previous = str(records[-1]["entry_hash"]) if records else "GENESIS"
            entry = _entry_wire(len(records), receipt, previous)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"); handle.flush(); os.fsync(handle.fileno())
            self._write_state(tuple(records) + (entry,)); return entry

    def _write_state(self, records: Tuple[Mapping[str, Any], ...]) -> None:
        _atomic_json(self.state_path, {"schema_version": 1, "contextual_assembly_contract_version": CONTEXTUAL_ASSEMBLY_SCHEMA_VERSION, "authority": "append-only Z8+Z9 informational weighting evidence; never execution authority", "entry_count": len(records), "last_sequence": None if not records else int(records[-1]["sequence"]), "last_entry_hash": None if not records else str(records[-1]["entry_hash"])})


def _validate_records(records: Tuple[Mapping[str, Any], ...]) -> List[str]:
    errors: List[str] = []; previous = "GENESIS"; seen_predictions = set(); seen_receipts = set()
    for index, value in enumerate(records):
        try: receipt, entry_hash = _parse(value)
        except ContextualAssemblyJournalError as exc: errors.append("sequence %d: %s" % (index, exc)); continue
        if value.get("schema_version") != 1 or value.get("sequence") != index: errors.append("sequence %d: schema/sequence mismatch" % index)
        if value.get("previous_hash") != previous: errors.append("sequence %d: previous_hash mismatch" % index)
        if receipt.final_prediction_id in seen_predictions: errors.append("sequence %d: duplicate final prediction" % index)
        if receipt.receipt_id in seen_receipts: errors.append("sequence %d: duplicate contextual receipt" % index)
        seen_predictions.add(receipt.final_prediction_id); seen_receipts.add(receipt.receipt_id); previous = entry_hash
    return errors


def validate_contextual_assembly_journal(root: Path) -> List[str]:
    journal = ContextualAssemblyJournal(root); full_kernel = (root / "state/current_state.json").is_file(); errors: List[str] = []
    if full_kernel and not journal.path.is_file(): errors.append("missing required journal: memory/contextual_assemblies.jsonl")
    try: records = journal.entries()
    except ContextualAssemblyJournalError as exc: return errors + [str(exc)]
    errors.extend(_validate_records(records))
    if not journal.state_path.is_file(): return errors + (["missing required state file: state/contextual_assembly_journal.json"] if full_kernel else [])
    try: state = json.loads(journal.state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc: return errors + ["state/contextual_assembly_journal.json unreadable: %s" % exc]
    if state.get("schema_version") != 1 or state.get("contextual_assembly_contract_version") != CONTEXTUAL_ASSEMBLY_SCHEMA_VERSION: errors.append("contextual journal state schema invalid")
    if state.get("entry_count") != len(records): errors.append("contextual journal state count mismatch")
    expected_sequence = None if not records else len(records) - 1; expected_hash = None if not records else records[-1].get("entry_hash")
    if state.get("last_sequence") != expected_sequence: errors.append("contextual journal last_sequence mismatch")
    if state.get("last_entry_hash") != expected_hash: errors.append("contextual journal last_entry_hash mismatch")
    return errors
