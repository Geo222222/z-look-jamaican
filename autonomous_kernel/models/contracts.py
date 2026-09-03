from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

from ..operations import canonical_hash


MODEL_SCHEMA_VERSION = "1.0"
MODEL_LIFECYCLE_STATES = {"CANDIDATE"}


class ModelDefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class ModelDefinition:
    model_id: str
    version: str
    family: str
    lifecycle_state: str
    required_representation_type: str
    target_metric: str
    supported_horizons_ns: Tuple[int, ...]
    parameters: Mapping[str, Any]
    schema_version: str = MODEL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MODEL_SCHEMA_VERSION:
            raise ModelDefinitionError("unsupported model definition schema")
        for field in ("model_id", "version", "family", "required_representation_type", "target_metric"):
            if not str(getattr(self, field)).strip():
                raise ModelDefinitionError("%s is required" % field)
        if self.lifecycle_state not in MODEL_LIFECYCLE_STATES:
            raise ModelDefinitionError("Z4 models must remain CANDIDATE")
        if not self.supported_horizons_ns or any(int(value) <= 0 for value in self.supported_horizons_ns):
            raise ModelDefinitionError("supported_horizons_ns must contain positive horizons")
        if len(set(self.supported_horizons_ns)) != len(self.supported_horizons_ns):
            raise ModelDefinitionError("supported_horizons_ns must be unique")
        if not isinstance(self.parameters, Mapping):
            raise ModelDefinitionError("model parameters must be a mapping")

    @property
    def model_ref(self) -> str:
        return "%s@%s" % (self.model_id, self.version)

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model_id": self.model_id,
            "version": self.version,
            "family": self.family,
            "lifecycle_state": self.lifecycle_state,
            "required_representation_type": self.required_representation_type,
            "target_metric": self.target_metric,
            "supported_horizons_ns": [int(value) for value in self.supported_horizons_ns],
            "parameters": dict(self.parameters),
        }

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        value = self.body()
        value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}
        return value
