from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

from ..models.baselines import baseline_model_set
from ..models.liquidity_baselines import liquidity_baseline_model_set
from ..models.magnitude_baselines import magnitude_baseline_model_set
from ..operations import canonical_hash
from ..prediction.question_bound import QuestionBoundPrediction
from ..questions.catalog import question_catalog_v1
from .contracts import build_expert_claim, build_expert_contract, validate_expert_contract


class ExpertAdapterError(ValueError):
    pass


DIRECTION_REF = "ECONOMIC_ROOT_DIRECTION_10S@1.0.0"
MAGNITUDE_REF = "ECONOMIC_ROOT_MAGNITUDE_30S@1.0.0"
LIQUIDITY_REF = "ECONOMIC_ROOT_LIQUIDITY_DETERIORATION_30S@1.0.0"
LIQUIDITY_MODEL_IDS = {
    "LIQUIDITY-NULL-PRIOR": "BENCHMARK",
    "SPREAD-DEPTH-PRESSURE": "CANDIDATE_MODEL",
    "BOOK-DEPLETION-STRESS": "CANDIDATE_MODEL",
}
MAGNITUDE_MODEL_IDS = {
    "BOOK-CONTEXT-BPS": "CANDIDATE_MODEL",
    "MARKET-WIDE-DRIFT-BPS": "CANDIDATE_MODEL",
}


def _question_map() -> Dict[str, Any]:
    return {question.question_ref: question for question in question_catalog_v1()}


def _roles_for_model(model: Any) -> Tuple[Tuple[str, str, str], ...]:
    """Return only roles justified by the model's actual current inputs.

    Null prior is permitted as an explicit BENCHMARK on Direction and Magnitude.
    Magnitude candidate models must consume MARKET_WIDE_CONTEXT through bridged
    MARKET_WIDE_EXPERIENCE. Direction microstructure baselines remain Direction-only.
    """
    model_id = str(model.definition.model_id)
    if model_id in LIQUIDITY_MODEL_IDS:
        return ((LIQUIDITY_REF, "LIQUIDITY", LIQUIDITY_MODEL_IDS[model_id]),)
    if model_id in MAGNITUDE_MODEL_IDS:
        return ((MAGNITUDE_REF, "MAGNITUDE", MAGNITUDE_MODEL_IDS[model_id]),)
    if model_id == "NULL-PRIOR":
        return ((DIRECTION_REF, "DIRECTION", "BENCHMARK"), (MAGNITUDE_REF, "MAGNITUDE", "BENCHMARK"))
    return ((DIRECTION_REF, "DIRECTION", "CANDIDATE_MODEL"),)


def implemented_baseline_expert_contracts() -> Tuple[Mapping[str, Any], ...]:
    """Return expert roles backed by executable code already in the repository.

    Curriculum presence is not implementation existence; implementation existence
    is not earned competence. Benchmarks are marked separately from candidate
    predictive models.
    """
    questions = _question_map()
    contracts: List[Mapping[str, Any]] = []
    for model in tuple(baseline_model_set()) + tuple(liquidity_baseline_model_set()) + tuple(magnitude_baseline_model_set()):
        model_ref = model.definition.model_ref
        if model.definition.model_id in LIQUIDITY_MODEL_IDS:
            module = "autonomous_kernel.models.liquidity_baselines"
        elif model.definition.model_id in MAGNITUDE_MODEL_IDS:
            module = "autonomous_kernel.models.magnitude_baselines"
        else:
            module = "autonomous_kernel.models.baselines"
        for question_ref, role, implementation_class in _roles_for_model(model):
            question = questions[question_ref]
            if int(question.horizon_ns) not in set(int(v) for v in model.definition.supported_horizons_ns):
                continue
            implementation_ref = "%s:%s" % (module, model.__class__.__name__)
            implementation_hash = canonical_hash({
                "implementation_ref": implementation_ref,
                "model_definition": model.definition.to_wire(),
                "expert_role": role,
                "implementation_class": implementation_class,
                "question_ref": question_ref,
            })
            contracts.append(build_expert_contract(
                expert_id="%s_%s_EXPERT" % (model.definition.model_id.replace("-", "_"), role),
                version="1.0.0",
                species=str(model.definition.family),
                implementation_ref=implementation_ref,
                implementation_hash=implementation_hash,
                model_refs=(model_ref,),
                question_refs=(question_ref,),
                required_artifact_types=question.required_artifact_types,
                allowed_feature_families=question.required_feature_families,
                parameters={
                    "source": "EXISTING_BASELINE_MODEL_V1",
                    "implementation_class": implementation_class,
                    "target_metric": model.definition.target_metric,
                },
            ))
    return tuple(contracts)


