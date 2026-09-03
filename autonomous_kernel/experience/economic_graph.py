from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from ..observation.instruments import CanonicalInstrument


ECONOMIC_GRAPH_SCHEMA_VERSION = "1.0"


class EconomicGraphError(ValueError):
    pass


class InstrumentRole(str, Enum):
    SPOT = "SPOT"
    PERPETUAL = "PERPETUAL"
    DATED_FUTURE = "DATED_FUTURE"
    INDEX = "INDEX"
    BASKET = "BASKET"
    REFERENCE = "REFERENCE"


class EconomicRelationshipType(str, Enum):
    SAME_UNDERLYING = "SAME_UNDERLYING"
    SPOT_DERIVATIVE = "SPOT_DERIVATIVE"
    TERM_STRUCTURE = "TERM_STRUCTURE"
    QUOTE_FAMILY = "QUOTE_FAMILY"
    BASKET_MEMBER = "BASKET_MEMBER"
    REFERENCE_COMPONENT = "REFERENCE_COMPONENT"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _nonempty(value: str, field: str) -> str:
    text = str(value).strip()
    if not text:
        raise EconomicGraphError(f"{field} is required")
    return text


@dataclass(frozen=True)
class EconomicInstrumentNode:
    """Structural identity for one canonical market expression.

    `economic_root_id` identifies the underlying economic thing (for example
    `ASSET.BTC`). `quote_family_id` allows structurally related quotes to be
    declared without pretending their prices or settlement risks are identical.
    """

    node_id: str
    instrument: CanonicalInstrument
    role: InstrumentRole
    economic_root_id: str
    quote_family_id: str
    contract_spec_ref: Optional[str] = None

    def __post_init__(self) -> None:
        _nonempty(self.node_id, "node_id")
        _nonempty(self.economic_root_id, "economic_root_id")
        _nonempty(self.quote_family_id, "quote_family_id")
        if self.role is InstrumentRole.DATED_FUTURE and not self.instrument.expiry:
            raise EconomicGraphError("DATED_FUTURE nodes require instrument expiry")
        if self.role is InstrumentRole.SPOT and self.instrument.market_type != "SPOT":
            raise EconomicGraphError("SPOT role requires a SPOT canonical instrument")

    def body(self) -> Dict[str, object]:
        return {
            "node_id": self.node_id,
            "instrument": self.instrument.to_wire(),
            "role": self.role.value,
            "economic_root_id": self.economic_root_id,
            "quote_family_id": self.quote_family_id,
            "contract_spec_ref": self.contract_spec_ref,
        }


