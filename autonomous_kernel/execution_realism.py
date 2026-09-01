"""Deterministic comparison of observed venue facts and configured execution assumptions."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping


def evaluate_public_assumptions(observation: Mapping[str, Any], configured: Mapping[str, str]) -> Mapping[str, Any]:
    normalized = observation["normalized"]
    if normalized.get("type") != "microstructure_snapshot" or normalized.get("truth_class") != "OBSERVED_PUBLIC_MARKET_DATA":
        raise ValueError("public microstructure observation required")
    if observation.get("quality", {}).get("status") != "VALID":
        raise ValueError("only VALID observations may calibrate assumptions")
    rules = normalized["product_rules"]
    spread_half = Decimal(str(normalized["quoted_spread_bps"])) / Decimal("2")
    configured_half = Decimal(configured["half_spread_bps"])
    slippage_values = [Decimal(str(side["slippage_bps"])) for walk in normalized["depth_walks"].values() for side in walk.values()]
    all_depth_sufficient = all(bool(side["sufficient_depth"]) for walk in normalized["depth_walks"].values() for side in walk.values())
    configured_slippage = Decimal(configured["slippage_bps"])
    venue_quantity_step = Decimal(str(rules["base_increment"]))
    configured_quantity_step = Decimal(configured["quantity_step"])
    venue_price_step = Decimal(str(rules["quote_increment"]))
    configured_price_step = Decimal(configured["price_step"])
    venue_minimum = Decimal(str(rules["min_market_funds"]))
    configured_minimum = Decimal(configured["minimum_notional_usd"])
    capacity = Decimal(configured["available_capacity_base"])
    capacity_walk = normalized["depth_walks"].get(str(capacity))
    fields = {
        "half_spread_bps": {"truth_class": "OBSERVED", "observed": str(spread_half), "configured": str(configured_half), "result": "SUPPORTED_AS_CONSERVATIVE_BOUND_SINGLE_SNAPSHOT" if spread_half <= configured_half else "REJECTED_BY_SNAPSHOT"},
        "slippage_bps": {"truth_class": "OBSERVED_DEPTH_DERIVED", "observed_max": str(max(slippage_values)), "configured": str(configured_slippage), "all_depth_sufficient": all_depth_sufficient, "result": "SUPPORTED_AS_CONSERVATIVE_BOUND_SINGLE_SNAPSHOT" if all_depth_sufficient and max(slippage_values) <= configured_slippage else "REJECTED_BY_SNAPSHOT"},
        "quantity_step": {"truth_class": "OBSERVED_PRODUCT_RULE", "observed": str(venue_quantity_step), "configured": str(configured_quantity_step), "result": "SUPPORTED_CONSERVATIVE_NOT_VENUE_EXACT" if configured_quantity_step >= venue_quantity_step and configured_quantity_step % venue_quantity_step == 0 else "REJECTED_BY_VENUE_RULE"},
        "price_step": {"truth_class": "OBSERVED_PRODUCT_RULE", "observed": str(venue_price_step), "configured": str(configured_price_step), "result": "MATCHED" if configured_price_step == venue_price_step else "REJECTED_BY_VENUE_RULE"},
        "minimum_notional_usd": {"truth_class": "OBSERVED_PRODUCT_RULE", "observed": str(venue_minimum), "configured": str(configured_minimum), "result": "SUPPORTED_CONSERVATIVE_NOT_VENUE_EXACT" if configured_minimum >= venue_minimum else "REJECTED_BY_VENUE_RULE"},
        "available_capacity_base": {"truth_class": "OBSERVED_DEPTH_DERIVED", "observed": str(capacity), "configured": str(capacity), "result": "SUPPORTED_SINGLE_SNAPSHOT" if capacity_walk and all(side["sufficient_depth"] for side in capacity_walk.values()) else "REJECTED_BY_SNAPSHOT"},
    }
    for name in ("fee_bps", "latency_ms", "fill_ratio"):
        fields[name] = {"truth_class": "CONFIGURED", "configured": configured[name], "observed": None, "result": "UNAVAILABLE_FOR_QUALIFICATION"}
    return {"schema_version": 1, "observation_id": observation["observation_id"], "result": "PARTIALLY_CALIBRATED_SINGLE_SNAPSHOT", "fields": fields, "capital_or_live_authority_earned": False, "economic_edge_evidence": False}
