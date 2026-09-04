"""Perception-only lineage graph adapted from preserved market-object work.

Canonical Z1/Z2/Z9 artifacts remain authoritative. This graph adds typed,
point-in-time relationships and provenance without restoring historical strategy
or opportunity authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple, Union

from ..operations import canonical_hash

GRAPH_SCHEMA_VERSION = "1.0"
GRAPH_LAYERS = ("EVIDENCE", "MEASUREMENT", "DERIVED", "STRUCTURE", "PERCEPTION", "CONTEXT", "STATE", "TRANSITION", "STORY")
GRAPH_LAYER_RANK = {name: index for index, name in enumerate(GRAPH_LAYERS)}
NODE_TYPE_LAYER = {
    "OBSERVATION": "EVIDENCE",
    "REPRESENTATION": "MEASUREMENT",
    "DETERMINISTIC_DERIVATION": "DERIVED",
    "MARKET_STRUCTURE": "STRUCTURE",
    "PERCEPTION_SIGNAL": "PERCEPTION",
    "MARKET_CONTEXT": "CONTEXT",
    "MARKET_STATE": "STATE",
    "STATE_TRANSITION": "TRANSITION",
    "MARKET_STORY": "STORY",
}
TRUTH_CLASSES = {
    "EVIDENCE": {"OBSERVED_EVIDENCE"},
    "MEASUREMENT": {"NORMALIZED_MEASUREMENT"},
    "DERIVED": {"DETERMINISTIC_CALCULATION", "STATISTICAL_ESTIMATE"},
    "STRUCTURE": {"DETERMINISTIC_CLASSIFICATION", "PATTERN_CANDIDATE"},
    "PERCEPTION": {"SECONDARY_PERCEPTION"},
    "CONTEXT": {"OBSERVED_CONTEXT", "DERIVED_CONTEXT"},
    "STATE": {"DETERMINISTIC_CLASSIFICATION"},
    "TRANSITION": {"DETERMINISTIC_CLASSIFICATION"},
    "STORY": {"HYPOTHESIS_COMPOSITION"},
}
PERCEPTION_GRAPH_AUTHORITY = {
    "perception_only": True,
    "defines_resolver_truth": False,
    "selects_models": False,
    "claims_model_competence": False,
    "sets_adaptive_weights": False,
    "economic_decision": False,
    "capital_allocation": False,
    "risk_authorization": False,
    "external_execution": False,
}
FORBIDDEN_NODE_TYPES = {"STRATEGY_APPLICABILITY", "OPPORTUNITY_CANDIDATE", "ORDER_INTENT", "PORTFOLIO_ACTION", "EXECUTION_REQUEST"}

class PerceptionGraphError(ValueError):
    pass

@dataclass(frozen=True)
class GraphRef:
    node_id: str
    relationship: str
    required: bool = True
    expected_node_type: Optional[str] = None

    def to_wire(self) -> Mapping[str, Any]:
        return asdict(self)

def _refs(values: Sequence[Union[GraphRef, Mapping[str, Any]]]) -> Tuple[Mapping[str, Any], ...]:
    output = []
    for value in values:
        item = value.to_wire() if isinstance(value, GraphRef) else dict(value)
        if not str(item.get("node_id", "")) or not str(item.get("relationship", "")):
            raise PerceptionGraphError("graph refs require node_id and relationship")
        output.append(item)
    if len({str(item["node_id"]) for item in output}) != len(output):
        raise PerceptionGraphError("graph refs must not duplicate parent node ids")
    return tuple(output)

def build_graph_node(*, node_id: str, node_type: str, truth_class: str, subject_id: str, cutoff_at_ns: int, known_at_ns: int, source_refs: Sequence[str], input_refs: Sequence[Union[GraphRef, Mapping[str, Any]]], method: Mapping[str, Any], quality: Mapping[str, Any], payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if node_type in FORBIDDEN_NODE_TYPES or node_type not in NODE_TYPE_LAYER:
        raise PerceptionGraphError("node type is outside ZLJ perception authority")
    layer = NODE_TYPE_LAYER[node_type]
    if truth_class not in TRUTH_CLASSES[layer]:
        raise PerceptionGraphError("truth class is invalid for graph layer")
    if not node_id or not subject_id or int(cutoff_at_ns) < 0 or int(known_at_ns) < 0 or int(known_at_ns) > int(cutoff_at_ns):
        raise PerceptionGraphError("graph node identity/timing invalid")
    sources = tuple(str(value) for value in source_refs)
    if not sources or any(not value for value in sources) or len(set(sources)) != len(sources):
        raise PerceptionGraphError("source_refs must be unique and non-empty")
    refs = _refs(input_refs)
    if layer == "EVIDENCE" and refs:
        raise PerceptionGraphError("evidence nodes cannot depend on graph nodes")
    if not isinstance(method, Mapping) or not method.get("name") or not method.get("version"):
        raise PerceptionGraphError("method name/version required")
    if not isinstance(quality, Mapping) or quality.get("status") not in {"QUALIFIED", "VALID", "DEGRADED", "STALE", "UNAVAILABLE", "REJECTED"}:
        raise PerceptionGraphError("quality status invalid")
    body: Dict[str, Any] = {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "node_id": str(node_id), "node_type": node_type, "layer": layer, "truth_class": truth_class,
        "subject_id": str(subject_id), "cutoff_at_ns": int(cutoff_at_ns), "known_at_ns": int(known_at_ns),
        "source_refs": list(sources), "input_refs": [dict(item) for item in refs], "method": dict(method),
        "quality": dict(quality), "payload": dict(payload), "authority": dict(PERCEPTION_GRAPH_AUTHORITY),
    }
    value = dict(body)
    value["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
    validate_graph_node(value)
    return value

def validate_graph_node(node: Mapping[str, Any], resolver: Optional[Callable[[str], Optional[Mapping[str, Any]]]] = None) -> None:
    if node.get("schema_version") != GRAPH_SCHEMA_VERSION:
        raise PerceptionGraphError("graph node schema invalid")
    node_type = str(node.get("node_type", ""))
    if node_type in FORBIDDEN_NODE_TYPES or node_type not in NODE_TYPE_LAYER:
        raise PerceptionGraphError("graph node type outside ZLJ perception authority")
    layer = str(node.get("layer", ""))
    if layer != NODE_TYPE_LAYER[node_type]:
        raise PerceptionGraphError("graph node layer/type mismatch")
    if node.get("truth_class") not in TRUTH_CLASSES[layer]:
        raise PerceptionGraphError("graph node truth class invalid")
    if node.get("authority") != PERCEPTION_GRAPH_AUTHORITY:
        raise PerceptionGraphError("graph authority boundary changed")
    cutoff, known = node.get("cutoff_at_ns"), node.get("known_at_ns")
    if not str(node.get("node_id", "")) or not str(node.get("subject_id", "")) or not isinstance(cutoff, int) or not isinstance(known, int) or cutoff < 0 or known < 0 or known > cutoff:
        raise PerceptionGraphError("graph node identity/timing invalid")
    refs = node.get("input_refs")
    if not isinstance(refs, list):
        raise PerceptionGraphError("graph input_refs invalid")
    if layer == "EVIDENCE" and refs:
        raise PerceptionGraphError("evidence graph node cannot have parents")
    seen = set()
    for ref in refs:
        if not isinstance(ref, Mapping):
            raise PerceptionGraphError("graph reference malformed")
        parent_id = str(ref.get("node_id", ""))
        if not parent_id or not str(ref.get("relationship", "")) or parent_id in seen or parent_id == node["node_id"]:
            raise PerceptionGraphError("graph reference identity invalid")
        seen.add(parent_id)
        if resolver is None:
            continue
        parent = resolver(parent_id)
        if parent is None:
            if bool(ref.get("required", True)):
                raise PerceptionGraphError("required graph parent missing")
            continue
        validate_graph_node(parent)
        if GRAPH_LAYER_RANK[str(parent["layer"])] >= GRAPH_LAYER_RANK[layer]:
            raise PerceptionGraphError("same-layer or upward graph dependency forbidden")
        expected = ref.get("expected_node_type")
        if expected and parent.get("node_type") != expected:
            raise PerceptionGraphError("graph parent type mismatch")
        if int(parent["known_at_ns"]) > int(node["cutoff_at_ns"]):
            raise PerceptionGraphError("graph dependency violates point-in-time cutoff")
    sources = node.get("source_refs")
    if not isinstance(sources, list) or not sources or any(not str(value) for value in sources) or len(set(sources)) != len(sources):
        raise PerceptionGraphError("graph source refs invalid")
    integrity = node.get("integrity")
    body = {key: value for key, value in node.items() if key != "integrity"}
    if not isinstance(integrity, Mapping) or integrity.get("algorithm") != "sha256" or integrity.get("content_hash") != canonical_hash(body):
        raise PerceptionGraphError("graph node integrity mismatch")
