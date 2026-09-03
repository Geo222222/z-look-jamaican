from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

SUPPORTED_SCHEMA = "1.0.0"
VALID_AVAILABILITY = {"available", "unknown", "not_earned", "blocked", "unavailable"}


class MonitorContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContractSnapshot:
    raw: Mapping[str, Any]

    @property
    def contract(self) -> Mapping[str, Any]:
        return self.raw["contract"]

    @property
    def sections(self) -> Mapping[str, Any]:
        return self.raw["sections"]

    def section(self, name: str) -> Mapping[str, Any]:
        return self.sections.get(name, {
            "availability": {"state": "unavailable", "reason": "Section %r is absent from the contract." % name},
            "freshness": {"state": "unknown", "expectation": "unknown"},
            "provenance": {"source": "contract_absent", "source_id": name, "paths": [], "integrity": {"algorithm": "sha256", "by_path": {}}},
            "data": {},
        })


def validate_snapshot(payload: Mapping[str, Any]) -> ContractSnapshot:
    if not isinstance(payload, Mapping):
        raise MonitorContractError("monitor_snapshot output is not an object")
    contract = payload.get("contract")
    sections = payload.get("sections")
    if not isinstance(contract, Mapping) or not isinstance(sections, Mapping):
        raise MonitorContractError("monitor_snapshot is missing contract or sections")
    if contract.get("name") != "z-look-jamaican-monitor-snapshot":
        raise MonitorContractError("unexpected monitor contract name")
    if contract.get("schema_version") != SUPPORTED_SCHEMA:
        raise MonitorContractError("unsupported monitor contract schema: %r" % contract.get("schema_version"))
    if contract.get("read_only") is not True:
        raise MonitorContractError("monitor contract does not assert read_only=true")
    for name, section in sections.items():
        if not isinstance(section, Mapping):
            raise MonitorContractError("section %r is not an object" % name)
        state = (section.get("availability") or {}).get("state")
        if state not in VALID_AVAILABILITY:
            raise MonitorContractError("section %r has invalid availability state %r" % (name, state))
    return ContractSnapshot(payload)


def _invoke_json(root: Path, args: Sequence[str], *, timeout: Optional[float] = None) -> Mapping[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [os.getenv("ZLOOK_PYTHON", "python"), "-m", "autonomous_kernel"] + list(args),
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout or float(os.getenv("ZLOOK_MONITOR_COMMAND_TIMEOUT_SECONDS", "15")),
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stdout.strip() or proc.stderr.strip()
        raise MonitorContractError("kernel command failed (%d): %s" % (proc.returncode, detail[:2000]))
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise MonitorContractError("kernel command emitted invalid JSON: %s" % exc) from exc
    if not isinstance(payload, Mapping):
        raise MonitorContractError("kernel command output is not an object")
    return payload


def invoke_snapshot(root: Path) -> ContractSnapshot:
    return validate_snapshot(_invoke_json(root, ["monitor_snapshot", "--json"]))


def invoke_operator_snapshot(root: Path) -> Mapping[str, Any]:
    payload = _invoke_json(root, ["operator_snapshot"], timeout=float(os.getenv("ZLOOK_OPERATOR_SNAPSHOT_TIMEOUT_SECONDS", "20")))
    if (payload.get("contract") or {}).get("name") != "zlj-operator-console":
        raise MonitorContractError("unexpected operator snapshot contract")
    return payload


def invoke_operator_catalog(root: Path) -> Mapping[str, Any]:
    return _invoke_json(root, ["operator_catalog"])


def invoke_operator_command(root: Path, request: Mapping[str, Any]) -> Mapping[str, Any]:
    return _invoke_json(
        root,
        ["operator_command", "--request-json", json.dumps(dict(request), sort_keys=True, separators=(",", ":"))],
        timeout=float(os.getenv("ZLOOK_OPERATOR_COMMAND_TIMEOUT_SECONDS", "30")),
    )


def availability(section: Mapping[str, Any]) -> str:
    return str((section.get("availability") or {}).get("state") or "unknown")


def section_data(section: Mapping[str, Any]) -> Mapping[str, Any]:
    value = section.get("data")
    return value if isinstance(value, Mapping) else {}


def overview_view(snapshot: ContractSnapshot) -> Mapping[str, Any]:
    s = snapshot.section
    health = s("system_health")
    exp = s("active_experiment")
    decisions = s("decisions")
    evidence = s("evidence_events")
    opps = s("opportunities")
    economics = s("economics")
    exposure = s("financial_exposure")
    dq = s("data_quality")
    goals = s("goals_tasks")

    hd = section_data(health)
    ed = section_data(exp)
    dd = section_data(decisions)
    evd = section_data(evidence)
    od = section_data(opps)
    ecd = section_data(economics)
    exd = section_data(exposure)
    dqd = section_data(dq)
    gd = section_data(goals)

    opportunity_items = od.get("items") if isinstance(od.get("items"), list) else []
    evidence_items = evd.get("items") if isinstance(evd.get("items"), list) else []
    realized = ecd.get("realized_totals") if isinstance(ecd.get("realized_totals"), Mapping) else {}
    decision_counts = dd.get("counts") if isinstance(dd.get("counts"), Mapping) else {}
    heartbeat = hd.get("heartbeat") if isinstance(hd.get("heartbeat"), Mapping) else {}

    return {
        "contract": snapshot.contract,
        "system": {
            "availability": availability(health),
            "system_id": hd.get("system_id"),
            "root_state": hd.get("root_state"),
            "strategy_stage": hd.get("strategy_stage"),
            "validation_status": hd.get("validation_status"),
            "heartbeat": heartbeat,
        },
        "active_experiment": {
            "availability": availability(exp),
            "id": ed.get("experiment_id"),
            "mode": ed.get("mode"),
            "summary": ed.get("summary"),
            "task_ids": ed.get("task_ids", []),
            "records": ed.get("records", []),
            "freshness": exp.get("freshness", {}),
            "provenance": exp.get("provenance", {}),
        },
        "metrics": {
            "decisions_total": decision_counts.get("total"),
            "decisions_prospective": decision_counts.get("prospective"),
            "decisions_resolved": decision_counts.get("resolved"),
            "timestamp_violations": decision_counts.get("timestamp_violations"),
            "eligible_long": decision_counts.get("eligible_long"),
            "evidence_events": len(evidence_items),
            "opportunities": len(opportunity_items),
            "retained_revenue_usd": realized.get("retained_revenue_usd"),
            "realized_profit_usd": realized.get("realized_profit_usd"),
            "recorded_exposure_usd": exd.get("recorded_current_exposure_usd"),
            "external_untracked_exposure": exd.get("external_untracked_exposure"),
            "next_task_id": gd.get("next_task_id"),
        },
        "availability": {name: availability(snapshot.section(name)) for name in snapshot.sections},
        "data_quality": dqd,
        "top_opportunities": opportunity_items[:8],
        "recent_evidence": evidence_items[-12:][::-1],
        "decisions": dd,
    }
