"""Executable Magnitude 30s candidate baselines. These do not redefine resolver truth.

Coefficients are not claimed empirically optimal. Models must consume both
SPOT_MICROSTRUCTURE (instrument state) and MARKET_WIDE_CONTEXT (bridged
MARKET_WIDE_EXPERIENCE). They never allocate capital.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Mapping, Tuple

from ..experience.market_wide import MarketWideExperienceState
from ..representation.contracts import RepresentationFrame
from .baselines import BaselineModelError
from .contracts import ModelDefinition
from .liquidity_baselines import extract_liquidity_features


MAGNITUDE_HORIZON_NS = 30_000_000_000
MAGNITUDE_TARGET_METRIC = "AGGREGATE_MIDPOINT_RETURN_BPS_30S_V1"
COEFFICIENT_STATUS = "NOT_CLAIMED_EMPIRICALLY_OPTIMAL"


def _definition(model_id: str, family: str, parameters: Mapping[str, Any]) -> ModelDefinition:
    payload = {
        "coefficient_status": COEFFICIENT_STATUS,
        "capital_authority": False,
        "forecast_role": "SIGNED_MIDPOINT_RETURN_BPS_30S",
        **dict(parameters),
    }
    return ModelDefinition(
        model_id=model_id,
        version="1.0.0",
        family=family,
        lifecycle_state="CANDIDATE",
        required_representation_type="INSTRUMENT_STATE",
        target_metric=MAGNITUDE_TARGET_METRIC,
        supported_horizons_ns=(MAGNITUDE_HORIZON_NS,),
        parameters=payload,
    )


def _wire_decimal(value: Decimal) -> str:
    return format(value, "f")


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
        raise BaselineModelError("Magnitude models require a usable MARKET_WIDE_EXPERIENCE")
    if "MARKET_WIDE_CONTEXT" not in str(market_wide.state.get("feature_quality") or {}):
        quality = market_wide.state.get("feature_quality")
        if not isinstance(quality, Mapping):
            raise BaselineModelError("MARKET_WIDE_EXPERIENCE lacks feature_quality")


class BookContextBpsModel:
    definition = _definition(
        "BOOK-CONTEXT-BPS",
        "MICROSTRUCTURE_BOOK_IMBALANCE",
        {
            "implementation_class": "CANDIDATE_MODEL",
            "imbalance_scale_bps": "12",
            "context_scale": "0.15",
        },
    )

    def forecast_magnitude(
        self,
        frame: RepresentationFrame,
        market_wide: MarketWideExperienceState,
    ) -> Tuple[Decimal, Mapping[str, Any]]:
        _require_market_wide(market_wide)
        features = extract_liquidity_features(frame)
        imbalance = features["imbalance_signed"]
        context_return = _trajectory_last(market_wide, "aggregate_return_bps")
        expected = imbalance * Decimal(str(self.definition.parameters["imbalance_scale_bps"]))
        expected = expected + context_return * Decimal(str(self.definition.parameters["context_scale"]))
        return expected, {
            "model_id": self.definition.model_id,
            "imbalance_signed": _wire_decimal(imbalance),
            "market_wide_aggregate_return_bps": _wire_decimal(context_return),
            "market_wide_status": market_wide.status,
            "expected_move_bps": _wire_decimal(expected),
        }


class MarketWideDriftBpsModel:
    definition = _definition(
        "MARKET-WIDE-DRIFT-BPS",
        "CONDITIONAL_MEAN",
        {
            "implementation_class": "CANDIDATE_MODEL",
            "drift_scale": "0.50",
            "dispersion_dampen": "0.05",
        },
    )

    def forecast_magnitude(
        self,
        frame: RepresentationFrame,
        market_wide: MarketWideExperienceState,
    ) -> Tuple[Decimal, Mapping[str, Any]]:
        _require_market_wide(market_wide)
        if frame.representation_type != "INSTRUMENT_STATE":
            raise BaselineModelError("Magnitude models require INSTRUMENT_STATE")
        extract_liquidity_features(frame)
        drift = _trajectory_last(market_wide, "aggregate_return_bps")
        dispersion = _trajectory_last(market_wide, "cross_sectional_return_dispersion_bps")
        expected = drift * Decimal(str(self.definition.parameters["drift_scale"]))
        expected = expected - dispersion * Decimal(str(self.definition.parameters["dispersion_dampen"]))
        return expected, {
            "model_id": self.definition.model_id,
            "market_wide_aggregate_return_bps": _wire_decimal(drift),
            "market_wide_dispersion_bps": _wire_decimal(dispersion),
            "market_wide_status": market_wide.status,
            "expected_move_bps": _wire_decimal(expected),
        }


def magnitude_baseline_model_set() -> Tuple[Any, ...]:
    return (BookContextBpsModel(), MarketWideDriftBpsModel())
