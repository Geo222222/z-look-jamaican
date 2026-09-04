from __future__ import annotations

from typing import Any, Dict, Mapping

from ..operations import canonical_hash
from .catalog import question_catalog_v1
from .contracts import EVIDENCE_CUTOFF_POLICY, QuestionContractError, QuestionFamily, QuestionRegistrySnapshot
from .evolution import (
    MATERIAL_REVERSAL_MIN_FORWARD_ABS_BPS,
    MATERIAL_REVERSAL_MIN_FORWARD_TO_TRAILING_RATIO,
    MATERIAL_REVERSAL_MIN_TRAILING_ABS_BPS,
    REVERSAL_MATERIAL_RESOLVER_IMPLEMENTATION_REF,
    REVERSAL_QUESTION_V1_1_REF,
    REVERSAL_QUESTION_V1_2_REF,
    REVERSAL_QUESTION_V1_REF,
    REVERSAL_ROOT_PATH_RESOLVER_IMPLEMENTATION_REF,
    reversal_question_v1_1,
    reversal_question_v1_2,
)
from .readiness import RESOLVER_READY_IMPLEMENTATIONS_V1, build_complete_resolver_ready_registry_v1_2


QUESTION_REGISTRY_V1_CERTIFICATION_SCHEMA_VERSION = "1.1"
QUESTION_REGISTRY_V1_QUALIFIED = "QUESTION_REGISTRY_V1_QUALIFIED"
QUESTION_REGISTRY_V1_QUALIFIED_VERSION = "1.3.0-question-registry-v1-qualified"
CANONICAL_REGISTRY_ID = "ZLJ-MARKET-QUESTIONS"
DEFERRED_QUESTION_FAMILIES_V1 = (QuestionFamily.EXECUTION_SUITABILITY.value,)

_REQUIRED_FORBIDDEN_FAMILIES = {
    "FUTURE_OUTCOME",
    "POST_CUTOFF_MARKET_DATA",
    "BENJAMIN_CAPITAL_STATE",
    "HAND_EXECUTION_RESULT",
}

_CERTIFICATION_GUARANTEES = {
    "immutable_question_identity": True,
    "exact_input_contract": True,
    "exact_horizon": True,
    "exact_resolver_identity_and_version": True,
    "deterministic_outcome_contract": True,
    "provenance_bound": True,
    "leakage_protected": True,
    "replay_required": True,
    "same_evidence_same_answer_required": True,
    "retrospective_definition_mutation_forbidden": True,
    "missing_or_invalid_evidence_fails_closed": True,
    "relationship_compatibility_proof_required": True,
    "material_reversal_thresholds_preregistered": True,
    "sign_reversal_history_preserved": True,
    "registry_version_frozen": True,
}


def _canonical_definitions() -> Dict[str, Any]:
    definitions = {item.question_ref: item for item in question_catalog_v1()}
    sign_reversal = reversal_question_v1_1()
    material_reversal = reversal_question_v1_2()
    definitions[sign_reversal.question_ref] = sign_reversal
    definitions[material_reversal.question_ref] = material_reversal
    return definitions


def resolver_ready_refs_v1_qualified() -> Dict[str, str]:
    """Return the exact active question-ref -> resolver examination surface."""
    originals = {item.question_id: item for item in question_catalog_v1()}
    refs = {
        originals[question_id].question_ref: implementation
        for question_id, implementation in sorted(RESOLVER_READY_IMPLEMENTATIONS_V1.items())
    }
    refs[REVERSAL_QUESTION_V1_2_REF] = REVERSAL_MATERIAL_RESOLVER_IMPLEMENTATION_REF
    return refs


def build_question_registry_v1_qualified(
    base: QuestionRegistrySnapshot,
    *,
    known_at_ns: int,
    effective_at_ns: int,
) -> QuestionRegistrySnapshot:
    """Create the exact frozen resolver-ready registry used by the first Expert School.

    This freezes truth semantics only. It does not qualify a model, score an expert,
    authorize capital, or make execution reachable.
    """
    return build_complete_resolver_ready_registry_v1_2(
        base,
        version=QUESTION_REGISTRY_V1_QUALIFIED_VERSION,
        known_at_ns=int(known_at_ns),
        effective_at_ns=int(effective_at_ns),
    )


