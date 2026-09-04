from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..operations import canonical_hash
from ..store import writer_lock
from .question_bound import QuestionBoundPrediction, QuestionPredictionError
from .question_expert import (
    QuestionExpertPrediction,
    QuestionExpertPredictionError,
)
from .question_journal import (
    QuestionPredictionJournal,
    validate_question_prediction_journal,
)


class QuestionExpertPredictionJournalError(RuntimeError):
    pass


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s." % path.name,
        suffix=".tmp",
        dir=str(path.parent),
    )
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


def _entry_body(
    sequence: int,
    expert_prediction: QuestionExpertPrediction,
    base_prediction_journal_entry_hash: str,
    journaled_at_ns: int,
    previous_hash: str,
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "sequence": int(sequence),
        "expert_prediction": expert_prediction.to_wire(),
        "base_prediction_journal_entry_hash": str(
            base_prediction_journal_entry_hash
        ),
        "journaled_at_ns": int(journaled_at_ns),
        "previous_hash": previous_hash,
    }


def _entry_wire(
    sequence: int,
    expert_prediction: QuestionExpertPrediction,
    base_prediction_journal_entry_hash: str,
    journaled_at_ns: int,
    previous_hash: str,
) -> Dict[str, Any]:
    body = _entry_body(
        sequence,
        expert_prediction,
        base_prediction_journal_entry_hash,
        journaled_at_ns,
        previous_hash,
    )
    value = dict(body)
    value["entry_hash"] = canonical_hash(body)
    return value


def _parse_entry(
    value: Mapping[str, Any],
) -> Tuple[QuestionExpertPrediction, str]:
    body = {key: item for key, item in value.items() if key != "entry_hash"}
    expected = canonical_hash(body)
    if value.get("entry_hash") != expected:
        raise QuestionExpertPredictionJournalError(
            "question expert prediction journal entry hash mismatch"
        )
    raw = value.get("expert_prediction")
    if not isinstance(raw, Mapping):
        raise QuestionExpertPredictionJournalError(
            "question expert prediction journal entry is malformed"
        )
    try:
        expert_prediction = QuestionExpertPrediction.from_wire(raw)
    except (QuestionExpertPredictionError, ValueError, TypeError) as exc:
        raise QuestionExpertPredictionJournalError(
            "journal contains invalid question expert prediction: %s" % exc
        ) from exc
    return expert_prediction, expected


def _base_prediction_lineage_error(
    root: Path,
    expert_prediction: QuestionExpertPrediction,
    base_prediction_journal_entry_hash: str,
) -> Optional[str]:
    errors = validate_question_prediction_journal(root)
    if errors:
        return "question prediction journal invalid: " + "; ".join(errors)

    matches = []
    for entry in QuestionPredictionJournal(root).entries():
        raw = entry.get("prediction")
        if not isinstance(raw, Mapping):
            continue
        if raw.get("prediction_id") == expert_prediction.prediction.prediction_id:
            matches.append(entry)
    if len(matches) != 1:
        return "expert prediction must cite exactly one base question prediction"

    entry = matches[0]
    if entry.get("entry_hash") != base_prediction_journal_entry_hash:
        return "base prediction journal entry hash mismatch"
    try:
        base_prediction = QuestionBoundPrediction.from_wire(entry.get("prediction", {}))
    except (QuestionPredictionError, ValueError, TypeError) as exc:
        return "cited base question prediction is invalid: %s" % exc
    if base_prediction.to_wire() != expert_prediction.prediction.to_wire():
        return "expert prediction does not exactly wrap cited base prediction"
    return None


