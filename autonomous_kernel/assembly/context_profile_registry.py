from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from ..models.registry import ModelRegistry, ModelRegistryError, validate_model_registry
from ..operations import canonical_hash
from ..store import writer_lock
from .contextual import ContextualAssemblyError, ModelContextProfile


PROFILE_ARTIFACT_SCHEMA_VERSION = 1
PROFILE_REGISTRY_SCHEMA_VERSION = 1
PROFILE_CONTRACT_VERSION = "1.0"
PROFILE_REGISTRY_AUTHORITY = "immutable governed model-context relevance declarations; no model promotion, capital, risk, or execution authority"


class ModelContextProfileRegistryError(RuntimeError):
    pass


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


def _refs(values: Sequence[str]) -> Tuple[str, ...]:
    refs = tuple(str(value) for value in values)
    if not refs or any(not value for value in refs) or len(refs) != len(set(refs)):
        raise ModelContextProfileRegistryError("profile evidence_refs must contain unique non-empty values")
    return refs


def _profile_id(model_ref: str, profile_version: str) -> str:
    return "MCP-%s" % canonical_hash({"model_ref": str(model_ref), "profile_version": str(profile_version)})[:32]


def _regime_mapping(value: Any, field: str) -> Mapping[str, Tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ModelContextProfileRegistryError("%s must be an object" % field)
    result: Dict[str, Tuple[str, ...]] = {}
    for key, members in value.items():
        if isinstance(members, (str, bytes)) or not isinstance(members, Sequence):
            raise ModelContextProfileRegistryError("%s[%s] must be an array" % (field, key))
        result[str(key)] = tuple(str(member) for member in members)
    return result


def profile_from_mapping(value: Mapping[str, Any]) -> ModelContextProfile:
    dependencies = value.get("feature_dependencies", [])
    if isinstance(dependencies, (str, bytes)) or not isinstance(dependencies, Sequence):
        raise ModelContextProfileRegistryError("feature_dependencies must be an array")
    try:
        return ModelContextProfile(
            model_ref=str(value.get("model_ref", "")),
            feature_dependencies=tuple(str(item) for item in dependencies),
            preferred_regimes=_regime_mapping(value.get("preferred_regimes", {}), "preferred_regimes"),
            adverse_regimes=_regime_mapping(value.get("adverse_regimes", {}), "adverse_regimes"),
            diversity_group=str(value.get("diversity_group", "")),
            profile_version=str(value.get("profile_version", "1.0")),
        )
    except ContextualAssemblyError as exc:
        raise ModelContextProfileRegistryError(str(exc)) from exc


def _profile_from_wire(value: Mapping[str, Any]) -> ModelContextProfile:
    profile = profile_from_mapping(value)
    integrity = value.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("algorithm") != "sha256" or integrity.get("content_hash") != profile.content_hash():
        raise ModelContextProfileRegistryError("model context profile integrity mismatch")
    if value.get("authority") != profile.body().get("authority"):
        raise ModelContextProfileRegistryError("model context profile authority boundary mismatch")
    return profile


def _artifact_body(profile: ModelContextProfile, model_record: Mapping[str, Any], *, registered_at_ns: int, evidence_refs: Sequence[str]) -> Dict[str, Any]:
    return {
        "schema_version": PROFILE_ARTIFACT_SCHEMA_VERSION,
        "profile_id": _profile_id(profile.model_ref, profile.profile_version),
        "profile": profile.to_wire(),
        "model_binding": {
            "model_ref": profile.model_ref,
            "definition_hash": str(model_record.get("definition_hash", "")),
            "artifact_hash": str(model_record.get("artifact_hash", "")),
            "model_registered_at_ns": int(model_record.get("registered_at_ns", -1)),
        },
        "governance": {
            "profile_registered_at_ns": int(registered_at_ns),
            "evidence_refs": list(evidence_refs),
            "immutability": "SAME_MODEL_REF_AND_PROFILE_VERSION_CANNOT_CHANGE_CONTENT",
            "authority": PROFILE_REGISTRY_AUTHORITY,
        },
    }


def _validate_artifact(root: Path, document: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    body = {key: value for key, value in document.items() if key != "integrity"}
    if document.get("schema_version") != PROFILE_ARTIFACT_SCHEMA_VERSION:
        errors.append("model context profile artifact schema invalid")
    if canonical_hash(body) != document.get("integrity", {}).get("content_hash"):
        return errors + ["model context profile artifact content hash mismatch"]
    profile_value = document.get("profile")
    if not isinstance(profile_value, Mapping):
        return errors + ["model context profile is missing"]
    try:
        profile = _profile_from_wire(profile_value)
    except ModelContextProfileRegistryError as exc:
        return errors + [str(exc)]
    if document.get("profile_id") != _profile_id(profile.model_ref, profile.profile_version):
        errors.append("model context profile_id mismatch")

    binding = document.get("model_binding")
    governance = document.get("governance")
    if not isinstance(binding, Mapping) or not isinstance(governance, Mapping):
        return errors + ["model context profile governance/model binding missing"]
    if binding.get("model_ref") != profile.model_ref:
        errors.append("model context profile model binding mismatch")
    registered_at = governance.get("profile_registered_at_ns")
    model_registered_at = binding.get("model_registered_at_ns")
    if not isinstance(registered_at, int) or registered_at < 0 or not isinstance(model_registered_at, int) or model_registered_at < 0 or registered_at < model_registered_at:
        errors.append("model context profile registration timing invalid")
    evidence = governance.get("evidence_refs")
    if not isinstance(evidence, list) or not evidence or any(not str(item) for item in evidence) or len(evidence) != len(set(evidence)):
        errors.append("model context profile governance evidence invalid")
    if governance.get("authority") != PROFILE_REGISTRY_AUTHORITY:
        errors.append("model context profile registry authority mismatch")

    try:
        model_state = ModelRegistry(root).state()
    except (json.JSONDecodeError, ModelRegistryError) as exc:
        return errors + ["model registry unavailable for context profile binding: %s" % exc]
    record = model_state.get("models", {}).get(profile.model_ref)
    if not isinstance(record, Mapping):
        errors.append("model context profile binds unregistered model %s" % profile.model_ref)
    else:
        if binding.get("definition_hash") != record.get("definition_hash") or binding.get("artifact_hash") != record.get("artifact_hash") or binding.get("model_registered_at_ns") != record.get("registered_at_ns"):
            errors.append("model context profile binding differs from governed model identity")
    return errors


class ModelContextProfileRegistry:
    """Immutable context-policy artifacts bound to governed model identities."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.directory = self.root / "artifacts/model_context_profiles"
        self.index_path = self.root / "state/model_context_profiles.json"

    def register(self, profile: ModelContextProfile, *, registered_at_ns: int, evidence_refs: Sequence[str]) -> Mapping[str, Any]:
        registered_at = int(registered_at_ns)
        if registered_at < 0:
            raise ModelContextProfileRegistryError("profile registered_at_ns must be non-negative")
        refs = _refs(evidence_refs)
        model_errors = validate_model_registry(self.root, require_state=False)
        if model_errors:
            raise ModelContextProfileRegistryError("model registry invalid: " + "; ".join(model_errors))
        model_state = ModelRegistry(self.root).state()
        model_record = model_state.get("models", {}).get(profile.model_ref)
        if not isinstance(model_record, Mapping):
            raise ModelContextProfileRegistryError("profile model_ref is not registered in governed model registry")
        if registered_at < int(model_record.get("registered_at_ns", -1)):
            raise ModelContextProfileRegistryError("context profile cannot be registered before its model")

        body = _artifact_body(profile, model_record, registered_at_ns=registered_at, evidence_refs=refs)
        document = dict(body)
        document["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
        path = self.directory / (str(body["profile_id"]) + ".json")
        with writer_lock(self.root):
            if path.exists():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise ModelContextProfileRegistryError("existing model context profile artifact is unreadable") from exc
                if existing != document:
                    raise ModelContextProfileRegistryError("model context profile version is immutable; register a new profile_version")
                self.rebuild_index()
                return existing
            _atomic_json(path, document)
            self.rebuild_index()
        return document

    def rebuild_index(self) -> Mapping[str, Any]:
        items: List[Mapping[str, Any]] = []
        seen = set()
        if self.directory.is_dir():
            for path in sorted(self.directory.glob("*.json")):
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise ModelContextProfileRegistryError("%s is invalid JSON: %s" % (path.name, exc)) from exc
                errors = _validate_artifact(self.root, document)
                if errors:
                    raise ModelContextProfileRegistryError("%s: %s" % (path.name, "; ".join(errors)))
                profile = _profile_from_wire(document["profile"])
                identity = (profile.model_ref, profile.profile_version)
                if identity in seen:
                    raise ModelContextProfileRegistryError("duplicate model_ref/profile_version context policy")
                seen.add(identity)
                items.append(
                    {
                        "profile_id": document["profile_id"],
                        "path": path.relative_to(self.root).as_posix(),
                        "model_ref": profile.model_ref,
                        "profile_version": profile.profile_version,
                        "profile_hash": profile.content_hash(),
                        "diversity_group": profile.diversity_group,
                        "feature_dependencies": sorted(profile.feature_dependencies),
                        "profile_registered_at_ns": document["governance"]["profile_registered_at_ns"],
                        "model_definition_hash": document["model_binding"]["definition_hash"],
                        "model_artifact_hash": document["model_binding"]["artifact_hash"],
                        "artifact_content_hash": document["integrity"]["content_hash"],
                    }
                )
        items.sort(key=lambda item: (str(item["model_ref"]), int(item["profile_registered_at_ns"]), str(item["profile_version"]), str(item["profile_id"])))
        index = {
            "schema_version": PROFILE_REGISTRY_SCHEMA_VERSION,
            "profile_contract_version": PROFILE_CONTRACT_VERSION,
            "authority": PROFILE_REGISTRY_AUTHORITY,
            "items": items,
        }
        _atomic_json(self.index_path, index)
        return index

    def resolve(self, model_refs: Sequence[str], *, as_of_ns: int) -> Tuple[ModelContextProfile, ...]:
        as_of = int(as_of_ns)
        refs = tuple(sorted(str(item) for item in model_refs))
        if as_of < 0:
            raise ModelContextProfileRegistryError("profile resolution as_of_ns must be non-negative")
        if not refs or any(not item for item in refs) or len(refs) != len(set(refs)):
            raise ModelContextProfileRegistryError("profile resolution model_refs must be unique and non-empty")
        errors = validate_model_context_profile_registry(self.root, require_state=True)
        if errors:
            raise ModelContextProfileRegistryError("model context profile registry invalid: " + "; ".join(errors))
        index = json.loads(self.index_path.read_text(encoding="utf-8"))
        output: List[ModelContextProfile] = []
        for model_ref in refs:
            candidates = [item for item in index["items"] if item.get("model_ref") == model_ref and int(item.get("profile_registered_at_ns", -1)) <= as_of]
            if not candidates:
                raise ModelContextProfileRegistryError("no governed ModelContextProfile was registered by cutoff for %s" % model_ref)
            selected = max(candidates, key=lambda item: (int(item["profile_registered_at_ns"]), str(item["profile_version"]), str(item["profile_id"])))
            path = (self.root / str(selected["path"])).resolve()
            try:
                path.relative_to(self.root)
            except ValueError as exc:
                raise ModelContextProfileRegistryError("profile registry path escapes repository") from exc
            document = json.loads(path.read_text(encoding="utf-8"))
            profile = _profile_from_wire(document["profile"])
            if profile.content_hash() != selected.get("profile_hash"):
                raise ModelContextProfileRegistryError("profile registry hash mismatch for %s" % model_ref)
            output.append(profile)
        return tuple(output)


def validate_model_context_profile_registry(root: Path, *, require_state: bool = True) -> List[str]:
    root = root.resolve()
    registry = ModelContextProfileRegistry(root)
    full_kernel = (root / "state/current_state.json").is_file()
    if not registry.index_path.is_file():
        return ["missing required state file: state/model_context_profiles.json"] if require_state and full_kernel else []
    try:
        index = json.loads(registry.index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ["state/model_context_profiles.json: unreadable JSON: %s" % exc]
    if index.get("schema_version") != PROFILE_REGISTRY_SCHEMA_VERSION or index.get("profile_contract_version") != PROFILE_CONTRACT_VERSION or index.get("authority") != PROFILE_REGISTRY_AUTHORITY or not isinstance(index.get("items"), list):
        return ["state/model_context_profiles.json: invalid schema"]

    model_errors = validate_model_registry(root, require_state=False)
    if model_errors:
        return ["model registry invalid while validating context profiles: " + "; ".join(model_errors)]

    errors: List[str] = []
    seen_profile_ids = set()
    seen_versions = set()
    indexed_paths = set()
    for item in index["items"]:
        if not isinstance(item, Mapping):
            errors.append("state/model_context_profiles.json: malformed item")
            continue
        profile_id = str(item.get("profile_id", ""))
        identity = (str(item.get("model_ref", "")), str(item.get("profile_version", "")))
        if not profile_id or profile_id in seen_profile_ids or not all(identity) or identity in seen_versions:
            errors.append("state/model_context_profiles.json: profile identities must be unique and non-empty")
            continue
        seen_profile_ids.add(profile_id)
        seen_versions.add(identity)
        path = (root / str(item.get("path", ""))).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            errors.append("model context profile index path escapes repository")
            continue
        indexed_paths.add(path)
        if not path.is_file():
            errors.append("model context profile index references missing artifact %s" % profile_id)
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append("%s: invalid JSON: %s" % (item.get("path"), exc))
            continue
        errors.extend("%s: %s" % (item.get("path"), error) for error in _validate_artifact(root, document))
        if document.get("profile_id") != profile_id or document.get("integrity", {}).get("content_hash") != item.get("artifact_content_hash"):
            errors.append("model context profile index artifact binding mismatch for %s" % profile_id)
        try:
            profile = _profile_from_wire(document["profile"])
        except (KeyError, ModelContextProfileRegistryError):
            continue
        if profile.content_hash() != item.get("profile_hash") or profile.model_ref != identity[0] or profile.profile_version != identity[1]:
            errors.append("model context profile index profile binding mismatch for %s" % profile_id)

    if registry.directory.is_dir():
        durable_paths = set(path.resolve() for path in registry.directory.glob("*.json"))
        missing_from_index = durable_paths - indexed_paths
        if missing_from_index:
            errors.append("state/model_context_profiles.json omits durable profile artifacts: %s" % ", ".join(sorted(path.name for path in missing_from_index)))
    return errors
