"""Validated, atomic access to the repository's durable operating state.

This module belongs to the control plane. It does not hold credentials, sign
transactions, move capital, or grant authority. Production execution and
Governor enforcement require separate deterministic components when justified.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ROOT_STATES = {
    "BOOTSTRAP",
    "DISCOVERY",
    "RESEARCH",
    "EXPERIMENT",
    "BUILD",
    "VALIDATE",
    "DEPLOY",
    "OBSERVE",
    "REFLECT",
    "INCIDENT",
    "SUSPENDED",
    "PARKED",
    "REJECTED",
}

TRANSITIONS = {
    "BOOTSTRAP": {"DISCOVERY", "INCIDENT", "SUSPENDED"},
    "DISCOVERY": {"RESEARCH", "INCIDENT", "SUSPENDED", "PARKED"},
    "RESEARCH": {"EXPERIMENT", "REJECTED", "PARKED", "INCIDENT", "SUSPENDED"},
    "EXPERIMENT": {"BUILD", "EXPERIMENT", "REJECTED", "PARKED", "INCIDENT", "SUSPENDED"},
    "BUILD": {"VALIDATE", "INCIDENT", "SUSPENDED", "REJECTED"},
    "VALIDATE": {"DEPLOY", "BUILD", "REJECTED", "SUSPENDED", "INCIDENT"},
    "DEPLOY": {"OBSERVE", "INCIDENT", "SUSPENDED"},
    "OBSERVE": {"REFLECT", "INCIDENT", "SUSPENDED"},
    "REFLECT": {"DISCOVERY", "RESEARCH", "EXPERIMENT", "BUILD", "OBSERVE", "SUSPENDED", "REJECTED"},
    "INCIDENT": {"BUILD", "VALIDATE", "OBSERVE", "SUSPENDED"},
    "SUSPENDED": {"DISCOVERY", "RESEARCH", "BUILD", "VALIDATE", "OBSERVE", "REJECTED"},
    "PARKED": {"DISCOVERY", "RESEARCH", "REJECTED"},
    "REJECTED": {"DISCOVERY", "RESEARCH"},
}

TASK_STATUSES = {"ready", "in_progress", "completed", "blocked", "rejected"}

# Owner-controlled version-1 registry anchor. The Root Agent may not update this
# constant or the registry. An owner registry change must deliberately update
# both, producing an obvious reviewed code/config diff rather than mutable state
# silently blessing a new destination.
OWNER_TREASURY_REGISTRY_SHA256_V1 = "9b0ea44bb2871dc149ca22d71c3f829f3dd18f6278437856208ffd275156ba1a"

REQUIRED_JSON_FILES = (
    "state/current_state.json",
    "state/objectives.json",
    "state/backlog.json",
    "state/agents.json",
    "state/deployments.json",
    "state/incidents.json",
    "state/resume.json",
    "state/operational_wallets.json",
    "opportunities/register.json",
    "accounting/ledger.json",
)

REQUIRED_JSONL_FILES = (
    "state/transitions.jsonl",
    "state/transactions.jsonl",
    "memory/decisions.jsonl",
    "memory/experiments.jsonl",
    "memory/rejections.jsonl",
    "memory/reflections.jsonl",
    "metrics/economic.jsonl",
    "metrics/system.jsonl",
    "evidence/sources.jsonl",
)

FORBIDDEN_SECRET_FIELDS = {
    "private_key",
    "seed_phrase",
    "mnemonic",
    "secret_value",
    "password",
    "api_key",
    "credential_value",
}


class StateValidationError(Exception):
    """Raised when durable state violates its schema or safety invariants."""

    def __init__(self, errors: Sequence[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: record must be an object")
            records.append(value)
    return records


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(value), separators=(",", ":"), sort_keys=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _append_jsonl_once(path: Path, value: Mapping[str, Any]) -> None:
    record_id = value.get("id")
    if not record_id:
        raise ValueError("journal records require an id for idempotent recovery")
    if path.exists() and any(record.get("id") == record_id for record in load_jsonl(path)):
        return
    _append_jsonl(path, value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk_forbidden_fields(value: Any, location: str, errors: List[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_SECRET_FIELDS:
                errors.append(f"{location}: forbidden secret-bearing field {key!r}")
            _walk_forbidden_fields(child, f"{location}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_forbidden_fields(child, f"{location}[{index}]", errors)


def _ids(items: Iterable[Mapping[str, Any]]) -> List[str]:
    return [str(item.get("id", "")) for item in items]


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def writer_lock(root: Path):
    """Acquire an exclusive repository writer lease and clear only proven-orphan locks."""

    lock_path = root / "state/.writer.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    host = socket.gethostname()
    payload = {
        "schema_version": 1,
        "token": token,
        "pid": os.getpid(),
        "host": host,
        "created_at": utc_now(),
    }
    encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")

    for _ in range(2):
        try:
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                existing_text = lock_path.read_text(encoding="utf-8")
                existing = json.loads(existing_text)
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"writer lock exists but cannot be verified: {exc}") from exc
            if existing.get("host") == host and not _process_alive(int(existing.get("pid", -1))):
                if lock_path.read_text(encoding="utf-8") != existing_text:
                    raise RuntimeError("writer lock changed during stale-lock verification")
                lock_path.unlink()
                continue
            raise RuntimeError(
                f"state writer lock is held by pid {existing.get('pid')} on {existing.get('host')}"
            )
        else:
            try:
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            break
    else:
        raise RuntimeError("could not acquire state writer lock")

    try:
        yield
    finally:
        try:
            existing = load_json(lock_path)
            if existing.get("token") != token:
                raise RuntimeError("writer lock ownership changed; refusing to remove it")
            lock_path.unlink()
        except FileNotFoundError:
            raise RuntimeError("writer lock disappeared before release")


def _safe_transaction_path(root: Path, relative: str, allowed: Sequence[str]) -> Path:
    if relative not in allowed:
        raise ValueError(f"transaction target is not allowed: {relative}")
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"transaction target escapes repository: {relative}") from exc
    return target


def _apply_transaction(root: Path, journal: Mapping[str, Any]) -> None:
    allowed_writes = (
        "state/current_state.json",
        "state/resume.json",
        "state/backlog.json",
    )
    allowed_appends = ("state/transitions.jsonl",)
    for write in journal.get("writes", []):
        target = _safe_transaction_path(root, write["path"], allowed_writes)
        errors: List[str] = []
        _walk_forbidden_fields(write["document"], write["path"], errors)
        if errors:
            raise StateValidationError(errors)
        _atomic_write_json(target, write["document"])
    for append in journal.get("appends", []):
        target = _safe_transaction_path(root, append["path"], allowed_appends)
        _append_jsonl_once(target, append["record"])


def _commit_transaction(
    root: Path,
    writes: Sequence[Tuple[str, Mapping[str, Any]]],
    appends: Sequence[Tuple[str, Mapping[str, Any]]] = (),
) -> Dict[str, Any]:
    transaction_id = f"TXN-{uuid.uuid4().hex}"
    journal = {
        "schema_version": 1,
        "id": transaction_id,
        "created_at": utc_now(),
        "status": "prepared",
        "writes": [{"path": path, "document": dict(document)} for path, document in writes],
        "appends": [{"path": path, "record": dict(record)} for path, record in appends],
    }
    pending_path = root / "state/pending_transaction.json"
    if pending_path.exists():
        raise RuntimeError("a pending state transaction requires recovery")
    _atomic_write_json(pending_path, journal)
    _apply_transaction(root, journal)
    completion = {
        "id": transaction_id,
        "created_at": journal["created_at"],
        "completed_at": utc_now(),
        "type": "state_transaction",
        "write_paths": [path for path, _ in writes],
        "append_paths": [path for path, _ in appends],
        "status": "completed",
    }
    _append_jsonl_once(root / "state/transactions.jsonl", completion)
    pending_path.unlink()
    return completion


def recover_pending(root: Optional[Path] = None) -> Dict[str, Any]:
    """Idempotently roll a prepared state transaction forward after interruption."""

    root = Path(root) if root is not None else repository_root()
    with writer_lock(root):
        pending_path = root / "state/pending_transaction.json"
        if not pending_path.exists():
            return {"status": "clean", "recovered_transaction_id": None}
        journal = load_json(pending_path)
        if journal.get("schema_version") != 1 or journal.get("status") != "prepared" or not journal.get("id"):
            raise ValueError("pending transaction journal is invalid; preserve it for incident review")
        _apply_transaction(root, journal)
        completion = {
            "id": journal["id"],
            "created_at": journal.get("created_at"),
            "completed_at": utc_now(),
            "type": "state_transaction",
            "write_paths": [item["path"] for item in journal.get("writes", [])],
            "append_paths": [item["path"] for item in journal.get("appends", [])],
            "status": "recovered",
        }
        _append_jsonl_once(root / "state/transactions.jsonl", completion)
        pending_path.unlink()
        validate(root)
        return {"status": "recovered", "recovered_transaction_id": journal["id"]}


def validate(root: Optional[Path] = None) -> List[str]:
    """Validate durable state and return the names of completed checks."""

    root = Path(root) if root is not None else repository_root()
    errors: List[str] = []
    checks: List[str] = []
    json_documents: Dict[str, Any] = {}
    jsonl_documents: Dict[str, List[Dict[str, Any]]] = {}

    if (root / "state/pending_transaction.json").exists():
        errors.append("pending state transaction detected; run `python -m autonomous_kernel recover`")
    checks.append("no_pending_transaction")

    for relative in REQUIRED_JSON_FILES:
        path = root / relative
        if not path.is_file():
            errors.append(f"missing required state file: {relative}")
            continue
        try:
            document = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{relative}: unreadable JSON: {exc}")
            continue
        json_documents[relative] = document
        _walk_forbidden_fields(document, relative, errors)
    checks.append("required_json_documents")

    for relative in REQUIRED_JSONL_FILES:
        path = root / relative
        if not path.is_file():
            errors.append(f"missing required journal: {relative}")
            continue
        try:
            records = load_jsonl(path)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
            continue
        jsonl_documents[relative] = records
        for index, record in enumerate(records, start=1):
            _walk_forbidden_fields(record, f"{relative}:{index}", errors)
    checks.append("jsonl_journals")

    current = json_documents.get("state/current_state.json", {})
    objectives = json_documents.get("state/objectives.json", {})
    backlog = json_documents.get("state/backlog.json", {})
    agents = json_documents.get("state/agents.json", {})
    resume = json_documents.get("state/resume.json", {})
    opportunities = json_documents.get("opportunities/register.json", {})
    wallets = json_documents.get("state/operational_wallets.json", {})
    ledger = json_documents.get("accounting/ledger.json", {})
    evidence_records = jsonl_documents.get("evidence/sources.jsonl", [])
    evidence_ids = {record.get("id") for record in evidence_records if record.get("id")}

    for record in evidence_records:
        expected_digest = record.get("sha256")
        record_path = record.get("path")
        if not expected_digest or not record_path:
            continue
        relative = str(record_path).split("#", 1)[0]
        target = (root / relative).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            errors.append(f"evidence/sources.jsonl: {record.get('id')} path escapes repository")
            continue
        if not target.is_file():
            errors.append(f"evidence/sources.jsonl: {record.get('id')} artifact is missing: {relative}")
        elif _sha256(target) != expected_digest:
            errors.append(f"evidence/sources.jsonl: {record.get('id')} artifact checksum mismatch")
    checks.append("evidence_artifact_integrity")

    if current.get("schema_version") != 1:
        errors.append("state/current_state.json: schema_version must be 1")
    if current.get("root_state") not in ROOT_STATES:
        errors.append("state/current_state.json: invalid root_state")
    governor = current.get("governor", {})
    expected_governor = {
        "external_owner_funding_authorized_usd": 0,
        "production_financial_trading": "disabled",
        "max_daily_loss_usd": 0,
        "max_single_trade_usd": 0,
        "max_concurrent_financial_exposure_usd": 0,
        "treasury_sweeps": "disabled",
    }
    for key, expected in expected_governor.items():
        if governor.get(key) != expected:
            errors.append(f"state/current_state.json: Governor snapshot {key} must equal {expected!r}")
    checks.append("governor_zero_exposure")

    treasury_path = root / "config/treasury_destinations.yaml"
    expected_hash = current.get("treasury_registry", {}).get("sha256_at_inspection")
    if not treasury_path.is_file():
        errors.append("missing immutable treasury destination registry")
    elif not expected_hash:
        errors.append("state/current_state.json: missing treasury registry hash")
    elif expected_hash != OWNER_TREASURY_REGISTRY_SHA256_V1:
        errors.append("state/current_state.json: treasury hash does not match the owner-controlled version-1 anchor")
    elif _sha256(treasury_path) != OWNER_TREASURY_REGISTRY_SHA256_V1:
        errors.append("treasury destination registry differs from inspected immutable snapshot")
    checks.append("treasury_registry_integrity")

    objective_items = objectives.get("items", [])
    objective_ids = _ids(objective_items)
    if len(objective_ids) != len(set(objective_ids)) or any(not item_id for item_id in objective_ids):
        errors.append("state/objectives.json: objective IDs must be present and unique")
    objective_id_set = set(objective_ids)
    for item in objective_items:
        parent = item.get("parent_objective_id")
        if parent is not None and parent not in objective_id_set:
            errors.append(f"state/objectives.json: {item.get('id')} has unknown parent {parent}")
    checks.append("objective_graph")

    backlog_items = backlog.get("items", [])
    task_ids = _ids(backlog_items)
    if len(task_ids) != len(set(task_ids)) or any(not task_id for task_id in task_ids):
        errors.append("state/backlog.json: task IDs must be present and unique")
    task_id_set = set(task_ids)
    for task in backlog_items:
        task_id = task.get("id")
        if task.get("status") not in TASK_STATUSES:
            errors.append(f"state/backlog.json: {task_id} has invalid status")
        if task.get("parent_objective_id") not in objective_id_set:
            errors.append(f"state/backlog.json: {task_id} has unknown parent objective")
        if not isinstance(task.get("priority_score"), (int, float)):
            errors.append(f"state/backlog.json: {task_id} needs numeric priority_score")
        for dependency in task.get("depends_on", []):
            if dependency not in task_id_set:
                errors.append(f"state/backlog.json: {task_id} has unknown dependency {dependency}")
    checks.append("backlog_references")

    agent_items = agents.get("items", [])
    agent_task_ids = [item.get("task_id") for item in agent_items]
    if len(agent_task_ids) != len(set(agent_task_ids)):
        errors.append("state/agents.json: specialist task IDs must be unique")
    for item in agent_items:
        if item.get("task_id") not in task_id_set:
            errors.append(f"state/agents.json: unknown task {item.get('task_id')}")
    checks.append("specialist_registry")

    next_task_id = current.get("next_task_id")
    if next_task_id is not None and next_task_id not in task_id_set:
        errors.append("state/current_state.json: next_task_id is not in backlog")
    if resume.get("next_task_id") != next_task_id:
        errors.append("state/resume.json: next_task_id does not match current state")
    active_task_ids = sorted(task["id"] for task in backlog_items if task.get("status") == "in_progress")
    if sorted(resume.get("active_task_ids", [])) != active_task_ids:
        errors.append("state/resume.json: active_task_ids do not match in-progress backlog tasks")
    checks.append("resume_checkpoint")

    opportunity_items = opportunities.get("items", [])
    opportunity_ids = _ids(opportunity_items)
    if len(opportunity_ids) != len(set(opportunity_ids)):
        errors.append("opportunities/register.json: opportunity IDs must be unique")
    ranked = sorted((item for item in opportunity_items if item.get("rank") is not None), key=lambda item: item["rank"])
    if [item.get("rank") for item in ranked] != list(range(1, len(ranked) + 1)):
        errors.append("opportunities/register.json: ranks must be contiguous from 1")
    for item in opportunity_items:
        if not isinstance(item.get("priority_score"), (int, float)):
            errors.append(f"opportunities/register.json: {item.get('id')} needs numeric priority_score")
        if not item.get("next_experiment_id"):
            errors.append(f"opportunities/register.json: {item.get('id')} needs next_experiment_id")
        for evidence_id in item.get("evidence_ids", []):
            if evidence_id not in evidence_ids:
                errors.append(f"opportunities/register.json: {item.get('id')} references unknown evidence {evidence_id}")
    checks.append("opportunity_ranking")
    checks.append("opportunity_evidence_references")

    if wallets.get("private_material_permitted_in_repository") is not False:
        errors.append("state/operational_wallets.json: private material must be prohibited")
    if not isinstance(wallets.get("items"), list):
        errors.append("state/operational_wallets.json: items must be a list")
    checks.append("wallet_secret_boundary")

    if ledger.get("currency") != "USD" or not isinstance(ledger.get("entries"), list):
        errors.append("accounting/ledger.json: expected an empty-or-populated USD entries ledger")
    if ledger.get("production_capital_authorized_usd") != 0:
        errors.append("accounting/ledger.json: production capital authorization must be zero")
    checks.append("economic_ledger_boundary")

    if errors:
        raise StateValidationError(errors)
    return checks


def next_work(root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    root = Path(root) if root is not None else repository_root()
    backlog = load_json(root / "state/backlog.json")
    items = backlog.get("items", [])
    return _next_from_items(items)


def _next_from_items(items: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    completed = {item["id"] for item in items if item.get("status") == "completed"}
    eligible = [
        item
        for item in items
        if item.get("status") == "ready" and set(item.get("depends_on", [])).issubset(completed)
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda item: (-float(item["priority_score"]), str(item["id"])))
    return dict(eligible[0])


def transition(
    new_state: str,
    trigger: str,
    decision_id: str,
    evidence: Sequence[str],
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Record an allowed root-state transition using atomic state files."""

    root = Path(root) if root is not None else repository_root()
    with writer_lock(root):
        validate(root)
        current_path = root / "state/current_state.json"
        resume_path = root / "state/resume.json"
        current = load_json(current_path)
        resume = load_json(resume_path)
        previous = current["root_state"]
        if new_state not in TRANSITIONS.get(previous, set()):
            raise ValueError(f"transition {previous} -> {new_state} is not allowed")
        if not trigger.strip() or not decision_id.strip() or not evidence:
            raise ValueError("trigger, decision_id, and at least one evidence reference are required")

        timestamp = utc_now()
        record = {
            "id": f"TRANSITION-{uuid.uuid4().hex}",
            "created_at": timestamp,
            "created_by": "root-agent",
            "type": "state_transition",
            "previous_state": previous,
            "new_state": new_state,
            "trigger": trigger,
            "evidence": list(evidence),
            "decision_id": decision_id,
            "rollback_or_demotion_condition": "Re-enter an earlier safe state if validation, evidence, or Governor assumptions fail.",
        }
        current["root_state"] = new_state
        current["updated_at"] = timestamp
        resume["root_state"] = new_state
        resume["updated_at"] = timestamp
        resume["last_transition_id"] = record["id"]
        resume["checkpoint"] = trigger
        _commit_transaction(
            root,
            (
                ("state/current_state.json", current),
                ("state/resume.json", resume),
            ),
            (("state/transitions.jsonl", record),),
        )
        validate(root)
        return record


