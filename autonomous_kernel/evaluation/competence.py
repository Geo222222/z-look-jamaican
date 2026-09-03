from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from ..operations import canonical_hash
from ..prediction.contracts import Prediction
from ..prediction.journal import PredictionJournal, validate_prediction_journal
from .contracts import PredictionOutcome
from .journal import OutcomeJournal, validate_outcome_journal


COMPETENCE_SCHEMA_VERSION = "1.0"
SAMPLE_STRENGTH_PRIOR_COUNT = 50


class CompetenceError(RuntimeError):
    pass


def _mean(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _text(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True)
class CompetenceProfile:
    model_ref: str
    instrument_id: str
    horizon_ns: int
    target_metric: str
    evidence_class: str
    as_of_ns: int
    prediction_count: int
    resolved_count: int
    unresolvable_count: int
    pending_count: int
    metrics: Mapping[str, Any]
    prediction_hashes: Tuple[str, ...]
    outcome_hashes: Tuple[str, ...]
    schema_version: str = COMPETENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != COMPETENCE_SCHEMA_VERSION:
            raise CompetenceError("unsupported competence schema")
        if not self.model_ref or not self.instrument_id or not self.target_metric or not self.evidence_class:
            raise CompetenceError("competence identity fields are required")
        if self.horizon_ns <= 0 or self.as_of_ns < 0:
            raise CompetenceError("competence timing is invalid")
        counts = (self.prediction_count, self.resolved_count, self.unresolvable_count, self.pending_count)
        if any(value < 0 for value in counts):
            raise CompetenceError("competence counts cannot be negative")
        if self.resolved_count + self.unresolvable_count + self.pending_count != self.prediction_count:
            raise CompetenceError("competence outcome counts must partition predictions")
        if len(self.prediction_hashes) != self.prediction_count or len(set(self.prediction_hashes)) != len(self.prediction_hashes):
            raise CompetenceError("prediction lineage must match prediction_count")
        if len(self.outcome_hashes) != self.resolved_count + self.unresolvable_count or len(set(self.outcome_hashes)) != len(self.outcome_hashes):
            raise CompetenceError("outcome lineage must match final outcomes")
        if not isinstance(self.metrics, Mapping):
            raise CompetenceError("competence metrics must be a mapping")

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "identity": {
                "model_ref": self.model_ref,
                "instrument_id": self.instrument_id,
                "horizon_ns": int(self.horizon_ns),
                "target_metric": self.target_metric,
                "evidence_class": self.evidence_class,
            },
            "as_of_ns": int(self.as_of_ns),
            "counts": {
                "prediction_count": int(self.prediction_count),
                "resolved_count": int(self.resolved_count),
                "unresolvable_count": int(self.unresolvable_count),
                "pending_count": int(self.pending_count),
            },
            "metrics": dict(self.metrics),
            "lineage": {
                "prediction_hashes": list(self.prediction_hashes),
                "outcome_hashes": list(self.outcome_hashes),
                "prediction_set_hash": canonical_hash({"hashes": list(self.prediction_hashes)}),
                "outcome_set_hash": canonical_hash({"hashes": list(self.outcome_hashes)}),
            },
        }

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        value = self.body()
        value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}
        return value


