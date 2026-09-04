from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

from ..experts.adapters import operational_expert_inventory
from ..experts.school import build_baseline_expert_school
from ..intelligence.runtime import IntelligenceRuntime, validate_event_chain


def expert_intelligence_projection(root: Path) -> Mapping[str, Any]:
    school = build_baseline_expert_school()
    implemented = operational_expert_inventory()
    runtime = IntelligenceRuntime(root)
    events = runtime.events()
    errors = validate_event_chain(events)
    state = runtime.state() if not errors else {
        "claims": {},
        "scores": [],
        "competence": None,
        "assemblies": [],
        "publications": [],
        "qualifications": [],
        "handoffs": [],
        "event_count": len(events),
    }
    species_counts: Dict[str, int] = {}
    for expert in school["experts"]:
        species = str(expert["species"])
        species_counts[species] = species_counts.get(species, 0) + 1
    publications = list(state.get("publications") or [])
    qualifications = list(state.get("qualifications") or [])
    handoffs = list(state.get("handoffs") or [])
    latest_publication = publications[-1] if publications else None
    latest_qualification = qualifications[-1] if qualifications else None
    latest_handoff = handoffs[-1] if handoffs else None
    if latest_qualification is None:
        eligibility = "NONE"
        benjamin_handoff = "NO_RUNTIME_PUBLICATION" if not publications else "NOT_QUALIFIED"
    elif latest_qualification.get("status") == "ELIGIBLE" and latest_handoff:
        eligibility = "ELIGIBLE"
        benjamin_handoff = "HANDOFF_PUBLISHED"
    elif latest_qualification.get("status") == "ELIGIBLE":
        eligibility = "ELIGIBLE"
        benjamin_handoff = "ELIGIBLE"
    else:
        eligibility = "BLOCKED"
        benjamin_handoff = "BLOCKED"
    return {
        "construction": {
            "expert_contracts": "BUILT",
            "expert_curriculum": "BUILT",
            "operational_prediction_adapter": "BUILT",
            "journal_learning_sync": "BUILT",
            "question_specific_evaluation": "BUILT",
            "competence_memory": "BUILT",
            "contextual_competence": "BUILT",
            "adaptive_expert_assembly": "BUILT",
            "intelligence_publication": "BUILT",
            "benjamin_publication_gate": "BUILT",
        },
        "school": {
            "schema_version": school["schema_version"],
            "lifecycle_state": school["lifecycle_state"],
            "curriculum_expert_count": school["expert_count"],
            "implemented_expert_count": implemented["implemented_expert_count"],
            "candidate_model_expert_count": implemented["candidate_model_expert_count"],
            "benchmark_expert_count": implemented["benchmark_expert_count"],
            "implemented_by_question": implemented["by_question"],
            "implemented_by_species": implemented["by_species"],
            "species_counts": species_counts,
            "claims_competence": school["claims_competence"],
            "sets_adaptive_weights": school["sets_adaptive_weights"],
            "authority": school["authority"],
            "curriculum_hash": school["integrity"]["content_hash"],
            "implemented_inventory_hash": implemented["integrity"]["content_hash"],
        },
        "runtime": {
            "journal": "VALID" if not errors else "INVALID",
            "journal_errors": list(errors),
            "event_count": int(state.get("event_count", 0) or 0),
            "claim_count": len(state.get("claims") or {}),
            "score_count": len(state.get("scores") or []),
            "competence_available": bool(state.get("competence")),
            "assembly_count": len(state.get("assemblies") or []),
            "publication_count": len(publications),
            "internal_intelligence_exists": bool(publications),
            "latest_publication": latest_publication,
            "qualification_count": len(qualifications),
            "handoff_count": len(handoffs),
            "latest_qualification": latest_qualification,
            "latest_handoff": latest_handoff,
            "benjamin": {
                "eligibility_status": eligibility,
                "blocking_reasons": list((latest_qualification or {}).get("blocking_reasons") or []),
                "policy_version": None if latest_qualification is None else (latest_qualification.get("policy") or {}).get("policy_version"),
                "qualification_timestamp_ns": None if latest_qualification is None else latest_qualification.get("qualification_cutoff_ns"),
                "handoff_count": len(handoffs),
                "latest_handoff": latest_handoff,
            },
        },
        "qualification": {
            "expert_population": "IMPLEMENTED_CANDIDATES_PRESENT" if implemented["implemented_expert_count"] else "CURRICULUM_ONLY",
            "earned_competence": "AVAILABLE" if state.get("competence") else "NOT_YET_EARNED",
            "internal_intelligence": "AVAILABLE" if publications else "NO_RUNTIME_PUBLICATION",
            "benjamin_eligibility": eligibility,
            "benjamin_handoff": benjamin_handoff,
            "live_capital_authority": "NONE",
        },
    }