class QuestionExpertPredictionJournal:
    """Append-only proof that a prospective claim came from an eligible expert."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.path = self.root / "memory/question_expert_predictions.jsonl"
        self.state_path = (
            self.root / "state/question_expert_prediction_journal.json"
        )

    def entries(self) -> Tuple[Mapping[str, Any], ...]:
        if not self.path.is_file():
            return ()
        records: List[Mapping[str, Any]] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise QuestionExpertPredictionJournalError(
                    "question expert prediction journal line %d is invalid JSON"
                    % line_number
                ) from exc
            if not isinstance(value, dict):
                raise QuestionExpertPredictionJournalError(
                    "question expert prediction journal line %d must be an object"
                    % line_number
                )
            records.append(value)
        return tuple(records)

    def append(
        self,
        expert_prediction: QuestionExpertPrediction,
        *,
        base_prediction_journal_entry_hash: str,
        journaled_at_ns: int,
    ) -> Mapping[str, Any]:
        prediction = expert_prediction.prediction
        journaled = int(journaled_at_ns)
        if journaled < prediction.created_at_ns:
            raise QuestionExpertPredictionJournalError(
                "expert prediction cannot be journaled before created_at_ns"
            )
        if journaled >= prediction.resolves_at_ns:
            raise QuestionExpertPredictionJournalError(
                "prospective expert prediction must be journaled before resolution horizon"
            )
        lineage_error = _base_prediction_lineage_error(
            self.root,
            expert_prediction,
            str(base_prediction_journal_entry_hash),
        )
        if lineage_error:
            raise QuestionExpertPredictionJournalError(
                "expert prediction base lineage invalid: " + lineage_error
            )

        with writer_lock(self.root):
            records = self.entries()
            errors = _validate_records(records)
            if errors:
                raise QuestionExpertPredictionJournalError(
                    "existing question expert prediction journal is invalid: "
                    + "; ".join(errors)
                )
            for record in records:
                existing, _ = _parse_entry(record)
                if (
                    existing.prediction.prediction_id
                    != prediction.prediction_id
                ):
                    continue
                if existing.to_wire() != expert_prediction.to_wire():
                    raise QuestionExpertPredictionJournalError(
                        "prediction_id already has different expert lineage"
                    )
                if (
                    record.get("base_prediction_journal_entry_hash")
                    != base_prediction_journal_entry_hash
                ):
                    raise QuestionExpertPredictionJournalError(
                        "prediction_id already cites different base journal lineage"
                    )
                return record

            previous_hash = (
                str(records[-1]["entry_hash"]) if records else "GENESIS"
            )
            entry = _entry_wire(
                len(records),
                expert_prediction,
                str(base_prediction_journal_entry_hash),
                journaled,
                previous_hash,
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(entry, sort_keys=True, separators=(",", ":"))
                    + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            self._write_state(tuple(records) + (entry,))
            return entry

    def _write_state(
        self, records: Tuple[Mapping[str, Any], ...]
    ) -> None:
        _atomic_json(
            self.state_path,
            {
                "schema_version": 1,
                "question_expert_prediction_contract_version": "1.0",
                "authority": (
                    "append-only prospective expert eligibility lineage; "
                    "outcomes and evaluation remain separate"
                ),
                "entry_count": len(records),
                "last_sequence": (
                    None if not records else int(records[-1]["sequence"])
                ),
                "last_entry_hash": (
                    None if not records else str(records[-1]["entry_hash"])
                ),
            },
        )

    def rebuild_state(self) -> Mapping[str, Any]:
        records = self.entries()
        errors = _validate_records(records)
        if errors:
            raise QuestionExpertPredictionJournalError(
                "question expert prediction journal cannot rebuild state: "
                + "; ".join(errors)
            )
        for index, record in enumerate(records):
            expert_prediction, _ = _parse_entry(record)
            lineage_error = _base_prediction_lineage_error(
                self.root,
                expert_prediction,
                str(record.get("base_prediction_journal_entry_hash", "")),
            )
            if lineage_error:
                raise QuestionExpertPredictionJournalError(
                    "sequence %d base lineage invalid: %s"
                    % (index, lineage_error)
                )
        self._write_state(records)
        return json.loads(self.state_path.read_text(encoding="utf-8"))


def _validate_records(
    records: Tuple[Mapping[str, Any], ...]
) -> List[str]:
    errors: List[str] = []
    previous = "GENESIS"
    seen = set()
    for index, value in enumerate(records):
        try:
            expert_prediction, entry_hash = _parse_entry(value)
        except QuestionExpertPredictionJournalError as exc:
            errors.append("sequence %d: %s" % (index, exc))
            continue
        if value.get("schema_version") != 1:
            errors.append("sequence %d: unsupported journal schema" % index)
        if value.get("sequence") != index:
            errors.append("sequence %d: sequence mismatch" % index)
        if value.get("previous_hash") != previous:
            errors.append("sequence %d: previous_hash mismatch" % index)
        journaled = value.get("journaled_at_ns")
        prediction = expert_prediction.prediction
        if not isinstance(journaled, int) or journaled < prediction.created_at_ns:
            errors.append("sequence %d: journal timing invalid" % index)
        elif journaled >= prediction.resolves_at_ns:
            errors.append(
                "sequence %d: expert prediction was journaled after resolution"
                % index
            )
        prediction_id = prediction.prediction_id
        if prediction_id in seen:
            errors.append(
                "sequence %d: duplicate expert prediction_id" % index
            )
        seen.add(prediction_id)
        previous = entry_hash
    return errors


def validate_question_expert_prediction_journal(root: Path) -> List[str]:
    journal = QuestionExpertPredictionJournal(root)
    try:
        records = journal.entries()
    except QuestionExpertPredictionJournalError as exc:
        return [str(exc)]
    errors = _validate_records(records)
    if not errors:
        for index, record in enumerate(records):
            try:
                expert_prediction, _ = _parse_entry(record)
            except QuestionExpertPredictionJournalError:
                continue
            lineage_error = _base_prediction_lineage_error(
                journal.root,
                expert_prediction,
                str(record.get("base_prediction_journal_entry_hash", "")),
            )
            if lineage_error:
                errors.append(
                    "sequence %d: expert prediction base lineage invalid: %s"
                    % (index, lineage_error)
                )
    if not journal.state_path.is_file():
        return errors
    try:
        state = json.loads(journal.state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(
            "question expert prediction journal state unreadable: %s" % exc
        )
        return errors
    if (
        state.get("schema_version") != 1
        or state.get("question_expert_prediction_contract_version") != "1.0"
    ):
        errors.append("question expert prediction journal state schema invalid")
    if state.get("entry_count") != len(records):
        errors.append("question expert prediction journal state count mismatch")
    expected_sequence = None if not records else len(records) - 1
    expected_hash = None if not records else records[-1].get("entry_hash")
    if state.get("last_sequence") != expected_sequence:
        errors.append(
            "question expert prediction journal state last_sequence mismatch"
        )
    if state.get("last_entry_hash") != expected_hash:
        errors.append(
            "question expert prediction journal state last_entry_hash mismatch"
        )
    return errors
