"""Authoritative, deterministic, read-only observer snapshot.

This module deliberately imports no mutation functions and performs no network,
signer, wallet, scheduler, experiment, or recovery action.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .store import StateValidationError, load_json, load_jsonl, repository_root, validate


CONTRACT_SCHEMA_VERSION = "1.0.0"
AVAILABILITY_STATES = ("available", "unknown", "not_earned", "blocked", "unavailable")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _latest_timestamp(records: Iterable[Mapping[str, Any]], fields: Sequence[str]) -> Optional[str]:
    candidates: List[tuple[float, str]] = []
    for record in records:
        for field in fields:
            value = record.get(field)
            parsed = _timestamp(value)
            if parsed is not None:
                candidates.append((parsed, str(value)))
    return max(candidates)[1] if candidates else None


def _provenance(
    root: Path,
    source_id: str,
    paths: Sequence[str],
    observed_at: str,
    authoritative_at: Optional[str],
    source: str = "repository_files",
) -> Mapping[str, Any]:
    integrity: Dict[str, Optional[str]] = {}
    for relative in paths:
        path = root / relative
        integrity[relative] = _sha256(path) if path.is_file() else None
    return {
        "source": source,
        "source_id": source_id,
        "path": paths[0] if len(paths) == 1 else None,
        "paths": list(paths),
        "observed_at": observed_at,
        "authoritative_at": authoritative_at,
        "schema_version": 1,
        "integrity": {"algorithm": "sha256", "by_path": integrity},
    }


def _section(
    root: Path,
    source_id: str,
    paths: Sequence[str],
    observed_at: str,
    authoritative_at: Optional[str],
    data: Any,
    availability: str = "available",
    reason: Optional[str] = None,
    freshness: Optional[Mapping[str, Any]] = None,
    source: str = "repository_files",
) -> Mapping[str, Any]:
    if availability not in AVAILABILITY_STATES:
        raise ValueError(f"invalid monitoring availability: {availability}")
    return {
        "provenance": _provenance(root, source_id, paths, observed_at, authoritative_at, source),
        "availability": {"state": availability, "reason": reason},
        "freshness": freshness or {"expectation": "event_driven", "state": "unknown"},
        "data": data,
    }


def _git_identity(root: Path) -> Mapping[str, Any]:
    git_dir = root / ".git"
    head_path = git_dir / "HEAD"
    if not head_path.is_file():
        return {"availability": "unavailable", "commit": None, "branch": None}
    head = head_path.read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        ref = head[5:]
        ref_path = git_dir / ref
        commit = ref_path.read_text(encoding="utf-8").strip() if ref_path.is_file() else None
        return {"availability": "available" if commit else "unknown", "commit": commit, "branch": ref.rsplit("/", 1)[-1], "ref": ref}
    return {"availability": "available", "commit": head, "branch": None, "ref": None}


def _parse_treasury(path: Path) -> Mapping[str, Any]:
    """Parse the intentionally simple owner registry without a YAML dependency."""
    destinations: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped.startswith("- id:"):
            if current:
                destinations.append(current)
            current = {"id": stripped.split(":", 1)[1].strip()}
        elif current and stripped.startswith(("asset:", "network:", "address:", "status:")):
            key, value = stripped.split(":", 1)
            current[key] = value.strip().strip('"')
    if current:
        destinations.append(current)
    for item in destinations:
        item["validation_state"] = "blocked" if str(item.get("status", "")).startswith("blocked") else "registry_active"
    return {
        "registry_version": 1,
        "purpose": "owner_treasury_withdrawal_destinations",
        "mutable_by_root_agent": False,
        "private_keys_expected": False,
        "destinations": destinations,
        "sweep_enabled": False,
    }


def _ledger_totals(entries: Sequence[Mapping[str, Any]]) -> Mapping[str, float]:
    fields = ("realized_revenue_usd", "realized_profit_usd", "realized_cost_usd", "retained_revenue_usd")
    return {field: sum(float(entry.get(field, 0) or 0) for entry in entries) for field in fields}


def monitor_snapshot(root: Optional[Path] = None, observed_at: Optional[str] = None) -> Mapping[str, Any]:
    """Build the observer contract from disk without modifying any source."""
    root = Path(root) if root is not None else repository_root()
    observed_at = observed_at or _iso_now()
    observed_epoch = _timestamp(observed_at)

    current = load_json(root / "state/current_state.json")
    resume = load_json(root / "state/resume.json")
    objectives = load_json(root / "state/objectives.json")
    backlog = load_json(root / "state/backlog.json")
    agents = load_json(root / "state/agents.json")
    shadow = load_json(root / "state/market_shadow.json")
    opportunities = load_json(root / "opportunities/register.json")
    ledger = load_json(root / "accounting/ledger.json")
    wallets = load_json(root / "state/operational_wallets.json")
    deployments = load_json(root / "state/deployments.json")
    incidents = load_json(root / "state/incidents.json")
    experiments = load_jsonl(root / "memory/experiments.jsonl")
    reflections = load_jsonl(root / "memory/reflections.jsonl")
    evidence = load_jsonl(root / "evidence/sources.jsonl")
    economic_metrics = load_jsonl(root / "metrics/economic.jsonl")
    system_metrics = load_jsonl(root / "metrics/system.jsonl")

    validation_errors: List[str] = []
    try:
        validation_checks = validate(root)
    except StateValidationError as exc:
        validation_checks = []
        validation_errors = exc.errors

    shadow_at = shadow.get("updated_at")
    age_seconds = None
    if observed_epoch is not None and _timestamp(shadow_at) is not None:
        age_seconds = max(0, int(observed_epoch - float(_timestamp(shadow_at))))
    heartbeat_state = "fresh" if age_seconds is not None and age_seconds <= 1800 else "stale" if age_seconds is not None else "unknown"
    decisions = shadow.get("decisions", [])
    timestamp_violations = [item["id"] for item in decisions if int(item["observed_at"]) >= int(item["actionable_at"])]
    pending = [item for item in decisions if item.get("status") == "pending"]
    resolved = [item for item in decisions if item.get("status") == "resolved"]
    active_experiment_id = shadow.get("experiment_id")
    active_records = [item for item in experiments if item.get("id") == active_experiment_id]
    active_evidence_paths = sorted(
        {
            str(item["path"])
            for item in evidence
            if active_experiment_id
            and active_experiment_id in str(item.get("id", ""))
            and item.get("path")
            and "preregistration" in str(item.get("source_type", ""))
        }
    )

    section_data: Dict[str, Mapping[str, Any]] = {}
    section_data["system_health"] = _section(
        root, "MONITOR-SYSTEM-HEALTH", ["state/current_state.json", "state/resume.json", "state/market_shadow.json", "metrics/system.jsonl"], observed_at,
        max(filter(None, [current.get("updated_at"), resume.get("updated_at"), shadow_at]), default=None),
        {"system_id": current.get("system_id"), "root_state": current.get("root_state"), "strategy_stage": current.get("strategy_stage"), "validation_status": "ok" if not validation_errors else "invalid", "validation_checks": validation_checks, "validation_errors": validation_errors, "heartbeat": {"automation_ids": resume.get("active_automation_ids", []), "expected_interval_seconds": 900, "last_shadow_observation_at": shadow_at, "age_seconds": age_seconds, "state": heartbeat_state}, "system_metrics": system_metrics},
        availability="available" if not validation_errors else "blocked", reason=None if not validation_errors else "Durable-state validation failed.",
        freshness={"expectation": "15-minute heartbeat for active shadow; other state is event-driven", "state": heartbeat_state, "age_seconds": age_seconds},
    )
    section_data["active_experiment"] = _section(
        root, "MONITOR-ACTIVE-EXPERIMENT", ["state/market_shadow.json", "memory/experiments.jsonl", *active_evidence_paths], observed_at, shadow_at,
        {"experiment_id": active_experiment_id, "records": active_records, "mode": shadow.get("mode"), "summary": shadow.get("summary"), "task_ids": resume.get("active_task_ids", [])},
        freshness={"expectation": "15-minute shadow heartbeat", "state": heartbeat_state, "age_seconds": age_seconds},
    )
    section_data["experiment_history"] = _section(root, "MONITOR-EXPERIMENT-HISTORY", ["memory/experiments.jsonl"], observed_at, _latest_timestamp(experiments, ("created_at",)), {"items": experiments}, freshness={"expectation": "append on experiment planning, observation, result, or closure", "state": "event_driven"})
    section_data["decisions"] = _section(root, "MONITOR-SHADOW-DECISIONS", ["state/market_shadow.json"], observed_at, shadow_at, {"experiment_id": active_experiment_id, "prospective": pending, "resolved": resolved, "counts": {"total": len(decisions), "prospective": len(pending), "resolved": len(resolved), "eligible_long": shadow.get("summary", {}).get("eligible_long", 0), "timestamp_violations": len(timestamp_violations)}, "timestamp_violation_ids": timestamp_violations, "shadow_net_return_sum": shadow.get("summary", {}).get("net_return_sum")}, freshness={"expectation": "15-minute shadow heartbeat", "state": heartbeat_state, "age_seconds": age_seconds})
    section_data["evidence_events"] = _section(root, "MONITOR-EVIDENCE-EVENTS", ["evidence/sources.jsonl"], observed_at, _latest_timestamp(evidence, ("captured_at",)), {"items": evidence}, freshness={"expectation": "append when material evidence is captured", "state": "event_driven"})
    section_data["data_quality"] = _section(root, "MONITOR-DATA-QUALITY", ["evidence/sources.jsonl", "state/incidents.json", "state/market_shadow.json"], observed_at, max(filter(None, [incidents.get("updated_at"), shadow_at]), default=None), {"repository_validation": "ok" if not validation_errors else "invalid", "evidence_integrity_check": "passed" if "evidence_artifact_integrity" in validation_checks else "failed_or_unavailable", "timestamp_violations": len(timestamp_violations), "timestamp_violation_ids": timestamp_violations, "resolved_data_quality_incidents": [item for item in incidents.get("items", []) if item.get("type") == "research_data_quality" and str(item.get("status", "")).startswith("resolved")], "open_incidents": [item for item in incidents.get("items", []) if not str(item.get("status", "")).startswith("resolved")]}, availability="available" if not validation_errors and not timestamp_violations else "blocked", reason=None if not validation_errors and not timestamp_violations else "Integrity or prospective timestamp checks failed.")
    section_data["opportunities"] = _section(root, "MONITOR-OPPORTUNITIES", ["opportunities/register.json"], observed_at, opportunities.get("updated_at"), opportunities)
    section_data["reflections"] = _section(root, "MONITOR-REFLECTIONS", ["memory/reflections.jsonl"], observed_at, _latest_timestamp(reflections, ("created_at",)), {"items": reflections})
    section_data["goals_tasks"] = _section(root, "MONITOR-GOALS-TASKS", ["state/objectives.json", "state/backlog.json", "state/agents.json", "state/resume.json"], observed_at, max(filter(None, [objectives.get("updated_at"), backlog.get("updated_at"), agents.get("updated_at"), resume.get("updated_at")]), default=None), {"objectives": objectives.get("items", []), "tasks": backlog.get("items", []), "active_task_ids": resume.get("active_task_ids", []), "next_task_id": resume.get("next_task_id"), "assignments": agents.get("items", [])})
    totals = _ledger_totals(ledger.get("entries", []))
    earned = bool(ledger.get("entries"))
    section_data["economics"] = _section(root, "MONITOR-ECONOMICS", ["accounting/ledger.json", "metrics/economic.jsonl"], observed_at, max(filter(None, [ledger.get("updated_at"), _latest_timestamp(economic_metrics, ("created_at",))]), default=None), {"currency": ledger.get("currency"), "realized_ledger_entries": ledger.get("entries", []), "realized_totals": totals, "retained_revenue_state": "available" if earned else "not_earned", "economic_metrics": economic_metrics, "shadow_pnl_excluded_from_realized": True}, availability="available" if earned else "not_earned", reason=None if earned else "No realized economic ledger entry has been earned or recorded.")
    section_data["financial_exposure"] = _section(root, "MONITOR-FINANCIAL-EXPOSURE", ["state/current_state.json", "accounting/ledger.json", "state/operational_wallets.json"], observed_at, max(filter(None, [current.get("updated_at"), ledger.get("updated_at"), wallets.get("updated_at")]), default=None), {"recorded_current_exposure_usd": 0, "production_capital_authorized_usd": ledger.get("production_capital_authorized_usd"), "max_concurrent_financial_exposure_usd": current.get("governor", {}).get("max_concurrent_financial_exposure_usd"), "capital_movement": current.get("capabilities", {}).get("capital_movement"), "operational_wallet_count": len(wallets.get("items", [])), "external_untracked_exposure": "unknown"})
    wallet_state = "available" if wallets.get("items") else "not_earned"
    section_data["wallets"] = _section(root, "MONITOR-OPERATIONAL-WALLETS", ["state/operational_wallets.json"], observed_at, wallets.get("updated_at"), {"public_metadata": wallets.get("items", []), "private_material_exposed": False, "observation": wallets.get("observation"), "decision": wallets.get("decision")}, availability=wallet_state, reason=None if wallets.get("items") else "No operational wallet capability has been economically justified or created.")
    treasury = _parse_treasury(root / "config/treasury_destinations.yaml")
    section_data["treasury"] = _section(root, "MONITOR-TREASURY", ["config/treasury_destinations.yaml", "state/current_state.json"], observed_at, current.get("updated_at"), {**treasury, "registry_sha256_anchor": current.get("treasury_registry", {}).get("sha256_at_inspection"), "sweep_state": "blocked", "sweep_reason": "Governor/readiness gate disables treasury sweeps."}, availability="blocked", reason="Destinations are visible, but sweeps are disabled until the deterministic readiness gate is earned.")
    section_data["governor"] = _section(root, "MONITOR-GOVERNOR", ["docs/GOVERNOR.md", "state/current_state.json"], observed_at, current.get("updated_at"), {"state": current.get("governor"), "capabilities": current.get("capabilities"), "owner_only_blockers": current.get("owner_only_blockers", []), "validation_status": "ok" if "governor_zero_exposure" in validation_checks else "invalid"}, availability="available" if "governor_zero_exposure" in validation_checks else "blocked")
    git_identity = _git_identity(root)
    deployment_capability = "available" if deployments.get("items") else "not_earned"
    section_data["deployments"] = _section(root, "MONITOR-DEPLOYMENTS", ["state/deployments.json", "state/resume.json", ".git/HEAD"], observed_at, max(filter(None, [deployments.get("updated_at"), resume.get("updated_at")]), default=None), {"version_identity": git_identity, "registry": deployments.get("items", []), "active_automation_ids": resume.get("active_automation_ids", []), "product_deployment_state": deployment_capability, "live_external_scheduler_status": "unknown"}, availability="available", reason="Registry and Git identity are available; no product deployment has been earned and live scheduler state is external.")
    section_data["incidents"] = _section(root, "MONITOR-INCIDENTS", ["state/incidents.json"], observed_at, incidents.get("updated_at"), incidents)
    section_data["runtime_logs"] = _section(root, "MONITOR-RUNTIME-LOGS", [], observed_at, None, {"items": []}, availability="unavailable", reason="No canonical runtime/system log sink is registered in the repository.", source="capability_absent")
    section_data["model_provider_qualification"] = _section(root, "MONITOR-MODEL-PROVIDER", ["state/incidents.json"], observed_at, incidents.get("updated_at"), {"qualification_registry": None, "related_incident_ids": [item.get("id") for item in incidents.get("items", []) if "platform" in str(item.get("type", ""))]}, availability="unavailable", reason="No model/provider qualification registry exists; provider incidents remain authoritative incident records.")

    return {
        "contract": {"name": "z-look-jamaican-monitor-snapshot", "schema_version": CONTRACT_SCHEMA_VERSION, "read_only": True, "observed_at": observed_at, "availability_states": list(AVAILABILITY_STATES), "unknown_semantics": {"unknown": "The canonical source cannot establish the value.", "not_earned": "The capability or economic outcome has not passed its required evidence gate.", "blocked": "An explicit Governor, validation, policy, or readiness gate prevents the capability.", "unavailable": "No canonical source or capability currently exists."}},
        "sections": section_data,
    }