def _profile_metrics(items: Sequence[Tuple[Prediction, PredictionOutcome]]) -> Mapping[str, Any]:
    if not items:
        return {
            "mean_absolute_error_bps": None,
            "root_mean_squared_error_bps": None,
            "mean_forecast_bias_bps": None,
            "directional_accuracy": None,
            "brier_score": None,
            "mean_probability_positive": None,
            "actual_positive_rate": None,
            "calibration_gap": None,
            "interval_coverage": None,
            "sample_strength": "0",
            "sample_strength_prior_count": SAMPLE_STRENGTH_PRIOR_COUNT,
            "direction_rule": "EXPECTED_MOVE_STRICTLY_POSITIVE_V1",
        }

    errors: List[Decimal] = []
    abs_errors: List[Decimal] = []
    squared_errors: List[Decimal] = []
    probabilities: List[Decimal] = []
    actuals: List[Decimal] = []
    direction_hits: List[Decimal] = []
    interval_hits: List[Decimal] = []

    for prediction, outcome in items:
        realized = Decimal(str(outcome.realized_return_bps))
        expected = Decimal(prediction.expected_move_bps)
        error = realized - expected
        errors.append(error)
        abs_errors.append(abs(error))
        squared_errors.append(error * error)
        probability = Decimal(prediction.probability_positive)
        actual = Decimal(int(outcome.actual_positive))
        probabilities.append(probability)
        actuals.append(actual)
        predicted_positive = expected > 0
        direction_hits.append(Decimal("1") if predicted_positive == bool(outcome.actual_positive) else Decimal("0"))
        if prediction.interval_low_bps is not None and prediction.interval_high_bps is not None:
            low = Decimal(prediction.interval_low_bps)
            high = Decimal(prediction.interval_high_bps)
            interval_hits.append(Decimal("1") if low <= realized <= high else Decimal("0"))

    mae = _mean(abs_errors)
    rmse = _mean(squared_errors).sqrt()
    bias = _mean(errors)
    direction = _mean(direction_hits)
    brier = _mean([(probability - actual) ** 2 for probability, actual in zip(probabilities, actuals)])
    mean_probability = _mean(probabilities)
    actual_rate = _mean(actuals)
    calibration_gap = abs(mean_probability - actual_rate)
    coverage = None if not interval_hits else _text(_mean(interval_hits))
    count = Decimal(len(items))
    sample_strength = count / (count + Decimal(SAMPLE_STRENGTH_PRIOR_COUNT))
    return {
        "mean_absolute_error_bps": _text(mae),
        "root_mean_squared_error_bps": _text(rmse),
        "mean_forecast_bias_bps": _text(bias),
        "directional_accuracy": _text(direction),
        "brier_score": _text(brier),
        "mean_probability_positive": _text(mean_probability),
        "actual_positive_rate": _text(actual_rate),
        "calibration_gap": _text(calibration_gap),
        "interval_coverage": coverage,
        "sample_strength": _text(sample_strength),
        "sample_strength_prior_count": SAMPLE_STRENGTH_PRIOR_COUNT,
        "direction_rule": "EXPECTED_MOVE_STRICTLY_POSITIVE_V1",
    }


def build_competence_profiles(root: Path, *, as_of_ns: int) -> Tuple[CompetenceProfile, ...]:
    root = root.resolve()
    as_of = int(as_of_ns)
    if as_of < 0:
        raise CompetenceError("as_of_ns must be non-negative")
    prediction_errors = validate_prediction_journal(root)
    outcome_errors = validate_outcome_journal(root)
    if prediction_errors or outcome_errors:
        raise CompetenceError("evaluation sources invalid: " + "; ".join(prediction_errors + outcome_errors))

    predictions: Dict[str, Prediction] = {}
    prediction_hashes: Dict[str, str] = {}
    for entry in PredictionJournal(root).entries():
        prediction = Prediction.from_wire(entry["prediction"])
        if prediction.created_at_ns > as_of:
            continue
        predictions[prediction.prediction_id] = prediction
        prediction_hashes[prediction.prediction_id] = prediction.content_hash()

    outcomes: Dict[str, PredictionOutcome] = {}
    for entry in OutcomeJournal(root).entries():
        outcome = PredictionOutcome.from_wire(entry["outcome"])
        if outcome.decided_at_ns <= as_of:
            outcomes[outcome.prediction_id] = outcome

    groups: Dict[Tuple[str, str, int, str, str], List[Prediction]] = {}
    for prediction in predictions.values():
        if len(prediction.model_refs) != 1:
            continue
        key = (
            prediction.model_refs[0],
            prediction.instrument.canonical_id,
            prediction.horizon_ns,
            prediction.target_metric,
            prediction.evidence_class,
        )
        groups.setdefault(key, []).append(prediction)

    profiles: List[CompetenceProfile] = []
    for key in sorted(groups):
        model_ref, instrument_id, horizon_ns, target_metric, evidence_class = key
        group_predictions = sorted(groups[key], key=lambda item: (item.prediction_at_ns, item.prediction_id))
        resolved_pairs: List[Tuple[Prediction, PredictionOutcome]] = []
        final_outcomes: List[PredictionOutcome] = []
        unresolved_final_count = 0
        for prediction in group_predictions:
            outcome = outcomes.get(prediction.prediction_id)
            if outcome is None:
                continue
            final_outcomes.append(outcome)
            if outcome.status == "RESOLVED":
                resolved_pairs.append((prediction, outcome))
            else:
                unresolved_final_count += 1
        pending_count = len(group_predictions) - len(final_outcomes)
        profiles.append(
            CompetenceProfile(
                model_ref=model_ref,
                instrument_id=instrument_id,
                horizon_ns=horizon_ns,
                target_metric=target_metric,
                evidence_class=evidence_class,
                as_of_ns=as_of,
                prediction_count=len(group_predictions),
                resolved_count=len(resolved_pairs),
                unresolvable_count=unresolved_final_count,
                pending_count=pending_count,
                metrics=_profile_metrics(resolved_pairs),
                prediction_hashes=tuple(prediction_hashes[item.prediction_id] for item in group_predictions),
                outcome_hashes=tuple(outcome.content_hash() for outcome in sorted(final_outcomes, key=lambda item: item.prediction_id)),
            )
        )
    return tuple(profiles)
