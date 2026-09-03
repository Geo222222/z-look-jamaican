"""Governed durable registry for Z9 ModelContextProfile artifacts.

A context profile is an immutable declaration of which Z9 facts may influence
one exact Z5 model identity. Registration and activation are separate:
existence does not imply authority, and active policy is resolved as-of the
contextual assembly time for deterministic replay.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..models.registry import ModelRegistry, validate_model_registry
from ..operations import canonical_hash
from ..store import writer_lock
from .contextual import ModelContextProfile


PROFILE_REGISTRY_AUTHORITY = "governed immutable Z9 model-context profiles; activation is explicit and time-versioned"
PROFILE_ARTIFACT_SCHEMA_VERSION = 1
PROFILE_EVENT_SCHEMA_VERSION = 1


class ModelContextProfileRegistryError(RuntimeError):
    pass


def _refs(values: Sequence[str], field: str) -> Tuple[str, ...]:
    result = tuple(str(value) for value in values)
    if not result or any(not value for value in result) or len(result) != len(set(result)):
        raise ModelContextProfileRegistryError("%s must contain unique non-empty values" % field)
    return result


def _digest(value: str, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise ModelContextProfileRegistryError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise ModelContextProfileRegistryError("%s must be hexadecimal" % field) from exc
    return text


def _profile_id(profile: ModelContextProfile) -> str:
    return "MCP-%s" % profile.content_hash()[:32]


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


def _profile_from_wire(value: Mapping[str, Any]) -> ModelContextProfile:
    preferred = value.get("preferred_regimes")
    adverse = value.get("adverse_regimes")
    dependencies = value.get("feature_dependencies")
    if not isinstance(preferred, Mapping) or not isinstance(adverse, Mapping) or not isinstance(dependencies, list):
        raise ModelContextProfileRegistryError("model context profile wire is malformed")
    profile = ModelContextProfile(
        model_ref=str(value.get("model_ref", "")),
        feature_dependencies=tuple(str(item) for item in dependencies),
        preferred_regimes={str(key): tuple(str(item) for item in items) for key, items in preferred.items() if isinstance(items, list)},
        adverse_regimes={str(key): tuple(str(item) for item in items) for key, items in adverse.items() if isinstance(items, list)},
        diversity_group=str(value.get("diversity_group", "")),
        profile_version=str(value.get("profile_version", "")),
    )
    if profile.to_wire() != dict(value):
        raise ModelContextProfileRegistryError("model context profile integrity/content mismatch")
    return profile


def _event_body(sequence: int, event_type: str, model_ref: str, occurred_at_ns: int, payload: Mapping[str, Any], previous_hash: str) -> Dict[str, Any]:
    return {
        "schema_version": PROFILE_EVENT_SCHEMA_VERSION,
        "sequence": int(sequence),
        "event_type": str(event_type),
        "model_ref": str(model_ref),
        "occurred_at_ns": int(occurred_at_ns),
        "payload": dict(payload),
        "previous_hash": str(previous_hash),
    }


def _event_wire(sequence: int, event_type: str, model_ref: str, occurred_at_ns: int, payload: Mapping[str, Any], previous_hash: str) -> Dict[str, Any]:
    body = _event_body(sequence, event_type, model_ref, occurred_at_ns, payload, previous_hash)
    value = dict(body)
    value["event_hash"] = canonical_hash(body)
    return value


def _empty_projection() -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "authority": PROFILE_REGISTRY_AUTHORITY,
        "profiles": {},
        "active_by_model": {},
        "last_event_hash": None,
        "event_count": 0,
    }


def _validate_event_chain(events: Sequence[Mapping[str, Any]]) -> List[str]:
    errors: List[str] = []
    previous = "GENESIS"
    registered: Dict[str, Mapping[str, Any]] = {}
    version_identity: Dict[Tuple[str, str], str] = {}
    active: Dict[str, str] = {}
    last_time: Dict[str, int] = {}

    for index, event in enumerate(events):
        body = {key: value for key, value in event.items() if key != "event_hash"}
        if event.get("schema_version") != PROFILE_EVENT_SCHEMA_VERSION or event.get("sequence") != index:
            errors.append("context-profile event %d schema/sequence mismatch" % index)
        if event.get("previous_hash") != previous:
            errors.append("context-profile event %d previous_hash mismatch" % index)
        expected_hash = canonical_hash(body)
        if event.get("event_hash") != expected_hash:
            errors.append("context-profile event %d event_hash mismatch" % index)

        model_ref = str(event.get("model_ref", ""))
        occurred = event.get("occurred_at_ns")
        payload = event.get("payload")
        if not model_ref:
            errors.append("context-profile event %d lacks model_ref" % index)
        if not isinstance(occurred, int) or occurred < 0:
            errors.append("context-profile event %d has invalid occurred_at_ns" % index)
        elif model_ref in last_time and occurred < last_time[model_ref]:
            errors.append("context-profile time moved backwards for %s at sequence %d" % (model_ref, index))
        elif model_ref:
            last_time[model_ref] = occurred
        if not isinstance(payload, Mapping):
            errors.append("context-profile event %d payload invalid" % index)
            previous = expected_hash
            continue

        event_type = str(event.get("event_type", ""))
        profile_id = str(payload.get("profile_id", ""))
        profile_hash = str(payload.get("profile_hash", ""))
        profile_version = str(payload.get("profile_version", ""))
        try:
            _digest(profile_hash, "profile_hash")
        except ModelContextProfileRegistryError as exc:
            errors.append("context-profile event %d: %s" % (index, exc))

        if event_type == "PROFILE_REGISTERED":
            if not profile_id or profile_id in registered:
                errors.append("context-profile registration identity duplicated or missing at sequence %d" % index)
            key = (model_ref, profile_version)
            if not profile_version:
                errors.append("context-profile registration version missing at sequence %d" % index)
            elif key in version_identity and version_identity[key] != profile_id:
                errors.append("context-profile version identity drift for %s %s" % key)
            else:
                version_identity[key] = profile_id
            try:
                _digest(str(payload.get("model_definition_hash", "")), "model_definition_hash")
                _digest(str(payload.get("model_artifact_hash", "")), "model_artifact_hash")
                _digest(str(payload.get("artifact_content_hash", "")), "artifact_content_hash")
                _refs(payload.get("evidence_refs", []), "evidence_refs")
            except (ModelContextProfileRegistryError, TypeError) as exc:
                errors.append("context-profile registration %s invalid: %s" % (profile_id, exc))
            registered[profile_id] = dict(payload)

        elif event_type == "PROFILE_ACTIVATED":
            record = registered.get(profile_id)
            if record is None:
                errors.append("context-profile activation references unregistered profile %s" % profile_id)
            else:
                if record.get("model_ref") != model_ref or record.get("profile_hash") != profile_hash or record.get("profile_version") != profile_version:
                    errors.append("context-profile activation identity mismatch for %s" % profile_id)
                expected_previous = active.get(model_ref)
                if payload.get("previous_profile_id") != expected_previous:
                    errors.append("context-profile activation previous profile mismatch for %s" % model_ref)
                try:
                    _refs(payload.get("evidence_refs", []), "evidence_refs")
                except (ModelContextProfileRegistryError, TypeError) as exc:
                    errors.append("context-profile activation %s invalid: %s" % (profile_id, exc))
                active[model_ref] = profile_id
        else:
            errors.append("context-profile event %d has unknown event_type" % index)

        previous = expected_hash
    return errors


def _project_events(events: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    state = _empty_projection()
    for event in events:
        payload = event["payload"]
        model_ref = str(event["model_ref"])
        occurred = int(event["occurred_at_ns"])
        if event["event_type"] == "PROFILE_REGISTERED":
            profile_id = str(payload["profile_id"])
            state["profiles"][profile_id] = {
                "profile_id": profile_id,
                "model_ref": model_ref,
                "profile_version": payload["profile_version"],
                "profile_hash": payload["profile_hash"],
                "artifact_path": payload["artifact_path"],
                "artifact_content_hash": payload["artifact_content_hash"],
                "model_definition_hash": payload["model_definition_hash"],
                "model_artifact_hash": payload["model_artifact_hash"],
                "registered_at_ns": occurred,
                "registration_evidence_refs": list(payload["evidence_refs"]),
                "registration_event_hash": event["event_hash"],
            }
        else:
            profile_id = str(payload["profile_id"])
            state["active_by_model"][model_ref] = {
                "profile_id": profile_id,
                "profile_hash": payload["profile_hash"],
                "profile_version": payload["profile_version"],
                "activated_at_ns": occurred,
                "activation_evidence_refs": list(payload["evidence_refs"]),
                "activation_event_hash": event["event_hash"],
            }
    state["event_count"] = len(events)
    state["last_event_hash"] = None if not events else events[-1]["event_hash"]
    return state


def _artifact_body(profile: ModelContextProfile, model_record: Mapping[str, Any], registered_at_ns: int, evidence_refs: Sequence[str]) -> Dict[str, Any]:
    return {
        "schema_version": PROFILE_ARTIFACT_SCHEMA_VERSION,
        "profile_id": _profile_id(profile),
        "profile": profile.to_wire(),
        "profile_hash": profile.content_hash(),
        "model_identity": {
            "model_ref": profile.model_ref,
            "model_definition_hash": str(model_record["definition_hash"]),
            "model_artifact_hash": str(model_record["artifact_hash"]),
        },
        "registered_at_ns": int(registered_at_ns),
        "registration_evidence_refs": list(evidence_refs),
        "authority": "context relevance declaration only; never model lifecycle, capital, risk, or execution authority",
    }


def _artifact_wire(profile: ModelContextProfile, model_record: Mapping[str, Any], registered_at_ns: int, evidence_refs: Sequence[str]) -> Dict[str, Any]:
    body = _artifact_body(profile, model_record, registered_at_ns, evidence_refs)
    value = dict(body)
    value["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
    return value


def _validate_artifact(document: Mapping[str, Any]) -> Tuple[Optional[ModelContextProfile], List[str]]:
    errors: List[str] = []
    body = {key: value for key, value in document.items() if key != "integrity"}
    if document.get("schema_version") != PROFILE_ARTIFACT_SCHEMA_VERSION:
        errors.append("context-profile artifact schema invalid")
    if document.get("integrity", {}).get("content_hash") != canonical_hash(body):
        return None, errors + ["context-profile artifact content hash mismatch"]
    try:
        profile = _profile_from_wire(document.get("profile", {}))
    except (ModelContextProfileRegistryError, ValueError, TypeError) as exc:
        return None, errors + ["context-profile body invalid: %s" % exc]
    if document.get("profile_id") != _profile_id(profile) or document.get("profile_hash") != profile.content_hash():
        errors.append("context-profile artifact identity mismatch")
    identity = document.get("model_identity")
    if not isinstance(identity, Mapping) or identity.get("model_ref") != profile.model_ref:
        errors.append("context-profile model identity missing or mismatched")
    else:
        try:
            _digest(str(identity.get("model_definition_hash", "")), "model_definition_hash")
            _digest(str(identity.get("model_artifact_hash", "")), "model_artifact_hash")
        except ModelContextProfileRegistryError as exc:
            errors.append(str(exc))
    try:
        _refs(document.get("registration_evidence_refs", []), "registration_evidence_refs")
    except (ModelContextProfileRegistryError, TypeError) as exc:
        errors.append(str(exc))
    if not isinstance(document.get("registered_at_ns"), int) or int(document.get("registered_at_ns", -1)) < 0:
        errors.append("context-profile artifact registered_at_ns invalid")
    return profile, errors


def profile_set_hash(profiles: Sequence[ModelContextProfile]) -> str:
    ordered = sorted(profiles, key=lambda profile: profile.model_ref)
    return canonical_hash([{"model_ref": profile.model_ref, "profile_hash": profile.content_hash()} for profile in ordered])


class ModelContextProfileRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.directory = self.root / "artifacts/model_context_profiles"
        self.state_path = self.root / "state/model_context_profiles.json"
        self.events_path = self.root / "memory/model_context_profile_events.jsonl"

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
                raise ModelContextProfileRegistryError("context-profile event line %d invalid JSON" % line_number) from exc
            if not isinstance(value, dict):
                raise ModelContextProfileRegistryError("context-profile event must be an object")
            output.append(value)
        return tuple(output)

    def state(self) -> Mapping[str, Any]:
        if not self.state_path.is_file():
            return _empty_projection()
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ModelContextProfileRegistryError("context-profile state must be an object")
        return value

    def rebuild_state(self) -> Mapping[str, Any]:
        with writer_lock(self.root):
            events = self.events()
            errors = _validate_event_chain(events)
            if errors:
                raise ModelContextProfileRegistryError("context-profile journal invalid: " + "; ".join(errors))
            projection = _project_events(events)
            _atomic_json(self.state_path, projection)
            return projection

    def register(self, profile: ModelContextProfile, *, evidence_refs: Sequence[str], occurred_at_ns: int) -> Mapping[str, Any]:
        refs = _refs(evidence_refs, "evidence_refs")
        occurred = int(occurred_at_ns)
        if occurred < 0:
            raise ModelContextProfileRegistryError("occurred_at_ns must be non-negative")
        with writer_lock(self.root):
            model_errors = validate_model_registry(self.root)
            if model_errors:
                raise ModelContextProfileRegistryError("model registry invalid: " + "; ".join(model_errors))
            model_record = ModelRegistry(self.root).state().get("models", {}).get(profile.model_ref)
            if model_record is None:
                raise ModelContextProfileRegistryError("context profile requires an exact registered Z5 model_ref")

            events = self.events()
            errors = _validate_event_chain(events)
            if errors:
                raise ModelContextProfileRegistryError("existing context-profile journal invalid: " + "; ".join(errors))
            projection = _project_events(events)
            profile_id = _profile_id(profile)
            same_version = [record for record in projection["profiles"].values() if record["model_ref"] == profile.model_ref and record["profile_version"] == profile.profile_version]
            if same_version:
                existing = same_version[0]
                if existing["profile_hash"] != profile.content_hash() or existing["profile_id"] != profile_id:
                    raise ModelContextProfileRegistryError("model context profile version is immutable; version identity drift rejected")
                self._load_profile(profile_id)
                _atomic_json(self.state_path, projection)
                return existing

            artifact = _artifact_wire(profile, model_record, occurred, refs)
            path = self.directory / (profile_id + ".json")
            if path.exists():
                existing_artifact = json.loads(path.read_text(encoding="utf-8"))
                if existing_artifact != artifact:
                    raise ModelContextProfileRegistryError("context-profile artifact identity conflict")
            else:
                _atomic_json(path, artifact)

            payload = {
                "profile_id": profile_id,
                "model_ref": profile.model_ref,
                "profile_version": profile.profile_version,
                "profile_hash": profile.content_hash(),
                "artifact_path": path.relative_to(self.root).as_posix(),
                "artifact_content_hash": artifact["integrity"]["content_hash"],
                "model_definition_hash": str(model_record["definition_hash"]),
                "model_artifact_hash": str(model_record["artifact_hash"]),
                "evidence_refs": list(refs),
            }
            event = self._append_event(events, "PROFILE_REGISTERED", profile.model_ref, occurred, payload)
            projection = _project_events(tuple(events) + (event,))
            _atomic_json(self.state_path, projection)
            return projection["profiles"][profile_id]

    def activate(self, profile_id: str, *, evidence_refs: Sequence[str], occurred_at_ns: int) -> Mapping[str, Any]:
        refs = _refs(evidence_refs, "evidence_refs")
        occurred = int(occurred_at_ns)
        if occurred < 0:
            raise ModelContextProfileRegistryError("occurred_at_ns must be non-negative")
        with writer_lock(self.root):
            events = self.events()
            errors = _validate_event_chain(events)
            if errors:
                raise ModelContextProfileRegistryError("existing context-profile journal invalid: " + "; ".join(errors))
            projection = _project_events(events)
            record = projection["profiles"].get(str(profile_id))
            if record is None:
                raise ModelContextProfileRegistryError("cannot activate unregistered context profile")
            model_ref = str(record["model_ref"])
            if occurred < int(record["registered_at_ns"]):
                raise ModelContextProfileRegistryError("context-profile activation cannot precede registration")
            current = projection["active_by_model"].get(model_ref)
            if current is not None and occurred < int(current["activated_at_ns"]):
                raise ModelContextProfileRegistryError("context-profile activation time cannot move backwards")
            if current is not None and current["profile_id"] == profile_id:
                if current["activation_evidence_refs"] == list(refs):
                    _atomic_json(self.state_path, projection)
                    return current
                raise ModelContextProfileRegistryError("same-profile activation with different evidence is not idempotent")

            profile = self._load_profile(str(profile_id))
            model_errors = validate_model_registry(self.root)
            if model_errors:
                raise ModelContextProfileRegistryError("model registry invalid: " + "; ".join(model_errors))
            model_record = ModelRegistry(self.root).state().get("models", {}).get(model_ref)
            if model_record is None or model_record.get("definition_hash") != record["model_definition_hash"] or model_record.get("artifact_hash") != record["model_artifact_hash"]:
                raise ModelContextProfileRegistryError("registered context profile no longer binds exact Z5 model identity")
            if profile.content_hash() != record["profile_hash"]:
                raise ModelContextProfileRegistryError("registered context profile hash mismatch")

            payload = {
                "profile_id": str(profile_id),
                "profile_hash": record["profile_hash"],
                "profile_version": record["profile_version"],
                "previous_profile_id": None if current is None else current["profile_id"],
                "evidence_refs": list(refs),
            }
            event = self._append_event(events, "PROFILE_ACTIVATED", model_ref, occurred, payload)
            projection = _project_events(tuple(events) + (event,))
            _atomic_json(self.state_path, projection)
            return projection["active_by_model"][model_ref]

    def active_profile(self, model_ref: str, *, as_of_ns: int) -> ModelContextProfile:
        cutoff = int(as_of_ns)
        if cutoff < 0:
            raise ModelContextProfileRegistryError("as_of_ns must be non-negative")
        events = self.events()
        errors = _validate_event_chain(events)
        if errors:
            raise ModelContextProfileRegistryError("context-profile journal invalid: " + "; ".join(errors))
        active_id: Optional[str] = None
        for event in events:
            if int(event["occurred_at_ns"]) > cutoff or event["model_ref"] != model_ref:
                continue
            if event["event_type"] == "PROFILE_ACTIVATED":
                active_id = str(event["payload"]["profile_id"])
        if active_id is None:
            raise ModelContextProfileRegistryError("no active context profile for %s as of %d" % (model_ref, cutoff))
        return self._load_profile(active_id)

    def active_profiles(self, model_refs: Sequence[str], *, as_of_ns: int) -> Tuple[ModelContextProfile, ...]:
        refs = tuple(sorted(set(str(value) for value in model_refs)))
        if not refs or any(not value for value in refs) or len(refs) != len(tuple(model_refs)):
            raise ModelContextProfileRegistryError("model_refs must be unique and non-empty")
        return tuple(self.active_profile(model_ref, as_of_ns=as_of_ns) for model_ref in refs)

    def _load_profile(self, profile_id: str) -> ModelContextProfile:
        path = self.directory / (str(profile_id) + ".json")
        if not path.is_file():
            raise ModelContextProfileRegistryError("context-profile artifact missing: %s" % profile_id)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ModelContextProfileRegistryError("context-profile artifact unreadable: %s" % profile_id) from exc
        profile, errors = _validate_artifact(document)
        if errors or profile is None:
            raise ModelContextProfileRegistryError("context-profile artifact invalid: " + "; ".join(errors))
        return profile

    def _append_event(self, events: Sequence[Mapping[str, Any]], event_type: str, model_ref: str, occurred_at_ns: int, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        previous_hash = str(events[-1]["event_hash"]) if events else "GENESIS"
        event = _event_wire(len(events), event_type, model_ref, occurred_at_ns, payload, previous_hash)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event


def validate_context_profile_registry(root: Path) -> List[str]:
    root = root.resolve()
    registry = ModelContextProfileRegistry(root)
    full_kernel = (root / "state/current_state.json").is_file()
    if full_kernel and not registry.events_path.is_file():
        return ["missing required journal: memory/model_context_profile_events.jsonl"]
    if full_kernel and not registry.state_path.is_file():
        return ["missing required state file: state/model_context_profiles.json"]

    model_errors = validate_model_registry(root)
    if model_errors:
        return ["model registry invalid for context profiles: " + "; ".join(model_errors)]
    try:
        events = registry.events()
    except ModelContextProfileRegistryError as exc:
        return [str(exc)]
    errors = _validate_event_chain(events)
    if errors:
        return errors
    projected = _project_events(events)
    try:
        state = registry.state()
    except (json.JSONDecodeError, ModelContextProfileRegistryError) as exc:
        return ["context-profile state unreadable: %s" % exc]
    if state != projected:
        errors.append("context-profile projection differs from append-only activation source; rebuild required")

    model_state = ModelRegistry(root).state().get("models", {})
    expected_paths = set()
    for profile_id, record in projected["profiles"].items():
        path = root / record["artifact_path"]
        expected_paths.add(path.resolve())
        if not path.is_file():
            errors.append("context-profile artifact missing: %s" % profile_id)
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append("context-profile artifact unreadable %s: %s" % (profile_id, exc))
            continue
        profile, artifact_errors = _validate_artifact(document)
        errors.extend("%s: %s" % (profile_id, error) for error in artifact_errors)
        if profile is None:
            continue
        if profile.content_hash() != record["profile_hash"] or _profile_id(profile) != profile_id:
            errors.append("context-profile projection/artifact identity mismatch: %s" % profile_id)
        if document.get("integrity", {}).get("content_hash") != record["artifact_content_hash"]:
            errors.append("context-profile artifact hash differs from registration event: %s" % profile_id)
        if int(document.get("registered_at_ns", -1)) != int(record["registered_at_ns"]) or list(document.get("registration_evidence_refs", [])) != record["registration_evidence_refs"]:
            errors.append("context-profile registration provenance mismatch: %s" % profile_id)
        model_record = model_state.get(record["model_ref"])
        if model_record is None or model_record.get("definition_hash") != record["model_definition_hash"] or model_record.get("artifact_hash") != record["model_artifact_hash"]:
            errors.append("context-profile model identity no longer matches Z5 registry: %s" % profile_id)

    if registry.directory.is_dir():
        for path in registry.directory.glob("*.json"):
            if path.resolve() not in expected_paths:
                errors.append("unregistered/orphan context-profile artifact: %s" % path.name)
    return errors