def operational_expert_inventory() -> Mapping[str, Any]:
    contracts = implemented_baseline_expert_contracts()
    by_question: Dict[str, int] = {}
    by_species: Dict[str, int] = {}
    benchmark_count = 0
    candidate_model_count = 0
    for contract in contracts:
        question_ref = str(contract["question_refs"][0])
        by_question[question_ref] = by_question.get(question_ref, 0) + 1
        species = str(contract["species"])
        by_species[species] = by_species.get(species, 0) + 1
        if contract["parameters"].get("implementation_class") == "BENCHMARK":
            benchmark_count += 1
        else:
            candidate_model_count += 1
    body = {
        "schema_version": "1.0",
        "status": "IMPLEMENTED_CANDIDATE_EXPERTS",
        "implemented_expert_count": len(contracts),
        "candidate_model_expert_count": candidate_model_count,
        "benchmark_expert_count": benchmark_count,
        "contracts": list(contracts),
        "by_question": dict(sorted(by_question.items())),
        "by_species": dict(sorted(by_species.items())),
        "earned_competence": False,
        "capital_authority": False,
    }
    body["integrity"] = {
        "algorithm": "sha256",
        "content_hash": canonical_hash({key: value for key, value in body.items() if key != "integrity"}),
    }
    return body


def _answer_for_expert(prediction: QuestionBoundPrediction) -> Any:
    answer = prediction.answer
    if prediction.answer_kind == "BINARY":
        if answer.get("probability_1") is not None:
            return float(answer["probability_1"])
        value = answer.get("value")
        if value not in (0, 1):
            raise ExpertAdapterError("binary prediction lacks a valid probability or point answer")
        return float(value)
    if prediction.answer_kind == "CONTINUOUS":
        try:
            return float(answer["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ExpertAdapterError("continuous prediction lacks numeric value") from exc
    if prediction.answer_kind == "CATEGORICAL":
        probabilities = answer.get("probabilities")
        if probabilities is None:
            label = str(answer.get("value", "")).strip()
            if not label:
                raise ExpertAdapterError("categorical prediction lacks value")
            return {label: 1.0}
        values = {str(label): float(value) for label, value in probabilities.items()}
        if abs(sum(values.values()) - 1.0) > 1e-9:
            raise ExpertAdapterError("categorical probabilities must sum to one for expert testimony")
        return values
    raise ExpertAdapterError("distribution question predictions need a dedicated expert distribution contract")


def _evidence_refs(prediction: QuestionBoundPrediction) -> Tuple[str, ...]:
    values = ["question-prediction:%s:%s" % (prediction.prediction_id, prediction.content_hash())]
    for ref in prediction.artifact_refs:
        values.append("artifact:%s:%s:%s" % (ref.artifact_type, ref.artifact_id, ref.content_hash))
    return tuple(values)


def _experience_refs(prediction: QuestionBoundPrediction) -> Tuple[str, ...]:
    refs = [
        "experience:%s:%s:%s" % (ref.artifact_type, ref.artifact_id, ref.content_hash)
        for ref in prediction.artifact_refs
        if "EXPERIENCE" in str(ref.artifact_type).upper()
    ]
    if not refs:
        raise ExpertAdapterError("expert claim requires experience-bound prediction evidence")
    return tuple(refs)


def question_prediction_to_expert_claim(contract: Mapping[str, Any], prediction: QuestionBoundPrediction) -> Mapping[str, Any]:
    """Adapt exact question-bound model testimony into an immutable expert claim."""
    validate_expert_contract(contract)
    questions = _question_map()
    if prediction.question_ref not in contract["question_refs"]:
        raise ExpertAdapterError("prediction question is outside expert contract")
    if prediction.question_ref not in questions:
        raise ExpertAdapterError("prediction question is not canonical")
    question = questions[prediction.question_ref]
    if prediction.question_definition_hash != question.content_hash():
        raise ExpertAdapterError("prediction question definition is not canonical")
    if int(prediction.horizon_ns) != int(question.horizon_ns):
        raise ExpertAdapterError("prediction horizon differs from expert examination")

    contract_models = set(str(ref) for ref in contract.get("model_refs", ()))
    prediction_models = set(str(ref) for ref in prediction.model_refs)
    if contract_models:
        if not prediction_models or not prediction_models.issubset(contract_models):
            raise ExpertAdapterError("prediction model identity is outside expert contract")
    elif prediction_models:
        raise ExpertAdapterError("model-backed prediction cannot enter a model-unbound expert contract")

    artifact_types = {str(ref.artifact_type) for ref in prediction.artifact_refs}
    required_artifacts = set(str(value) for value in contract["required_artifact_types"])
    if not required_artifacts.issubset(artifact_types):
        raise ExpertAdapterError("prediction evidence omits required expert artifacts")
    allowed = set(str(value) for value in contract["allowed_feature_families"])
    observed_features = {str(feature) for ref in prediction.artifact_refs for feature in ref.feature_families}
    if not observed_features.issubset(allowed):
        raise ExpertAdapterError("prediction evidence exceeds expert feature allowlist")

    snapshot_hash = canonical_hash({
        "prediction_hash": prediction.content_hash(),
        "artifacts": [ref.to_wire() for ref in sorted(prediction.artifact_refs, key=lambda item: (item.artifact_type, item.artifact_id))],
    })
    return build_expert_claim(
        contract,
        question_ref=prediction.question_ref,
        cutoff_ns=prediction.cutoff_at_ns,
        answer=_answer_for_expert(prediction),
        evidence_refs=_evidence_refs(prediction),
        experience_refs=_experience_refs(prediction),
        input_snapshot_hash=snapshot_hash,
    )
