"""Read-only Z1-Z9 operator snapshot assembled from durable kernel state."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Mapping

from ..context.status import market_context_status
from ..monitor import monitor_snapshot
from .contracts import STAGE_METADATA, command_catalog
from .journal import validate_operator_journal


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


def _stage_metric(stage_id: str, root: Path) -> Dict[str, Any]:
    if stage_id == "Z1":
        state = _json(root, "state/canonical_market_data.json")
        observation_dir = root / "artifacts/market_data/observations"
        return {
            "canonical_batches": _count_items(state),
            "raw_observation_artifacts": len(list(observation_dir.glob("*.json"))) if observation_dir.is_dir() else 0,
        }
    if stage_id == "Z2":
        return {"representation_frames": _count_items(_json(root, "state/representations.json"))}
    if stage_id == "Z3":
        state = _json(root, "state/prediction_journal.json")
        return {"prediction_entries": int(state.get("entry_count", _count_jsonl(root, "memory/predictions.jsonl")) or 0)}
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
        return {"outcome_entries": int(state.get("entry_count", _count_jsonl(root, "memory/outcomes.jsonl")) or 0)}
    if stage_id == "Z7":
        return {"resolved_outcome_records": _count_jsonl(root, "memory/outcomes.jsonl"), "competence_is_reconstructed": True}
    if stage_id == "Z8":
        state = _json(root, "state/assembly_journal.json")
        return {
            "assembly_entries": int(state.get("entry_count", _count_jsonl(root, "memory/assemblies.jsonl")) or 0),
            "contextual_assembly_entries": _count_jsonl(root, "memory/contextual_assemblies.jsonl"),
        }
    if stage_id == "Z9":
        return {"context_frames": _count_items(_json(root, "state/market_context.json")), "context_status": market_context_status(root)}
    return {}


def _stage_availability(stage_id: str, metric: Mapping[str, Any]) -> str:
    numeric = [value for value in metric.values() if isinstance(value, int) and not isinstance(value, bool)]
    if any(value > 0 for value in numeric):
        return "AVAILABLE"
    if stage_id in {"Z4", "Z5", "Z7", "Z8", "Z9"}:
        return "CONSTRUCTED_NO_RUNTIME_RECORDS"
    return "NO_DURABLE_RUNTIME_RECORDS"


def _certification(root: Path) -> Dict[str, Any]:
    z8 = _json(root, "artifacts/evidence/market/z8-certification-inventory-20260903.json")
    hist = _json(root, "artifacts/evidence/market/exp-z8-hist-real-001-result.json")
    prospective = _json(root, "artifacts/evidence/market/exp-z8-prospective-002-result.json")
    z9_policy = _json(root, "artifacts/evidence/market/z9-certification-policy-v1.json")
    return {
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
    stages = []
    for metadata in STAGE_METADATA:
        metric = _stage_metric(str(metadata["id"]), root)
        stages.append({**dict(metadata), "availability": _stage_availability(str(metadata["id"]), metric), "metrics": metric})
    journal_errors = validate_operator_journal(root)
    return {
        "contract": {
            "name": "zlj-operator-console",
            "schema_version": "1.0",
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
        "certification": _certification(root),
        "controls": command_catalog(),
        "monitor": monitor,
    }
