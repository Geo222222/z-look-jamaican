"""Durable same-question Direction assembly from earned journal competence.

This is research/replay assembly only. It does not publish internal intelligence
or qualify a Benjamin handoff. Live Z9 DEGRADED remains insufficient context.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..context.status import market_context_status
from ..evaluation.question_journal import QuestionOutcomeJournal, validate_question_outcome_journal
from ..evaluation.question_outcome import QuestionBoundOutcome
from ..experts.adapters import implemented_baseline_expert_contracts, question_prediction_to_expert_claim
from ..experts.school import (
    ADAPTIVE_WEIGHT_POLICY_ID,
    EXPERT_AUTHORITY,
    assemble_expert_claims,
    build_competence_memory,
)
from ..intelligence.runtime import IntelligenceRuntime, validate_event_chain
from ..operations import canonical_hash
from ..prediction.question_bound import QuestionBoundPrediction
from ..prediction.question_journal import QuestionPredictionJournal, validate_question_prediction_journal
from .direction_loop import DIRECTION_QUESTION_REF, FORBIDDEN_FIELDS, HORIZON_NS, SUBJECT_ID


class DirectionAssemblyError(RuntimeError):
    pass


EXECUTABLE_DIRECTION_MODEL_IDS = ("NULL-PRIOR", "BOOK-IMBALANCE-LINEAR", "REPORTED-FLOW-LINEAR")


def _walk_forbidden(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            for forbidden in FORBIDDEN_FIELDS:
                if forbidden in lowered:
                    raise DirectionAssemblyError("assembly contains forbidden field %s" % key)
            _walk_forbidden(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_forbidden(item)


def _direction_contracts() -> Tuple[Mapping[str, Any], ...]:
    contracts = []
    for contract in implemented_baseline_expert_contracts():
        if DIRECTION_QUESTION_REF not in contract["question_refs"]:
            continue
        model_ids = tuple(str(ref).split("@", 1)[0] for ref in contract.get("model_refs") or ())
        if any(model_id in EXECUTABLE_DIRECTION_MODEL_IDS for model_id in model_ids):
            contracts.append(contract)
    if len(contracts) != 3:
        raise DirectionAssemblyError("expected three executable Direction experts")
    return tuple(contracts)


def _predictions(root: Path) -> Tuple[Tuple[QuestionBoundPrediction, int], ...]:
    errors = validate_question_prediction_journal(root)
    if errors:
        raise DirectionAssemblyError("question prediction journal invalid: " + "; ".join(errors))
    output = []
    for entry in QuestionPredictionJournal(root).entries():
        prediction = QuestionBoundPrediction.from_wire(entry.get("prediction", {}))
        output.append((prediction, int(entry["journaled_at_ns"])))
    return tuple(output)


def _outcomes(root: Path) -> Dict[str, QuestionBoundOutcome]:
    errors = validate_question_outcome_journal(root)
    if errors:
        raise DirectionAssemblyError("question outcome journal invalid: " + "; ".join(errors))
    output: Dict[str, QuestionBoundOutcome] = {}
    for entry in QuestionOutcomeJournal(root).entries():
        outcome = QuestionBoundOutcome.from_wire(entry.get("outcome", {}))
        if outcome.prediction_id in output:
            raise DirectionAssemblyError("prediction has duplicate question outcomes")
        output[outcome.prediction_id] = outcome
    return output


def _z9_view(root: Path) -> Mapping[str, Any]:
    status = market_context_status(root)
    latest = status.get("latest") if isinstance(status, Mapping) else None
    latest = latest if isinstance(latest, Mapping) else {}
    z9_status = str(latest.get("status") or "UNAVAILABLE")
    qualified = z9_status == "QUALIFIED"
    return {
        "status": z9_status,
        "qualified": qualified,
        "context_id": latest.get("context_id"),
        "content_hash": latest.get("content_hash") or latest.get("context_hash"),
        "cutoff_at_ns": latest.get("cutoff_at_ns"),
        "known_at_ns": latest.get("known_at_ns"),
        "current_context": {"subject_id": SUBJECT_ID} if not qualified else {"subject_id": SUBJECT_ID, "z9_status": z9_status},
    }


def _select_cutoff(
    rows: Sequence[Mapping[str, Any]],
    *,
    cutoff_at_ns: Optional[int],
) -> int:
    grouped: Dict[int, List[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["prediction"].cutoff_at_ns), []).append(row)
    if cutoff_at_ns is not None:
        if int(cutoff_at_ns) not in grouped or len(grouped[int(cutoff_at_ns)]) < 2:
            raise DirectionAssemblyError("requested Direction cutoff has fewer than two executable claims")
        return int(cutoff_at_ns)
    eligible = [cutoff for cutoff, items in grouped.items() if len(items) >= 2]
    if not eligible:
        raise DirectionAssemblyError("no Direction cutoff has two executable journaled claims")
    return max(eligible)


def assemble_direction_question(
    root: Path,
    *,
    known_at_ns: int,
    cutoff_at_ns: Optional[int] = None,
) -> Mapping[str, Any]:
    """Assemble executable Direction claims from durable journals and competence."""
    root = Path(root).resolve()
    known_at = int(known_at_ns)
    if known_at < 0:
        raise DirectionAssemblyError("known_at_ns must be non-negative")
    runtime = IntelligenceRuntime(root)
    chain_errors = validate_event_chain(runtime.events())
    if chain_errors:
        raise DirectionAssemblyError("expert intelligence journal invalid: " + "; ".join(chain_errors))
    state = runtime.state()
    persisted = state.get("competence")
    if isinstance(persisted, Mapping) and persisted.get("known_at_ns") is not None and int(persisted["known_at_ns"]) > known_at:
        raise DirectionAssemblyError("future competence cannot enter earlier assembly")
    contracts = _direction_contracts()
    contract_by_model = {}
    for contract in contracts:
        for model_ref in contract.get("model_refs") or ():
            contract_by_model[str(model_ref)] = contract
    outcomes = _outcomes(root)
    rows: List[Mapping[str, Any]] = []
    for prediction, journaled_at in _predictions(root):
        if journaled_at > known_at:
            continue
        if prediction.question_ref != DIRECTION_QUESTION_REF:
            continue
        if int(prediction.horizon_ns) != int(HORIZON_NS):
            continue
        if str(prediction.subject_id) != SUBJECT_ID:
            continue
        if len(prediction.model_refs) != 1:
            continue
        contract = contract_by_model.get(str(prediction.model_refs[0]))
        if contract is None:
            continue
        claim = question_prediction_to_expert_claim(contract, prediction)
        claim_hash = str(claim["integrity"]["content_hash"])
        recorded_claim = (state.get("claims") or {}).get(claim_hash)
        if not isinstance(recorded_claim, Mapping):
            raise DirectionAssemblyError("Direction claim is not in expert runtime: %s" % claim_hash)
        if recorded_claim.get("integrity", {}).get("content_hash") != claim_hash:
            raise DirectionAssemblyError("recorded Direction claim hash mismatch")
        outcome = outcomes.get(prediction.prediction_id)
        scored = any(str(item.get("claim_hash")) == claim_hash for item in (state.get("scores") or []) if isinstance(item, Mapping) and int(item.get("resolved_at_ns") or -1) <= known_at)
        rows.append(
            {
                "prediction": prediction,
                "contract": contract,
                "claim": claim,
                "outcome": outcome,
                "scored": scored,
            }
        )
    cutoff = _select_cutoff(rows, cutoff_at_ns=cutoff_at_ns)
    selected = [row for row in rows if int(row["prediction"].cutoff_at_ns) == cutoff]
    selected.sort(key=lambda item: (str(item["claim"]["expert_ref"]), str(item["claim"]["integrity"]["content_hash"])))
    for row in selected:
        if str(row["prediction"].subject_id) != SUBJECT_ID:
            raise DirectionAssemblyError("wrong subject cannot contribute")
        if row["prediction"].question_ref != DIRECTION_QUESTION_REF:
            raise DirectionAssemblyError("wrong-question claims cannot contribute")
        if not row["scored"]:
            raise DirectionAssemblyError("unresolved or unscored claims cannot contribute")
        outcome = row["outcome"]
        if outcome is None or outcome.status != "RESOLVED":
            raise DirectionAssemblyError("unresolved or unscored claims cannot contribute")
        if int(outcome.decided_at_ns) > known_at:
            raise DirectionAssemblyError("future outcomes cannot enter earlier assembly")
    current_hashes = {str(row["claim"]["integrity"]["content_hash"]) for row in selected}
    prior_scores = [
        item
        for item in (state.get("scores") or [])
        if isinstance(item, Mapping)
        and int(item.get("resolved_at_ns") or -1) <= known_at
        and str(item.get("claim_hash")) not in current_hashes
    ]
    weighting_memory = build_competence_memory(prior_scores, now_ns=known_at)
    z9 = _z9_view(root)
    claims = tuple(row["claim"] for row in selected)
    assembly = dict(assemble_expert_claims(claims, weighting_memory, dict(z9["current_context"]), assembly_at_ns=known_at))
    sample_counts = [int(item.get("sample_count") or 0) for item in assembly.get("expert_contributions") or []]
    max_weight = max((float(item.get("normalized_weight") or 0.0) for item in assembly.get("expert_contributions") or []), default=0.0)
    blocking = []
    if not z9["qualified"]:
        blocking.append("Z9_NOT_QUALIFIED")
    blocking.append("INSUFFICIENT_CONTEXTUAL_SUPPORT")
    if any(row["prediction"].mode != "PROSPECTIVE_SHADOW" for row in selected):
        blocking.append("EVIDENCE_CLASS_RESEARCH_ONLY")
    if max(sample_counts or [0]) < 20:
        blocking.append("SMALL_SAMPLE_SUPPORT")
    blocking.append("PROSPECTIVE_ASSEMBLY_NOT_QUALIFIED")
    envelope = {
        "schema_version": "1.0",
        "assembly_id": "QASM-%s" % canonical_hash({"assembly": assembly, "cutoff": cutoff, "known_at": known_at})[:32],
        "status": "RESEARCH_ONLY",
        "question_ref": DIRECTION_QUESTION_REF,
        "question_definition_hash": selected[0]["prediction"].question_definition_hash,
        "subject_id": SUBJECT_ID,
        "horizon_ns": HORIZON_NS,
        "cutoff_at_ns": cutoff,
        "known_at_ns": known_at,
        "contributing_prediction_ids": [row["prediction"].prediction_id for row in selected],
        "contributing_claim_hashes": [str(row["claim"]["integrity"]["content_hash"]) for row in selected],
        "competence_memory_hash": weighting_memory["integrity"]["content_hash"],
        "persisted_competence_hash": None if not isinstance(persisted, Mapping) else (persisted.get("integrity") or {}).get("content_hash"),
        "context_id": z9.get("context_id"),
        "context_hash": z9.get("content_hash"),
        "weight_policy_id": ADAPTIVE_WEIGHT_POLICY_ID,
        "weight_policy_version": assembly.get("weight_policy_version"),
        "assembled": assembly,
        "assembled_answer": assembly.get("assembled_estimate"),
        "disagreement": assembly.get("disagreement"),
        "evidence_independence": assembly.get("evidence_independence"),
        "sample_support": {
            "weighting_sample_counts": sample_counts,
            "max_weighting_n": max(sample_counts or [0]),
            "earned_competence_entry_count": 0 if not isinstance(persisted, Mapping) else int(persisted.get("entry_count") or 0),
        },
        "contextual_competence_status": "INSUFFICIENT_CONTEXTUAL_SUPPORT",
        "z9_status": z9["status"],
        "prospective_use": "BLOCKED",
        "prospective_blocked_reasons": blocking,
        "internal_intelligence_publication": "NOT_PUBLISHED",
        "benjamin_publication": "NOT_ELIGIBLE",
        "max_contributor_weight": max_weight,
        "authority": {
            **dict(EXPERT_AUTHORITY),
            "claims_competence": True,
            "sets_adaptive_weights": True,
            "adaptive_assembly_earned": False,
            "prospective_assembly": False,
            "internal_intelligence_published": False,
            "benjamin_eligible": False,
            "capital_decision": False,
        },
    }
    envelope["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash({key: value for key, value in envelope.items() if key != "integrity"})}
    _walk_forbidden(envelope)
    return envelope


def assemble_and_record_direction_question(
    root: Path,
    *,
    known_at_ns: int,
    cutoff_at_ns: Optional[int] = None,
) -> Mapping[str, Any]:
    envelope = assemble_direction_question(root, known_at_ns=known_at_ns, cutoff_at_ns=cutoff_at_ns)
    runtime = IntelligenceRuntime(root)
    existing = list(runtime.state().get("assemblies") or [])
    content_hash = envelope["integrity"]["content_hash"]
    if any((item.get("integrity") or {}).get("content_hash") == content_hash for item in existing if isinstance(item, Mapping)):
        return envelope
    runtime.record_assembly(envelope, occurred_at_ns=int(known_at_ns))
    return envelope


def direction_assembly_projection(root: Path) -> Mapping[str, Any]:
    root = Path(root).resolve()
    runtime = IntelligenceRuntime(root)
    assemblies = [item for item in (runtime.state().get("assemblies") or []) if isinstance(item, Mapping) and item.get("question_ref") == DIRECTION_QUESTION_REF]
    latest = assemblies[-1] if assemblies else None
    exists = latest is not None
    status = "ABSENT" if not exists else str(latest.get("status") or "RESEARCH_ONLY")
    return {
        "exists": exists,
        "status": status,
        "assembly_exists": "ASSEMBLY_EXISTS" if exists else "ASSEMBLY_ABSENT",
        "prospective_qualification": "BLOCKED" if exists else "NONE",
        "benjamin_eligible": False,
        "internal_intelligence_published": False,
        "contextual_competence_status": "INSUFFICIENT_CONTEXTUAL_SUPPORT",
        "count": len(assemblies),
        "latest": None
        if latest is None
        else {
            "assembly_id": latest.get("assembly_id"),
            "status": latest.get("status"),
            "question_ref": latest.get("question_ref"),
            "cutoff_at_ns": latest.get("cutoff_at_ns"),
            "known_at_ns": latest.get("known_at_ns"),
            "assembled_answer": latest.get("assembled_answer"),
            "disagreement": latest.get("disagreement"),
            "max_contributor_weight": latest.get("max_contributor_weight"),
            "sample_support": latest.get("sample_support"),
            "evidence_independence": latest.get("evidence_independence"),
            "contextual_competence_status": latest.get("contextual_competence_status"),
            "z9_status": latest.get("z9_status"),
            "prospective_use": latest.get("prospective_use"),
            "prospective_blocked_reasons": latest.get("prospective_blocked_reasons"),
            "contributors": (latest.get("assembled") or {}).get("expert_contributions"),
            "content_hash": (latest.get("integrity") or {}).get("content_hash"),
        },
        "authority": {
            "adaptive_assembly_earned": False,
            "prospective_assembly": False,
            "benjamin_eligible": False,
            "capital_decision": False,
        },
    }
