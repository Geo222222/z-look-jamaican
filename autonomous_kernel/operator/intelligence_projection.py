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
        "claims": {}, "scores": [], "competence": None, "assemblies": [], "publications": [], "event_count": len(events)
    }
    species_counts: Dict[str, int] = {}
    for expert in school["experts"]:
        species = str(expert["species"])
        species_counts[species] = species_counts.get(species, 0) + 1
    publications = state.get("publications") or []
    latest = publications[-1] if publications else None
    return {
        "construction": {
            "expert_contracts": "BUILT",
            "expert_curriculum": "BUILT",
            "operational_prediction_adapter": "BUILT",
            "question_specific_evaluation": "BUILT",
            "competence_memory": "BUILT",
            "contextual_competence": "BUILT",
            "adaptive_expert_assembly": "BUILT",
            "intelligence_publication": "BUILT",
        },
        "school": {
            "schema_version": school["schema_version"],
            "lifecycle_state": school["lifecycle_state"],
            "curriculum_expert_count": school["expert_count"],
            "implemented_expert_count": implemented["implemented_expert_count"],
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
            "latest_publication": latest,
        },
        "qualification": {
            "expert_population": "IMPLEMENTED_CANDIDATES_PRESENT" if implemented["implemented_expert_count"] else "CURRICULUM_ONLY",
            "earned_competence": "AVAILABLE" if state.get("competence") else "NOT_YET_EARNED",
            "benjamin_handoff": "AVAILABLE" if latest else "NO_RUNTIME_PUBLICATION",
            "live_capital_authority": "NONE",
        },
    }
