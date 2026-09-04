from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from ..store import writer_lock
from .contracts import BoundedJobError, validate_job_spec


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


def persist_job_spec(root: Path, spec: Mapping[str, Any]) -> Mapping[str, Any]:
    root = root.resolve()
    validate_job_spec(spec, root=root)
    path = root / "artifacts/evidence/jobs" / (str(spec["job_id"]) + ".json")
    with writer_lock(root):
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != dict(spec):
                raise BoundedJobError("immutable bounded job identity conflict")
            return existing
        _atomic_json(path, spec)
    return dict(spec)


def load_job_specs(root: Path) -> Sequence[Mapping[str, Any]]:
    root = root.resolve()
    directory = root / "artifacts/evidence/jobs"
    if not directory.is_dir():
        return ()
    specs = []
    seen_runs = set()
    for path in sorted(directory.glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        validate_job_spec(spec, root=root)
        for run in spec["runs"]:
            run_id = str(run["run_id"])
            if run_id in seen_runs:
                raise BoundedJobError("bounded job run ids must be globally unique")
            seen_runs.add(run_id)
        specs.append(spec)
    return tuple(specs)


def _command(spec: Mapping[str, Any]) -> Sequence[str]:
    action = spec["action"]
    args = spec["action_args"]
    if action == "VALIDATE_KERNEL":
        return (sys.executable, "-m", "autonomous_kernel", "validate")
    if action == "CONTEXT_MATERIALIZE":
        return (sys.executable, "-m", "autonomous_kernel", "context_materialize", "--cutoff-at-ns", str(args["cutoff_at_ns"]))
    if action == "EXPERT_SYNC":
        return (sys.executable, "-m", "autonomous_kernel", "expert_sync", "--known-at-ns", str(args["known_at_ns"]))
    raise BoundedJobError("bounded job action has no runtime mapping")


def job_status(root: Path, *, known_at_ns: int) -> Mapping[str, Any]:
    if int(known_at_ns) < 0:
        raise BoundedJobError("known_at_ns must be non-negative")
    root = root.resolve()
    items = []
    for spec in load_job_specs(root):
        runs = []
        for run in spec["runs"]:
            receipt_path = root / "runtime/background_jobs" / (str(run["run_id"]) + ".json")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else None
            state = str(receipt.get("status")) if receipt else ("READY" if int(run["not_before_ns"]) <= int(known_at_ns) else "SCHEDULED")
            runs.append({**dict(run), "state": state, "receipt": receipt})
        items.append({"job_id": spec["job_id"], "action": spec["action"], "runs": runs})
    return {"schema_version": 1, "known_at_ns": int(known_at_ns), "items": items, "authority": "read-only bounded research-job status"}


def execute_job_run(root: Path, *, job_id: str, run_id: str, known_at_ns: int, executor=None) -> Mapping[str, Any]:
    root = root.resolve()
    specs = {str(spec["job_id"]): spec for spec in load_job_specs(root)}
    spec = specs.get(str(job_id))
    if spec is None:
        raise BoundedJobError("unknown bounded job")
    run = next((item for item in spec["runs"] if item["run_id"] == run_id), None)
    if run is None:
        raise BoundedJobError("unknown bounded job run")
    if int(known_at_ns) < int(run["not_before_ns"]):
        raise BoundedJobError("bounded job run is not due")
    runtime = root / "runtime/background_jobs"
    receipt_path = runtime / (str(run_id) + ".json")
    if receipt_path.is_file():
        return json.loads(receipt_path.read_text(encoding="utf-8"))
    claim_path = runtime / (str(run_id) + ".claim")
    runtime.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(claim_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise BoundedJobError("bounded job run is already claimed") from exc
    os.close(descriptor)
    command = list(_command(spec))

    def default_executor(cmd, cwd, timeout_seconds):
        env = {"PYTHONUTF8": "1"}
        completed = subprocess.run(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=int(timeout_seconds), check=False, shell=False, env=env)
        return completed.returncode, completed.stdout, completed.stderr

    try:
        returncode, stdout, stderr = (executor or default_executor)(command, root, int(spec["timeout_seconds"]))
        receipt = {
            "schema_version": 1,
            "job_id": str(job_id),
            "run_id": str(run_id),
            "action": spec["action"],
            "status": "SUCCEEDED" if int(returncode) == 0 else "FAILED",
            "completed_at_ns": int(known_at_ns),
            "returncode": int(returncode),
            "stdout_tail": str(stdout)[-4000:],
            "stderr_tail": str(stderr)[-4000:],
            "capital_effect": "NONE",
            "credentials_used": False,
            "shell_used": False,
        }
        _atomic_json(receipt_path, receipt)
        return receipt
    finally:
        if claim_path.exists():
            claim_path.unlink()
