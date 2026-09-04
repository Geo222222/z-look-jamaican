from __future__ import annotations

import unittest

from autonomous_kernel.perception_graph import GraphRef, PerceptionGraphError, build_graph_node, validate_graph_node


class PerceptionGraphParentResolutionTests(unittest.TestCase):
    def test_required_missing_parent_fails_closed(self):
        node = build_graph_node(
            node_id="CTX-MISSING-PARENT",
            node_type="MARKET_CONTEXT",
            truth_class="DERIVED_CONTEXT",
            subject_id="MARKET.WIDE",
            cutoff_at_ns=100,
            known_at_ns=100,
            source_refs=("representation:missing",),
            input_refs=(GraphRef(node_id="REP-MISSING", relationship="CONTEXT_MEMBER", required=True, expected_node_type="REPRESENTATION"),),
            method={"name": "z9_context_adapter", "version": "1.0"},
            quality={"status": "QUALIFIED"},
            payload={},
        )
        with self.assertRaisesRegex(PerceptionGraphError, "required graph parent missing"):
            validate_graph_node(node, resolver=lambda _node_id: None)


if __name__ == "__main__":
    unittest.main()
