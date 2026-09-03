from __future__ import annotations

from decimal import Decimal
from statistics import median
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..evaluation.competence import CompetenceProfile
from ..models.registry import ModelRegistry, validate_model_registry
from ..operations import canonical_hash
from ..prediction.contracts import Prediction
from ..prediction.factory import create_prediction
from ..representation.contracts import RepresentationFrame
from .contracts import AssemblyReceipt, INTERVAL_POLICY_ID, WEIGHT_POLICY_ID


class AdaptiveAssemblyError(RuntimeError):
    pass


def _clamp(value: Decimal, low: Decimal = Decimal("0"), high: Decimal = Decimal("1")) -> Decimal:
    return min(high, max(low, value))


def _registry_record_at(registry: ModelRegistry, model_ref: str, as_of_ns: int) -> Mapping[str, Any]:
    errors = validate_model_registry(registry.root, require_state=False)
    if errors:
        raise AdaptiveAssemblyError("model registry invalid: " + "; ".join(errors))
    as_of = int(as_of_ns)
    record: Optional[Dict[str, Any]] = None
    for event in registry.events():
        if str(event.get("model_ref", "")) != model_ref:
            continue
        occurred = int(event.get("occurred_at_ns", -1))
        if occurred > as_of:
            continue
        payload = event.get("payload", {})
        if event.get("event_type") == "MODEL_REGISTERED":
            record = {
                "model_ref": model_ref,
                "state": "CANDIDATE",
                "definition_hash": str(payload.get("definition_hash", "")),
                "artifact_hash": str(payload.get("artifact_hash", "")),
                "registry_event_hash": str(event.get("event_hash", "")),
                "updated_at_ns": occurred,
            }
        elif event.get("event_type") == "MODEL_TRANSITION" and record is not None:
            record["state"] = str(payload.get("to_state", ""))
            record["registry_event_hash"] = str(event.get("event_hash", ""))
            record["updated_at_ns"] = occurred
    if record is None:
        raise AdaptiveAssemblyError("model %s was not registered by assembly time" % model_ref)
    return record


def _eligible_state(state: str, mode: str) -> bool:
    if mode == "PROSPECTIVE_SHADOW":
        return state in {"SHADOW", "QUALIFIED"}
    if mode == "HISTORICAL_REPLAY":
        return state not in {"QUARANTINED", "SUPERSEDED"}
    return False


def _select_profile(
    profiles: Sequence[CompetenceProfile],
    prediction: Prediction,
    assembly_at_ns: int,
) -> Optional[CompetenceProfile]:
    model_ref = prediction.model_refs[0]
    component_hash = prediction.content_hash()
    candidates = [
        profile
        for profile in profiles
        if profile.model_ref == model_ref
        and profile.instrument_id == prediction.instrument.canonical_id
        and profile.horizon_ns == prediction.horizon_ns
        and profile.target_metric == prediction.target_metric
        and profile.evidence_class == prediction.evidence_class
        and profile.as_of_ns <= assembly_at_ns
        and component_hash not in profile.prediction_hashes
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item.as_of_ns, item.content_hash()))
    latest_as_of = candidates[-1].as_of_ns
    latest = [item for item in candidates if item.as_of_ns == latest_as_of]
    hashes = {item.content_hash() for item in latest}
    if len(hashes) != 1:
        raise AdaptiveAssemblyError("ambiguous competence profiles exist at the same as_of_ns")
    return latest[-1]


def _profile_diagnostics(profile: Optional[CompetenceProfile]) -> Mapping[str, Any]:
    if profile is None:
        return {
            "competence_profile_hash": None,
            "competence_as_of_ns": None,
            "resolved_count": 0,
            "sample_strength": Decimal("0"),
            "mae_bps": None,
            "directional_accuracy": None,
            "brier_score": None,
            "calibration_gap": None,
        }
    sample_strength = Decimal(str(profile.metrics.get("sample_strength", "0")))
    if sample_strength < 0 or sample_strength > 1:
        raise AdaptiveAssemblyError("competence sample_strength lies outside 0..1")
    return {
        "competence_profile_hash": profile.content_hash(),
        "competence_as_of_ns": profile.as_of_ns,
        "resolved_count": profile.resolved_count,
        "sample_strength": sample_strength,
        "mae_bps": None if profile.metrics.get("mean_absolute_error_bps") is None else Decimal(str(profile.metrics["mean_absolute_error_bps"])),
        "directional_accuracy": None if profile.metrics.get("directional_accuracy") is None else Decimal(str(profile.metrics["directional_accuracy"])),
        "brier_score": None if profile.metrics.get("brier_score") is None else Decimal(str(profile.metrics["brier_score"])),
        "calibration_gap": None if profile.metrics.get("calibration_gap") is None else Decimal(str(profile.metrics["calibration_gap"])),
    }


def _error_scale(diagnostics: Sequence[Mapping[str, Any]]) -> Optional[Decimal]:
    values = [item["mae_bps"] for item in diagnostics if item.get("resolved_count", 0) > 0 and item.get("mae_bps") is not None]
    if not values:
        return None
    return Decimal(str(median(values)))


