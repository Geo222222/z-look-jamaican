from __future__ import annotations

from decimal import Decimal
from typing import Iterable, List, Sequence, Tuple

from ..prediction import Prediction, create_prediction
from ..representation import RepresentationFrame
from .contracts import ModelDefinition


DEFAULT_HORIZONS_NS = (10_000_000_000, 30_000_000_000, 90_000_000_000)


class BaselineModelError(ValueError):
    pass


def _clamp_probability(value: Decimal) -> Decimal:
    return min(Decimal("0.95"), max(Decimal("0.05"), value))


def _definition(model_id: str, family: str, parameters: dict) -> ModelDefinition:
    return ModelDefinition(
        model_id=model_id,
        version="1.0.0",
        family=family,
        lifecycle_state="CANDIDATE",
        required_representation_type="INSTRUMENT_STATE",
        target_metric="ZLJ_AGGREGATE_MIDPOINT_RETURN_BPS_V1",
        supported_horizons_ns=DEFAULT_HORIZONS_NS,
        parameters=parameters,
    )


class _BaselineModel:
    definition: ModelDefinition

    def forecast(self, frame: RepresentationFrame, horizon_ns: int) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
        raise NotImplementedError

    def predict(
        self,
        frame: RepresentationFrame,
        *,
        mode: str,
        prediction_at_ns: int,
        created_at_ns: int,
        horizon_ns: int,
    ) -> Prediction:
        horizon = int(horizon_ns)
        if frame.representation_type != self.definition.required_representation_type:
            raise BaselineModelError("model requires %s" % self.definition.required_representation_type)
        if horizon not in self.definition.supported_horizons_ns:
            raise BaselineModelError("unsupported model horizon")
        expected, probability, low, high = self.forecast(frame, horizon)
        return create_prediction(
            frame,
            mode=mode,
            prediction_at_ns=prediction_at_ns,
            created_at_ns=created_at_ns,
            horizon_ns=horizon,
            expected_move_bps=expected,
            probability_positive=probability,
            interval_low_bps=low,
            interval_high_bps=high,
            model_refs=(self.definition.model_ref,),
        )


class NullPriorModel(_BaselineModel):
    definition = _definition(
        "NULL-PRIOR",
        "NULL_PRIOR",
        {"expected_move_bps": "0", "probability_positive": "0.5", "interval_bps": "20"},
    )

    def forecast(self, frame: RepresentationFrame, horizon_ns: int) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
        return Decimal("0"), Decimal("0.5"), Decimal("-20"), Decimal("20")


class BookImbalanceLinearModel(_BaselineModel):
    definition = _definition(
        "BOOK-IMBALANCE-LINEAR",
        "MICROSTRUCTURE_BOOK_IMBALANCE",
        {"depth_band_bps": 10, "move_scale_bps": "12", "probability_scale": "0.25", "interval_bps": "18"},
    )

    def forecast(self, frame: RepresentationFrame, horizon_ns: int) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
        values: List[Decimal] = []
        venue_states = frame.state.get("venue_states", {})
        if isinstance(venue_states, dict):
            for venue_state in venue_states.values():
                if not isinstance(venue_state, dict):
                    continue
                book = venue_state.get("book", {})
                if not isinstance(book, dict) or book.get("status") != "QUALIFIED":
                    continue
                bands = book.get("depth_bands_bps", {})
                if not isinstance(bands, dict):
                    continue
                band = bands.get("10")
                if not isinstance(band, dict) or band.get("quote_notional_imbalance") is None:
                    continue
                values.append(Decimal(str(band["quote_notional_imbalance"])))
        imbalance = sum(values, Decimal("0")) / Decimal(len(values)) if values else Decimal("0")
        expected = imbalance * Decimal("12")
        probability = _clamp_probability(Decimal("0.5") + imbalance * Decimal("0.25"))
        interval = Decimal("18")
        return expected, probability, expected - interval, expected + interval


class ReportedFlowLinearModel(_BaselineModel):
    definition = _definition(
        "REPORTED-FLOW-LINEAR",
        "REPORTED_TRADE_FLOW",
        {"move_scale_bps": "10", "probability_scale": "0.20", "interval_bps": "22"},
    )

    def forecast(self, frame: RepresentationFrame, horizon_ns: int) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
        aggregate = frame.state.get("aggregate", {})
        flow = aggregate.get("trade_flow", {}) if isinstance(aggregate, dict) else {}
        if not isinstance(flow, dict):
            flow = {}
        buy = Decimal(str(flow.get("reported_buy_quote_notional", "0")))
        sell = Decimal(str(flow.get("reported_sell_quote_notional", "0")))
        denominator = buy + sell
        ratio = Decimal("0") if denominator <= 0 else (buy - sell) / denominator
        expected = ratio * Decimal("10")
        probability = _clamp_probability(Decimal("0.5") + ratio * Decimal("0.20"))
        interval = Decimal("22")
        return expected, probability, expected - interval, expected + interval


def baseline_model_set() -> Tuple[_BaselineModel, ...]:
    return (NullPriorModel(), BookImbalanceLinearModel(), ReportedFlowLinearModel())


def run_baseline_models(
    frame: RepresentationFrame,
    *,
    mode: str,
    prediction_at_ns: int,
    created_at_ns: int,
    horizon_ns: int,
    models: Sequence[_BaselineModel] = (),
) -> Tuple[Prediction, ...]:
    active = tuple(models) if models else baseline_model_set()
    return tuple(
        model.predict(
            frame,
            mode=mode,
            prediction_at_ns=prediction_at_ns,
            created_at_ns=created_at_ns,
            horizon_ns=horizon_ns,
        )
        for model in active
    )
