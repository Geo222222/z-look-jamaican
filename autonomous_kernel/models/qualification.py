from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..operations import canonical_hash
from ..store import writer_lock
from .registry import ALLOWED_TRANSITIONS, EVIDENCE_KIND_BY_TARGET, ModelRegistry, ModelRegistryError, validate_model_registry


QUALIFICATION_RECEIPT_SCHEMA_VERSION = "1.0"
TRANSITION_PROPOSAL_SCHEMA_VERSION = "1.0"
QUALIFICATION_AUTHORITY = {
    "evaluates_evidence": True,
    "may_propose_model_transition": True,
    "may_apply_only_through_model_registry": True,
    "model_self_certification": False,
    "capital_decision": False,
    "risk_authorization": False,
    "external_execution": False,
}

EVALUATION_MODE_BY_TARGET = {
    "REPLAY_QUALIFIED": "REPLAY",
    "WALK_FORWARD_QUALIFIED": "WALK_FORWARD",
    "SHADOW": "SHADOW",
    "QUALIFIED": "QUALIFICATION",
    "DEGRADED": "MONITORING",
    "QUARANTINED": "INTEGRITY",
    "SUPERSEDED": "SUCCESSION",
}


class ModelQualificationError(RuntimeError):
    pass


def _digest(value: Any, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise ModelQualificationError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise ModelQualificationError("%s must be SHA-256 hex" % field) from exc
    return text


def _refs(values: Sequence[str], field: str) -> Tuple[str, ...]:
    refs = tuple(str(value) for value in values)
    if not refs or any(not value for value in refs) or len(set(refs)) != len(refs):
        raise ModelQualificationError("%s must contain unique non-empty refs" % field)
    return refs


def _seal(body: Mapping[str, Any]) -> Dict[str, Any]:
    value = dict(body)
    value["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
    return value


def _validate_integrity(value: Mapping[str, Any], field: str) -> None:
    integrity = value.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("algorithm") != "sha256":
        raise ModelQualificationError("%s integrity missing" % field)
    body = {key: item for key, item in value.items() if key != "integrity"}
    if integrity.get("content_hash") != canonical_hash(body):
        raise ModelQualificationError("%s content hash mismatch" % field)


def build_evaluation_receipt(
    *,
    receipt_id: str,
    model_ref: str,
    model_definition_hash: str,
    model_artifact_hash: str,
    target_state: str,
    question_refs: Sequence[str],
    evaluation_artifact_refs: Sequence[str],
    evaluation_artifact_hashes: Sequence[str],
    sample_count: int,
    metrics: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    verdict: str,
    evaluated_at_ns: int,
    dataset_hash: Optional[str] = None,
    walk_forward_hash: Optional[str] = None,
    experiment_hash: Optional[str] = None,
) -> Mapping[str, Any]:
    if target_state not in EVALUATION_MODE_BY_TARGET:
        raise ModelQualificationError("unsupported qualification target state")
    if verdict not in {"SUPPORTED", "NOT_SUPPORTED", "BLOCKED"}:
        raise ModelQualificationError("evaluation verdict invalid")
    questions = _refs(question_refs, "question_refs")
    refs = _refs(evaluation_artifact_refs, "evaluation_artifact_refs")
    hashes = tuple(_digest(value, "evaluation_artifact_hash") for value in evaluation_artifact_hashes)
    if len(refs) != len(hashes):
        raise ModelQualificationError("evaluation artifact refs/hashes must align")
    n = int(sample_count)
    if not receipt_id or not model_ref or n < 0 or int(evaluated_at_ns) < 0:
        raise ModelQualificationError("evaluation receipt identity/timing invalid")
    if not isinstance(metrics, Mapping) or not isinstance(thresholds, Mapping):
        raise ModelQualificationError("metrics and thresholds must be mappings")
    optional_hashes = {}
    for key, value in (("dataset_hash", dataset_hash), ("walk_forward_hash", walk_forward_hash), ("experiment_hash", experiment_hash)):
        optional_hashes[key] = None if value is None else _digest(value, key)
    mode = EVALUATION_MODE_BY_TARGET[target_state]
    if mode == "WALK_FORWARD" and (
        optional_hashes["dataset_hash"] is None
        or optional_hashes["walk_forward_hash"] is None
        or optional_hashes["experiment_hash"] is None
    ):
        raise ModelQualificationError("walk-forward evaluation requires dataset, plan, and experiment identity")
    body = {
        "schema_version": QUALIFICATION_RECEIPT_SCHEMA_VERSION,
        "receipt_id": str(receipt_id),
        "model_ref": str(model_ref),
        "model_definition_hash": _digest(model_definition_hash, "model_definition_hash"),
        "model_artifact_hash": _digest(model_artifact_hash, "model_artifact_hash"),
        "target_state": str(target_state),
        "evaluation_mode": mode,
        "required_registry_evidence_kind": EVIDENCE_KIND_BY_TARGET[target_state],
        "question_refs": list(questions),
        "evaluation_artifacts": [
            {"ref": ref, "content_hash": digest}
            for ref, digest in zip(refs, hashes)
        ],
        "sample_count": n,
        "metrics": dict(metrics),
        "thresholds": dict(thresholds),
        "verdict": verdict,
        "evaluated_at_ns": int(evaluated_at_ns),
        **optional_hashes,
        "authority": dict(QUALIFICATION_AUTHORITY),
    }
    return _seal(body)


def validate_evaluation_receipt(receipt: Mapping[str, Any]) -> None:
    if receipt.get("schema_version") != QUALIFICATION_RECEIPT_SCHEMA_VERSION:
        raise ModelQualificationError("unsupported evaluation receipt schema")
    target = str(receipt.get("target_state", ""))
    if target not in EVALUATION_MODE_BY_TARGET:
        raise ModelQualificationError("evaluation receipt target invalid")
    if receipt.get("evaluation_mode") != EVALUATION_MODE_BY_TARGET[target]:
        raise ModelQualificationError("evaluation mode/target mismatch")
    if receipt.get("required_registry_evidence_kind") != EVIDENCE_KIND_BY_TARGET[target]:
        raise ModelQualificationError("registry evidence kind mismatch")
    if receipt.get("verdict") not in {"SUPPORTED", "NOT_SUPPORTED", "BLOCKED"}:
        raise ModelQualificationError("evaluation receipt verdict invalid")
    _digest(receipt.get("model_definition_hash"), "model_definition_hash")
    _digest(receipt.get("model_artifact_hash"), "model_artifact_hash")
    _refs(receipt.get("question_refs", ()), "question_refs")
    artifacts = receipt.get("evaluation_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ModelQualificationError("evaluation artifacts missing")
    seen = set()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or not str(artifact.get("ref", "")):
            raise ModelQualificationError("evaluation artifact malformed")
        ref = str(artifact["ref"])
        if ref in seen:
            raise ModelQualificationError("evaluation artifact refs duplicated")
        seen.add(ref)
        _digest(artifact.get("content_hash"), "evaluation artifact content_hash")
    if not isinstance(receipt.get("sample_count"), int) or receipt["sample_count"] < 0:
        raise ModelQualificationError("evaluation sample_count invalid")
    if not isinstance(receipt.get("metrics"), Mapping) or not isinstance(receipt.get("thresholds"), Mapping):
        raise ModelQualificationError("evaluation metrics/thresholds invalid")
    if not isinstance(receipt.get("evaluated_at_ns"), int) or receipt["evaluated_at_ns"] < 0:
        raise ModelQualificationError("evaluation timing invalid")
    if receipt.get("evaluation_mode") == "WALK_FORWARD":
        for field in ("dataset_hash", "walk_forward_hash", "experiment_hash"):
            _digest(receipt.get(field), field)
    if receipt.get("authority") != QUALIFICATION_AUTHORITY:
        raise ModelQualificationError("qualification authority boundary changed")
    _validate_integrity(receipt, "evaluation receipt")


def build_transition_proposal(registry: ModelRegistry, receipt: Mapping[str, Any], *, proposed_at_ns: int) -> Mapping[str, Any]:
    validate_evaluation_receipt(receipt)
    if receipt["verdict"] != "SUPPORTED":
        raise ModelQualificationError("only SUPPORTED evaluation evidence may propose a transition")
    errors = validate_model_registry(registry.root, require_state=False)
    if errors:
        raise ModelQualificationError("model registry invalid: " + "; ".join(errors))
    state = registry.state()
    record = (state.get("models") or {}).get(receipt["model_ref"])
    if not isinstance(record, Mapping):
        raise ModelQualificationError("evaluation receipt model is not registered")
    if record.get("definition_hash") != receipt["model_definition_hash"]:
        raise ModelQualificationError("evaluation receipt model definition identity mismatch")
    if record.get("artifact_hash") != receipt["model_artifact_hash"]:
        raise ModelQualificationError("evaluation receipt model artifact identity mismatch")
    current = str(record.get("state", ""))
    target = str(receipt["target_state"])
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ModelQualificationError("evaluation evidence cannot propose illegal lifecycle edge %s -> %s" % (current, target))
    if int(proposed_at_ns) < int(receipt["evaluated_at_ns"]):
        raise ModelQualificationError("transition cannot be proposed before evidence is evaluated")
    body = {
        "schema_version": TRANSITION_PROPOSAL_SCHEMA_VERSION,
        "proposal_id": "MTP-%s" % receipt["integrity"]["content_hash"][:32],
        "model_ref": receipt["model_ref"],
        "model_definition_hash": receipt["model_definition_hash"],
        "model_artifact_hash": receipt["model_artifact_hash"],
        "from_state": current,
        "to_state": target,
        "evidence_kind": receipt["required_registry_evidence_kind"],
        "evaluation_receipt_hash": receipt["integrity"]["content_hash"],
        "proposed_at_ns": int(proposed_at_ns),
        "authority": dict(QUALIFICATION_AUTHORITY),
    }
    return _seal(body)


def validate_transition_proposal(proposal: Mapping[str, Any]) -> None:
    if proposal.get("schema_version") != TRANSITION_PROPOSAL_SCHEMA_VERSION:
        raise ModelQualificationError("transition proposal schema invalid")
    target = str(proposal.get("to_state", ""))
    source = str(proposal.get("from_state", ""))
    if target not in EVIDENCE_KIND_BY_TARGET or proposal.get("evidence_kind") != EVIDENCE_KIND_BY_TARGET[target]:
        raise ModelQualificationError("transition proposal evidence kind invalid")
    if target not in ALLOWED_TRANSITIONS.get(source, set()):
        raise ModelQualificationError("transition proposal lifecycle edge invalid")
    for field in ("model_definition_hash", "model_artifact_hash", "evaluation_receipt_hash"):
        _digest(proposal.get(field), field)
    if not proposal.get("model_ref") or not source or not isinstance(proposal.get("proposed_at_ns"), int) or proposal["proposed_at_ns"] < 0:
        raise ModelQualificationError("transition proposal identity/timing invalid")
    if proposal.get("authority") != QUALIFICATION_AUTHORITY:
        raise ModelQualificationError("transition proposal authority boundary changed")
    _validate_integrity(proposal, "transition proposal")


class QualificationEvidenceStore:
    """Immutable evidence/proposal store. It cannot alter model lifecycle."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.directory = self.root / "artifacts/evidence/models/qualification"

    def _persist(self, prefix: str, value: Mapping[str, Any]) -> Mapping[str, Any]:
        digest = str(value["integrity"]["content_hash"])
        path = self.directory / ("%s-%s.json" % (prefix, digest[:32]))
        payload = json.dumps(dict(value), indent=2, sort_keys=True) + "\n"
        with writer_lock(self.root):
            if path.is_file():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing != dict(value):
                    raise ModelQualificationError("qualification evidence identity conflict")
                return existing
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent))
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return dict(value)

    def persist_receipt(self, receipt: Mapping[str, Any]) -> Mapping[str, Any]:
        validate_evaluation_receipt(receipt)
        return self._persist("receipt", receipt)

    def persist_proposal(self, proposal: Mapping[str, Any]) -> Mapping[str, Any]:
        validate_transition_proposal(proposal)
        return self._persist("proposal", proposal)

    def receipt_path(self, digest: str) -> Path:
        value = _digest(digest, "evaluation_receipt_hash")
        return self.directory / ("receipt-%s.json" % value[:32])

    def proposal_path(self, digest: str) -> Path:
        value = _digest(digest, "proposal_hash")
        return self.directory / ("proposal-%s.json" % value[:32])


def apply_transition_proposal(root: Path, *, proposal_hash: str, occurred_at_ns: int) -> Mapping[str, Any]:
    """Apply one persisted proposal exclusively through ModelRegistry.transition."""
    root = root.resolve()
    store = QualificationEvidenceStore(root)
    proposal_path = store.proposal_path(proposal_hash)
    if not proposal_path.is_file():
        raise ModelQualificationError("persisted transition proposal not found")
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    validate_transition_proposal(proposal)
    if proposal["integrity"]["content_hash"] != str(proposal_hash).lower():
        raise ModelQualificationError("transition proposal hash/path mismatch")
    receipt_path = store.receipt_path(proposal["evaluation_receipt_hash"])
    if not receipt_path.is_file():
        raise ModelQualificationError("persisted evaluation receipt not found")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    validate_evaluation_receipt(receipt)
    if receipt["integrity"]["content_hash"] != proposal["evaluation_receipt_hash"]:
        raise ModelQualificationError("proposal/evaluation receipt hash mismatch")
    if receipt["verdict"] != "SUPPORTED":
        raise ModelQualificationError("persisted receipt does not support transition")
    registry = ModelRegistry(root)
    errors = validate_model_registry(root, require_state=False)
    if errors:
        raise ModelQualificationError("model registry invalid: " + "; ".join(errors))
    record = (registry.state().get("models") or {}).get(proposal["model_ref"])
    if not isinstance(record, Mapping):
        raise ModelQualificationError("proposal model no longer registered")
    evidence_ref = "qualification-receipt:%s" % receipt["integrity"]["content_hash"]
    if record.get("state") != proposal["from_state"]:
        if record.get("state") == proposal["to_state"] and record.get("last_evidence_refs") == [evidence_ref]:
            return record
        raise ModelQualificationError("model lifecycle moved after proposal; re-evaluation required")
    if record.get("definition_hash") != proposal["model_definition_hash"] or record.get("artifact_hash") != proposal["model_artifact_hash"]:
        raise ModelQualificationError("model identity changed after proposal")
    try:
        return registry.transition(
            proposal["model_ref"],
            proposal["to_state"],
            evidence_kind=proposal["evidence_kind"],
            evidence_refs=(evidence_ref,),
            occurred_at_ns=int(occurred_at_ns),
        )
    except ModelRegistryError as exc:
        raise ModelQualificationError("registry rejected qualification transition: %s" % exc) from exc
