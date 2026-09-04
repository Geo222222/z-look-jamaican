from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Tuple

from ..models.baselines import baseline_model_set
from ..operations import canonical_hash
from ..prediction.question_bound import QuestionBoundPrediction
from ..questions.catalog import question_catalog_v1
from .contracts import ExpertContractError, build_expert_claim, build_expert_contract, validate_expert_contract


class ExpertAdapterError(ValueError):
    pass


IMPLEMENTED_BASELINE_ROLES = (
    # The existing baseline models expose both expected signed move and probability
    # positive, so each executable model can legitimately sit the two currently
    # compatible exams. No other question family is claimed here.
    ("ECONOMIC_ROOT_DIRECTION_10S@1.0.0", "DIRECTION"),
    ("ECONOMIC_ROOT_MAGNITUDE_30S@1.0.0", "MAGNITUDE"),
)


def _question_map() -> Dict[str, Any]:
    return {question.question_ref: question for question in question_catalog_v1()}


def implemented_baseline_expert_contracts() -> Tuple[Mapping[str, Any], ...]:
    """Return only experts backed by executable model code already in the repo.

    This inventory intentionally differs from the broader curriculum built by
    ``build_baseline_expert_school``. Curriculum presence is not implementation
    existence, and implementation existence is not earned competence.
    """
    questions = _question_map()
    contracts: List[Mapping[str, Any]] = []
    for model in baseline_model_set():
        model_ref = model.definition.model_ref
        for question_ref, role in IMPLEMENTED_BASELINE_ROLES:
            question = questions[question_ref]
            if int(question.horizon_ns) not in set(int(v) for v in model.definition.supported_horizons_ns):
                continue
            implementation_ref = "autonomous_kernel.models.baselines:%s" % model.__class__.__name__
            implementation_hash = canonical_hash({
                "implementation_ref": implementation_ref,
                "model_definition": model.definition.to_wire(),
                "expert_role": role,
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
                    "target_metric": model.definition.target_metric,
                },
            ))
    return tuple(contracts)


def operational_expert_inventory() -> Mapping[str, Any]:
    contracts = implemented_baseline_expert_contracts()
    by_question: Dict[str, int] = {}
    by_species: Dict[str, int] = {}
    for contract in contracts:
        question_ref = str(contract["question_refs"][0])
        by_question[question_ref] = by_question.get(question_ref, 0) + 1
        species = str(contract["species"])
        by_species[species] = by_species.get(species, 0) + 1
    body = {
        "schema_version": "1.0",
        "status": "IMPLEMENTED_CANDIDATE_EXPERTS",
        "implemented_expert_count": len(contracts),
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


def question_prediction_to_expert_claim(
    contract: Mapping[str, Any],
    prediction: QuestionBoundPrediction,
) -> Mapping[str, Any]:
    """Adapt exact question-bound model testimony into an immutable expert claim."""
    validate_expert_contract(contract)
    if prediction.question_ref not in contract["question_refs"]:
        raise ExpertAdapterError("prediction question is outside expert contract")
    if prediction.question_definition_hash != _question_map()[prediction.question_ref].content_hash():
        raise ExpertAdapterError("prediction question definition is not canonical")
    if int(prediction.horizon_ns) != int(_question_map()[prediction.question_ref].horizon_ns):
        raise ExpertAdapterError("prediction horizon differs from expert examination")

    contract_models = set(str(ref) for ref in contract.get("model_refs", ()))
    prediction_models = set(str(ref) for ref in prediction.model_refs)
    if contract_models:
        if not prediction_models or not prediction_models.issubset(contract_models):
            raise ExpertAdapterError("prediction model identity is outside expert contract")
    elif prediction_models:
        raise ExpertAdapterError("model-backed prediction cannot enter a model-unbound expert contract")

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
