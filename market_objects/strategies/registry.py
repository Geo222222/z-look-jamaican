"""Versioned catalog contract for strategy archetypes."""

import json
from pathlib import Path
from typing import Any, List, Mapping


OPS = {"EQ", "NE", "IN", "NOT_IN", "GT", "GTE", "LT", "LTE", "EXISTS", "TRUTHY"}


def validate_registry(document: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if document.get("schema_version") != 1 or not document.get("registry_id") or not document.get("registry_version"):
        errors.append("strategy registry schema/id/version invalid")
    if document.get("authority") != "KNOWLEDGE_ONLY_NO_EXECUTION_AUTHORITY":
        errors.append("strategy registry authority must be knowledge-only")
    policy = document.get("selection_policy", {})
    if policy.get("run_all_strategies") is not False or policy.get("record_every_considered_strategy") is not True:
        errors.append("strategy registry must prohibit run-all and preserve the consideration ledger")
    strategies = document.get("strategies", [])
    ids = [item.get("strategy_id") for item in strategies]
    if not strategies or len(ids) != len(set(ids)) or any(not item for item in ids):
        errors.append("strategy registry requires unique strategy IDs")
    for strategy in strategies:
        location = str(strategy.get("strategy_id"))
        if strategy.get("capital_authority") is not False or strategy.get("order_authority") is not False:
            errors.append(f"{location}: registry strategy cannot authorize money or orders")
        mechanism = strategy.get("mechanism", {})
        if not mechanism.get("economic_thesis") or not mechanism.get("null_hypothesis") or not mechanism.get("falsification"):
            errors.append(f"{location}: thesis, null, and falsification are required")
        for group in ("required", "supporting", "contraindications", "triggers"):
            for condition in strategy.get("conditions", {}).get(group, []):
                if not condition.get("condition_id") or not condition.get("object_type") or not condition.get("path") or condition.get("operator") not in OPS:
                    errors.append(f"{location}: invalid {group} condition")
        if not strategy.get("invalidation_conditions"):
            errors.append(f"{location}: invalidation conditions are required")
        economics = strategy.get("economics", {})
        if economics.get("qualification") not in {"NOT_EARNED", "PREREGISTERED", "REJECTED", "BACKTEST_SUPPORTED", "PROSPECTIVE_SUPPORTED", "SHADOW_QUALIFIED", "CAPITAL_ELIGIBLE", "LIVE"}:
            errors.append(f"{location}: invalid economics qualification")
    return errors


def load_registry(path: Path) -> Mapping[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_registry(document)
    if errors:
        raise ValueError("; ".join(errors))
    return document
