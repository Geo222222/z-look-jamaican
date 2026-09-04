from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.context.contracts import MarketContextFrame
from autonomous_kernel.experts import (
    assemble_expert_claims,
    build_baseline_expert_school,
    build_competence_memory,
    build_expert_claim,
    score_expert_claim,
)
from autonomous_kernel.intelligence import (
    BenjaminPublicationGateError,
    IntelligenceRuntime,
    assess_benjamin_publication_eligibility,
    build_benjamin_handoff,
    validate_benjamin_handoff,
)
from autonomous_kernel.research import extract_context_features


SECOND = 1_000_000_000
CURRENT_T = 100 * SECOND


def _contracts():
    school = build_baseline_expert_school()
    direction = [
        item for item in school["experts"]
        if item["expert_id"].startswith("ECONOMIC_ROOT_DIRECTION_10S_")
    ]
    return direction[:2]


def _context(status="QUALIFIED", known_at_ns=CURRENT_T - 1):
    frame_id = "REP-CONTEXT-BTC"
    frame_hash = "a" * 64
    return MarketContextFrame(
        context_id="CTX-BENJAMIN-GATE",
        context_type="MARKET_CONTEXT",
        cutoff_at_ns=CURRENT_T,
        known_at_ns=known_at_ns,
        status=status,
        builder_version="market-context-v1",
        parameters={},
        state={
            "members": {
                "ASSET.BTC": {
                    "frame_id": frame_id,
                    "frame_content_hash": frame_hash,
                }
            },
            "market": {
                "member_instrument_count": 2,
                "qualified_spot_count": 2,
                "return_breadth_count": 2,
                "aggregate_return_bps": "4.2",
                "breadth_positive": "0.75",
                "cross_sectional_return_dispersion_bps": "1.1",
                "median_realized_volatility_bps": "12",
                "median_spread_bps": "1.5",
                "liquidity_concentration_hhi": "0.52",
                "median_absolute_pairwise_correlation": "0.70",
            },
            "derivatives": {
                "relationship_count": 1,
                "relationships": [{"basis_bps": "3.0"}],
            },
            "regimes": {
                "direction": "RISK_ON",
                "volatility": "NORMAL",
                "liquidity": "NORMAL",
                "correlation": "COHERENT",
                "derivatives": "CONTANGO",
                "structure": "ORDERLY",
            },
            "feature_quality": {
                "CORE_MARKET": {"status": "QUALIFIED"},
                "LIQUIDITY": {"status": "QUALIFIED"},
                "CORRELATION": {"status": "QUALIFIED"},
                "DERIVATIVES": {"status": "QUALIFIED"},
            },
            "input_quality": {"degraded_reasons": []},
        },
        source_frame_ids=(frame_id,),
        source_frame_hashes=(frame_hash,),
        source_instrument_ids=("ASSET.BTC",),
    )


def _claim(contract, cutoff_ns, probability, suffix):
    return build_expert_claim(
        contract,
        question_ref=contract["question_refs"][0],
        cutoff_ns=cutoff_ns,
        answer=probability,
        evidence_refs=("evidence:%s" % suffix,),
        experience_refs=("experience:%s" % suffix,),
        input_snapshot_hash=("b" if suffix.endswith("a") else "c") * 64,
    )


def _memory_and_assembly(*, samples=10, probabilities=(0.78, 0.68), context_override=None):
    contracts = _contracts()
    context = _context()
    current_context = dict(extract_context_features(context)["features"])
    records = []
    for expert_index, contract in enumerate(contracts):
        for i in range(samples):
            cutoff = (1 + i) * SECOND
            historical = _claim(contract, cutoff, 0.70 + 0.02 * expert_index, "hist-%d-%d-%s" % (expert_index, i, "a" if expert_index == 0 else "b"))
            records.append(
                score_expert_claim(
                    contract,
                    historical,
                    True,
                    resolved_at_ns=cutoff + 10 * SECOND,
                    context=current_context,
                )
            )
    memory = build_competence_memory(records, now_ns=CURRENT_T - SECOND)
    claims = (
        _claim(contracts[0], CURRENT_T, probabilities[0], "current-a"),
        _claim(contracts[1], CURRENT_T, probabilities[1], "current-b"),
    )
    assembly_context = current_context if context_override is None else dict(context_override)
    assembly = assemble_expert_claims(claims, memory, assembly_context)
    return context, memory, assembly


