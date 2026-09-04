from __future__ import annotations

from typing import Any, Dict, Mapping

from ..operations import canonical_hash


POLICY_SCHEMA_VERSION = "1.0"
POLICY_ID = "BENJAMIN_PUBLICATION_QUALIFICATION_POLICY"
POLICY_VERSION = "1.0.0"

# Governance thresholds for Benjamin publication eligibility. These are not
# learned trading parameters and are not claimed to be empirically optimal.
POLICY_THRESHOLDS = {
    "minimum_expert_count": 2,
    "minimum_samples_per_expert": 5,
    "minimum_total_scored_samples": 20,
    "minimum_contextual_sample_support": 0.30,
    "minimum_assembly_confidence": 0.55,
    "maximum_disagreement": 0.35,
    "maximum_single_expert_weight": 0.75,
    "maximum_mean_pairwise_evidence_jaccard": 0.50,
    "maximum_context_age_ns": 30_000_000_000,
    "maximum_competence_age_ns": 21_600_000_000_000,
    "required_context_status": "QUALIFIED",
    "required_data_quality_state": "VALID",
}


class BenjaminPublicationPolicyError(ValueError):
    pass


def _digest(value: Any, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise BenjaminPublicationPolicyError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise BenjaminPublicationPolicyError("%s must be SHA-256 hex" % field) from exc
    return text


def build_benjamin_publication_policy_v1() -> Mapping[str, Any]:
    body: Dict[str, Any] = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "policy_version": POLICY_VERSION,
        "policy_class": "PUBLICATION_ELIGIBILITY_GOVERNANCE",
        "thresholds": dict(POLICY_THRESHOLDS),
        "empirical_status": "NOT_CLAIMED_OPTIMAL",
        "note": "Governance thresholds for whether ZLJ intelligence may be handed to Benjamin. Not trading, sizing, or risk thresholds.",
        "authority": {
            "economic_decision": False,
            "capital_allocation": False,
            "risk_authorization": False,
            "external_execution": False,
        },
    }
    policy = dict(body)
    policy["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
    validate_benjamin_publication_policy(policy)
    return policy


def validate_benjamin_publication_policy(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise BenjaminPublicationPolicyError("publication policy must be an object")
    if value.get("schema_version") != POLICY_SCHEMA_VERSION or value.get("policy_id") != POLICY_ID:
        raise BenjaminPublicationPolicyError("publication policy identity is invalid")
    if value.get("policy_version") != POLICY_VERSION:
        raise BenjaminPublicationPolicyError("publication policy version is invalid")
    if value.get("empirical_status") != "NOT_CLAIMED_OPTIMAL":
        raise BenjaminPublicationPolicyError("publication policy cannot claim empirical optimality")
    thresholds = value.get("thresholds")
    if not isinstance(thresholds, Mapping) or dict(thresholds) != dict(POLICY_THRESHOLDS):
        raise BenjaminPublicationPolicyError("publication policy thresholds drifted from the frozen v1 contract")
    integrity = value.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("algorithm") != "sha256":
        raise BenjaminPublicationPolicyError("publication policy integrity is missing")
    body = {key: item for key, item in value.items() if key != "integrity"}
    digest = _digest(integrity.get("content_hash"), "policy content_hash")
    if digest != canonical_hash(body):
        raise BenjaminPublicationPolicyError("publication policy content hash mismatch")
