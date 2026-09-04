from __future__ import annotations

import math
import statistics
from typing import Any, Dict, Mapping, Sequence

from ..operations import canonical_hash
from .contracts import RESEARCH_AUTHORITY, ResearchContractError


FALSIFICATION_SCHEMA_VERSION = "1.0"
FALSIFICATION_AUTHORITY = {
    **dict(RESEARCH_AUTHORITY),
    "may_falsify_candidate": True,
    "qualifies_model": False,
    "promotes_model": False,
}


def build_falsification_policy(
    *,
    policy_id: str,
    cost_scenarios_bps: Sequence[float],
    primary_cost_bps: float,
    multiplicity_count: int,
    minimum_observations: int,
    minimum_folds: int,
    minimum_positive_fold_fraction: float,
    maximum_drawdown: float,
    maximum_best_fold_profit_share: float,
    maximum_adjusted_p_value: float,
    minimum_mean_net_return: float = 0.0,
) -> Mapping[str, Any]:
    costs = tuple(float(value) for value in cost_scenarios_bps)
    if not policy_id or not costs or any(value < 0 for value in costs) or len(set(costs)) != len(costs):
        raise ResearchContractError("falsification cost scenarios must be unique and non-negative")
    if float(primary_cost_bps) not in costs:
        raise ResearchContractError("primary cost must be one of the preregistered scenarios")
    if int(multiplicity_count) <= 0 or int(minimum_observations) <= 0 or int(minimum_folds) <= 0:
        raise ResearchContractError("falsification count thresholds must be positive")
    for value, field in (
        (minimum_positive_fold_fraction, "minimum_positive_fold_fraction"),
        (maximum_drawdown, "maximum_drawdown"),
        (maximum_best_fold_profit_share, "maximum_best_fold_profit_share"),
        (maximum_adjusted_p_value, "maximum_adjusted_p_value"),
    ):
        if not 0.0 <= float(value) <= 1.0:
            raise ResearchContractError("%s must be in [0,1]" % field)
    body = {
        "schema_version": FALSIFICATION_SCHEMA_VERSION,
        "policy_id": str(policy_id),
        "cost_scenarios_bps": list(costs),
        "primary_cost_bps": float(primary_cost_bps),
        "multiplicity_count": int(multiplicity_count),
        "minimum_observations": int(minimum_observations),
        "minimum_folds": int(minimum_folds),
        "minimum_positive_fold_fraction": float(minimum_positive_fold_fraction),
        "maximum_drawdown": float(maximum_drawdown),
        "maximum_best_fold_profit_share": float(maximum_best_fold_profit_share),
        "maximum_adjusted_p_value": float(maximum_adjusted_p_value),
        "minimum_mean_net_return": float(minimum_mean_net_return),
        "authority": dict(FALSIFICATION_AUTHORITY),
    }
    value = dict(body)
    value["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
    return value


def _validate_policy(policy: Mapping[str, Any]) -> None:
    if policy.get("schema_version") != FALSIFICATION_SCHEMA_VERSION or policy.get("authority") != FALSIFICATION_AUTHORITY:
        raise ResearchContractError("falsification policy schema/authority invalid")
    body = {key: value for key, value in policy.items() if key != "integrity"}
    integrity = policy.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("content_hash") != canonical_hash(body):
        raise ResearchContractError("falsification policy integrity mismatch")


def _compounded(values: Sequence[float]) -> float:
    equity = 1.0
    for value in values:
        equity *= 1.0 + float(value)
    return equity - 1.0


def _max_drawdown(values: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    maximum = 0.0
    for value in values:
        equity *= 1.0 + float(value)
        peak = max(peak, equity)
        if peak > 0:
            maximum = max(maximum, (peak - equity) / peak)
    return maximum


def _one_sided_p_value(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 1.0
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values)
    if deviation == 0:
        return 0.0 if mean > 0 else 1.0
    statistic = mean / (deviation / math.sqrt(len(values)))
    return 0.5 * math.erfc(statistic / math.sqrt(2.0))


def evaluate_falsification(
    *,
    candidate_ref: str,
    question_ref: str,
    policy: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
    evaluated_at_ns: int,
    evidence_refs: Sequence[str],
) -> Mapping[str, Any]:
    _validate_policy(policy)
    if not candidate_ref or not question_ref or int(evaluated_at_ns) < 0:
        raise ResearchContractError("falsification evaluation identity/timing invalid")
    refs = tuple(str(value) for value in evidence_refs)
    if not refs or any(not value for value in refs) or len(set(refs)) != len(refs):
        raise ResearchContractError("falsification evidence refs must be unique and non-empty")
    rows = []
    for item in observations:
        if not isinstance(item, Mapping):
            raise ResearchContractError("falsification observation malformed")
        fold_id = str(item.get("fold_id", ""))
        gross = item.get("gross_return")
        sides = item.get("trading_sides", 2)
        if not fold_id or not isinstance(gross, (int, float)) or isinstance(gross, bool) or not isinstance(sides, int) or sides <= 0:
            raise ResearchContractError("falsification observation identity/value invalid")
        if not math.isfinite(float(gross)) or float(gross) <= -1.0:
            raise ResearchContractError("falsification gross return invalid")
        rows.append({"fold_id": fold_id, "gross_return": float(gross), "trading_sides": int(sides)})
    if not rows:
        raise ResearchContractError("falsification requires observations")

    folds = sorted({row["fold_id"] for row in rows})
    scenario_results: Dict[str, Mapping[str, Any]] = {}
    for cost_bps in policy["cost_scenarios_bps"]:
        net = [row["gross_return"] - row["trading_sides"] * float(cost_bps) / 10000.0 for row in rows]
        by_fold = {fold_id: [] for fold_id in folds}
        for row, net_return in zip(rows, net):
            by_fold[row["fold_id"]].append(net_return)
        fold_returns = [_compounded(by_fold[fold_id]) for fold_id in folds]
        positive = [value for value in fold_returns if value > 0]
        concentration = max(positive) / sum(positive) if positive and sum(positive) > 0 else 1.0
        p_value = _one_sided_p_value(net)
        scenario_results[str(cost_bps)] = {
            "cost_bps_per_side": float(cost_bps),
            "observation_count": len(rows),
            "fold_count": len(folds),
            "mean_net_return": statistics.fmean(net),
            "median_net_return": statistics.median(net),
            "net_compounded_return": _compounded(net),
            "maximum_drawdown": _max_drawdown(net),
            "positive_fold_fraction": sum(value > 0 for value in fold_returns) / len(folds),
            "best_fold_profit_share": concentration,
            "one_sided_p_value": p_value,
            "multiplicity_adjusted_p_value": min(1.0, p_value * int(policy["multiplicity_count"])),
            "folds": [{"fold_id": fold_id, "observation_count": len(by_fold[fold_id]), "net_compounded_return": _compounded(by_fold[fold_id])} for fold_id in folds],
        }

    primary = scenario_results[str(float(policy["primary_cost_bps"]))]
    gates = {
        "minimum_observations": primary["observation_count"] >= int(policy["minimum_observations"]),
        "minimum_folds": primary["fold_count"] >= int(policy["minimum_folds"]),
        "minimum_mean_net_return": primary["mean_net_return"] > float(policy["minimum_mean_net_return"]),
        "minimum_positive_fold_fraction": primary["positive_fold_fraction"] >= float(policy["minimum_positive_fold_fraction"]),
        "maximum_drawdown": primary["maximum_drawdown"] <= float(policy["maximum_drawdown"]),
        "maximum_best_fold_profit_share": primary["best_fold_profit_share"] <= float(policy["maximum_best_fold_profit_share"]),
        "maximum_adjusted_p_value": primary["multiplicity_adjusted_p_value"] <= float(policy["maximum_adjusted_p_value"]),
    }
    survived = all(gates.values())
    body = {
        "schema_version": FALSIFICATION_SCHEMA_VERSION,
        "candidate_ref": str(candidate_ref),
        "question_ref": str(question_ref),
        "policy_hash": policy["integrity"]["content_hash"],
        "evaluated_at_ns": int(evaluated_at_ns),
        "evidence_refs": list(refs),
        "scenario_results": scenario_results,
        "primary_gates": gates,
        "gate_failures": [name for name, passed in gates.items() if not passed],
        "decision": "SURVIVED_FALSIFICATION" if survived else "FALSIFIED",
        "authority": dict(FALSIFICATION_AUTHORITY),
    }
    value = dict(body)
    value["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
    return value
