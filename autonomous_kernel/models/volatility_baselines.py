"""Executable Volatility 60s candidate baselines. These do not redefine resolver truth.

Coefficients are engineering priors, not claimed empirically optimal. Models must
consume both SPOT_MICROSTRUCTURE and MARKET_WIDE_CONTEXT. Predicted realized
volatility is non-negative bps over the next 60 seconds.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Any, Dict, Mapping, Sequence, Tuple

from ..experience.market_wide import MarketWideExperienceState
from ..models.baselines import BaselineModelError
from ..models.contracts import ModelDefinition
from ..models.liquidity_baselines import extract_liquidity_features
from ..representation.contracts import RepresentationFrame


VOLATILITY_HORIZON_NS = 60_000_000_000
VOLATILITY_TARGET_METRIC = "REALIZED_AGGREGATE_MIDPOINT_VOLATILITY_BPS_60S_V1"
COEFFICIENT_STATUS = "NOT_CLAIMED_EMPIRICALLY_OPTIMAL"
NULL_PRIOR_BPS = "8"


def _definition(model_id: str, family: str, parameters: Mapping[str, Any]) -> ModelDefinition:
    payload = {
        "coefficient_status": COEFFICIENT_STATUS,
        "capital_authority": False,
        "forecast_role": "REALIZED_VOLATILITY_BPS_60S",
        **dict(parameters),
    }
    return ModelDefinition(
        model_id=model_id,
        version="1.0.0",
        family=family,
        lifecycle_state="CANDIDATE",
        required_representation_type="INSTRUMENT_STATE",
        target_metric=VOLATILITY_TARGET_METRIC,
        supported_horizons_ns=(VOLATILITY_HORIZON_NS,),
        parameters=payload,
    )


def _wire_decimal(value: Decimal) -> str:
    return format(value, "f")


def _nonneg(value: Decimal) -> Decimal:
    return value if value > 0 else Decimal("0")


def population_stdev_bps(returns: Sequence[Decimal]) -> Decimal:
    if not returns:
        return Decimal("0")
    with localcontext() as context:
        context.prec = 50
        mean = sum(returns, Decimal("0")) / Decimal(len(returns))
        variance = sum((value - mean) ** 2 for value in returns) / Decimal(len(returns))
        return +variance.sqrt()


def _trajectory_last(market_wide: MarketWideExperienceState, field: str) -> Decimal:
    trajectory = market_wide.state.get("trajectory")
    if not isinstance(trajectory, Mapping):
        return Decimal("0")
    item = trajectory.get(field)
    if not isinstance(item, Mapping) or item.get("last") is None:
        return Decimal("0")
    return Decimal(str(item["last"]))


def _require_market_wide(market_wide: MarketWideExperienceState) -> None:
    if market_wide.status == "UNAVAILABLE":
        raise BaselineModelError("Volatility models require a usable MARKET_WIDE_EXPERIENCE")
    quality = market_wide.state.get("feature_quality")
    if not isinstance(quality, Mapping):
        raise BaselineModelError("MARKET_WIDE_EXPERIENCE lacks feature_quality")


class VolatilityNullPriorModel:
    definition = _definition(
        "VOLATILITY-NULL-PRIOR",
        "NULL_PRIOR",
        {
            "implementation_class": "BENCHMARK",
            "neutral_volatility_bps": NULL_PRIOR_BPS,
            "parameter_version": "VOLATILITY_NULL_PRIOR_BPS_V1",
        },
    )

    def forecast_volatility(
        self,
        frame: RepresentationFrame,
        market_wide: MarketWideExperienceState,
        *,
        trailing_returns_bps: Sequence[Decimal] = (),
    ) -> Tuple[Decimal, Mapping[str, Any]]:
        del trailing_returns_bps
        _require_market_wide(market_wide)
        if frame.representation_type != "INSTRUMENT_STATE":
            raise BaselineModelError("Volatility models require INSTRUMENT_STATE")
        expected = Decimal(NULL_PRIOR_BPS)
        return expected, {
            "model_id": self.definition.model_id,
            "neutral_volatility_bps": NULL_PRIOR_BPS,
            "parameter_version": "VOLATILITY_NULL_PRIOR_BPS_V1",
            "market_wide_status": market_wide.status,
            "expected_volatility_bps": _wire_decimal(expected),
        }


class TrailingRealizedVolatilityModel:
    definition = _definition(
        "TRAILING-REALIZED-VOLATILITY",
        "TEMPORAL",
        {
            "implementation_class": "CANDIDATE_MODEL",
            "trailing_weight": "0.70",
            "context_weight": "0.30",
        },
    )

    def forecast_volatility(
        self,
        frame: RepresentationFrame,
        market_wide: MarketWideExperienceState,
        *,
        trailing_returns_bps: Sequence[Decimal] = (),
    ) -> Tuple[Decimal, Mapping[str, Any]]:
        _require_market_wide(market_wide)
        if frame.representation_type != "INSTRUMENT_STATE":
            raise BaselineModelError("Volatility models require INSTRUMENT_STATE")
        returns = tuple(Decimal(str(value)) for value in trailing_returns_bps)
        trailing = population_stdev_bps(returns)
        context_vol = _nonneg(_trajectory_last(market_wide, "median_realized_volatility_bps"))
        trailing_w = Decimal(str(self.definition.parameters["trailing_weight"]))
        context_w = Decimal(str(self.definition.parameters["context_weight"]))
        expected = _nonneg(trailing_w * trailing + context_w * context_vol)
        return expected, {
            "model_id": self.definition.model_id,
            "trailing_return_count": len(returns),
            "trailing_returns_bps": [_wire_decimal(value) for value in returns],
            "trailing_realized_vol_bps": _wire_decimal(trailing),
            "context_median_realized_volatility_bps": _wire_decimal(context_vol),
            "context_adjustment": _wire_decimal(context_w * context_vol),
            "market_wide_status": market_wide.status,
            "expected_volatility_bps": _wire_decimal(expected),
        }


class BookStressVolatilityModel:
    definition = _definition(
        "BOOK-STRESS-VOLATILITY",
        "ORDER_BOOK_PRESSURE",
        {
            "implementation_class": "CANDIDATE_MODEL",
            "base_bps": "3",
            "spread_scale": "1.20",
            "depth_scale": "6",
            "imbalance_scale": "4",
            "context_vol_scale": "0.25",
            "context_spread_scale": "0.15",
            "ref_quote_depth": "50000",
        },
    )

    def forecast_volatility(
        self,
        frame: RepresentationFrame,
        market_wide: MarketWideExperienceState,
        *,
        trailing_returns_bps: Sequence[Decimal] = (),
    ) -> Tuple[Decimal, Mapping[str, Any]]:
        del trailing_returns_bps
        _require_market_wide(market_wide)
        features = extract_liquidity_features(frame)
        spread = features["spread_bps"]
        depth = features["total_quote_depth_10bps"]
        imbalance = features["imbalance_magnitude"]
        ref_depth = Decimal(str(self.definition.parameters["ref_quote_depth"]))
        depth_stress = Decimal("1") - (depth / (depth + ref_depth))
        context_vol = _nonneg(_trajectory_last(market_wide, "median_realized_volatility_bps"))
        context_spread = _nonneg(_trajectory_last(market_wide, "median_spread_bps"))
        expected = _nonneg(
            Decimal(str(self.definition.parameters["base_bps"]))
            + Decimal(str(self.definition.parameters["spread_scale"])) * spread
            + Decimal(str(self.definition.parameters["depth_scale"])) * depth_stress
            + Decimal(str(self.definition.parameters["imbalance_scale"])) * imbalance
            + Decimal(str(self.definition.parameters["context_vol_scale"])) * context_vol
            + Decimal(str(self.definition.parameters["context_spread_scale"])) * context_spread
        )
        return expected, {
            "model_id": self.definition.model_id,
            "spread_bps": _wire_decimal(spread),
            "total_quote_depth_10bps": _wire_decimal(depth),
            "imbalance_magnitude": _wire_decimal(imbalance),
            "depth_stress": _wire_decimal(depth_stress),
            "context_median_realized_volatility_bps": _wire_decimal(context_vol),
            "context_median_spread_bps": _wire_decimal(context_spread),
            "market_wide_status": market_wide.status,
            "expected_volatility_bps": _wire_decimal(expected),
        }


def volatility_baseline_model_set() -> Tuple[Any, ...]:
    return (VolatilityNullPriorModel(), TrailingRealizedVolatilityModel(), BookStressVolatilityModel())