class BenjaminPublicationGateTests(unittest.TestCase):
    def test_empirically_supported_assembly_can_cross_benjamin_gate(self):
        context, memory, assembly = _memory_and_assembly()
        gate = assess_benjamin_publication_eligibility(
            assembly,
            memory,
            context,
            evaluated_at_ns=CURRENT_T + 1,
        )
        self.assertEqual(gate["status"], "ELIGIBLE")
        self.assertTrue(all(gate["checks"].values()))
        handoff = build_benjamin_handoff(
            assembly,
            memory,
            context,
            published_at_ns=CURRENT_T + 1,
            evidence_refs=("evidence:current:a", "evidence:current:b"),
        )
        validate_benjamin_handoff(handoff)
        self.assertEqual(handoff["consumer_boundary"]["may_be_consumed_by"], ["BENJAMIN"])
        self.assertFalse(handoff["authority"]["economic_decision"])
        self.assertEqual(handoff["intelligence"]["consumer_boundary"]["may_be_consumed_by"], ["ZLJ_INTERNAL"])
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            runtime.publish_benjamin_handoff(handoff, occurred_at_ns=CURRENT_T + 2)
            self.assertEqual(len(runtime.state()["benjamin_handoffs"]), 1)
            self.assertEqual(len(runtime.state()["publications"]), 0)

    def test_thin_competence_blocks_handoff(self):
        context, memory, assembly = _memory_and_assembly(samples=1)
        gate = assess_benjamin_publication_eligibility(assembly, memory, context, evaluated_at_ns=CURRENT_T + 1)
        self.assertEqual(gate["status"], "BLOCKED")
        self.assertIn("samples_per_expert_sufficient", gate["blocking_reasons"])
        self.assertIn("total_scored_samples_sufficient", gate["blocking_reasons"])
        with self.assertRaises(BenjaminPublicationGateError):
            build_benjamin_handoff(assembly, memory, context, published_at_ns=CURRENT_T + 1, evidence_refs=("a",))

    def test_stale_or_degraded_context_blocks_handoff(self):
        context, memory, assembly = _memory_and_assembly()
        stale_at = CURRENT_T + 31 * SECOND
        gate = assess_benjamin_publication_eligibility(assembly, memory, context, evaluated_at_ns=stale_at)
        self.assertEqual(gate["status"], "BLOCKED")
        self.assertIn("market_context_fresh", gate["blocking_reasons"])
        degraded = _context(status="DEGRADED")
        gate2 = assess_benjamin_publication_eligibility(assembly, memory, degraded, evaluated_at_ns=CURRENT_T + 1)
        self.assertEqual(gate2["status"], "BLOCKED")
        self.assertIn("market_context_qualified", gate2["blocking_reasons"])

    def test_context_mismatch_blocks_handoff(self):
        context = _context()
        canonical = dict(extract_context_features(context)["features"])
        canonical["context.regime.direction"] = "RISK_OFF"
        _, memory, assembly = _memory_and_assembly(context_override=canonical)
        gate = assess_benjamin_publication_eligibility(assembly, memory, context, evaluated_at_ns=CURRENT_T + 1)
        self.assertEqual(gate["status"], "BLOCKED")
        self.assertIn("assembly_context_matches_exact_z9", gate["blocking_reasons"])

    def test_high_disagreement_and_low_confidence_block_handoff(self):
        context, memory, assembly = _memory_and_assembly(probabilities=(0.95, 0.05))
        gate = assess_benjamin_publication_eligibility(assembly, memory, context, evaluated_at_ns=CURRENT_T + 1)
        self.assertEqual(gate["status"], "BLOCKED")
        self.assertIn("disagreement_bounded", gate["blocking_reasons"])
        self.assertIn("assembly_confidence_sufficient", gate["blocking_reasons"])


if __name__ == "__main__":
    unittest.main()
