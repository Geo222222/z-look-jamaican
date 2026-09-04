from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..operations import canonical_hash
from ..prediction.question_expert import (
    QuestionExpertPrediction,
    QuestionExpertPredictionError,
)
from ..prediction.question_expert_journal import (
    QuestionExpertPredictionJournal,
    validate_question_expert_prediction_journal,
)
from ..store import writer_lock
from .question_evaluation import (
    QuestionBoundEvaluation,
    QuestionEvaluationError,
    build_question_evaluation,
)
from .question_journal import (
    QuestionOutcomeJournal,
    validate_question_outcome_journal,
)
from .question_outcome import QuestionBoundOutcome, QuestionOutcomeError


class QuestionEvaluationJournalError(RuntimeError):
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
    evaluation: QuestionBoundEvaluation,
    journaled_at_ns: int,
    previous_hash: str,
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "sequence": int(sequence),
        "evaluation": evaluation.to_wire(),
        "journaled_at_ns": int(journaled_at_ns),
        "previous_hash": previous_hash,
    }


def _entry_wire(
    sequence: int,
    evaluation: QuestionBoundEvaluation,
    journaled_at_ns: int,
    previous_hash: str,
) -> Dict[str, Any]:
    body = _entry_body(sequence, evaluation, journaled_at_ns, previous_hash)
    value = dict(body)
    value["entry_hash"] = canonical_hash(body)
    return value


def _parse_entry(
    value: Mapping[str, Any],
) -> Tuple[QuestionBoundEvaluation, str]:
    body = {key: item for key, item in value.items() if key != "entry_hash"}
    expected = canonical_hash(body)
    if value.get("entry_hash") != expected:
        raise QuestionEvaluationJournalError(
            "question evaluation journal entry hash mismatch"
        )
    raw = value.get("evaluation")
    if not isinstance(raw, Mapping):
        raise QuestionEvaluationJournalError(
            "question evaluation journal entry is malformed"
        )
    try:
        evaluation = QuestionBoundEvaluation.from_wire(raw)
    except (QuestionEvaluationError, ValueError, TypeError) as exc:
        raise QuestionEvaluationJournalError(
            "journal contains invalid question evaluation: %s" % exc
        ) from exc
    return evaluation, expected


def _source_lineage_error(
    root: Path,
    evaluation: QuestionBoundEvaluation,
) -> Optional[str]:
    expert_errors = validate_question_expert_prediction_journal(root)
    if expert_errors:
        return (
            "question expert prediction journal invalid: "
            + "; ".join(expert_errors)
        )
    outcome_errors = validate_question_outcome_journal(root)
    if outcome_errors:
        return "question outcome journal invalid: " + "; ".join(outcome_errors)

    expert_matches = []
    for entry in QuestionExpertPredictionJournal(root).entries():
        raw = entry.get("expert_prediction")
        if not isinstance(raw, Mapping):
            continue
        prediction = raw.get("prediction")
        if (
            isinstance(prediction, Mapping)
            and prediction.get("prediction_id") == evaluation.prediction_id
        ):
            expert_matches.append(entry)
    if len(expert_matches) != 1:
        return "evaluation must cite exactly one journaled expert prediction"
    expert_entry = expert_matches[0]
    if (
        expert_entry.get("entry_hash")
        != evaluation.expert_prediction_journal_entry_hash
    ):
        return "expert prediction journal entry hash mismatch"
    try:
        expert_prediction = QuestionExpertPrediction.from_wire(
            expert_entry.get("expert_prediction", {})
        )
    except (QuestionExpertPredictionError, ValueError, TypeError) as exc:
        return "cited expert prediction is invalid: %s" % exc

    outcome_matches = []
    for entry in QuestionOutcomeJournal(root).entries():
        raw = entry.get("outcome")
        if not isinstance(raw, Mapping):
            continue
        if raw.get("outcome_id") == evaluation.outcome_id:
            outcome_matches.append(entry)
    if len(outcome_matches) != 1:
        return "evaluation must cite exactly one journaled question outcome"
    outcome_entry = outcome_matches[0]
    if outcome_entry.get("entry_hash") != evaluation.outcome_journal_entry_hash:
        return "outcome journal entry hash mismatch"
    try:
        outcome = QuestionBoundOutcome.from_wire(
            outcome_entry.get("outcome", {})
        )
    except (QuestionOutcomeError, ValueError, TypeError) as exc:
        return "cited question outcome is invalid: %s" % exc

    try:
        expected = build_question_evaluation(
            expert_prediction=expert_prediction,
            expert_prediction_journal_entry_hash=str(
                expert_entry.get("entry_hash", "")
            ),
            outcome=outcome,
            outcome_journal_entry_hash=str(
                outcome_entry.get("entry_hash", "")
            ),
            evaluated_at_ns=evaluation.evaluated_at_ns,
        )
    except QuestionEvaluationError as exc:
        return "evaluation sources cannot reproduce score: %s" % exc
    if expected.to_wire() != evaluation.to_wire():
        return "evaluation differs from mechanically reproduced score"
    return None


