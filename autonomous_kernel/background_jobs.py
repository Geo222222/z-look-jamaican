"""Deterministic, bounded background-job registry and launcher.

Status inspection is read-only. Launching is explicit and never happens from the
monitor. Jobs execute only allowlisted Python modules with no shell expansion.
"""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


ALLOWED_MODULES = {"experiments.microstream_qualification", "experiments.crypto_market_shadow"}
TERMINAL = {"SUCCEEDED", "FAILED", "BLOCKED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_background_jobs(document: Mapping[str, Any], root: Path | None = None) -> list[str]:
    errors: list[str] = []
    if document.get("schema_version") != 1:
        errors.append("state/background_jobs.json: schema_version must be 1")
    seen_jobs: set[str] = set()
    seen_runs: set[str] = set()
    for job in document.get("items", []):
        job_id = str(job.get("id", ""))
        if not job_id or job_id in seen_jobs:
            errors.append("state/background_jobs.json: job IDs must be present and unique")
        seen_jobs.add(job_id)
        if job.get("module") not in ALLOWED_MODULES:
            errors.append(f"state/background_jobs.json: {job_id} module is not allowlisted")
        if job.get("shell") is not False or job.get("capital_effect") != "NONE" or job.get("credentials_allowed") is not False:
            errors.append(f"state/background_jobs.json: {job_id} violates zero-effect job policy")
        if root is not None:
            relative = str(job.get("preregistration_path", ""))
            target = (root / relative).resolve()
            try:
                target.relative_to(root.resolve())
            except ValueError:
                errors.append(f"state/background_jobs.json: {job_id} preregistration escapes repository")
            else:
                if not target.is_file():
                    errors.append(f"state/background_jobs.json: {job_id} preregistration is missing")
                elif hashlib.sha256(target.read_bytes()).hexdigest() != job.get("preregistration_sha256"):
                    errors.append(f"state/background_jobs.json: {job_id} preregistration hash mismatch")
        for run in job.get("runs", []):
            run_id = str(run.get("id", ""))
            if not run_id or run_id in seen_runs:
                errors.append("state/background_jobs.json: run IDs must be present and globally unique")
            seen_runs.add(run_id)
            try:
                _epoch(str(run["not_before"]))
            except (KeyError, ValueError):
                errors.append(f"state/background_jobs.json: {run_id} has invalid not_before")
            if not isinstance(run.get("args"), list) or any(not isinstance(arg, str) for arg in run.get("args", [])):
                errors.append(f"state/background_jobs.json: {run_id} args must be strings")
    return errors


def status(root: Path, now: str | None = None) -> Mapping[str, Any]:
    observed_at = now or _now()
    registry = _load(root / "state/background_jobs.json")
    items = []
    for job in registry.get("items", []):
        runs = []
        for run in job.get("runs", []):
            receipt_path = root / "runtime/background_jobs" / f"{run['id']}.json"
            receipt = _load(receipt_path) if receipt_path.is_file() else None
            state = receipt.get("status") if receipt else ("READY" if _epoch(run["not_before"]) <= _epoch(observed_at) else "SCHEDULED")
            runs.append({**run, "state": state, "receipt": receipt})
        items.append({key: value for key, value in job.items() if key != "runs"} | {"runs": runs})
    return {"schema_version": 1, "observed_at": observed_at, "items": items}


def launch_due(root: Path, now: str | None = None, launcher: Callable[[list[str], Path], int] | None = None) -> Mapping[str, Any]:
    observed_at = now or _now()
    registry = _load(root / "state/background_jobs.json")
    launched, skipped = [], []

    def default_launcher(command: list[str], cwd: Path) -> int:
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, close_fds=os.name != "nt")
        return int(process.pid)

    spawn = launcher or default_launcher
    runtime = root / "runtime/background_jobs"
    runtime.mkdir(parents=True, exist_ok=True)
    for job in registry.get("items", []):
        if job.get("enabled") is not True:
            continue
        for run in job.get("runs", []):
            run_id = run["id"]
            if _epoch(run["not_before"]) > _epoch(observed_at):
                continue
            receipt_path = runtime / f"{run_id}.json"
            claim_path = runtime / f"{run_id}.claim"
            if receipt_path.exists() or claim_path.exists():
                skipped.append(run_id)
                continue
            try:
                descriptor = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                skipped.append(run_id)
                continue
            try:
                os.write(descriptor, json.dumps({"run_id": run_id, "claimed_at": observed_at}).encode("utf-8"))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            command = [sys.executable, "-m", "autonomous_kernel", "job_execute", "--job-id", job["id"], "--run-id", run_id]
            try:
                pid = spawn(command, root)
            except Exception:
                claim_path.unlink(missing_ok=True)
                raise
            launched.append({"job_id": job["id"], "run_id": run_id, "pid": pid})
    return {"status": "ok", "observed_at": observed_at, "launched": launched, "skipped": skipped}


def execute(root: Path, job_id: str, run_id: str) -> Mapping[str, Any]:
    registry = _load(root / "state/background_jobs.json")
    job = next((item for item in registry.get("items", []) if item.get("id") == job_id), None)
    if job is None or job.get("module") not in ALLOWED_MODULES:
        raise ValueError("unknown or non-allowlisted background job")
    run = next((item for item in job.get("runs", []) if item.get("id") == run_id), None)
    if run is None:
        raise ValueError("unknown background job run")
    runtime = root / "runtime/background_jobs"
    receipt_path = runtime / f"{run_id}.json"
    if receipt_path.exists():
        return _load(receipt_path)
    started_at = _now()
    command = [sys.executable, "-m", job["module"], "--root", str(root), *run.get("args", [])]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=int(job["timeout_seconds"]), check=False)
    receipt = {"schema_version": 1, "job_id": job_id, "run_id": run_id, "status": "SUCCEEDED" if completed.returncode == 0 else "FAILED", "started_at": started_at, "completed_at": _now(), "returncode": completed.returncode, "stdout_tail": completed.stdout[-4000:], "stderr_tail": completed.stderr[-4000:], "capital_effect": "NONE", "credentials_used": False}
    _atomic(receipt_path, receipt)
    (runtime / f"{run_id}.claim").unlink(missing_ok=True)
    return receipt