def _assert_semantic_guards(snapshot: QuestionRegistrySnapshot) -> None:
    if snapshot.registry_id != CANONICAL_REGISTRY_ID:
        raise QuestionContractError("qualified registry id is not canonical")
    if snapshot.version != QUESTION_REGISTRY_V1_QUALIFIED_VERSION:
        raise QuestionContractError("qualified registry version is not frozen v1")

    canonical = _canonical_definitions()
    actual = {entry.definition.question_ref: entry for entry in snapshot.entries}
    if set(actual) != set(canonical):
        missing = sorted(set(canonical).difference(actual))
        extra = sorted(set(actual).difference(canonical))
        raise QuestionContractError(
            "qualified registry question set changed: missing=%s extra=%s"
            % (",".join(missing) or "NONE", ",".join(extra) or "NONE")
        )

    for question_ref, expected in canonical.items():
        observed = actual[question_ref].definition
        if observed.content_hash() != expected.content_hash():
            raise QuestionContractError("question definition changed retrospectively: %s" % question_ref)
        if observed.evidence_cutoff_policy != EVIDENCE_CUTOFF_POLICY:
            raise QuestionContractError("question cutoff policy weakened: %s" % question_ref)
        if not _REQUIRED_FORBIDDEN_FAMILIES.issubset(set(observed.forbidden_feature_families)):
            raise QuestionContractError("question leakage/authority guard weakened: %s" % question_ref)

    expected_ready = resolver_ready_refs_v1_qualified()
    observed_ready = {
        ref: entry.resolver_implementation_ref
        for ref, entry in actual.items()
        if entry.lifecycle_state == "RESOLVER_READY"
    }
    if observed_ready != expected_ready:
        raise QuestionContractError("resolver-ready examination surface changed")

    for question_ref, implementation in expected_ready.items():
        entry = actual[question_ref]
        if entry.lifecycle_state != "RESOLVER_READY":
            raise QuestionContractError("question is not resolver-ready: %s" % question_ref)
        if entry.resolver_implementation_ref != implementation:
            raise QuestionContractError("resolver implementation changed: %s" % question_ref)
        if entry.qualification_evidence_refs:
            raise QuestionContractError("resolver readiness cannot masquerade as empirical qualification")

    historical_reversal = actual[REVERSAL_QUESTION_V1_REF]
    if historical_reversal.lifecycle_state != "DEFINED" or historical_reversal.resolver_implementation_ref is not None:
        raise QuestionContractError("historical reversal v1.0 cannot gain resolver authority retroactively")
    if historical_reversal.qualification_evidence_refs:
        raise QuestionContractError("historical reversal v1.0 cannot gain qualification evidence retroactively")

    sign_reversal = actual[REVERSAL_QUESTION_V1_1_REF]
    if sign_reversal.lifecycle_state != "RETIRED":
        raise QuestionContractError("sign-reversal v1.1 must be retained as retired historical truth")
    if sign_reversal.resolver_implementation_ref != REVERSAL_ROOT_PATH_RESOLVER_IMPLEMENTATION_REF:
        raise QuestionContractError("sign-reversal v1.1 historical resolver identity changed")
    if sign_reversal.qualification_evidence_refs:
        raise QuestionContractError("retired sign-reversal v1.1 cannot gain qualification evidence")

    active_execution_questions = [
        entry.definition.question_ref
        for entry in snapshot.entries
        if entry.definition.family is QuestionFamily.EXECUTION_SUITABILITY
    ]
    if active_execution_questions:
        raise QuestionContractError("execution suitability must remain deferred until shadow-execution truth exists")

    for question_ref in (
        "SPOT_DERIVATIVE_BASIS_CHANGE_5M@1.0.0",
        "SPOT_DERIVATIVE_RELATIVE_VALUE_CONVERGENCE_5M@1.0.0",
    ):
        definition = actual[question_ref].definition
        if definition.scope.value != "RELATIONSHIP":
            raise QuestionContractError("relationship question scope changed: %s" % question_ref)
        if "ECONOMIC_RELATIONSHIP_STATE" not in definition.required_artifact_types:
            raise QuestionContractError("relationship state is not mandatory: %s" % question_ref)
        if "ECONOMIC_RELATIONSHIP_STATE" not in definition.required_feature_families:
            raise QuestionContractError("relationship feature lineage is not mandatory: %s" % question_ref)
        if definition.parameters.get("quote_unit_policy") != "DIRECT_BASIS_REQUIRES_COMPATIBLE_QUOTE_UNITS":
            raise QuestionContractError("relationship quote/unit compatibility proof was weakened: %s" % question_ref)
        if "ECONOMIC_RELATIONSHIP_STATE" not in definition.outcome.resolution_evidence_families:
            raise QuestionContractError("relationship outcome provenance was weakened: %s" % question_ref)

    for question_ref in (
        "MARKET_DIRECTION_REGIME_15M@1.0.0",
        "MARKET_REGIME_PERSISTENCE_5M@1.0.0",
    ):
        definition = actual[question_ref].definition
        if definition.scope.value != "MARKET_WIDE":
            raise QuestionContractError("market-regime scope changed: %s" % question_ref)
        if "MARKET_WIDE_EXPERIENCE" not in definition.required_artifact_types:
            raise QuestionContractError("market-wide experience is not mandatory: %s" % question_ref)
        if "MARKET_WIDE_CONTEXT" not in definition.required_feature_families:
            raise QuestionContractError("qualified market context is not mandatory: %s" % question_ref)

    reversal = actual[REVERSAL_QUESTION_V1_2_REF].definition
    if "ECONOMIC_ROOT_PATH" not in reversal.required_artifact_types:
        raise QuestionContractError("material reversal must bind the prediction-time economic root path")
    if "ECONOMIC_ROOT_PATH" not in reversal.required_feature_families:
        raise QuestionContractError("material reversal root-path feature lineage is mandatory")
    if reversal.parameters.get("trailing_path_status") != "QUALIFIED":
        raise QuestionContractError("material reversal trailing path must be qualified")
    if reversal.parameters.get("trailing_path_type") != "ECONOMIC_ROOT_PATH":
        raise QuestionContractError("material reversal trailing path type changed")
    if reversal.parameters.get("instrument_policy") != "EXACT_PREDICTION_BOUND_SPOT_INSTRUMENT":
        raise QuestionContractError("material reversal reference instrument policy changed")
    if reversal.parameters.get("trailing_window_ns") != 60_000_000_000:
        raise QuestionContractError("material reversal trailing window changed")
    if reversal.parameters.get("trailing_grid_interval_ns") != 10_000_000_000:
        raise QuestionContractError("material reversal trailing grid changed")
    if reversal.parameters.get("zero_return_policy") != "EITHER_ZERO_MEANS_NO_REVERSAL":
        raise QuestionContractError("material reversal zero-return policy changed")
    if reversal.parameters.get("materiality_policy") != "OPPOSITE_SIGN_WITH_ABSOLUTE_AND_TRAILING_RATIO_FLOORS_V1":
        raise QuestionContractError("material reversal policy changed")
    if reversal.parameters.get("min_trailing_abs_bps") != MATERIAL_REVERSAL_MIN_TRAILING_ABS_BPS:
        raise QuestionContractError("material reversal trailing floor changed")
    if reversal.parameters.get("min_forward_abs_bps") != MATERIAL_REVERSAL_MIN_FORWARD_ABS_BPS:
        raise QuestionContractError("material reversal forward floor changed")
    if reversal.parameters.get("min_forward_to_trailing_ratio") != MATERIAL_REVERSAL_MIN_FORWARD_TO_TRAILING_RATIO:
        raise QuestionContractError("material reversal relative floor changed")


