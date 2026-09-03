"""Append-only receipts for operator-console command requests."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..operations import canonical_hash
from ..store import writer_lock

OPERATOR_JOURNAL_SCHEMA_VERSION = 1


class OperatorJournalError(RuntimeError):
    pass


def _body(sequence: int, receipt: Mapping[str, Any], previous_hash: str) -> Dict[str, Any]:
    return {
        "schema_version": OPERATOR_JOURNAL_SCHEMA_VERSION,
        "sequence": int(sequence),
        "receipt": dict(receipt),
        "previous_hash": str(previous_hash),
    }


def entries(root: Path) -> Sequence[Mapping[str, Any]]:
    path = root.resolve() / "memory/operator_commands.jsonl"
    if not path.is_file():
        return ()
    output: List[Mapping[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OperatorJournalError("operator receipt line %d invalid JSON" % line_number) from exc
        if not isinstance(value, dict):
            raise OperatorJournalError("operator receipt line %d must be an object" % line_number)
        output.append(value)
    return tuple(output)


def validate_operator_journal(root: Path) -> List[str]:
    try:
        records = entries(root)
    except OperatorJournalError as exc:
        return [str(exc)]
    errors: List[str] = []
    previous = "GENESIS"
    request_ids = set()
    for index, entry in enumerate(records):
        body = {key: value for key, value in entry.items() if key != "entry_hash"}
        if entry.get("schema_version") != OPERATOR_JOURNAL_SCHEMA_VERSION or entry.get("sequence") != index:
            errors.append("operator receipt sequence %d schema/sequence mismatch" % index)
        if entry.get("previous_hash") != previous:
            errors.append("operator receipt sequence %d previous_hash mismatch" % index)
        expected = canonical_hash(body)
        if entry.get("entry_hash") != expected:
            errors.append("operator receipt sequence %d entry_hash mismatch" % index)
        receipt = entry.get("receipt")
        if not isinstance(receipt, Mapping):
            errors.append("operator receipt sequence %d missing receipt object" % index)
        else:
            request_id = str(receipt.get("request_id") or "")
            if not request_id:
                errors.append("operator receipt sequence %d lacks request_id" % index)
            elif request_id in request_ids:
                errors.append("operator request_id duplicated: %s" % request_id)
            else:
                request_ids.add(request_id)
            if receipt.get("receipt_version") != "1.0":
                errors.append("operator receipt sequence %d has unsupported receipt_version" % index)
            if not str(receipt.get("command_id") or ""):
                errors.append("operator receipt sequence %d lacks command_id" % index)
            if receipt.get("control_class") != "MUTATING":
                errors.append("operator journal may persist only MUTATING command receipts")
            started = receipt.get("started_at_ns")
            completed = receipt.get("completed_at_ns")
            if not isinstance(started, int) or not isinstance(completed, int) or started < 0 or completed < started:
                errors.append("operator receipt sequence %d has invalid timing" % index)
            if receipt.get("capital_effect") != "NONE" or receipt.get("execution_effect") != "NONE":
                errors.append("operator receipt sequence %d violates ZLJ authority boundary" % index)
        previous = expected
    return errors


def receipt_for_request_id(root: Path, request_id: str) -> Optional[Mapping[str, Any]]:
    errors = validate_operator_journal(root)
    if errors:
        raise OperatorJournalError("operator journal invalid: " + "; ".join(errors))
    for entry in entries(root):
        receipt = entry.get("receipt")
        if isinstance(receipt, Mapping) and receipt.get("request_id") == request_id:
            return entry
    return None


def append_operator_receipt(root: Path, receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    root = root.resolve()
    request_id = str(receipt.get("request_id") or "")
    if not request_id:
        raise OperatorJournalError("mutating operator receipt requires request_id")
    with writer_lock(root):
        records = entries(root)
        errors = validate_operator_journal(root)
        if errors:
            raise OperatorJournalError("operator journal invalid: " + "; ".join(errors))
        for existing in records:
            existing_receipt = existing.get("receipt")
            if isinstance(existing_receipt, Mapping) and existing_receipt.get("request_id") == request_id:
                raise OperatorJournalError("operator request_id already exists: %s" % request_id)
        previous = str(records[-1]["entry_hash"]) if records else "GENESIS"
        body = _body(len(records), receipt, previous)
        entry = dict(body)
        entry["entry_hash"] = canonical_hash(body)
        path = root / "memory/operator_commands.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return entry
