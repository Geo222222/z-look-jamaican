"""Executable Liquidity candidate baselines. These do not redefine resolver truth.

Reported trade side is PROVIDER_REPORTED_SIDE_NOT_AGGRESSOR_INFERENCE. A trade-flow
Liquidity expert is deferred until aggressor consumption can be justified without
reinterpretation.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Mapping, Tuple

from ..evaluation.liquidity_resolver import _qualified_book_metrics
from ..evaluation.question_resolvers import QuestionResolverError
from ..representation.contracts import RepresentationFrame
from .baselines import BaselineModelError, _clamp_probability
from .contracts import ModelDefinition


LIQUIDITY_HORIZON_NS = 30_000_000_000
LIQUIDITY_TARGET_METRIC = "SPREAD_UP_AND_DEPTH10_DOWN_30S_V1"
COEFFICIENT_STATUS = "NOT_CLAIMED_EMPIRICALLY_OPTIMAL"


def _definition(model_id: str, family: str, parameters: Mapping[str, Any]) -> ModelDefinition:
    payload = {
        "coefficient_status": COEFFICIENT_STATUS,
        "capital_authority": False,
        "forecast_role": "P_LIQUIDITY_DETERIORATION_30S",
        **dict(parameters),
    }
    return ModelDefinition(
        model_id=model_id,
        version="1.0.0",
        family=family,
        lifecycle_state="CANDIDATE",
        required_representation_type="INSTRUMENT_STATE",
        target_metric=LIQUIDITY_TARGET_METRIC,
        supported_horizons_ns=(LIQUIDITY_HORIZON_NS,),
        parameters=payload,
    )


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def extract_liquidity_features(frame: RepresentationFrame) -> Dict[str, Any]:
    """Deterministic cutoff-known book features. No future deltas."""
    if frame.representation_type != "INSTRUMENT_STATE":
        raise BaselineModelError("Liquidity models require INSTRUMENT_STATE")
    try:
        venues, spread_bps, total_quote_depth = _qualified_book_metrics(frame)
    except QuestionResolverError as exc:
        raise BaselineModelError("cutoff book is not a legal Liquidity baseline: %s" % exc) from exc
    venue_states = frame.state.get("venue_states")
    bid_quote = Decimal("0")
    ask_quote = Decimal("0")
    venue_depths = []
    imbalances = []
    best_bid_quote = Decimal("0")
    best_ask_quote = Decimal("0")
    for venue in venues:
        book = venue_states[venue]["book"]
        band = book["depth_bands_bps"]["10"]
        bid_q = _decimal(band["bid_quote_notional"])
        ask_q = _decimal(band["ask_quote_notional"])
        bid_quote += bid_q
        ask_quote += ask_q
        venue_depths.append(bid_q + ask_q)
        total_side = bid_q + ask_q
        if total_side > 0:
            imbalances.append(abs(bid_q - ask_q) / total_side)
        bid_px = _decimal(book["best_bid"])
        ask_px = _decimal(book["best_ask"])
        bid_size = _decimal(book.get("best_bid_size") or "0")
        ask_size = _decimal(book.get("best_ask_size") or "0")
        best_bid_quote += bid_px * bid_size
        best_ask_quote += ask_px * ask_size
    total = bid_quote + ask_quote
    imbalance_signed = Decimal("0") if total == 0 else (bid_quote - ask_quote) / total
    imbalance_magnitude = abs(imbalance_signed)
    thin_side = min(bid_quote, ask_quote)
    thick_side = max(bid_quote, ask_quote)
    thin_side_ratio = Decimal("1") if thick_side == 0 else thin_side / thick_side
    top_quote = best_bid_quote + best_ask_quote
    top_concentration = Decimal("0") if total_quote_depth == 0 else min(Decimal("1"), top_quote / total_quote_depth)
    min_venue = min(venue_depths) if venue_depths else Decimal("0")
    max_venue = max(venue_depths) if venue_depths else Decimal("0")
    venue_depth_dispersion = Decimal("0") if max_venue == 0 else (Decimal("1") - (min_venue / max_venue))
    mean_imbalance = sum(imbalances, Decimal("0")) / Decimal(len(imbalances)) if imbalances else Decimal("0")
    return {
        "venues": venues,
        "qualified_venue_count": len(venues),
        "spread_bps": spread_bps,
        "total_quote_depth_10bps": total_quote_depth,
        "bid_quote_notional_10bps": bid_quote,
        "ask_quote_notional_10bps": ask_quote,
        "imbalance_signed": imbalance_signed,
        "imbalance_magnitude": imbalance_magnitude,
        "thin_side_ratio": thin_side_ratio,
        "top_concentration": top_concentration,
        "venue_depth_dispersion": venue_depth_dispersion,
        "mean_venue_imbalance": mean_imbalance,
    }


def _wire_decimal(value: Decimal) -> str:
    return format(value, "f")


class LiquidityNullPriorModel:
    definition = _definition(
        "LIQUIDITY-NULL-PRIOR",
        "SPREAD",
        {
            "implementation_class": "BENCHMARK",
            "neutral_probability": "0.5",
        },
    )

    def forecast_liquidity(self, frame: RepresentationFrame) -> Tuple[Decimal, Mapping[str, Any]]:
        extract_liquidity_features(frame)
        probability = Decimal("0.5")
        return probability, {
            "model_id": self.definition.model_id,
            "neutral_probability": "0.5",
            "final_deterioration_probability": _wire_decimal(probability),
        }


class SpreadDepthPressureModel:
    definition = _definition(
        "SPREAD-DEPTH-PRESSURE",
        "ORDER_BOOK_PRESSURE",
        {
            "implementation_class": "CANDIDATE_MODEL",
            "ref_spread_bps": "5",
            "ref_quote_depth": "50000",
            "spread_weight": "0.22",
            "depth_weight": "0.28",
            "venue_thinness_weight": "0.04",
        },
    )

    def forecast_liquidity(self, frame: RepresentationFrame) -> Tuple[Decimal, Mapping[str, Any]]:
        features = extract_liquidity_features(frame)
        ref_spread = Decimal(str(self.definition.parameters["ref_spread_bps"]))
        ref_depth = Decimal(str(self.definition.parameters["ref_quote_depth"]))
        spread_weight = Decimal(str(self.definition.parameters["spread_weight"]))
        depth_weight = Decimal(str(self.definition.parameters["depth_weight"]))
        venue_weight = Decimal(str(self.definition.parameters["venue_thinness_weight"]))
        observed_spread = features["spread_bps"]
        total_depth = features["total_quote_depth_10bps"]
        spread_pressure = (observed_spread / ref_spread) - Decimal("1")
        normalized_depth_pressure = Decimal("1") - (total_depth / (total_depth + ref_depth))
        venue_pressure = features["venue_depth_dispersion"]
        raw_score = (
            spread_weight * spread_pressure
            + depth_weight * normalized_depth_pressure
            + venue_weight * venue_pressure
        )
        probability = _clamp_probability(Decimal("0.5") + raw_score)
        return probability, {
            "model_id": self.definition.model_id,
            "observed_spread_bps": _wire_decimal(observed_spread),
            "total_quote_depth_10bps": _wire_decimal(total_depth),
            "qualified_venue_count": features["qualified_venue_count"],
            "spread_pressure": _wire_decimal(spread_pressure),
            "normalized_depth_pressure": _wire_decimal(normalized_depth_pressure),
            "venue_depth_dispersion": _wire_decimal(venue_pressure),
            "final_raw_score": _wire_decimal(raw_score),
            "final_deterioration_probability": _wire_decimal(probability),
        }


class BookDepletionStressModel:
    definition = _definition(
        "BOOK-DEPLETION-STRESS",
        "DEPTH",
        {
            "implementation_class": "CANDIDATE_MODEL",
            "imbalance_weight": "0.35",
            "thin_side_weight": "0.40",
            "top_concentration_weight": "0.15",
            "venue_local_weight": "0.10",
        },
    )

    def forecast_liquidity(self, frame: RepresentationFrame) -> Tuple[Decimal, Mapping[str, Any]]:
        features = extract_liquidity_features(frame)
        imbalance_w = Decimal(str(self.definition.parameters["imbalance_weight"]))
        thin_w = Decimal(str(self.definition.parameters["thin_side_weight"]))
        top_w = Decimal(str(self.definition.parameters["top_concentration_weight"]))
        venue_w = Decimal(str(self.definition.parameters["venue_local_weight"]))
        imbalance_mag = features["imbalance_magnitude"]
        thin_side_ratio = features["thin_side_ratio"]
        thin_side_stress = Decimal("1") - thin_side_ratio
        top_concentration = features["top_concentration"]
        venue_local = features["venue_depth_dispersion"]
        depletion_score = (
            imbalance_w * imbalance_mag
            + thin_w * thin_side_stress
            + top_w * top_concentration
            + venue_w * venue_local
        )
        probability = _clamp_probability(Decimal("0.5") + depletion_score - Decimal("0.20"))
        return probability, {
            "model_id": self.definition.model_id,
            "bid_quote_notional_10bps": _wire_decimal(features["bid_quote_notional_10bps"]),
            "ask_quote_notional_10bps": _wire_decimal(features["ask_quote_notional_10bps"]),
            "total_quote_depth_10bps": _wire_decimal(features["total_quote_depth_10bps"]),
            "imbalance_magnitude": _wire_decimal(imbalance_mag),
            "thin_side_ratio": _wire_decimal(thin_side_ratio),
            "top_concentration": _wire_decimal(top_concentration),
            "venue_local_depletion": _wire_decimal(venue_local),
            "depletion_score": _wire_decimal(depletion_score),
            "final_deterioration_probability": _wire_decimal(probability),
        }


def liquidity_baseline_model_set() -> Tuple[Any, ...]:
    return (LiquidityNullPriorModel(), SpreadDepthPressureModel(), BookDepletionStressModel())