@dataclass(frozen=True)
class EconomicRelationship:
    relationship_id: str
    relationship_type: EconomicRelationshipType
    source_node_id: str
    target_node_id: str
    bidirectional: bool = True
    rationale: str = ""

    def __post_init__(self) -> None:
        _nonempty(self.relationship_id, "relationship_id")
        _nonempty(self.source_node_id, "source_node_id")
        _nonempty(self.target_node_id, "target_node_id")
        if self.source_node_id == self.target_node_id:
            raise EconomicGraphError("relationship endpoints must differ")
        if not str(self.rationale).strip():
            raise EconomicGraphError("relationship rationale is required")

    def body(self) -> Dict[str, object]:
        return {
            "relationship_id": self.relationship_id,
            "relationship_type": self.relationship_type.value,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "bidirectional": self.bidirectional,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class EconomicInstrumentGraph:
    graph_id: str
    graph_version: str
    effective_at_ns: int
    known_at_ns: int
    nodes: Tuple[EconomicInstrumentNode, ...]
    relationships: Tuple[EconomicRelationship, ...]
    schema_version: str = ECONOMIC_GRAPH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ECONOMIC_GRAPH_SCHEMA_VERSION:
            raise EconomicGraphError("unsupported Economic Instrument Graph schema")
        _nonempty(self.graph_id, "graph_id")
        _nonempty(self.graph_version, "graph_version")
        if self.effective_at_ns < 0 or self.known_at_ns < 0:
            raise EconomicGraphError("graph timestamps must be non-negative")
        if self.known_at_ns < self.effective_at_ns:
            raise EconomicGraphError("known_at_ns cannot precede effective_at_ns")
        if not self.nodes:
            raise EconomicGraphError("Economic Instrument Graph requires at least one node")

        node_ids = [node.node_id for node in self.nodes]
        canonical_ids = [node.instrument.canonical_id for node in self.nodes]
        relationship_ids = [item.relationship_id for item in self.relationships]
        if len(set(node_ids)) != len(node_ids):
            raise EconomicGraphError("node_id values must be unique")
        if len(set(canonical_ids)) != len(canonical_ids):
            raise EconomicGraphError("canonical instruments may appear only once in a graph")
        if len(set(relationship_ids)) != len(relationship_ids):
            raise EconomicGraphError("relationship_id values must be unique")

        known_nodes = set(node_ids)
        for relationship in self.relationships:
            if relationship.source_node_id not in known_nodes or relationship.target_node_id not in known_nodes:
                raise EconomicGraphError("relationship references an unknown graph node")
            self._validate_relationship_semantics(relationship)

    def _node_map(self) -> Dict[str, EconomicInstrumentNode]:
        return {node.node_id: node for node in self.nodes}

    def _validate_relationship_semantics(self, relationship: EconomicRelationship) -> None:
        nodes = self._node_map()
        source = nodes[relationship.source_node_id]
        target = nodes[relationship.target_node_id]
        kind = relationship.relationship_type

        if kind in {EconomicRelationshipType.SAME_UNDERLYING, EconomicRelationshipType.SPOT_DERIVATIVE, EconomicRelationshipType.TERM_STRUCTURE}:
            if source.economic_root_id != target.economic_root_id:
                raise EconomicGraphError(f"{kind.value} requires the same economic_root_id")
        if kind is EconomicRelationshipType.SPOT_DERIVATIVE:
            roles = {source.role, target.role}
            if InstrumentRole.SPOT not in roles or not roles.intersection({InstrumentRole.PERPETUAL, InstrumentRole.DATED_FUTURE}):
                raise EconomicGraphError("SPOT_DERIVATIVE requires one spot and one derivative node")
        if kind is EconomicRelationshipType.TERM_STRUCTURE:
            derivative_roles = {InstrumentRole.PERPETUAL, InstrumentRole.DATED_FUTURE}
            if source.role not in derivative_roles or target.role not in derivative_roles:
                raise EconomicGraphError("TERM_STRUCTURE requires derivative nodes")
        if kind is EconomicRelationshipType.QUOTE_FAMILY and source.quote_family_id != target.quote_family_id:
            raise EconomicGraphError("QUOTE_FAMILY requires matching quote_family_id")
        if kind is EconomicRelationshipType.BASKET_MEMBER and target.role is not InstrumentRole.BASKET:
            raise EconomicGraphError("BASKET_MEMBER target must be a basket node")
        if kind is EconomicRelationshipType.REFERENCE_COMPONENT and target.role not in {InstrumentRole.INDEX, InstrumentRole.REFERENCE}:
            raise EconomicGraphError("REFERENCE_COMPONENT target must be an index/reference node")

    def body(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "graph_id": self.graph_id,
            "graph_version": self.graph_version,
            "effective_at_ns": self.effective_at_ns,
            "known_at_ns": self.known_at_ns,
            "nodes": [node.body() for node in sorted(self.nodes, key=lambda item: item.node_id)],
            "relationships": [
                item.body() for item in sorted(self.relationships, key=lambda relation: relation.relationship_id)
            ],
        }

    def content_hash(self) -> str:
        return _sha256(self.body())

    def to_wire(self) -> Dict[str, object]:
        body = self.body()
        body["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}
        return body

    def nodes_for_root(self, economic_root_id: str) -> Tuple[EconomicInstrumentNode, ...]:
        return tuple(sorted((node for node in self.nodes if node.economic_root_id == economic_root_id), key=lambda item: item.node_id))

    def related_nodes(
        self,
        node_id: str,
        *,
        relationship_types: Optional[Sequence[EconomicRelationshipType]] = None,
    ) -> Tuple[EconomicInstrumentNode, ...]:
        nodes = self._node_map()
        if node_id not in nodes:
            raise KeyError(node_id)
        allowed = None if relationship_types is None else set(relationship_types)
        related = set()
        for relationship in self.relationships:
            if allowed is not None and relationship.relationship_type not in allowed:
                continue
            if relationship.source_node_id == node_id:
                related.add(relationship.target_node_id)
            elif relationship.bidirectional and relationship.target_node_id == node_id:
                related.add(relationship.source_node_id)
        return tuple(nodes[item] for item in sorted(related))

    @classmethod
    def from_wire(cls, value: Mapping[str, object]) -> "EconomicInstrumentGraph":
        nodes_raw = value.get("nodes")
        relationships_raw = value.get("relationships")
        if not isinstance(nodes_raw, Sequence) or isinstance(nodes_raw, (str, bytes)):
            raise EconomicGraphError("nodes must be an array")
        if not isinstance(relationships_raw, Sequence) or isinstance(relationships_raw, (str, bytes)):
            raise EconomicGraphError("relationships must be an array")

        nodes = []
        for raw in nodes_raw:
            if not isinstance(raw, Mapping):
                raise EconomicGraphError("graph node must be an object")
            instrument = raw.get("instrument")
            if not isinstance(instrument, Mapping):
                raise EconomicGraphError("graph node instrument is malformed")
            nodes.append(
                EconomicInstrumentNode(
                    node_id=str(raw.get("node_id", "")),
                    instrument=CanonicalInstrument(
                        canonical_id=str(instrument.get("canonical_id", "")),
                        asset_class=str(instrument.get("asset_class", "")),
                        market_type=str(instrument.get("market_type", "")),
                        base_asset=str(instrument.get("base_asset", "")),
                        quote_asset=str(instrument.get("quote_asset", "")),
                        settlement_asset=None if instrument.get("settlement_asset") is None else str(instrument.get("settlement_asset")),
                        expiry=None if instrument.get("expiry") is None else str(instrument.get("expiry")),
                    ),
                    role=InstrumentRole(str(raw.get("role", ""))),
                    economic_root_id=str(raw.get("economic_root_id", "")),
                    quote_family_id=str(raw.get("quote_family_id", "")),
                    contract_spec_ref=None if raw.get("contract_spec_ref") is None else str(raw.get("contract_spec_ref")),
                )
            )

        relationships = []
        for raw in relationships_raw:
            if not isinstance(raw, Mapping):
                raise EconomicGraphError("relationship must be an object")
            relationships.append(
                EconomicRelationship(
                    relationship_id=str(raw.get("relationship_id", "")),
                    relationship_type=EconomicRelationshipType(str(raw.get("relationship_type", ""))),
                    source_node_id=str(raw.get("source_node_id", "")),
                    target_node_id=str(raw.get("target_node_id", "")),
                    bidirectional=bool(raw.get("bidirectional", True)),
                    rationale=str(raw.get("rationale", "")),
                )
            )

        graph = cls(
            schema_version=str(value.get("schema_version", "")),
            graph_id=str(value.get("graph_id", "")),
            graph_version=str(value.get("graph_version", "")),
            effective_at_ns=int(value.get("effective_at_ns", -1)),
            known_at_ns=int(value.get("known_at_ns", -1)),
            nodes=tuple(nodes),
            relationships=tuple(relationships),
        )
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("content_hash") != graph.content_hash():
            raise EconomicGraphError("Economic Instrument Graph content hash mismatch")
        return graph
