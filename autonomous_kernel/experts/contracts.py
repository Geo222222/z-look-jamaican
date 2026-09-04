from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..operations import canonical_hash
from ..questions.catalog import question_catalog_v1
from ..questions.certification import resolver_ready_refs_v1_qualified
from ..questions.evolution import reversal_question_v1_2


EXPERT_CONTRACT_SCHEMA_VERSION = "1.0"
EXPERT_CLAIM_SCHEMA_VERSION = "1.0"
EXPERT_LIFECYCLE_STATE = "CANDIDATE"

EXPERT_AUTHORITY = {
    "defines_examination_truth": False,
    "claims_competence": False,
    "sets_adaptive_weights": False,
    "capital_decision": False,
    "risk_authorization": False,
    "external_execution": False,
}

CLAIM_KIND_BY_ANSWER_KIND = {
    "BINARY": "PROBABILITY",
    "CONTINUOUS": "POINT_ESTIMATE",
    "CATEGORICAL": "CATEGORICAL_DISTRIBUTION",
    "DISTRIBUTION": "DISTRIBUTION",
}


class ExpertContractError(ValueError):
    pass


def _unique_strings(values: Sequence[str], field: str, *, allow_empty: bool = False) -> Tuple[str, ...]:
    result = tuple(str(value) for value in values)
    if (not result and not allow_empty) or any(not value.strip() for value in result):
        raise ExpertContractError("%s must contain non-empty values" % field)
    if len(set(result)) != len(result):
        raise ExpertContractError("%s must contain unique values" % field)
    return result