def _skill(diagnostic: Mapping[str, Any], error_scale: Optional[Decimal]) -> Optional[Decimal]:
    if int(diagnostic.get("resolved_count", 0)) <= 0:
        return None
    required = (
        diagnostic.get("directional_accuracy"),
        diagnostic.get("brier_score"),
        diagnostic.get("calibration_gap"),
        diagnostic.get("mae_bps"),
    )
    if any(value is None for value in required):
        raise AdaptiveAssemblyError("resolved competence profile lacks required reliability metrics")

    direction = _clamp(Decimal("2") * diagnostic["directional_accuracy"] - Decimal("1"))
    brier = _clamp((Decimal("0.25") - diagnostic["brier_score"]) / Decimal("0.25"))
    calibration = _clamp(Decimal("1") - Decimal("2") * diagnostic["calibration_gap"])
    mae = diagnostic["mae_bps"]
    if error_scale is None:
        error = Decimal("0.5")
    elif error_scale == 0:
        error = Decimal("1") if mae == 0 else Decimal("0")
    else:
        error = _clamp(error_scale / (error_scale + mae)) if mae >= 0 else Decimal("0")
    return (direction + brier + calibration + error) / Decimal("4")


def _raw_weight_score(sample_strength: Decimal, skill: Optional[Decimal]) -> Decimal:
    if skill is None or sample_strength == 0:
        return Decimal("1")
    # Conservative evidence shrinkage. With no evidence every model receives 1.
    # Mature poor evidence can reduce only to 0.5; mature strong evidence can
    # increase only to 1.5. This prevents one short run from monopolizing the ensemble.
    score = Decimal("1") + Decimal("0.5") * sample_strength * (Decimal("2") * skill - Decimal("1"))
    return min(Decimal("1.5"), max(Decimal("0.5"), score))


def _validate_components(frame: RepresentationFrame, predictions: Sequence[Prediction], assembly_at_ns: int) -> Tuple[Prediction, ...]:
    if len(predictions) < 2:
        raise AdaptiveAssemblyError("adaptive assembly requires at least two component predictions")
    ordered = tuple(sorted(predictions, key=lambda item: item.model_refs))
    model_refs: List[str] = []
    first = ordered[0]
    frame_hash = frame.content_hash()
    for prediction in ordered:
        if len(prediction.model_refs) != 1:
            raise AdaptiveAssemblyError("component predictions must each belong to exactly one model")
        model_ref = prediction.model_refs[0]
        if model_ref in model_refs:
            raise AdaptiveAssemblyError("duplicate model component")
        model_refs.append(model_ref)
        if prediction.instrument != frame.instrument:
            raise AdaptiveAssemblyError("component instrument does not match representation")
        if prediction.representation_frame_id != frame.frame_id or prediction.representation_content_hash != frame_hash:
            raise AdaptiveAssemblyError("component prediction does not bind the exact representation")
        if prediction.representation_status != frame.status:
            raise AdaptiveAssemblyError("component representation status mismatch")
        comparisons = (
            (prediction.mode == first.mode, "mode"),
            (prediction.evidence_class == first.evidence_class, "evidence_class"),
            (prediction.prediction_at_ns == first.prediction_at_ns, "prediction_at_ns"),
            (prediction.horizon_ns == first.horizon_ns, "horizon_ns"),
            (prediction.resolves_at_ns == first.resolves_at_ns, "resolves_at_ns"),
            (prediction.target_metric == first.target_metric, "target_metric"),
            (prediction.reference_price == first.reference_price, "reference_price"),
            (prediction.reference_price_source == first.reference_price_source, "reference_price_source"),
        )
        failed = [field for passed, field in comparisons if not passed]
        if failed:
            raise AdaptiveAssemblyError("component prediction contract mismatch: %s" % ", ".join(failed))
        if prediction.created_at_ns > assembly_at_ns:
            raise AdaptiveAssemblyError("assembly cannot use a component created in its future")
    if first.mode == "PROSPECTIVE_SHADOW":
        if frame.status != "QUALIFIED":
            raise AdaptiveAssemblyError("prospective assembly requires QUALIFIED representation")
        if assembly_at_ns >= first.resolves_at_ns:
            raise AdaptiveAssemblyError("prospective assembly must occur before resolution")
    return ordered


