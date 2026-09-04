"""Universal contracts for a reference-linked market-object graph."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence


SCHEMA_VERSION = 1
OBJECT_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._:-]{2,159}$")
LAYERS = (
    "EVIDENCE",
    "MEASUREMENT",
    "DERIVED_MATH",
    "STRUCTURE",
    "PERCEPTION",
    "CONTEXT",
    "STATE",
    "TRANSITION",
    "STORY",
    "STRATEGY_APPLICABILITY",
    "OPPORTUNITY",
    "INDEX",
)
LAYER_RANK = {layer: index for index, layer in enumerate(LAYERS)}
OBJECT_TYPE_LAYER = {
    "MARKET_OBSERVATION": "EVIDENCE",
    "TRADE_OBSERVATION": "EVIDENCE",
    "ORDER_BOOK_OBSERVATION": "EVIDENCE",
    "FUNDING_OBSERVATION": "EVIDENCE",
    "OPEN_INTEREST_OBSERVATION": "EVIDENCE",
    "FUNDAMENTAL_OBSERVATION": "EVIDENCE",
    "EVENT_OBSERVATION": "EVIDENCE",
    "PORTFOLIO_OBSERVATION": "EVIDENCE",
    "CHART_IMAGE_EVIDENCE": "EVIDENCE",
    "NORMALIZED_MEASUREMENT": "MEASUREMENT",
    "TECHNICAL_CALCULATION": "DERIVED_MATH",
    "STATISTICAL_CALCULATION": "DERIVED_MATH",
    "MICROSTRUCTURE_CALCULATION": "DERIVED_MATH",
    "RELATIVE_CALCULATION": "DERIVED_MATH",
    "VOLATILITY_CALCULATION": "DERIVED_MATH",
    "PRICE_STRUCTURE": "STRUCTURE",
    "PATTERN_DETECTION": "STRUCTURE",
    "CHART_PERCEPTION": "PERCEPTION",
    "MARKET_CONTEXT": "CONTEXT",
    "TREND_STATE": "STATE",
    "VOLATILITY_STATE": "STATE",
    "MOMENTUM_STATE": "STATE",
    "LIQUIDITY_STATE": "STATE",
    "PARTICIPATION_STATE": "STATE",
    "POSITIONING_STATE": "STATE",
    "CORRELATION_STATE": "STATE",
    "RISK_STATE": "STATE",
    "STATE_TRANSITION": "TRANSITION",
    "MARKET_STORY": "STORY",
    "STRATEGY_APPLICABILITY": "STRATEGY_APPLICABILITY",
    "OPPORTUNITY_CANDIDATE": "OPPORTUNITY",
    "MARKET_WORLD_SNAPSHOT": "INDEX",
}
TRUTH_CLASS_BY_LAYER = {
    "EVIDENCE": {"OBSERVED_EVIDENCE"},
    "MEASUREMENT": {"NORMALIZED_MEASUREMENT"},
    "DERIVED_MATH": {"DETERMINISTIC_CALCULATION", "STATISTICAL_ESTIMATE"},
    "STRUCTURE": {"DETERMINISTIC_CLASSIFICATION", "PATTERN_CANDIDATE"},
    "PERCEPTION": {"SECONDARY_PERCEPTION"},
    "CONTEXT": {"OBSERVED_CONTEXT", "CALENDAR_CONTEXT"},
    "STATE": {"DETERMINISTIC_CLASSIFICATION"},
    "TRANSITION": {"DETERMINISTIC_CLASSIFICATION"},
    "STORY": {"HYPOTHESIS_COMPOSITION"},
    "STRATEGY_APPLICABILITY": {"APPLICABILITY_ASSESSMENT"},
    "OPPORTUNITY": {"ECONOMIC_CANDIDATE"},
    "INDEX": {"REFERENCE_INDEX"},
}
AUTHORITY_BY_LAYER = {
    "EVIDENCE": "SOURCE_EVIDENCE",
    "MEASUREMENT": "DERIVED_NO_EXECUTION_AUTHORITY",
    "DERIVED_MATH": "DERIVED_NO_EXECUTION_AUTHORITY",
    "STRUCTURE": "CLASSIFICATION_NO_EXECUTION_AUTHORITY",
    "PERCEPTION": "SECONDARY_PERCEPTION",
    "CONTEXT": "CONTEXT_NO_EXECUTION_AUTHORITY",
    "STATE": "CLASSIFICATION_NO_EXECUTION_AUTHORITY",
    "TRANSITION": "CLASSIFICATION_NO_EXECUTION_AUTHORITY",
    "STORY": "HYPOTHESIS_NO_EXECUTION_AUTHORITY",
    "STRATEGY_APPLICABILITY": "ADVISORY_NO_EXECUTION_AUTHORITY",
    "OPPORTUNITY": "CANDIDATE_NO_EXECUTION_AUTHORITY",
    "INDEX": "INDEX_ONLY",
}
FORBIDDEN_FIELDS = {
    "private_key",
    "seed_phrase",
    "mnemonic",
    "password",
    "api_key",
    "secret_value",
    "credential_value",
}


def canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def market_ref(object_id: str) -> str:
    return f"market://{object_id}"


def object_id_from_ref(reference: str) -> str:
    if not str(reference).startswith("market://"):
        raise ValueError(f"not a market object reference: {reference}")
    return str(reference)[len("market://") :]


@dataclass(frozen=True)
class MarketObjectRef:
    ref: str
    relationship: str
    required: bool = True
    expected_object_type: Optional[str] = None

    @classmethod
    def to(cls, object_id: str, relationship: str, required: bool = True, expected_object_type: Optional[str] = None) -> "MarketObjectRef":
        return cls(market_ref(object_id), relationship, required, expected_object_type)


@dataclass(frozen=True)
class MarketObject:
    schema_version: int
    object_id: str
    object_type: str
    layer: str
    truth_class: str
    authority: str
    subject: Mapping[str, Any]
    effective_at: str
    created_at: str
    source_time_range: Mapping[str, Any]
    input_refs: Sequence[Mapping[str, Any]]
    method: Mapping[str, Any]
    quality: Mapping[str, Any]
    permissions: Mapping[str, Any]
    payload: Mapping[str, Any]
    integrity: Mapping[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _walk(value: Any, location: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            path = f"{location}.{key}" if location else str(key)
            yield path, child
            yield from _walk(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            path = f"{location}[{index}]"
            yield path, child
            yield from _walk(child, path)


def _payload_market_refs(payload: Mapping[str, Any]) -> set[str]:
    return {
        value
        for _, value in _walk(payload)
        if isinstance(value, str) and value.startswith("market://")
    }


def build_object(
    *, object_id: str, object_type: str, truth_class: str, subject: Mapping[str, Any],
    effective_at: str, created_at: str, source_time_range: Mapping[str, Any],
    input_refs: Sequence[MarketObjectRef | Mapping[str, Any]], method: Mapping[str, Any],
    quality: Mapping[str, Any], payload: Mapping[str, Any],
) -> Dict[str, Any]:
    if object_type not in OBJECT_TYPE_LAYER:
        raise ValueError(f"unknown market object type: {object_type}")
    layer = OBJECT_TYPE_LAYER[object_type]
    refs = [asdict(item) if isinstance(item, MarketObjectRef) else dict(item) for item in input_refs]
    document: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "object_id": object_id,
        "object_type": object_type,
        "layer": layer,
        "truth_class": truth_class,
        "authority": AUTHORITY_BY_LAYER[layer],
        "subject": dict(subject),
        "effective_at": effective_at,
        "created_at": created_at,
        "source_time_range": dict(source_time_range),
        "input_refs": refs,
        "method": dict(method),
        "quality": dict(quality),
        "permissions": {
            "execution_authority": False,
            "capital_authority": False,
            "can_create_execution_request": False,
        },
        "payload": dict(payload),
    }
    document["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(document)}
    errors = validate_market_object(document)
    if errors:
        raise ValueError("; ".join(errors))
    return document


def validate_market_object(
    document: Mapping[str, Any], resolver: Optional[Callable[[str], Optional[Mapping[str, Any]]]] = None
) -> List[str]:
    errors: List[str] = []
    object_id = str(document.get("object_id", ""))
    object_type = str(document.get("object_type", ""))
    layer = str(document.get("layer", ""))
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{object_id or '<unknown>'}: schema_version must be {SCHEMA_VERSION}")
    if not OBJECT_ID_PATTERN.fullmatch(object_id):
        errors.append(f"{object_id or '<unknown>'}: invalid object_id")
    expected_layer = OBJECT_TYPE_LAYER.get(object_type)
    if expected_layer is None:
        errors.append(f"{object_id}: unknown object_type {object_type}")
    elif layer != expected_layer:
        errors.append(f"{object_id}: object_type {object_type} belongs to {expected_layer}, not {layer}")
    if document.get("truth_class") not in TRUTH_CLASS_BY_LAYER.get(layer, set()):
        errors.append(f"{object_id}: truth_class is invalid for layer {layer}")
    if document.get("authority") != AUTHORITY_BY_LAYER.get(layer):
        errors.append(f"{object_id}: authority is invalid for layer {layer}")
    subject = document.get("subject", {})
    if not isinstance(subject, Mapping) or not subject.get("instrument") or not subject.get("exchange"):
        errors.append(f"{object_id}: subject requires instrument and exchange")
    if not document.get("effective_at") or not document.get("created_at"):
        errors.append(f"{object_id}: effective_at and created_at are required")
    if not isinstance(document.get("source_time_range"), Mapping):
        errors.append(f"{object_id}: source_time_range must be an object")
    method = document.get("method", {})
    if not isinstance(method, Mapping) or not method.get("name") or not method.get("version"):
        errors.append(f"{object_id}: method name/version are required")
    quality = document.get("quality", {})
    if not isinstance(quality, Mapping) or quality.get("status") not in {"VALID", "DEGRADED", "UNAVAILABLE", "STALE", "REJECTED"}:
        errors.append(f"{object_id}: invalid quality status")
    permissions = document.get("permissions", {})
    if any(permissions.get(field) is not False for field in ("execution_authority", "capital_authority", "can_create_execution_request")):
        errors.append(f"{object_id}: market objects cannot grant execution or capital authority")
    for path, value in _walk(document):
        leaf = path.rsplit(".", 1)[-1].split("[", 1)[0].lower()
        if leaf in FORBIDDEN_FIELDS:
            errors.append(f"{object_id}: forbidden secret-bearing field at {path}")
    refs = document.get("input_refs", [])
    if not isinstance(refs, list):
        errors.append(f"{object_id}: input_refs must be a list")
        refs = []
    declared_refs = set()
    for index, item in enumerate(refs):
        if not isinstance(item, Mapping):
            errors.append(f"{object_id}: input_refs[{index}] must be an object")
            continue
        reference = str(item.get("ref", ""))
        try:
            parent_id = object_id_from_ref(reference)
        except ValueError:
            errors.append(f"{object_id}: invalid input reference {reference}")
            continue
        declared_refs.add(reference)
        if parent_id == object_id:
            errors.append(f"{object_id}: object cannot reference itself")
        if not item.get("relationship"):
            errors.append(f"{object_id}: reference {reference} requires a relationship")
        if layer == "EVIDENCE":
            errors.append(f"{object_id}: raw evidence cannot depend on market objects")
        if resolver is not None:
            parent = resolver(parent_id)
            if parent is None:
                if item.get("required", True):
                    errors.append(f"{object_id}: required parent is missing: {reference}")
                continue
            parent_layer = str(parent.get("layer"))
            if layer != "INDEX" and LAYER_RANK.get(parent_layer, 999) >= LAYER_RANK.get(layer, -1):
                errors.append(f"{object_id}: invalid upward/same-layer dependency {parent_layer} -> {layer}")
            expected_type = item.get("expected_object_type")
            if expected_type and parent.get("object_type") != expected_type:
                errors.append(f"{object_id}: {reference} expected {expected_type}, observed {parent.get('object_type')}")
    undeclared = _payload_market_refs(document.get("payload", {})) - declared_refs
    if undeclared:
        errors.append(f"{object_id}: payload contains undeclared market refs: {sorted(undeclared)}")
    content = dict(document)
    integrity = content.pop("integrity", {})
    if integrity.get("algorithm") != "sha256" or integrity.get("content_hash") != canonical_hash(content):
        errors.append(f"{object_id}: integrity mismatch")
    return errors
