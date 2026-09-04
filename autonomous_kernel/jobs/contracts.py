from __future__ import annotations

import hashlib
from typing import Any, Dict, Mapping, Sequence, Tuple

from ..operations import canonical_hash


JOB_SCHEMA_VERSION = "1.0"
ALLOWED_JOB_ACTIONS = {
    "VALIDATE_KERNEL",
    "CONTEXT_MATERIALIZE",
    "EXPERT_SYNC",
}
JOB_AUTHORITY = {
    "research_observation_only": True,
    "shell_allowed": False,
    "credentials_allowed": False,
    "capital_effect": "NONE",
    "economic_decision": False,
    "risk_authorization": False,
    "external_execution": False,
}


class BoundedJobError(ValueError):
    pass


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_action_args(action: str, args: Mapping[str, Any]) -> None:
    if action == "VALIDATE_KERNEL":
        if args:
            raise BoundedJobError("VALIDATE_KERNEL accepts no job arguments")
        return
    if action == "CONTEXT_MATERIALIZE":
        if set(args) != {"cutoff_at_ns"} or not isinstance(args.get("cutoff_at_ns"), int) or int(args["cutoff_at_ns"]) < 0:
            raise BoundedJobError("CONTEXT_MATERIALIZE requires non-negative cutoff_at_ns")
        return
    if action == "EXPERT_SYNC":
        if set(args) != {"known_at_ns"} or not isinstance(args.get("known_at_ns"), int) or int(args["known_at_ns"]) < 0:
            raise BoundedJobError("EXPERT_SYNC requires non-negative known_at_ns")
        return
    raise BoundedJobError("job action is not allowlisted")


def build_job_spec(
    *,
    job_id: str,
    action: str,
    action_args: Mapping[str, Any],
    preregistration_path: str,
    preregistration_sha256: str,
    run_ids: Sequence[str],
    not_before_ns: Sequence[int],
    timeout_seconds: int,
    created_at_ns: int,
) -> Mapping[str, Any]:
    if not job_id or action not in ALLOWED_JOB_ACTIONS:
        raise BoundedJobError("bounded job identity/action invalid")
    _validate_action_args(action, action_args)
    if not preregistration_path or preregistration_path.startswith("/") or ".." in preregistration_path.replace("\\", "/").split("/"):
        raise BoundedJobError("preregistration path must remain repository-relative")
    digest = str(preregistration_sha256).lower()
    if len(digest) != 64:
        raise BoundedJobError("preregistration_sha256 must be SHA-256 hex")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise BoundedJobError("preregistration_sha256 must be SHA-256 hex") from exc
    ids = tuple(str(value) for value in run_ids)
    times = tuple(int(value) for value in not_before_ns)
    if not ids or len(ids) != len(times) or any(not value for value in ids) or len(set(ids)) != len(ids):
        raise BoundedJobError("job run ids/times must be aligned, unique, and non-empty")
    if any(value < 0 for value in times) or int(timeout_seconds) <= 0 or int(created_at_ns) < 0:
        raise BoundedJobError("bounded job timing invalid")
    runs = [{"run_id": run_id, "not_before_ns": when} for run_id, when in zip(ids, times)]
    body: Dict[str, Any] = {
        "schema_version": JOB_SCHEMA_VERSION,
        "job_id": str(job_id),
        "action": action,
        "action_args": dict(action_args),
        "preregistration_path": str(preregistration_path),
        "preregistration_sha256": digest,
        "runs": runs,
        "timeout_seconds": int(timeout_seconds),
        "created_at_ns": int(created_at_ns),
        "authority": dict(JOB_AUTHORITY),
    }
    value = dict(body)
    value["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
    validate_job_spec(value)
    return value


def validate_job_spec(spec: Mapping[str, Any], *, root=None) -> None:
    if spec.get("schema_version") != JOB_SCHEMA_VERSION or spec.get("authority") != JOB_AUTHORITY:
        raise BoundedJobError("bounded job schema/authority invalid")
    action = str(spec.get("action", ""))
    if action not in ALLOWED_JOB_ACTIONS:
        raise BoundedJobError("bounded job action is not allowlisted")
    args = spec.get("action_args")
    if not isinstance(args, Mapping):
        raise BoundedJobError("bounded job args malformed")
    _validate_action_args(action, args)
    runs = spec.get("runs")
    if not isinstance(runs, list) or not runs:
        raise BoundedJobError("bounded job runs missing")
    seen = set()
    for run in runs:
        if not isinstance(run, Mapping):
            raise BoundedJobError("bounded job run malformed")
        run_id = str(run.get("run_id", ""))
        when = run.get("not_before_ns")
        if not run_id or run_id in seen or not isinstance(when, int) or when < 0:
            raise BoundedJobError("bounded job run identity/timing invalid")
        seen.add(run_id)
    if not isinstance(spec.get("timeout_seconds"), int) or spec["timeout_seconds"] <= 0:
        raise BoundedJobError("bounded job timeout invalid")
    if not isinstance(spec.get("created_at_ns"), int) or spec["created_at_ns"] < 0:
        raise BoundedJobError("bounded job created_at_ns invalid")
    preregistration_path = str(spec.get("preregistration_path", ""))
    if not preregistration_path or preregistration_path.startswith("/") or ".." in preregistration_path.replace("\\", "/").split("/"):
        raise BoundedJobError("bounded job preregistration path invalid")
    digest = str(spec.get("preregistration_sha256", ""))
    if len(digest) != 64:
        raise BoundedJobError("bounded job preregistration hash invalid")
    if root is not None:
        target = (root.resolve() / preregistration_path).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise BoundedJobError("bounded job preregistration escapes repository") from exc
        if not target.is_file():
            raise BoundedJobError("bounded job preregistration missing")
        if sha256_file(target) != digest:
            raise BoundedJobError("bounded job preregistration hash mismatch")
    integrity = spec.get("integrity")
    body = {key: value for key, value in spec.items() if key != "integrity"}
    if not isinstance(integrity, Mapping) or integrity.get("algorithm") != "sha256" or integrity.get("content_hash") != canonical_hash(body):
        raise BoundedJobError("bounded job integrity mismatch")
