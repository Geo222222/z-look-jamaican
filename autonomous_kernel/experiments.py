"""Validation for the canonical first-class experiment registry."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping


EXPERIMENT_STATUSES = {"PREREGISTERED", "RUNNING", "RESOLVED", "FAILED", "SUSPENDED", "REJECTED"}


def validate_experiment_registry(document: Mapping[str, Any], root: Path) -> list[str]:
    errors: list[str] = []
    if document.get("schema_version") != 1:
        errors.append("state/experiments.json: schema_version must be 1")
    items = document.get("items")
    if not isinstance(items, list):
        return errors + ["state/experiments.json: items must be a list"]
    ids: set[str] = set()
    for item in items:
        experiment_id = str(item.get("id", ""))
        if not experiment_id or experiment_id in ids:
            errors.append("state/experiments.json: IDs must be present and unique")
        ids.add(experiment_id)
        if item.get("status") not in EXPERIMENT_STATUSES:
            errors.append(f"state/experiments.json: {experiment_id} has invalid status")
        for field in ("hypothesis", "preregistration_path", "preregistration_sha256", "evidence_gate", "failure_gate", "lineage"):
            if not item.get(field):
                errors.append(f"state/experiments.json: {experiment_id} missing {field}")
        relative = item.get("preregistration_path")
        if relative:
            candidate = (root / str(relative)).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                errors.append(f"state/experiments.json: {experiment_id} preregistration escapes repository")
                continue
            if not candidate.is_file():
                errors.append(f"state/experiments.json: {experiment_id} preregistration missing")
            elif hashlib.sha256(candidate.read_bytes()).hexdigest() != item.get("preregistration_sha256"):
                errors.append(f"state/experiments.json: {experiment_id} preregistration hash mismatch")
        if item.get("status") == "RUNNING" and not item.get("resume_command"):
            errors.append(f"state/experiments.json: running {experiment_id} needs resume_command")
    return errors