def update_task(
    task_id: str,
    status: str,
    root: Optional[Path] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Update a backlog task and atomically refresh the durable resume pointer."""

    if status not in TASK_STATUSES:
        raise ValueError(f"invalid task status: {status}")
    root = Path(root) if root is not None else repository_root()
    with writer_lock(root):
        validate(root)
        backlog_path = root / "state/backlog.json"
        current_path = root / "state/current_state.json"
        resume_path = root / "state/resume.json"
        backlog = load_json(backlog_path)
        current = load_json(current_path)
        resume = load_json(resume_path)
        match = None
        for item in backlog.get("items", []):
            if item.get("id") == task_id:
                match = item
                break
        if match is None:
            raise KeyError(f"unknown task: {task_id}")
        match["status"] = status
        timestamp = utc_now()
        match["updated_at"] = timestamp
        backlog["updated_at"] = timestamp

        candidate = _next_from_items(backlog.get("items", []))
        next_task_id = candidate.get("id") if candidate else None
        current["next_task_id"] = next_task_id
        current["updated_at"] = timestamp
        resume["next_task_id"] = next_task_id
        resume["active_task_ids"] = sorted(
            item["id"] for item in backlog.get("items", []) if item.get("status") == "in_progress"
        )
        resume["updated_at"] = timestamp
        _commit_transaction(
            root,
            (
                ("state/backlog.json", backlog),
                ("state/current_state.json", current),
                ("state/resume.json", resume),
            ),
        )
        validate(root)
        return dict(match), candidate


def status_summary(root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(root) if root is not None else repository_root()
    checks = validate(root)
    current = load_json(root / "state/current_state.json")
    backlog = load_json(root / "state/backlog.json")
    opportunities = load_json(root / "opportunities/register.json")
    task_counts: Dict[str, int] = {status: 0 for status in sorted(TASK_STATUSES)}
    for item in backlog.get("items", []):
        task_counts[item["status"]] = task_counts.get(item["status"], 0) + 1
    return {
        "status": "ok",
        "system_id": current["system_id"],
        "root_state": current["root_state"],
        "strategy_stage": current["strategy_stage"],
        "next_task_id": current.get("next_task_id"),
        "active_task_ids": sorted(
            item["id"] for item in backlog.get("items", []) if item.get("status") == "in_progress"
        ),
        "active_program_ids": current.get("active_program_ids", []),
        "task_counts": task_counts,
        "opportunity_count": len(opportunities.get("items", [])),
        "validation_checks": checks,
        "updated_at": current["updated_at"],
    }
