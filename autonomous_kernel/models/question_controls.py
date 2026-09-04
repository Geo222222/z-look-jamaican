"""Transparent question-bound baseline experts for prospective shadow learning.

These implementations are deliberately simple and auditable. They are candidate
experts, not empirical competence claims. Every output is a forecast answer for
one immutable QuestionDefinition and has no capital/risk/execution authority.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, Mapping, Optional, Sequence, Tuple

from ..experience.market_wide import MarketWideExperienceState
from ..questions.contracts import QuestionDefinition, QuestionRegistrySnapshot
from ..representation.contracts import RepresentationFrame
from .question_experts import (
    QuestionExpertDefinition,
    QuestionExpertError,
    bind_question,
    validate_expert_question_compatibility,
)


DIRECTION_QUESTION_ID = "ECONOMIC_ROOT_DIRECTION_10S"
FRAGILITY_QUESTION_ID = "ECONOMIC_ROOT_FRAGILITY_MAE_60S"
SUPPORTED_SUBJECT = "ASSET.BTC"


def _question(registry: QuestionRegistrySnapshot, question_id: str) -> QuestionDefinition:
    matches = [entry.definition for entry in registry.entries if entry.definition.question_id == question_id]
    if len(matches) != 1:
        raise QuestionExpertError("question registry does not contain one exact %s definition" % question_id)
    return matches[0]


def _definition(
    *,
    question: QuestionDefinition,
    expert_id: str,
    family: str,
    implementation_ref: str,
    feature_schema_id: str,
    parameters: Mapping[str, object],
) -> QuestionExpertDefinition:
    expert = QuestionExpertDefinition(
        expert_id=expert_id,
        version="1.0.0",
        family=family,
        implementation_ref=implementation_ref,
        implementation_version="1.0.0",
        question_bindings=(bind_question(question),),
        required_artifact_types=question.required_artifact_types,
        required_feature_families=question.required_feature_families,
        allowed_feature_families=question.required_feature_families,
        required_timescales=question.required_timescales,
        feature_schema_id=feature_schema_id,
        feature_schema_version="1.0.0",
        training_mode="NONE",
        training_data_cutoff_ns=None,
        training_completed_at_ns=None,
        supported_subject_ids=(SUPPORTED_SUBJECT,),
        parameters=dict(parameters),
    )
    validate_expert_question_compatibility(expert, question)
    return expert


def candidate_question_experts(registry: QuestionRegistrySnapshot) -> Tuple[QuestionExpertDefinition, ...]:
    direction = _question(registry, DIRECTION_QUESTION_ID)
    fragility = _question(registry, FRAGILITY_QUESTION_ID)
    return (
        _definition(
            question=direction,
            expert_id="DIRECTION-NULL-PRIOR",
            family="CONTROL_NULL_PRIOR",
            implementation_ref="autonomous_kernel.models.question_controls.direction_null_v1",
            feature_schema_id="ZLJ.QUESTION_FEATURES.DIRECTION.NULL",
            parameters={"probability_1": "0.5", "decision_rule": "STRICTLY_ABOVE_HALF_IS_POSITIVE"},
        ),
        _definition(
            question=direction,
            expert_id="DIRECTION-BOOK-IMBALANCE-LINEAR",
            family="MICROSTRUCTURE_BOOK_IMBALANCE_LINEAR",
            implementation_ref="autonomous_kernel.models.question_controls.direction_book_imbalance_linear_v1",
            feature_schema_id="ZLJ.QUESTION_FEATURES.DIRECTION.BOOK_IMBALANCE_10BPS",
            parameters={"depth_band_bps": 10, "probability_scale": "0.25", "probability_floor": "0.05", "probability_ceiling": "0.95"},
        ),
        _definition(
            question=direction,
            expert_id="DIRECTION-REPORTED-FLOW-LINEAR",
            family="REPORTED_TRADE_FLOW_LINEAR",
            implementation_ref="autonomous_kernel.models.question_controls.direction_reported_flow_linear_v1",
            feature_schema_id="ZLJ.QUESTION_FEATURES.DIRECTION.REPORTED_FLOW",
            parameters={"probability_scale": "0.20", "probability_floor": "0.05", "probability_ceiling": "0.95", "truth_class": "PROVIDER_REPORTED_SIDE_NOT_AGGRESSOR_INFERENCE"},
        ),
        _definition(
            question=fragility,
            expert_id="FRAGILITY-NULL-ZERO",
            family="CONTROL_ZERO_MAE",
            implementation_ref="autonomous_kernel.models.question_controls.fragility_zero_v1",
            feature_schema_id="ZLJ.QUESTION_FEATURES.FRAGILITY.NULL",
            parameters={"mae_bps": "0"},
        ),
        _definition(
            question=fragility,
            expert_id="FRAGILITY-TRAILING-VOLATILITY",
            family="TRAILING_MARKET_VOLATILITY_BASELINE",
            implementation_ref="autonomous_kernel.models.question_controls.fragility_trailing_volatility_v1",
            feature_schema_id="ZLJ.QUESTION_FEATURES.FRAGILITY.TRAILING_VOLATILITY",
            parameters={"forecast_rule": "CURRENT_MEDIAN_REALIZED_VOLATILITY_BPS_AS_MAE_BASELINE", "floor_bps": "0"},
        ),
    )


def _clamp_probability(value: Decimal) -> Decimal:
    return min(Decimal("0.95"), max(Decimal("0.05"), value))


def _venue_book_imbalances(frame: RepresentationFrame) -> Tuple[Decimal, ...]:
    output = []
    venues = frame.state.get("venue_states")
    if not isinstance(venues, Mapping):
        return ()
    for venue_state in venues.values():
        if not isinstance(venue_state, Mapping):
            continue
        book = venue_state.get("book")
        if not isinstance(book, Mapping) or book.get("status") != "QUALIFIED":
            continue
        bands = book.get("depth_bands_bps")
        band = bands.get("10") if isinstance(bands, Mapping) else None
        if isinstance(band, Mapping) and band.get("quote_notional_imbalance") is not None:
            output.append(Decimal(str(band["quote_notional_imbalance"])))
    return tuple(output)


def _reported_flow_ratio(frame: RepresentationFrame) -> Decimal:
    aggregate = frame.state.get("aggregate")
    flow = aggregate.get("trade_flow") if isinstance(aggregate, Mapping) else None
    if not isinstance(flow, Mapping):
        return Decimal("0")
    buy = Decimal(str(flow.get("reported_buy_quote_notional", "0")))
    sell = Decimal(str(flow.get("reported_sell_quote_notional", "0")))
    return Decimal("0") if buy + sell <= 0 else (buy - sell) / (buy + sell)


def _direction_answer(probability: Decimal) -> Dict[str, object]:
    probability = _clamp_probability(probability)
    return {
        "value": 1 if probability > Decimal("0.5") else 0,
        "probability_1": format(probability, "f"),
    }


def forecast_question_expert(
    expert: QuestionExpertDefinition,
    *,
    question: QuestionDefinition,
    instrument_state: RepresentationFrame,
    market_wide_experience: Optional[MarketWideExperienceState] = None,
) -> Dict[str, object]:
    """Return one deterministic candidate-expert answer.

    Missing evidence is rejected. The caller decides whether that expert is
    eligible for the current prospective cycle; this function never degrades a
    required input into an invented value.
    """
    validate_expert_question_compatibility(expert, question)
    if expert.supported_subject_ids and SUPPORTED_SUBJECT not in expert.supported_subject_ids:
        raise QuestionExpertError("expert does not support BTC economic root")
    if instrument_state.representation_type != "INSTRUMENT_STATE" or instrument_state.status != "QUALIFIED":
        raise QuestionExpertError("question expert requires qualified instrument state")
    if instrument_state.instrument.canonical_id != "CRYPTO.SPOT.BTC-USD":
        raise QuestionExpertError("shadow expert v1 is restricted to canonical BTC-USD spot")

    if expert.expert_id == "DIRECTION-NULL-PRIOR":
        return _direction_answer(Decimal("0.5"))
    if expert.expert_id == "DIRECTION-BOOK-IMBALANCE-LINEAR":
        values = _venue_book_imbalances(instrument_state)
        if not values:
            raise QuestionExpertError("book-imbalance expert has no qualified 10-bps book imbalance")
        imbalance = sum(values, Decimal("0")) / Decimal(len(values))
        return _direction_answer(Decimal("0.5") + imbalance * Decimal("0.25"))
    if expert.expert_id == "DIRECTION-REPORTED-FLOW-LINEAR":
        return _direction_answer(Decimal("0.5") + _reported_flow_ratio(instrument_state) * Decimal("0.20"))
    if expert.expert_id == "FRAGILITY-NULL-ZERO":
        if market_wide_experience is None or market_wide_experience.status != "QUALIFIED":
            raise QuestionExpertError("fragility expert requires qualified market-wide experience")
        return {"value": "0"}
    if expert.expert_id == "FRAGILITY-TRAILING-VOLATILITY":
        if market_wide_experience is None or market_wide_experience.status != "QUALIFIED":
            raise QuestionExpertError("fragility expert requires qualified market-wide experience")
        current = market_wide_experience.state.get("current")
        market = current.get("market") if isinstance(current, Mapping) else None
        value = market.get("median_realized_volatility_bps") if isinstance(market, Mapping) else None
        if value is None:
            raise QuestionExpertError("market-wide experience has no current median realized volatility")
        amount = max(Decimal("0"), Decimal(str(value)))
        return {"value": format(amount, "f")}
    raise QuestionExpertError("no runtime implementation exists for expert %s" % expert.expert_id)
