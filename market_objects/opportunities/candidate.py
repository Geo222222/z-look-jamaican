"""Economically gated opportunity candidates; never execution requests."""

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from ..core import MarketObjectRef, build_object


def _decimal(value: Any, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be decimal-compatible") from exc
    if not parsed.is_finite():
        raise ValueError(f"{name} must be finite")
    return parsed


def opportunity_candidate(
    *, object_id: str, applicability: Mapping[str, Any], liquidity_state: Mapping[str, Any],
    portfolio: Mapping[str, Any], created_at: str, entry_plan: Mapping[str, Any],
    exit_plan: Mapping[str, Any], risk_plan: Mapping[str, Any], economics: Mapping[str, Any],
    additional_input_objects: Sequence[Mapping[str, Any]] = (),
) -> Mapping[str, Any]:
    if applicability.get("object_type") != "STRATEGY_APPLICABILITY" or liquidity_state.get("object_type") != "LIQUIDITY_STATE" or portfolio.get("object_type") != "PORTFOLIO_OBSERVATION":
        raise ValueError("opportunity requires applicability, liquidity, and portfolio objects")
    gross = _decimal(economics.get("expected_gross_payoff_bps"), "expected_gross_payoff_bps")
    costs = _decimal(economics.get("expected_total_cost_bps"), "expected_total_cost_bps")
    net = gross - costs
    minimum = _decimal(economics.get("minimum_required_net_edge_bps"), "minimum_required_net_edge_bps")
    gates = {
        "applicability_high": applicability["payload"]["applicability"] == "HIGH",
        "watch_trigger_present": applicability["payload"]["status"] == "WATCH",
        "economic_qualification_earned": applicability["payload"]["economic_qualification"] in {"PROSPECTIVE_SUPPORTED", "SHADOW_QUALIFIED", "CAPITAL_ELIGIBLE", "LIVE"},
        "entry_defined": bool(entry_plan.get("trigger") and entry_plan.get("price_basis")),
        "exit_defined": bool(exit_plan.get("profit_condition") and exit_plan.get("time_stop")),
        "risk_defined": bool(risk_plan.get("invalidation") and risk_plan.get("maximum_loss_usd") is not None),
        "net_edge_sufficient": net >= minimum and net > 0,
        "liquidity_sufficient": liquidity_state["payload"]["state"] == "HEALTHY",
        "portfolio_allows_exposure": portfolio["payload"]["strategy_exposure_allowed"] is True and _decimal(portfolio["payload"]["available_risk_budget_usd"], "available_risk_budget_usd") > 0,
    }
    qualified = all(gates.values())
    inputs = [applicability, liquidity_state, portfolio, *additional_input_objects]
    relationships = {applicability["object_id"]: "APPLICABILITY_GATE", liquidity_state["object_id"]: "LIQUIDITY_GATE", portfolio["object_id"]: "PORTFOLIO_GATE"}
    relationships.update({item["object_id"]: "ECONOMIC_INPUT" for item in additional_input_objects})
    refs = [MarketObjectRef.to(item["object_id"], relationships[item["object_id"]], expected_object_type=item["object_type"]) for item in inputs]
    return build_object(
        object_id=object_id, object_type="OPPORTUNITY_CANDIDATE", truth_class="ECONOMIC_CANDIDATE",
        subject=applicability["subject"], effective_at=applicability["effective_at"], created_at=created_at,
        source_time_range=applicability["source_time_range"], input_refs=refs,
        method={"name": "DETERMINISTIC_ECONOMIC_OPPORTUNITY_GATE", "version": "1.0.0", "deterministic": True},
        quality={"status": "VALID", "all_gates_passed": qualified},
        payload={"strategy_applicability_ref": f"market://{applicability['object_id']}", "liquidity_state_ref": f"market://{liquidity_state['object_id']}", "portfolio_ref": f"market://{portfolio['object_id']}", "status": "QUALIFIED_CANDIDATE" if qualified else "BLOCKED_CANDIDATE", "entry_plan": dict(entry_plan), "exit_plan": dict(exit_plan), "risk_plan": dict(risk_plan), "economics": {**dict(economics), "calculated_net_edge_bps": str(net)}, "gates": gates, "failed_gates": [name for name, passed in gates.items() if not passed], "execution_authority": False, "capital_authority": False, "execution_request_created": False},
    )
