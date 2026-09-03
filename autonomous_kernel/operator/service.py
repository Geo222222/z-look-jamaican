"""Canonical operator command execution.

Only commands declared AVAILABLE in the stable operator contract may run.
The console cannot create authority that the kernel does not already possess.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Mapping

from ..assembly.context_profiles import validate_context_profile_registry
from ..assembly.contextual_journal import validate_contextual_assembly_journal
from ..assembly.contextual_lineage import validate_contextual_assembly_lineage
from ..assembly.journal import validate_assembly_journal
from ..assembly.lineage import validate_assembly_lineage
from ..context.service import materialize_market_context
from ..context.store import validate_market_context_store
from ..evaluation.journal import validate_outcome_journal
from ..models.registry import validate_model_registry
from ..store import StateValidationError, recover_pending, validate
from .contracts import command_spec
from .journal import append_operator_receipt, receipt_for_request_id, validate_operator_journal
from .snapshot import build_operator_snapshot


class OperatorCommandError(RuntimeError):
    pass


def _mutations_enabled() -> bool:
    return os.getenv("ZLOOK_OPERATOR_MUTATIONS_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _full_validation(root: Path) -> Dict[str, Any]:
    checks = list(validate(root))
    validators = (
        ("model_registry", validate_model_registry),
        ("outcome_journal", validate_outcome_journal),
        ("assembly_journal", validate_assembly_journal),
        ("assembly_lineage", validate_assembly_lineage),
        ("market_context_store", validate_market_context_store),
        ("context_profile_registry", validate_context_profile_registry),
        ("contextual_assembly_journal", validate_contextual_assembly_journal),
        ("contextual_assembly_lineage", validate_contextual_assembly_lineage),
        ("operator_journal", validate_operator_journal),
    )
    for name, validator in validators:
        errors = validator(root)
        if errors:
            raise StateValidationError(errors)
        checks.append(name)
    return {"checks": checks}


def execute_operator_command(root: Path, request: Mapping[str, Any]) -> Dict[str, Any]:
    root = root.resolve()
    command_id = str(request.get("command_id") or "")
    if not command_id:
        raise OperatorCommandError("command_id is required")
    try:
        spec = command_spec(command_id)
    except KeyError as exc:
        raise OperatorCommandError(str(exc)) from exc
    if spec.state == "LOCKED":
        raise OperatorCommandError("%s is constitutionally locked and cannot be executed by ZLJ" % command_id)
    if spec.state != "AVAILABLE":
        raise OperatorCommandError("%s is not implemented by the authoritative operator contract" % command_id)
    if spec.confirmation_required and request.get("confirm") is not True:
        raise OperatorCommandError("%s requires explicit confirm=true" % command_id)
    if spec.control_class == "MUTATING" and not _mutations_enabled():
        raise OperatorCommandError("operator mutations are disabled; set ZLOOK_OPERATOR_MUTATIONS_ENABLED=true outside the UI")

    parameters = request.get("parameters")
    parameters = parameters if isinstance(parameters, Mapping) else {}
    request_id = str(request.get("request_id") or "")
    if spec.control_class == "MUTATING":
        if not request_id:
            raise OperatorCommandError("mutating operator commands require request_id")
        existing = receipt_for_request_id(root, request_id)
        if existing is not None:
            return {"status": "ok", "receipt": existing["receipt"], "journal_entry_hash": existing["entry_hash"], "durability": "REPLAYED_OPERATOR_RECEIPT"}

    started = time.time_ns()
    result: Any
    if command_id == "VALIDATE_KERNEL":
        result = _full_validation(root)
    elif command_id == "RECOVER_PENDING":
        result = recover_pending(root)
    elif command_id == "MATERIALIZE_CONTEXT":
        if "cutoff_at_ns" not in parameters:
            raise OperatorCommandError("MATERIALIZE_CONTEXT requires cutoff_at_ns")
        cutoff = int(parameters["cutoff_at_ns"])
        materialized = materialize_market_context(root, cutoff_at_ns=cutoff)
        result = {
            "context": materialized.context.to_wire(),
            "selected_frame_count": len(materialized.selected_frame_ids),
            "selected_instrument_ids": list(materialized.selected_instrument_ids),
        }
    else:
        raise OperatorCommandError("operator command has no executable implementation")

    completed = time.time_ns()
    receipt = {
        "receipt_version": "1.0",
        "request_id": request_id,
        "command_id": command_id,
        "control_class": spec.control_class,
        "started_at_ns": started,
        "completed_at_ns": completed,
        "parameters": dict(parameters),
        "result": result,
        "capital_effect": "NONE",
        "execution_effect": "NONE",
    }
    if spec.control_class == "MUTATING":
        journal_entry = append_operator_receipt(root, receipt)
        return {"status": "ok", "receipt": receipt, "journal_entry_hash": journal_entry["entry_hash"], "durability": "APPEND_ONLY_OPERATOR_JOURNAL"}
    return {"status": "ok", "receipt": receipt, "journal_entry_hash": None, "durability": "READ_ONLY_QUERY_NOT_JOURNALED"}


def operator_catalog() -> Dict[str, Any]:
    from .contracts import command_catalog
    value = command_catalog()
    value["mutations_enabled"] = _mutations_enabled()
    return value


def operator_snapshot(root: Path) -> Dict[str, Any]:
    return build_operator_snapshot(root)