def _sha256(value: str, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise ExpertContractError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise ExpertContractError("%s must be SHA-256 hex" % field) from exc
    return text


def _active_questions() -> Dict[str, Any]:
    definitions = {item.question_ref: item for item in question_catalog_v1()}
    material_reversal = reversal_question_v1_2()
    definitions[material_reversal.question_ref] = material_reversal
    ready = resolver_ready_refs_v1_qualified()
    return {ref: definitions[ref] for ref in ready}


def _contract_body(
    *,
    expert_id: str,
    version: str,
    species: str,
    implementation_ref: str,
    implementation_hash: str,
    model_refs: Sequence[str],
    question_refs: Sequence[str],
    required_artifact_types: Sequence[str],
    allowed_feature_families: Sequence[str],
    parameters: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "schema_version": EXPERT_CONTRACT_SCHEMA_VERSION,
        "expert_id": str(expert_id),
        "version": str(version),
        "expert_ref": "%s@%s" % (expert_id, version),
        "species": str(species),
        "lifecycle_state": EXPERT_LIFECYCLE_STATE,
        "implementation_ref": str(implementation_ref),
        "implementation_hash": str(implementation_hash).lower(),
        "model_refs": list(model_refs),
        "question_refs": list(question_refs),
        "required_artifact_types": list(required_artifact_types),
        "allowed_feature_families": list(allowed_feature_families),
        "parameters": dict(parameters),
        "authority": dict(EXPERT_AUTHORITY),
    }


def build_expert_contract(
    *,
    expert_id: str,
    version: str,
    species: str,
    implementation_ref: str,
    implementation_hash: str,
    model_refs: Sequence[str],
    question_refs: Sequence[str],
    required_artifact_types: Sequence[str],
    allowed_feature_families: Sequence[str],
    parameters: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Any]:
    """Build a frozen Phase-9 expert definition.

    An expert is a question-bound claimant. This contract cannot express earned
    competence, adaptive assembly weight, Benjamin economic judgment, Watchman
    authorization, or Hand execution authority.
    """
    body = _contract_body(
        expert_id=expert_id,
        version=version,
        species=species,
        implementation_ref=implementation_ref,
        implementation_hash=implementation_hash,
        model_refs=model_refs,
        question_refs=question_refs,
        required_artifact_types=required_artifact_types,
        allowed_feature_families=allowed_feature_families,
        parameters=parameters or {},
    )
    contract = dict(body)
    contract["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
    validate_expert_contract(contract)
    return contract


def validate_expert_contract(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise ExpertContractError("expert contract must be a mapping")
    if value.get("schema_version") != EXPERT_CONTRACT_SCHEMA_VERSION:
        raise ExpertContractError("unsupported expert contract schema")

    for field in ("expert_id", "version", "expert_ref", "species", "implementation_ref"):
        if not str(value.get(field, "")).strip():
            raise ExpertContractError("%s is required" % field)
    if value.get("expert_ref") != "%s@%s" % (value.get("expert_id"), value.get("version")):
        raise ExpertContractError("expert_ref does not match expert identity")
    if value.get("lifecycle_state") != EXPERT_LIFECYCLE_STATE:
        raise ExpertContractError("Phase 9 experts must remain CANDIDATE")

    _sha256(str(value.get("implementation_hash", "")), "implementation_hash")
    model_refs = _unique_strings(value.get("model_refs", ()), "model_refs", allow_empty=True)
    question_refs = _unique_strings(value.get("question_refs", ()), "question_refs")
    required_artifacts = set(_unique_strings(value.get("required_artifact_types", ()), "required_artifact_types"))
    allowed_features = set(_unique_strings(value.get("allowed_feature_families", ()), "allowed_feature_families"))
    if not isinstance(value.get("parameters"), Mapping):
        raise ExpertContractError("parameters must be a mapping")

    active = _active_questions()
    unknown = set(question_refs).difference(active)
    if unknown:
        raise ExpertContractError("expert references non-active examination question: %s" % ", ".join(sorted(unknown)))

    for question_ref in question_refs:
        question = active[question_ref]
        missing_artifacts = set(question.required_artifact_types).difference(required_artifacts)
        if missing_artifacts:
            raise ExpertContractError(
                "expert evidence contract omits required artifacts for %s: %s"
                % (question_ref, ", ".join(sorted(missing_artifacts)))
            )
        missing_features = set(question.required_feature_families).difference(allowed_features)
        if missing_features:
            raise ExpertContractError(
                "expert feature contract omits required families for %s: %s"
                % (question_ref, ", ".join(sorted(missing_features)))
            )
        forbidden = set(question.forbidden_feature_families).intersection(allowed_features)
        if forbidden:
            raise ExpertContractError(
                "expert feature contract admits forbidden families for %s: %s"
                % (question_ref, ", ".join(sorted(forbidden)))
            )

    if value.get("authority") != EXPERT_AUTHORITY:
        raise ExpertContractError("expert authority boundary changed")
    for forbidden_field in (
        "competence",
        "competence_score",
        "adaptive_weight",
        "assembly_weight",
        "capital_action",
        "risk_authorization",
        "execution_instruction",
    ):
        if forbidden_field in value:
            raise ExpertContractError("expert contract cannot contain %s" % forbidden_field)

    integrity = value.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("algorithm") != "sha256":
        raise ExpertContractError("expert contract integrity is missing")
    body = {key: item for key, item in value.items() if key != "integrity"}
    if integrity.get("content_hash") != canonical_hash(body):
        raise ExpertContractError("expert contract content hash mismatch")

    # Empty model_refs are legitimate for deterministic mathematical baselines.
    tuple(model_refs)


def build_expert_claim(
    contract: Mapping[str, Any],
    *,
    question_ref: str,
    cutoff_ns: int,
    answer: Any,
    evidence_refs: Sequence[str],
    experience_refs: Sequence[str],
    input_snapshot_hash: str,
) -> Mapping[str, Any]:
    """Build one immutable, question-bound expert answer at cutoff T."""
    validate_expert_contract(contract)
    question_ref = str(question_ref)
    if question_ref not in contract["question_refs"]:
        raise ExpertContractError("expert is not contracted for question_ref")
    if int(cutoff_ns) < 0:
        raise ExpertContractError("cutoff_ns must be non-negative")
    evidence = _unique_strings(evidence_refs, "evidence_refs")
    experiences = _unique_strings(experience_refs, "experience_refs")
    snapshot_hash = _sha256(input_snapshot_hash, "input_snapshot_hash")

    question = _active_questions()[question_ref]
    claim_kind = CLAIM_KIND_BY_ANSWER_KIND[question.outcome.answer_kind.value]
    _validate_answer(claim_kind, answer)

    body = {
        "schema_version": EXPERT_CLAIM_SCHEMA_VERSION,
        "expert_ref": contract["expert_ref"],
        "expert_contract_hash": contract["integrity"]["content_hash"],
        "question_ref": question_ref,
        "question_definition_hash": question.content_hash(),
        "cutoff_ns": int(cutoff_ns),
        "horizon_ns": int(question.horizon_ns),
        "claim_kind": claim_kind,
        "answer": answer,
        "evidence_refs": list(evidence),
        "experience_refs": list(experiences),
        "input_snapshot_hash": snapshot_hash,
        "authority": dict(EXPERT_AUTHORITY),
    }
    claim = dict(body)
    claim["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
    validate_expert_claim(contract, claim)
    return claim


def _validate_answer(claim_kind: str, answer: Any) -> None:
    if claim_kind == "PROBABILITY":
        if isinstance(answer, bool) or not isinstance(answer, (int, float)) or not 0.0 <= float(answer) <= 1.0:
            raise ExpertContractError("probability answer must be numeric in [0,1]")
    elif claim_kind == "POINT_ESTIMATE":
        if isinstance(answer, bool) or not isinstance(answer, (int, float)):
            raise ExpertContractError("point estimate answer must be numeric")
    elif claim_kind in {"CATEGORICAL_DISTRIBUTION", "DISTRIBUTION"}:
        if not isinstance(answer, Mapping) or not answer:
            raise ExpertContractError("distribution answer must be a non-empty mapping")
        values = list(answer.values())
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) or float(item) < 0.0 for item in values):
            raise ExpertContractError("distribution probabilities must be non-negative numeric values")
        if abs(sum(float(item) for item in values) - 1.0) > 1e-9:
            raise ExpertContractError("distribution probabilities must sum to 1")
    else:
        raise ExpertContractError("unsupported expert claim kind")


def validate_expert_claim(contract: Mapping[str, Any], value: Mapping[str, Any]) -> None:
    validate_expert_contract(contract)
    if not isinstance(value, Mapping) or value.get("schema_version") != EXPERT_CLAIM_SCHEMA_VERSION:
        raise ExpertContractError("expert claim schema is invalid")
    if value.get("expert_ref") != contract.get("expert_ref"):
        raise ExpertContractError("expert claim identity mismatch")
    if value.get("expert_contract_hash") != contract.get("integrity", {}).get("content_hash"):
        raise ExpertContractError("expert claim contract hash mismatch")

    question_ref = str(value.get("question_ref", ""))
    if question_ref not in contract.get("question_refs", ()):
        raise ExpertContractError("expert claim question is outside contract")
    question = _active_questions()[question_ref]
    if value.get("question_definition_hash") != question.content_hash():
        raise ExpertContractError("expert claim question definition hash mismatch")
    if value.get("horizon_ns") != question.horizon_ns:
        raise ExpertContractError("expert claim horizon changed")
    if not isinstance(value.get("cutoff_ns"), int) or value["cutoff_ns"] < 0:
        raise ExpertContractError("expert claim cutoff is invalid")

    expected_kind = CLAIM_KIND_BY_ANSWER_KIND[question.outcome.answer_kind.value]
    if value.get("claim_kind") != expected_kind:
        raise ExpertContractError("expert claim kind does not match question answer semantics")
    _validate_answer(expected_kind, value.get("answer"))
    _unique_strings(value.get("evidence_refs", ()), "evidence_refs")
    _unique_strings(value.get("experience_refs", ()), "experience_refs")
    _sha256(str(value.get("input_snapshot_hash", "")), "input_snapshot_hash")

    if value.get("authority") != EXPERT_AUTHORITY:
        raise ExpertContractError("expert claim authority boundary changed")
    for forbidden_field in (
        "realized_outcome",
        "competence",
        "competence_score",
        "adaptive_weight",
        "assembly_weight",
        "capital_action",
        "risk_authorization",
        "execution_instruction",
    ):
        if forbidden_field in value:
            raise ExpertContractError("expert claim cannot contain %s" % forbidden_field)

    integrity = value.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("algorithm") != "sha256":
        raise ExpertContractError("expert claim integrity is missing")
    body = {key: item for key, item in value.items() if key != "integrity"}
    if integrity.get("content_hash") != canonical_hash(body):
        raise ExpertContractError("expert claim content hash mismatch")
