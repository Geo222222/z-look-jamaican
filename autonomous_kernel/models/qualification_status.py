from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping

from .qualification import (
    ModelQualificationError,
    validate_evaluation_receipt,
    validate_transition_proposal,
)


def validate_qualification_evidence_store(root: Path) -> List[str]:
    root = root.resolve()
    directory = root / "artifacts/evidence/models/qualification"
    if not directory.is_dir():
        return []
    errors: List[str] = []
    receipts: Dict[str, Mapping[str, Any]] = {}
    proposals: Dict[str, Mapping[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append("%s: unreadable qualification evidence: %s" % (path.name, exc))
            continue
        if not isinstance(value, Mapping):
            errors.append("%s: qualification evidence must be an object" % path.name)
            continue
        try:
            if path.name.startswith("receipt-"):
                validate_evaluation_receipt(value)
                digest = str(value["integrity"]["content_hash"])
                if path.name != "receipt-%s.json" % digest[:32]:
                    errors.append("%s: receipt filename/hash mismatch" % path.name)
                if digest in receipts:
                    errors.append("%s: duplicate receipt content hash" % path.name)
                receipts[digest] = value
            elif path.name.startswith("proposal-"):
                validate_transition_proposal(value)
                digest = str(value["integrity"]["content_hash"])
                if path.name != "proposal-%s.json" % digest[:32]:
                    errors.append("%s: proposal filename/hash mismatch" % path.name)
                if digest in proposals:
                    errors.append("%s: duplicate proposal content hash" % path.name)
                proposals[digest] = value
            else:
                errors.append("%s: unknown qualification evidence file type" % path.name)
        except (ModelQualificationError, KeyError, TypeError, ValueError) as exc:
            errors.append("%s: %s" % (path.name, exc))
    for digest, proposal in proposals.items():
        receipt_hash = str(proposal.get("evaluation_receipt_hash", ""))
        receipt = receipts.get(receipt_hash)
        if receipt is None:
            errors.append("proposal-%s: referenced evaluation receipt is missing" % digest[:32])
            continue
        if receipt.get("model_ref") != proposal.get("model_ref"):
            errors.append("proposal-%s: model_ref differs from receipt" % digest[:32])
        if receipt.get("model_definition_hash") != proposal.get("model_definition_hash"):
            errors.append("proposal-%s: model definition differs from receipt" % digest[:32])
        if receipt.get("model_artifact_hash") != proposal.get("model_artifact_hash"):
            errors.append("proposal-%s: model artifact differs from receipt" % digest[:32])
        if receipt.get("target_state") != proposal.get("to_state"):
            errors.append("proposal-%s: target state differs from receipt" % digest[:32])
        if receipt.get("required_registry_evidence_kind") != proposal.get("evidence_kind"):
            errors.append("proposal-%s: evidence kind differs from receipt" % digest[:32])
    return errors


def qualification_evidence_status(root: Path) -> Mapping[str, Any]:
    root = root.resolve()
    directory = root / "artifacts/evidence/models/qualification"
    receipt_count = len(list(directory.glob("receipt-*.json"))) if directory.is_dir() else 0
    proposal_count = len(list(directory.glob("proposal-*.json"))) if directory.is_dir() else 0
    errors = validate_qualification_evidence_store(root)
    return {
        "status": "VALID" if not errors else "INVALID",
        "receipt_count": receipt_count,
        "proposal_count": proposal_count,
        "errors": errors,
        "authority": "evidence and proposals only; ModelRegistry remains lifecycle authority",
    }
