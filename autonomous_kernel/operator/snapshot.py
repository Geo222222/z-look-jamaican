"""Read-only Z1-Z9 operator snapshot assembled from durable kernel state."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from ..context.status import market_context_status
from ..models.qualification_status import qualification_evidence_status
from ..monitor import monitor_snapshot
from ..questions.catalog import default_question_registry_v1
from ..questions.certification import (
    QUESTION_REGISTRY_V1_QUALIFIED,
    build_question_registry_v1_qualified,
    certify_question_registry_v1,
)
from .contracts import STAGE_METADATA, command_catalog
from .intelligence_projection import expert_intelligence_projection
from .journal import validate_operator_journal
from .perception import perception_projection
from .research_projection import research_qualification_projection
from ..learning.direction_loop import question_learning_projection
from ..synthesis.service import market_synthesis_projection


def _json(root: Path, relative: str) -> Mapping[str, Any]:
    path = root / relative
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _count_jsonl(root: Path, relative: str) -> int:
    path = root / relative
    if not path.is_file():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def _count_items(value: Mapping[str, Any], key: str = "items") -> int:
    items = value.get(key)
    if isinstance(items, list):
        return len(items)
    if isinstance(items, Mapping):
        return len(items)
    return 0


def _stage_metric(stage_id: str, root: Path, perception: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    perception = perception or {}
    if stage_id == "Z1":
        state = _json(root, "state/canonical_market_data.json")
        observation_dir = root / "artifacts/market_data/observations"
        observer = perception.get("observer") if isinstance(perception.get("observer"), Mapping) else {}
        return {
            "canonical_batches": _count_items(state),
            "raw_observation_artifacts": len(list(observation_dir.glob("*.json"))) if observation_dir.is_dir() else 0,
            "feed_status": perception.get("feed_status") or "NO CURRENT EVIDENCE",
            "observer_status": observer.get("status"),
            "last_success_at": observer.get("last_success_at"),
            "age_ns": observer.get("age_ns"),
        }
    if stage_id == "Z2":
        return {
            "representation_frames": _count_items(_json(root, "state/representations.json")),
            "operational_status": perception.get("z2_status") or "NO CURRENT FRAME",
        }
    if stage_id == "Z3":
        state = _json(root, "state/prediction_journal.json")
        learning = question_learning_projection(root)
        return {
            "prediction_entries": int(state.get("entry_count", _count_jsonl(root, "memory/predictions.jsonl")) or 0),
            "question_bound_predictions": int(learning.get("prediction_count") or 0),
            "latest_question": (learning.get("latest_prediction") or {}).get("question_ref"),
            "latest_horizon_ns": (learning.get("latest_prediction") or {}).get("horizon_ns"),
            "latest_models": (learning.get("latest_prediction") or {}).get("model_refs"),
            "latest_cutoff_at_ns": (learning.get("latest_prediction") or {}).get("cutoff_at_ns"),
        }
    if stage_id in {"Z4", "Z5"}:
        state = _json(root, "state/model_registry.json")
        models = state.get("models")
        models = models if isinstance(models, Mapping) else {}
        counts: Dict[str, int] = {}
        for record in models.values():
            if isinstance(record, Mapping):
                model_state = str(record.get("state") or "UNKNOWN")
                counts[model_state] = counts.get(model_state, 0) + 1
        return {"registered_models": len(models), "lifecycle_counts": counts}
    if stage_id == "Z6":
        state = _json(root, "state/outcome_journal.json")
        learning = question_learning_projection(root)
        counts = learning.get("status_counts") if isinstance(learning.get("status_counts"), Mapping) else {}
        return {
            "outcome_entries": int(state.get("entry_count", _count_jsonl(root, "memory/outcomes.jsonl")) or 0),
            "question_bound_outcomes": int(learning.get("outcome_count") or 0),
            "resolved": int(counts.get("RESOLVED") or 0),
            "unresolvable": int(counts.get("UNRESOLVABLE") or 0),
            "pending": int(counts.get("PENDING") or 0),
        }
    if stage_id == "Z7":
        intel = expert_intelligence_projection(root)
        runtime = intel.get("runtime") if isinstance(intel.get("runtime"), Mapping) else {}
        return {
            "resolved_outcome_records": _count_jsonl(root, "memory/outcomes.jsonl"),
            "question_bound_scores": int(runtime.get("score_count") or 0),
            "competence_available": bool(runtime.get("competence_available")),
            "competence_is_reconstructed": True,
        }
    if stage_id == "Z8":
        state = _json(root, "state/assembly_journal.json")
        learning = question_learning_projection(root)
        assembly = learning.get("assembly") if isinstance(learning.get("assembly"), Mapping) else {}
        latest = assembly.get("latest") if isinstance(assembly.get("latest"), Mapping) else {}
        return {
            "assembly_entries": int(state.get("entry_count", _count_jsonl(root, "memory/assemblies.jsonl")) or 0),
            "contextual_assembly_entries": _count_jsonl(root, "memory/contextual_assemblies.jsonl"),
            "direction_assembly_count": int(assembly.get("count") or 0),
            "direction_assembly_status": assembly.get("status") or "ABSENT",
            "direction_research_only": latest.get("status") == "RESEARCH_ONLY",
            "direction_prospective_use": latest.get("prospective_use") or assembly.get("prospective_qualification") or "NONE",
            "liquidity_assembly_count": int((learning.get("liquidity_assembly") or {}).get("count") or 0),
            "liquidity_assembly_status": (learning.get("liquidity_assembly") or {}).get("status") or "ABSENT",
            "liquidity_research_only": ((learning.get("liquidity_assembly") or {}).get("latest") or {}).get("status") == "RESEARCH_ONLY",
        }
    if stage_id == "Z9":
        synthesis = market_synthesis_projection(root)
        latest = synthesis.get("latest") if isinstance(synthesis.get("latest"), Mapping) else {}
        return {
            "context_frames": _count_items(_json(root, "state/market_context.json")),
            "context_status": market_context_status(root),
            "operational_status": perception.get("z9_status") or "NO CURRENT FRAME",
            "market_synthesis_exists": bool(synthesis.get("exists")),
            "market_synthesis_status": synthesis.get("status") or "ABSENT",
            "market_synthesis_completeness": latest.get("completeness"),
            "market_synthesis_confidence": latest.get("synthesis_confidence"),
        }
    return {}


def _stage_availability(stage_id: str, metric: Mapping[str, Any]) -> str:
    if stage_id == "Z1":
        return str(metric.get("feed_status") or "NO CURRENT EVIDENCE")
    if stage_id == "Z2":
        return str(metric.get("operational_status") or "NO CURRENT FRAME")
    if stage_id == "Z9":
        return str(metric.get("operational_status") or "NO CURRENT FRAME")
    numeric = [value for value in metric.values() if isinstance(value, int) and not isinstance(value, bool)]
    if any(value > 0 for value in numeric):
        return "AVAILABLE"
    if stage_id in {"Z4", "Z5", "Z7", "Z8"}:
        return "CONSTRUCTED_NO_RUNTIME_RECORDS"
    return "NO_DURABLE_RUNTIME_RECORDS"


def _question_registry() -> Dict[str, Any]:
    base = default_question_registry_v1(registered_at_ns=0, effective_at_ns=0)
    qualified = build_question_registry_v1_qualified(base, known_at_ns=0, effective_at_ns=0)
    certificate = certify_question_registry_v1(qualified)
    questions = []
    for entry in qualified.entries:
        definition = entry.definition
        questions.append(
            {
                "question_ref": definition.question_ref,
                "question_id": definition.question_id,
                "version": definition.version,
                "family": definition.family.value,
                "scope": definition.scope.value,
                "asks": definition.asks,
                "horizon_ns": definition.horizon_ns,
                "answer_kind": definition.outcome.answer_kind.value,
                "outcome_metric_id": definition.outcome.metric_id,
                "resolver_policy_id": definition.outcome.resolver_policy_id,
                "resolver_implementation_ref": entry.resolver_implementation_ref,
                "lifecycle_state": entry.lifecycle_state,
                "definition_hash": definition.content_hash(),
                "evidence_cutoff_policy": definition.evidence_cutoff_policy,
                "required_artifact_types": list(definition.required_artifact_types),
                "required_feature_families": list(definition.required_feature_families),
                "parameters": dict(definition.parameters),
            }
        )
    active = [item for item in questions if item["lifecycle_state"] == "RESOLVER_READY"]
    retired = [item for item in questions if item["lifecycle_state"] == "RETIRED"]
    defined = [item for item in questions if item["lifecycle_state"] == "DEFINED"]
    return {
        "status": QUESTION_REGISTRY_V1_QUALIFIED,
        "registry": certificate["registry"],
        "certificate": certificate,
        "questions": questions,
        "summary": {
            "active_resolver_ready": len(active),
            "retired_historical": len(retired),
            "defined_historical": len(defined),
            "deferred_families": list(certificate["deferred_question_families"]),
        },
        "authority": certificate["authority"],
        "guarantees": certificate["guarantees"],
    }


def _certification(root: Path) -> Dict[str, Any]:
    z8 = _json(root, "artifacts/evidence/market/z8-certification-inventory-20260903.json")
    hist = _json(root, "artifacts/evidence/market/exp-z8-hist-real-001-result.json")
    prospective = _json(root, "artifacts/evidence/market/exp-z8-prospective-002-result.json")
    z9_policy = _json(root, "artifacts/evidence/market/z9-certification-policy-v1.json")
    question_registry = _question_registry()
    return {
        "question_registry": {
            "status": question_registry["status"],
            "registry_id": question_registry["registry"]["registry_id"],
            "version": question_registry["registry"]["version"],
            "content_hash": question_registry["registry"]["content_hash"],
            "certificate_hash": question_registry["certificate"]["integrity"]["content_hash"],
            "resolver_ready": question_registry["summary"]["active_resolver_ready"],
            "deferred_families": question_registry["summary"]["deferred_families"],
        },
        "z8_historical": {
            "experiment_id": hist.get("experiment_id", "EXP-Z8-HIST-REAL-001"),
            "decision": hist.get("qualification_decision") or hist.get("decision") or ((hist.get("qualification") or {}).get("decision")) or "NOT_EARNED",
            "result_hash": hist.get("result_hash") or (hist.get("integrity") or {}).get("result_content_hash") or (hist.get("integrity") or {}).get("content_hash"),
        },
        "z8_prospective": {
            "experiment_id": prospective.get("experiment_id", "EXP-Z8-PROSPECTIVE-002"),
            "decision": prospective.get("qualification_decision") or prospective.get("decision") or ((prospective.get("qualification") or {}).get("decision")) or "SINGLE_SESSION_PROSPECTIVE_MECHANISM_SUPPORTED",
            "result_hash": prospective.get("result_hash") or (prospective.get("integrity") or {}).get("result_content_hash") or (prospective.get("integrity") or {}).get("content_hash"),
        },
        "z8_inventory": z8,
        "z9": {
            "construction": "CERTIFIED",
            "market_wide_empirical": "DATA_BLOCKED",
            "spot_derivative_empirical": "DATA_BLOCKED",
            "contextual_performance": "DATA_BLOCKED",
            "policy_present": bool(z9_policy),
        },
    }


def build_operator_snapshot(root: Path) -> Dict[str, Any]:
    root = root.resolve()
    monitor = monitor_snapshot(root)
    perception = perception_projection(root)
    stages = []
    for metadata in STAGE_METADATA:
        metric = _stage_metric(str(metadata["id"]), root, perception)
        stages.append({**dict(metadata), "availability": _stage_availability(str(metadata["id"]), metric), "metrics": metric})
    journal_errors = validate_operator_journal(root)
    return {
        "contract": {
            "name": "zlj-operator-console",
            "schema_version": "1.2",
            "generated_at_ns": time.time_ns(),
            "authority": "read/control projection only; domain journals and services remain authoritative",
        },
        "system": {
            "mode": "SHADOW_ONLY",
            "live_execution": "LOCKED_FALSE",
            "capital_authority": "NONE",
            "operator_journal": "VALID" if not journal_errors else "INVALID",
            "operator_journal_errors": journal_errors,
        },
        "stages": stages,
        "perception": perception,
        "question_registry": _question_registry(),
        "question_learning": question_learning_projection(root),
        "market_synthesis": market_synthesis_projection(root),
        "expert_intelligence": expert_intelligence_projection(root),
        "research_qualification": research_qualification_projection(),
        "model_qualification": qualification_evidence_status(root),
        "certification": _certification(root),
        "controls": command_catalog(),
        "monitor": monitor,
    }
