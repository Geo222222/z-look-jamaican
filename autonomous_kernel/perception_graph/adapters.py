from __future__ import annotations

from typing import Any, Mapping

from ..context.contracts import MarketContextFrame
from ..representation.contracts import RepresentationFrame
from .contracts import GraphRef, build_graph_node


def representation_to_graph_node(frame: RepresentationFrame) -> Mapping[str, Any]:
    return build_graph_node(
        node_id="REPGRAPH-%s" % frame.frame_id,
        node_type="REPRESENTATION",
        truth_class="NORMALIZED_MEASUREMENT",
        subject_id=frame.instrument.canonical_id,
        cutoff_at_ns=int(frame.cutoff_at_ns),
        known_at_ns=int(frame.known_at_ns),
        source_refs=tuple("observation:%s" % value for value in frame.source_observation_ids),
        input_refs=(),
        method={"name": "z2_representation_adapter", "version": "1.0", "source_builder_version": frame.builder_version},
        quality={"status": frame.status},
        payload={
            "representation_type": frame.representation_type,
            "representation_frame_id": frame.frame_id,
            "representation_content_hash": frame.content_hash(),
            "state": dict(frame.state),
        },
    )


def context_to_graph_node(context: MarketContextFrame, representation_nodes: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
    refs = []
    for frame_id in context.source_frame_ids:
        node_id = "REPGRAPH-%s" % frame_id
        if node_id not in representation_nodes:
            raise ValueError("market context graph adapter requires all exact source representation nodes")
        refs.append(GraphRef(node_id=node_id, relationship="CONTEXT_MEMBER", expected_node_type="REPRESENTATION"))
    return build_graph_node(
        node_id="CTXGRAPH-%s" % context.context_id,
        node_type="MARKET_CONTEXT",
        truth_class="DERIVED_CONTEXT",
        subject_id="MARKET.WIDE",
        cutoff_at_ns=int(context.cutoff_at_ns),
        known_at_ns=int(context.known_at_ns),
        source_refs=tuple("representation:%s" % value for value in context.source_frame_ids),
        input_refs=tuple(refs),
        method={"name": "z9_context_adapter", "version": "1.0", "source_builder_version": context.builder_version},
        quality={"status": context.status},
        payload={
            "market_context_id": context.context_id,
            "market_context_content_hash": context.content_hash(),
            "source_set_hash": context.source_set_hash(),
            "state": dict(context.state),
        },
    )
