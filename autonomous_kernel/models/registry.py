from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..operations import canonical_hash
from ..store import writer_lock
from .contracts import ModelDefinition


MODEL_STATES = {
    "CANDIDATE",
    "REPLAY_QUALIFIED",
    "WALK_FORWARD_QUALIFIED",
    "SHADOW",
    "QUALIFIED",
    "DEGRADED",
    "QUARANTINED",
    "SUPERSEDED",
}

ALLOWED_TRANSITIONS = {
    "CANDIDATE": {"REPLAY_QUALIFIED", "QUARANTINED"},
    "REPLAY_QUALIFIED": {"WALK_FORWARD_QUALIFIED", "DEGRADED", "QUARANTINED"},
    "WALK_FORWARD_QUALIFIED": {"SHADOW", "DEGRADED", "QUARANTINED"},
    "SHADOW": {"QUALIFIED", "DEGRADED", "QUARANTINED"},
    "QUALIFIED": {"DEGRADED", "QUARANTINED", "SUPERSEDED"},
    "DEGRADED": {"SHADOW", "QUARANTINED", "SUPERSEDED"},
    "QUARANTINED": set(),
    "SUPERSEDED": set(),
}

EVIDENCE_KIND_BY_TARGET = {
    "REPLAY_QUALIFIED": "REPLAY_EVALUATION",
    "WALK_FORWARD_QUALIFIED": "WALK_FORWARD_EVALUATION",
    "SHADOW": "SHADOW_EVALUATION",
    "QUALIFIED": "QUALIFICATION_DECISION",
    "DEGRADED": "MONITORING_EVIDENCE",
    "QUARANTINED": "INTEGRITY_EVIDENCE",
    "SUPERSEDED": "SUCCESSION_EVIDENCE",
}


class ModelRegistryError(RuntimeError):
    pass


def _digest(value: str, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise ModelRegistryError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise ModelRegistryError("%s must be hexadecimal" % field) from exc
    return text


def _refs(values: Sequence[str], field: str, *, allow_empty: bool = False) -> Tuple[str, ...]:
    result = tuple(str(value) for value in values)
    if (not result and not allow_empty) or any(not value for value in result) or len(set(result)) != len(result):
        raise ModelRegistryError("%s must contain unique non-empty values" % field)
    return result


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(value), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _event_body(sequence: int, event_type: str, model_ref: str, occurred_at_ns: int, payload: Mapping[str, Any], previous_hash: str) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "sequence": int(sequence),
        "event_type": event_type,
        "model_ref": model_ref,
        "occurred_at_ns": int(occurred_at_ns),
        "payload": dict(payload),
        "previous_hash": previous_hash,
    }


def _event_wire(sequence: int, event_type: str, model_ref: str, occurred_at_ns: int, payload: Mapping[str, Any], previous_hash: str) -> Dict[str, Any]:
    body = _event_body(sequence, event_type, model_ref, occurred_at_ns, payload, previous_hash)
    value = dict(body)
    value["event_hash"] = canonical_hash(body)
    return value


class ModelRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.state_path = self.root / "state/model_registry.json"
        self.events_path = self.root / "memory/model_transitions.jsonl"

    def state(self) -> Dict[str, Any]:
        if not self.state_path.is_file():
            return {
                "schema_version": 1,
                "authority": "governed model lifecycle; model code cannot self-certify",
                "models": {},
                "last_event_hash": None,
                "event_count": 0,
            }
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ModelRegistryError("model registry state must be an object")
        return value

    def events(self) -> Tuple[Mapping[str, Any], ...]:
        if not self.events_path.is_file():
            return ()
        output: List[Mapping[str, Any]] = []
        for line_number, line in enumerate(self.events_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ModelRegistryError("model transition line %d is invalid JSON" % line_number) from exc
            if not isinstance(value, dict):
                raise ModelRegistryError("model transition line must be an object")
            output.append(value)
        return tuple(output)

    def register(
        self,
        definition: ModelDefinition,
        *,
        artifact_hash: str,
        code_ref: str,
        training_data_refs: Sequence[str] = (),
        occurred_at_ns: int,
    ) -> Mapping[str, Any]:
        artifact = _digest(artifact_hash, "artifact_hash")
        if not code_ref:
            raise ModelRegistryError("code_ref is required")
        training = _refs(training_data_refs, "training_data_refs", allow_empty=True)
        with writer_lock(self.root):
            errors = validate_model_registry(self.root, require_state=False)
            if errors:
                raise ModelRegistryError("existing model registry invalid: " + "; ".join(errors))
            state = self.state()
            existing = state["models"].get(definition.model_ref)
            payload = {
                "definition": definition.to_wire(),
                "definition_hash": definition.content_hash(),
                "artifact_hash": artifact,
                "code_ref": code_ref,
                "training_data_refs": list(training),
                "initial_state": "CANDIDATE",
            }
            if existing is not None:
                expected = {
                    "definition": existing["definition"],
                    "definition_hash": existing["definition_hash"],
                    "artifact_hash": existing["artifact_hash"],
                    "code_ref": existing["code_ref"],
                    "training_data_refs": existing["training_data_refs"],
                    "initial_state": "CANDIDATE",
                }
                if expected != payload:
                    raise ModelRegistryError("model_ref already registered with different artifact identity")
                return existing
            event = self._append_event("MODEL_REGISTERED", definition.model_ref, int(occurred_at_ns), payload)
            state = self.state()
            record = {
                "model_ref": definition.model_ref,
                "definition": definition.to_wire(),
                "definition_hash": definition.content_hash(),
                "artifact_hash": artifact,
                "code_ref": code_ref,
                "training_data_refs": list(training),
                "state": "CANDIDATE",
                "registered_at_ns": int(occurred_at_ns),
                "updated_at_ns": int(occurred_at_ns),
                "last_evidence_kind": None,
                "last_evidence_refs": [],
                "last_event_hash": event["event_hash"],
            }
            state["models"][definition.model_ref] = record
            self._persist_state(state, event)
            return record

    def transition(
        self,
        model_ref: str,
        target_state: str,
        *,
        evidence_kind: str,
        evidence_refs: Sequence[str],
        occurred_at_ns: int,
    ) -> Mapping[str, Any]:
        if target_state not in MODEL_STATES:
            raise ModelRegistryError("unknown target model state")
        expected_kind = EVIDENCE_KIND_BY_TARGET.get(target_state)
        if expected_kind is None or evidence_kind != expected_kind:
            raise ModelRegistryError("%s requires evidence kind %s" % (target_state, expected_kind))
        refs = _refs(evidence_refs, "evidence_refs")
        with writer_lock(self.root):
            errors = validate_model_registry(self.root, require_state=False)
            if errors:
                raise ModelRegistryError("existing model registry invalid: " + "; ".join(errors))
            state = self.state()
            record = state["models"].get(model_ref)
            if record is None:
                raise ModelRegistryError("model is not registered")
            current = str(record["state"])
            if target_state == current:
                if record.get("last_evidence_kind") == evidence_kind and record.get("last_evidence_refs") == list(refs):
                    return record
                raise ModelRegistryError("same-state transition with different evidence is not idempotent")
            if target_state not in ALLOWED_TRANSITIONS[current]:
                raise ModelRegistryError("illegal model transition: %s -> %s" % (current, target_state))
            payload = {
                "from_state": current,
                "to_state": target_state,
                "evidence_kind": evidence_kind,
                "evidence_refs": list(refs),
            }
            event = self._append_event("MODEL_TRANSITION", model_ref, int(occurred_at_ns), payload)
            state = self.state()
            record = state["models"][model_ref]
            record["state"] = target_state
            record["updated_at_ns"] = int(occurred_at_ns)
            record["last_evidence_kind"] = evidence_kind
            record["last_evidence_refs"] = list(refs)
            record["last_event_hash"] = event["event_hash"]
            self._persist_state(state, event)
            return record

    def eligible(self, model_ref: str, purpose: str) -> bool:
        record = self.state().get("models", {}).get(model_ref)
        if record is None:
            return False
        state = record.get("state")
        if purpose == "QUALIFIED_SERVING":
            return state == "QUALIFIED"
        if purpose == "SHADOW_EVALUATION":
            return state in {"SHADOW", "QUALIFIED"}
        if purpose == "HISTORICAL_RESEARCH":
            return state not in {"QUARANTINED", "SUPERSEDED"}
        raise ModelRegistryError("unknown eligibility purpose")

    def _append_event(self, event_type: str, model_ref: str, occurred_at_ns: int, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        events = self.events()
        previous_hash = str(events[-1]["event_hash"]) if events else "GENESIS"
        event = _event_wire(len(events), event_type, model_ref, occurred_at_ns, payload, previous_hash)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def _persist_state(self, state: Dict[str, Any], last_event: Mapping[str, Any]) -> None:
        state["schema_version"] = 1
        state["authority"] = "governed model lifecycle; model code cannot self-certify"
        state["event_count"] = int(last_event["sequence"]) + 1
        state["last_event_hash"] = str(last_event["event_hash"])
        _atomic_json(self.state_path, state)


def validate_model_registry(root: Path, *, require_state: bool = True) -> List[str]:
    registry = ModelRegistry(root)
    errors: List[str] = []
    try:
        events = registry.events()
    except ModelRegistryError as exc:
        return [str(exc)]
    previous = "GENESIS"
    seen_registration = set()
    last_state: Dict[str, str] = {}
    for index, event in enumerate(events):
        body = {key: value for key, value in event.items() if key != "event_hash"}
        if event.get("schema_version") != 1 or event.get("sequence") != index:
            errors.append("model transition sequence %d schema/sequence mismatch" % index)
        if event.get("previous_hash") != previous:
            errors.append("model transition sequence %d previous_hash mismatch" % index)
        expected_hash = canonical_hash(body)
        if event.get("event_hash") != expected_hash:
            errors.append("model transition sequence %d event_hash mismatch" % index)
        model_ref = str(event.get("model_ref", ""))
        payload = event.get("payload", {})
        if event.get("event_type") == "MODEL_REGISTERED":
            if model_ref in seen_registration:
                errors.append("model %s registered more than once" % model_ref)
            seen_registration.add(model_ref)
            last_state[model_ref] = "CANDIDATE"
        elif event.get("event_type") == "MODEL_TRANSITION":
            current = last_state.get(model_ref)
            from_state = payload.get("from_state") if isinstance(payload, dict) else None
            to_state = payload.get("to_state") if isinstance(payload, dict) else None
            if current is None or from_state != current or to_state not in ALLOWED_TRANSITIONS.get(current, set()):
                errors.append("model %s has invalid transition at sequence %d" % (model_ref, index))
            else:
                last_state[model_ref] = str(to_state)
        else:
            errors.append("model transition sequence %d has unknown event_type" % index)
        previous = expected_hash

    if not registry.state_path.is_file():
        if require_state and (root / "state/current_state.json").is_file():
            errors.append("missing required state file: state/model_registry.json")
        return errors
    try:
        state = registry.state()
    except (json.JSONDecodeError, ModelRegistryError) as exc:
        errors.append("model registry state unreadable: %s" % exc)
        return errors
    if state.get("schema_version") != 1 or not isinstance(state.get("models"), dict):
        errors.append("model registry state schema invalid")
        return errors
    if state.get("event_count") != len(events):
        errors.append("model registry event_count mismatch")
    expected_last_hash = None if not events else events[-1].get("event_hash")
    if state.get("last_event_hash") != expected_last_hash:
        errors.append("model registry last_event_hash mismatch")
    for model_ref, expected_state in last_state.items():
        record = state["models"].get(model_ref)
        if record is None:
            errors.append("model registry state missing %s" % model_ref)
            continue
        if record.get("state") != expected_state:
            errors.append("model registry state projection mismatch for %s" % model_ref)
        definition = record.get("definition")
        if not isinstance(definition, dict) or definition.get("integrity", {}).get("content_hash") != record.get("definition_hash"):
            errors.append("model registry definition hash mismatch for %s" % model_ref)
        try:
            _digest(str(record.get("artifact_hash", "")), "artifact_hash")
        except ModelRegistryError as exc:
            errors.append("%s: %s" % (model_ref, exc))
    return errors