def certify_question_registry_v1(snapshot: QuestionRegistrySnapshot) -> Mapping[str, Any]:
    """Return a deterministic, tamper-evident qualification certificate."""
    _assert_semantic_guards(snapshot)
    ready_refs = resolver_ready_refs_v1_qualified()
    entries = {entry.definition.question_ref: entry for entry in snapshot.entries}
    ready = []
    for question_ref in sorted(ready_refs):
        entry = entries[question_ref]
        definition = entry.definition
        ready.append(
            {
                "question_ref": question_ref,
                "definition_hash": definition.content_hash(),
                "family": definition.family.value,
                "scope": definition.scope.value,
                "horizon_ns": definition.horizon_ns,
                "outcome_metric_id": definition.outcome.metric_id,
                "resolver_policy_id": definition.outcome.resolver_policy_id,
                "resolver_implementation_ref": entry.resolver_implementation_ref,
            }
        )

    body: Dict[str, Any] = {
        "schema_version": QUESTION_REGISTRY_V1_CERTIFICATION_SCHEMA_VERSION,
        "certification_id": QUESTION_REGISTRY_V1_QUALIFIED,
        "registry": {
            "registry_id": snapshot.registry_id,
            "version": snapshot.version,
            "content_hash": snapshot.content_hash(),
            "known_at_ns": snapshot.known_at_ns,
            "effective_at_ns": snapshot.effective_at_ns,
        },
        "resolver_ready_questions": ready,
        "historical_defined_questions": [REVERSAL_QUESTION_V1_REF],
        "historical_retired_questions": [REVERSAL_QUESTION_V1_1_REF],
        "deferred_question_families": list(DEFERRED_QUESTION_FAMILIES_V1),
        "guarantees": dict(_CERTIFICATION_GUARANTEES),
        "authority": {
            "defines_examination_truth": True,
            "selects_model": False,
            "claims_model_competence": False,
            "sets_adaptive_weights": False,
            "capital_decision": False,
            "risk_authorization": False,
            "external_execution": False,
        },
    }
    certificate = dict(body)
    certificate["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
    return certificate


def validate_question_registry_v1_certificate(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise QuestionContractError("registry qualification certificate must be a mapping")
    integrity = value.get("integrity")
    if not isinstance(integrity, Mapping):
        raise QuestionContractError("registry qualification certificate integrity is missing")
    body = {key: item for key, item in value.items() if key != "integrity"}
    if integrity.get("algorithm") != "sha256" or integrity.get("content_hash") != canonical_hash(body):
        raise QuestionContractError("registry qualification certificate content hash mismatch")
    if value.get("schema_version") != QUESTION_REGISTRY_V1_CERTIFICATION_SCHEMA_VERSION:
        raise QuestionContractError("registry qualification certificate schema is invalid")
    if value.get("certification_id") != QUESTION_REGISTRY_V1_QUALIFIED:
        raise QuestionContractError("registry qualification certification id is invalid")
    if tuple(value.get("historical_defined_questions", ())) != (REVERSAL_QUESTION_V1_REF,):
        raise QuestionContractError("registry qualification historical-defined boundary changed")
    if tuple(value.get("historical_retired_questions", ())) != (REVERSAL_QUESTION_V1_1_REF,):
        raise QuestionContractError("registry qualification historical-retired boundary changed")
    if tuple(value.get("deferred_question_families", ())) != DEFERRED_QUESTION_FAMILIES_V1:
        raise QuestionContractError("registry qualification deferred-family boundary changed")
    guarantees = value.get("guarantees")
    if not isinstance(guarantees, Mapping) or guarantees != _CERTIFICATION_GUARANTEES:
        raise QuestionContractError("registry qualification guarantees changed")
    authority = value.get("authority")
    expected_authority = {
        "defines_examination_truth": True,
        "selects_model": False,
        "claims_model_competence": False,
        "sets_adaptive_weights": False,
        "capital_decision": False,
        "risk_authorization": False,
        "external_execution": False,
    }
    if not isinstance(authority, Mapping) or authority != expected_authority:
        raise QuestionContractError("registry qualification authority boundary changed")


def verify_question_registry_v1_certificate(
    snapshot: QuestionRegistrySnapshot,
    certificate: Mapping[str, Any],
) -> None:
    validate_question_registry_v1_certificate(certificate)
    expected = certify_question_registry_v1(snapshot)
    if dict(certificate) != dict(expected):
        raise QuestionContractError("registry qualification certificate does not match exact registry snapshot")