class QuestionEvaluationJournal:
    """Append-only evaluation history; competence remains a later derived layer."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.path = self.root / "memory/question_evaluations.jsonl"
        self.state_path = self.root / "state/question_evaluation_journal.json"

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
                raise QuestionEvaluationJournalError(
                    "question evaluation journal line %d is invalid JSON"
                    % line_number
                ) from exc
            if not isinstance(value, dict):
                raise QuestionEvaluationJournalError(
                    "question evaluation journal line %d must be an object"
                    % line_number
                )
            records.append(value)
        return tuple(records)

    def append(
        self,
        evaluation: QuestionBoundEvaluation,
        *,
        journaled_at_ns: int,
    ) -> Mapping[str, Any]:
        journaled = int(journaled_at_ns)
        if journaled < evaluation.evaluated_at_ns:
            raise QuestionEvaluationJournalError(
                "evaluation cannot be journaled before evaluated_at_ns"
            )
        lineage_error = _source_lineage_error(self.root, evaluation)
        if lineage_error:
            raise QuestionEvaluationJournalError(
                "evaluation lineage invalid: " + lineage_error
            )

        with writer_lock(self.root):
            records = self.entries()
            errors = _validate_records(records)
            if errors:
                raise QuestionEvaluationJournalError(
                    "existing question evaluation journal is invalid: "
                    + "; ".join(errors)
                )
            for record in records:
                existing, _ = _parse_entry(record)
                if existing.prediction_id != evaluation.prediction_id:
                    continue
                if existing.to_wire() != evaluation.to_wire():
                    raise QuestionEvaluationJournalError(
                        "prediction already has a different evaluation"
                    )
                return record

            previous_hash = (
                str(records[-1]["entry_hash"]) if records else "GENESIS"
            )
            entry = _entry_wire(
                len(records),
                evaluation,
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
                "question_evaluation_contract_version": "1.0",
                "authority": (
                    "append-only model scoring derived from immutable "
                    "predictions and market outcomes; no competence or capital authority"
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
            raise QuestionEvaluationJournalError(
                "question evaluation journal cannot rebuild state: "
                + "; ".join(errors)
            )
        for index, record in enumerate(records):
            evaluation, _ = _parse_entry(record)
            lineage_error = _source_lineage_error(self.root, evaluation)
            if lineage_error:
                raise QuestionEvaluationJournalError(
                    "sequence %d evaluation lineage invalid: %s"
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
            evaluation, entry_hash = _parse_entry(value)
        except QuestionEvaluationJournalError as exc:
            errors.append("sequence %d: %s" % (index, exc))
            continue
        if value.get("schema_version") != 1:
            errors.append("sequence %d: unsupported journal schema" % index)
        if value.get("sequence") != index:
            errors.append("sequence %d: sequence mismatch" % index)
        if value.get("previous_hash") != previous:
            errors.append("sequence %d: previous_hash mismatch" % index)
        journaled = value.get("journaled_at_ns")
        if (
            not isinstance(journaled, int)
            or journaled < evaluation.evaluated_at_ns
        ):
            errors.append("sequence %d: journal timing invalid" % index)
        if evaluation.prediction_id in seen:
            errors.append(
                "sequence %d: duplicate prediction evaluation" % index
            )
        seen.add(evaluation.prediction_id)
        previous = entry_hash
    return errors


def validate_question_evaluation_journal(root: Path) -> List[str]:
    journal = QuestionEvaluationJournal(root)
    try:
        records = journal.entries()
    except QuestionEvaluationJournalError as exc:
        return [str(exc)]
    errors = _validate_records(records)
    if not errors:
        for index, record in enumerate(records):
            try:
                evaluation, _ = _parse_entry(record)
            except QuestionEvaluationJournalError:
                continue
            lineage_error = _source_lineage_error(
                journal.root, evaluation
            )
            if lineage_error:
                errors.append(
                    "sequence %d: evaluation lineage invalid: %s"
                    % (index, lineage_error)
                )
    if not journal.state_path.is_file():
        return errors
    try:
        state = json.loads(journal.state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(
            "question evaluation journal state unreadable: %s" % exc
        )
        return errors
    if (
        state.get("schema_version") != 1
        or state.get("question_evaluation_contract_version") != "1.0"
    ):
        errors.append("question evaluation journal state schema invalid")
    if state.get("entry_count") != len(records):
        errors.append("question evaluation journal state count mismatch")
    expected_sequence = None if not records else len(records) - 1
    expected_hash = None if not records else records[-1].get("entry_hash")
    if state.get("last_sequence") != expected_sequence:
        errors.append(
            "question evaluation journal state last_sequence mismatch"
        )
    if state.get("last_entry_hash") != expected_hash:
        errors.append(
            "question evaluation journal state last_entry_hash mismatch"
        )
    return errors