def assemble_prediction(
    frame: RepresentationFrame,
    component_predictions: Sequence[Prediction],
    competence_profiles: Sequence[CompetenceProfile],
    registry: ModelRegistry,
    *,
    assembly_at_ns: int,
) -> Tuple[Prediction, AssemblyReceipt]:
    """Build a deterministic, evidence-weighted prediction without static authority.

    Registry state is evaluated at assembly time, not at the current wall clock.
    Competence is matched to the exact model/instrument/horizon/target/evidence
    segment and may not include the component prediction being assembled.
    """
    assembly_at = int(assembly_at_ns)
    if assembly_at < 0:
        raise AdaptiveAssemblyError("assembly_at_ns must be non-negative")
    components = _validate_components(frame, component_predictions, assembly_at)
    mode = components[0].mode

    provisional: List[Dict[str, Any]] = []
    diagnostics: List[Mapping[str, Any]] = []
    for prediction in components:
        model_ref = prediction.model_refs[0]
        registry_record = _registry_record_at(registry, model_ref, assembly_at)
        if not _eligible_state(str(registry_record["state"]), mode):
            raise AdaptiveAssemblyError(
                "model %s is not eligible for %s assembly at %d (state=%s)"
                % (model_ref, mode, assembly_at, registry_record["state"])
            )
        profile = _select_profile(competence_profiles, prediction, assembly_at)
        diagnostic = _profile_diagnostics(profile)
        diagnostics.append(diagnostic)
        provisional.append(
            {
                "prediction": prediction,
                "registry": registry_record,
                "profile": profile,
                "diagnostic": diagnostic,
            }
        )

    scale = _error_scale(diagnostics)
    raw_scores: List[Decimal] = []
    for item in provisional:
        diagnostic = item["diagnostic"]
        skill = _skill(diagnostic, scale)
        item["skill"] = skill
        score = _raw_weight_score(diagnostic["sample_strength"], skill)
        item["raw_score"] = score
        raw_scores.append(score)
    total_score = sum(raw_scores, Decimal("0"))
    if total_score <= 0:
        raise AdaptiveAssemblyError("adaptive weight score total must be positive")

    weights: List[Decimal] = []
    running = Decimal("0")
    for index, score in enumerate(raw_scores):
        if index == len(raw_scores) - 1:
            weight = Decimal("1") - running
        else:
            weight = score / total_score
            running += weight
        weights.append(weight)

    expected = sum(
        Decimal(item["prediction"].expected_move_bps) * weight
        for item, weight in zip(provisional, weights)
    )
    probability = sum(
        Decimal(item["prediction"].probability_positive) * weight
        for item, weight in zip(provisional, weights)
    )
    all_intervals = all(
        item["prediction"].interval_low_bps is not None and item["prediction"].interval_high_bps is not None
        for item in provisional
    )
    interval_low = None
    interval_high = None
    if all_intervals:
        interval_low = min(Decimal(item["prediction"].interval_low_bps) for item in provisional)
        interval_high = max(Decimal(item["prediction"].interval_high_bps) for item in provisional)

    model_refs = tuple(item["prediction"].model_refs[0] for item in provisional)
    assembled = create_prediction(
        frame,
        mode=mode,
        prediction_at_ns=components[0].prediction_at_ns,
        created_at_ns=assembly_at,
        horizon_ns=components[0].horizon_ns,
        expected_move_bps=expected,
        probability_positive=probability,
        interval_low_bps=interval_low,
        interval_high_bps=interval_high,
        model_refs=model_refs,
    )

    contributors: List[Mapping[str, Any]] = []
    for item, weight in zip(provisional, weights):
        prediction = item["prediction"]
        registry_record = item["registry"]
        diagnostic = item["diagnostic"]
        skill = item["skill"]
        contributors.append(
            {
                "model_ref": prediction.model_refs[0],
                "registry_state": registry_record["state"],
                "registry_event_hash": registry_record["registry_event_hash"],
                "model_definition_hash": registry_record["definition_hash"],
                "model_artifact_hash": registry_record["artifact_hash"],
                "component_prediction_id": prediction.prediction_id,
                "component_prediction_hash": prediction.content_hash(),
                "competence_profile_hash": diagnostic["competence_profile_hash"],
                "competence_as_of_ns": diagnostic["competence_as_of_ns"],
                "competence_status": "MATCHED" if item["profile"] is not None else "NO_PRIOR_MATCHED_COMPETENCE",
                "resolved_count": diagnostic["resolved_count"],
                "sample_strength": format(diagnostic["sample_strength"], "f"),
                "skill": None if skill is None else format(skill, "f"),
                "mae_bps": None if diagnostic["mae_bps"] is None else format(diagnostic["mae_bps"], "f"),
                "raw_weight_score": format(item["raw_score"], "f"),
                "normalized_weight": format(weight, "f"),
            }
        )

    receipt_material = {
        "assembly_at_ns": assembly_at,
        "assembled_prediction_hash": assembled.content_hash(),
        "contributors": contributors,
        "weight_policy_id": WEIGHT_POLICY_ID,
        "interval_policy_id": INTERVAL_POLICY_ID,
    }
    receipt_id = "ASM-%s" % canonical_hash(receipt_material)[:32]
    receipt = AssemblyReceipt(
        receipt_id=receipt_id,
        assembly_at_ns=assembly_at,
        mode=assembled.mode,
        evidence_class=assembled.evidence_class,
        representation_frame_id=frame.frame_id,
        representation_content_hash=frame.content_hash(),
        prediction_at_ns=assembled.prediction_at_ns,
        horizon_ns=assembled.horizon_ns,
        resolves_at_ns=assembled.resolves_at_ns,
        target_metric=assembled.target_metric,
        assembled_prediction_id=assembled.prediction_id,
        assembled_prediction_content_hash=assembled.content_hash(),
        contributors=tuple(contributors),
    )
    return assembled, receipt
