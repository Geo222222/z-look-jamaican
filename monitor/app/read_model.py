"""Read-only operator API composition from canonical kernel projections."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .contract import SUPPORTED_SCHEMAS, MonitorContractError, overview_view, validate_snapshot


def _ensure_kernel_path(root: Path) -> None:
    text = str(root)
    if text not in sys.path:
        sys.path.insert(0, text)


def _git_sha(root: Path) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = (proc.stdout or "").strip()
    return value or None


_SNAPSHOT_CACHE: Dict[str, Any] = {"root": None, "monotonic": 0.0, "payload": None}
_HEALTH_CACHE: Dict[str, Any] = {"root": None, "monotonic": 0.0, "payload": None}


def operator_snapshot_payload(root: Path) -> Mapping[str, Any]:
    _ensure_kernel_path(root)
    now = time.monotonic()
    if (
        _SNAPSHOT_CACHE["payload"] is not None
        and _SNAPSHOT_CACHE["root"] == str(root)
        and now - float(_SNAPSHOT_CACHE["monotonic"]) < 2.0
    ):
        return _SNAPSHOT_CACHE["payload"]
    from autonomous_kernel.operator import operator_snapshot
    payload = operator_snapshot(root)
    if (payload.get("contract") or {}).get("name") != "zlj-operator-console":
        raise MonitorContractError("unexpected operator snapshot contract")
    _SNAPSHOT_CACHE["root"] = str(root)
    _SNAPSHOT_CACHE["monotonic"] = now
    _SNAPSHOT_CACHE["payload"] = payload
    return payload


def monitor_snapshot_payload(root: Path) -> Mapping[str, Any]:
    _ensure_kernel_path(root)
    from autonomous_kernel.monitor import monitor_snapshot
    return dict(monitor_snapshot(root))


def build_health(root: Path) -> Dict[str, Any]:
    _ensure_kernel_path(root)
    from autonomous_kernel.operator.journal import validate_operator_journal
    from autonomous_kernel.intelligence.runtime import IntelligenceRuntime, validate_event_chain

    known_at_ns = time.time_ns()
    now = time.monotonic()
    cached = _HEALTH_CACHE.get("payload")
    if cached is not None and _HEALTH_CACHE.get("root") == str(root) and now - float(_HEALTH_CACHE.get("monotonic") or 0) < 15.0:
        payload = dict(cached)
        payload["known_at_ns"] = known_at_ns
        return payload
    try:
        operator_journal_errors = list(validate_operator_journal(root))
    except Exception as exc:
        operator_journal_errors = [str(exc)]
    try:
        intelligence_errors = list(validate_event_chain(IntelligenceRuntime(root).events()))
    except Exception as exc:
        intelligence_errors = [str(exc)]
    validation_status = "ok"
    validation_errors: Tuple[str, ...] = ()
    try:
        from autonomous_kernel.store import StateValidationError, validate
        validate(root)
    except StateValidationError as exc:
        validation_status = "failed"
        validation_errors = tuple(str(item) for item in (exc.args[0] if exc.args else ()))
    except Exception as exc:
        validation_status = "failed"
        validation_errors = (str(exc),)
    degraded = bool(operator_journal_errors) or bool(intelligence_errors) or validation_status != "ok"
    status = "BACKEND_DEGRADED" if degraded else "BACKEND_ONLINE"
    payload = {
        "connectivity": "BACKEND_ONLINE",
        "status": status,
        "http_status": 200,
        "backend_version": "3.1.0",
        "commit_sha": _git_sha(root),
        "known_at_ns": known_at_ns,
        "journal_validity": "INVALID" if operator_journal_errors else "VALID",
        "operator_journal_errors": operator_journal_errors,
        "intelligence_journal_validity": "INVALID" if intelligence_errors else "VALID",
        "intelligence_journal_errors": intelligence_errors,
        "state_validation": validation_status,
        "state_validation_errors": list(validation_errors),
        "runtime_mode": "SHADOW_ONLY",
        "live_execution": "LOCKED",
        "capital_authority": "NONE",
        "subsystems": {
            "operator_journal": "INVALID" if operator_journal_errors else "VALID",
            "intelligence_journal": "INVALID" if intelligence_errors else "VALID",
            "durable_state": "OK" if validation_status == "ok" else "FAILED",
        },
        "authority": {
            "economic_decision": False,
            "capital_allocation": False,
            "risk_authorization": False,
            "external_execution": False,
            "provider_order_creation": False,
            "may_be_consumed_by": ["ZLJ_OPERATOR"],
        },
        "source_root": str(root),
    }
    _HEALTH_CACHE["root"] = str(root)
    _HEALTH_CACHE["monotonic"] = now
    _HEALTH_CACHE["payload"] = payload
    return payload


def _stage(snapshot: Mapping[str, Any], stage_id: str) -> Mapping[str, Any]:
    for item in snapshot.get("stages") or ():
        if isinstance(item, Mapping) and item.get("id") == stage_id:
            return item
    return {}


def _monitor_section(snapshot: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    sections = ((snapshot.get("monitor") or {}).get("sections") or {})
    value = sections.get(name)
    return value if isinstance(value, Mapping) else {}


def slice_system(snapshot: Mapping[str, Any], health: Mapping[str, Any]) -> Dict[str, Any]:
    system = dict(snapshot.get("system") or {})
    system.update({
        "backend_status": health.get("status"),
        "connectivity": health.get("connectivity"),
        "commit_sha": health.get("commit_sha"),
        "state_validation": health.get("state_validation"),
        "runtime_mode": health.get("runtime_mode"),
        "live_execution": health.get("live_execution"),
        "capital_authority": health.get("capital_authority"),
    })
    return {"system": system, "contract": snapshot.get("contract"), "authority": health.get("authority")}


def slice_overview(snapshot: Mapping[str, Any], health: Mapping[str, Any]) -> Dict[str, Any]:
    intel = snapshot.get("expert_intelligence") if isinstance(snapshot.get("expert_intelligence"), Mapping) else {}
    return {
        "backend_status": health.get("status"),
        "system": snapshot.get("system"),
        "stages": snapshot.get("stages"),
        "certification": snapshot.get("certification"),
        "expert_intelligence": {
            "qualification": intel.get("qualification"),
            "runtime": (intel.get("runtime") or {}).get("benjamin"),
            "internal_intelligence_exists": ((intel.get("runtime") or {}).get("internal_intelligence_exists")),
        },
        "jobs": slice_jobs(snapshot),
        "authority": health.get("authority"),
    }


def slice_market(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    perception = snapshot.get("perception") if isinstance(snapshot.get("perception"), Mapping) else {}
    return {
        "stage": _stage(snapshot, "Z1"),
        "instrument_state": _stage(snapshot, "Z2"),
        "feed_status": perception.get("feed_status"),
        "observer": perception.get("observer"),
        "latest_instrument_state": perception.get("latest_instrument_state"),
        "data_quality": _monitor_section(snapshot, "data_quality"),
        "authority": perception.get("authority"),
    }


def slice_context(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    perception = snapshot.get("perception") if isinstance(snapshot.get("perception"), Mapping) else {}
    return {
        "stage": _stage(snapshot, "Z9"),
        "operational_status": perception.get("z9_status"),
        "latest_context": perception.get("latest_context"),
        "context_store": perception.get("context_store"),
        "certification": (snapshot.get("certification") or {}).get("z9"),
        "authority": perception.get("authority"),
    }


def slice_questions(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    return {"question_registry": snapshot.get("question_registry")}


def slice_experts(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    intel = snapshot.get("expert_intelligence") if isinstance(snapshot.get("expert_intelligence"), Mapping) else {}
    return {"school": intel.get("school"), "construction": intel.get("construction"), "runtime": intel.get("runtime")}


def slice_outcomes(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    return {"stage": _stage(snapshot, "Z6")}


def slice_competence(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    intel = snapshot.get("expert_intelligence") if isinstance(snapshot.get("expert_intelligence"), Mapping) else {}
    return {
        "stage": _stage(snapshot, "Z7"),
        "earned_competence": (intel.get("qualification") or {}).get("earned_competence"),
        "runtime": {
            "score_count": (intel.get("runtime") or {}).get("score_count"),
            "competence_available": (intel.get("runtime") or {}).get("competence_available"),
        },
    }


def slice_assembly(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    return {"stage": _stage(snapshot, "Z8"), "runtime_assembly_count": ((snapshot.get("expert_intelligence") or {}).get("runtime") or {}).get("assembly_count")}


def slice_research(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    return {"research_qualification": snapshot.get("research_qualification"), "certification": snapshot.get("certification")}


def slice_jobs(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    goals = _monitor_section(snapshot, "goals_tasks")
    data = goals.get("data") if isinstance(goals.get("data"), Mapping) else {}
    return {
        "availability": (goals.get("availability") or {}).get("state"),
        "next_task_id": data.get("next_task_id"),
        "active_task_ids": data.get("active_task_ids") or [],
        "highest_priority_autonomous_next_action": data.get("highest_priority_autonomous_next_action"),
    }


def slice_intelligence(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    intel = snapshot.get("expert_intelligence") if isinstance(snapshot.get("expert_intelligence"), Mapping) else {}
    runtime = intel.get("runtime") if isinstance(intel.get("runtime"), Mapping) else {}
    return {
        "internal_intelligence_exists": bool(runtime.get("internal_intelligence_exists")),
        "publication_count": runtime.get("publication_count") or 0,
        "latest_publication": runtime.get("latest_publication"),
        "qualification": intel.get("qualification"),
    }


def slice_benjamin_handoff(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    intel = snapshot.get("expert_intelligence") if isinstance(snapshot.get("expert_intelligence"), Mapping) else {}
    runtime = intel.get("runtime") if isinstance(intel.get("runtime"), Mapping) else {}
    benjamin = runtime.get("benjamin") if isinstance(runtime.get("benjamin"), Mapping) else {}
    return {
        "eligibility_status": benjamin.get("eligibility_status") or (intel.get("qualification") or {}).get("benjamin_eligibility"),
        "blocking_reasons": benjamin.get("blocking_reasons") or [],
        "policy_version": benjamin.get("policy_version"),
        "qualification_timestamp_ns": benjamin.get("qualification_timestamp_ns"),
        "handoff_count": benjamin.get("handoff_count") or 0,
        "latest_handoff": benjamin.get("latest_handoff"),
        "benjamin_handoff": (intel.get("qualification") or {}).get("benjamin_handoff"),
        "authority": {
            "economic_decision_remains_with": "BENJAMIN",
            "risk_authorization_remains_with": "WATCHMAN",
            "execution_remains_with": "THE_HAND",
            "capital_allocation": False,
            "live_execution": False,
        },
    }


def snapshot_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compose_operator_overview(root: Path) -> Mapping[str, Any]:
    monitor = validate_snapshot(monitor_snapshot_payload(root))
    if monitor.contract.get("schema_version") not in SUPPORTED_SCHEMAS:
        raise MonitorContractError("unsupported monitor contract schema: %r" % monitor.contract.get("schema_version"))
    return overview_view(monitor)
