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
    IntelligenceRuntimeError,
    assess_benjamin_publication_qualification,
    build_benjamin_handoff,
    build_benjamin_publication_policy_v1,
    build_intelligence_publication,
    project_runtime,
    validate_benjamin_handoff,
    validate_intelligence_publication,
)
from autonomous_kernel.intelligence.gate import REASON_DATA_QUALITY_NOT_VALID
from autonomous_kernel.intelligence.policy import POLICY_THRESHOLDS, POLICY_VERSION
from autonomous_kernel.operations import canonical_hash
from autonomous_kernel.operator.intelligence_projection import expert_intelligence_projection


CUTOFF = 13_000_000_010
PUBLISHED = 13_000_000_001
CONTEXT = {"regime": "RISK_ON", "liquidity": "NORMAL"}


def _contracts(count=2):
    school = build_baseline_expert_school()
    contracts = [item for item in school["experts"] if item["expert_id"].startswith("ECONOMIC_ROOT_DIRECTION_10S_")]
    return contracts[:count]


def _claim(contract, probability, evidence_ref, *, cutoff_ns=1_000_000_000):
    return build_expert_claim(
        contract,
        question_ref=contract["question_refs"][0],
        cutoff_ns=cutoff_ns,
        answer=probability,
        evidence_refs=(evidence_ref,),
        experience_refs=("experience:%s" % evidence_ref,),
        input_snapshot_hash=("%x" % (abs(hash(evidence_ref)) % 15 + 1)) * 64,
    )


def _context(*, status="QUALIFIED", known_at_ns=13_000_000_000, cutoff_at_ns=13_000_000_005, context_id="CTX-GATE-1"):
    digest = "a" * 64
    return MarketContextFrame(
        context_id=context_id,
        context_type="MARKET_CONTEXT",
        cutoff_at_ns=cutoff_at_ns,
        known_at_ns=known_at_ns,
        status=status,
        builder_version="context-v1",
        parameters={},
        state={
            "members": {"BTC-USD": {"frame_id": "FR-1", "frame_content_hash": digest}},
            "regimes": {"direction": "RISK_ON", "liquidity": "NORMAL", "volatility": "NORMAL"},
            "market": {"member_instrument_count": 1},
        },
        source_frame_ids=("FR-1",),
        source_frame_hashes=(digest,),
        source_instrument_ids=("BTC-USD",),
    )


def _world(*, samples=12, answers=(0.72, 0.68), shared_evidence=False, context=None, competence_now=13_000_000_000):
    contracts = _contracts(2)
    records = []
    latest = []
    for index, contract in enumerate(contracts):
        last = None
        for sample in range(samples):
            evidence = "evidence:shared" if shared_evidence else "evidence:%d:%d" % (index, sample)
            claim = _claim(contract, answers[index], evidence, cutoff_ns=1_000_000_000 + sample)
            last = claim
            records.append(score_expert_claim(contract, claim, True, resolved_at_ns=12_000_000_000 + sample, context=CONTEXT))
        latest.append(last)
    memory = build_competence_memory(records, now_ns=competence_now)
    assembly = assemble_expert_claims(tuple(latest), memory, CONTEXT)
    z9 = context if context is not None else _context()
    refs = []
    for claim in latest:
        for ref in claim["evidence_refs"]:
            if ref not in refs:
                refs.append(ref)
    publication = build_intelligence_publication(
        assembly,
        published_at_ns=PUBLISHED,
        evidence_refs=tuple(refs),
        competence_memory_hash=memory["integrity"]["content_hash"],
        market_context_hash=z9.content_hash(),
        question_definition_hash=latest[0]["question_definition_hash"],
        horizon_ns=latest[0]["horizon_ns"],
    )
    return {
        "contracts": contracts,
        "claims": latest,
        "memory": memory,
        "assembly": assembly,
        "context": z9,
        "publication": publication,
        "data_quality": {"state": "VALID"},
    }


def _qualify(world, **overrides):
    kwargs = {
        "qualification_cutoff_ns": CUTOFF,
        "claims": world["claims"],
        "data_quality": world["data_quality"],
    }
    kwargs.update(overrides)
    return assess_benjamin_publication_qualification(
        world["publication"],
        world["assembly"],
        world["memory"],
        world["context"],
        **kwargs,
    )


class BenjaminPublicationQualificationTests(unittest.TestCase):
    def test_01_well_supported_publication_is_eligible_and_creates_handoff(self):
        world = _world()
        result = _qualify(world)
        self.assertEqual(result["status"], "ELIGIBLE")
        self.assertEqual(result["blocking_reasons"], [])
        self.assertEqual(result["policy"]["policy_version"], POLICY_VERSION)
        self.assertEqual(result["policy"]["policy_hash"], build_benjamin_publication_policy_v1()["integrity"]["content_hash"])
        handoff = build_benjamin_handoff(
            world["publication"], result, world["assembly"], world["memory"], world["context"], world["claims"], created_at_ns=CUTOFF
        )
        validate_benjamin_handoff(handoff)
        self.assertEqual(handoff["authority"]["may_be_consumed_by"], "BENJAMIN")
        self.assertEqual(handoff["authority"]["economic_decision_remains_with"], "BENJAMIN")
        self.assertEqual(handoff["authority"]["risk_authorization_remains_with"], "WATCHMAN")
        self.assertEqual(handoff["authority"]["execution_remains_with"], "THE_HAND")
        self.assertTrue(handoff["denies"]["capital_allocation"])
        self.assertNotEqual(handoff["handoff_type"], world["publication"]["publication_type"])

    def test_02_insufficient_total_samples_is_blocked(self):
        world = _world(samples=6)
        result = _qualify(world)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("INSUFFICIENT_TOTAL_SAMPLE_SUPPORT", result["blocking_reasons"])

    def test_03_insufficient_contextual_samples_is_blocked(self):
        world = _world(samples=1)
        result = _qualify(world)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("INSUFFICIENT_CONTEXTUAL_SAMPLE_SUPPORT", result["blocking_reasons"])

    def test_04_stale_competence_is_blocked(self):
        late = CUTOFF + 30_000_000_000_000
        world = _world(competence_now=1, context=_context(known_at_ns=late - 1_000, cutoff_at_ns=late - 500))
        result = _qualify(world, qualification_cutoff_ns=late)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("STALE_COMPETENCE", result["blocking_reasons"])

    def test_05_stale_context_is_blocked(self):
        world = _world(context=_context(known_at_ns=1, cutoff_at_ns=2))
        result = _qualify(world, qualification_cutoff_ns=1 + 40_000_000_000)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("STALE_CONTEXT", result["blocking_reasons"])

    def test_06_unqualified_z9_context_is_blocked(self):
        world = _world(context=_context(status="DEGRADED"))
        result = _qualify(world)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("CONTEXT_NOT_QUALIFIED", result["blocking_reasons"])

    def test_07_excessive_disagreement_is_blocked(self):
        world = _world(answers=(0.95, 0.05))
        result = _qualify(world)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("EXCESSIVE_DISAGREEMENT", result["blocking_reasons"])

    def test_08_excessive_single_expert_dominance_is_blocked(self):
        world = _world(answers=(0.99, 0.01))
        result = _qualify(world)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("EXCESSIVE_EXPERT_DOMINANCE", result["blocking_reasons"])

    def test_09_excessive_evidence_overlap_is_blocked(self):
        world = _world(shared_evidence=True)
        result = _qualify(world)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("INSUFFICIENT_EVIDENCE_INDEPENDENCE", result["blocking_reasons"])

    def test_10_mismatched_assembly_context_is_blocked(self):
        world = _world(context=_context(context_id="CTX-OTHER"))
        world["publication"] = build_intelligence_publication(
            world["assembly"],
            published_at_ns=PUBLISHED,
            evidence_refs=tuple(ref for claim in world["claims"] for ref in claim["evidence_refs"]),
            competence_memory_hash=world["memory"]["integrity"]["content_hash"],
            market_context_hash=_context().content_hash(),
            question_definition_hash=world["claims"][0]["question_definition_hash"],
            horizon_ns=world["claims"][0]["horizon_ns"],
        )
        result = _qualify(world)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("ASSEMBLY_CONTEXT_MISMATCH", result["blocking_reasons"])

    def test_11_mismatched_competence_provenance_is_blocked(self):
        world = _world()
        other = _world(samples=12, answers=(0.71, 0.69))
        world["memory"] = other["memory"]
        result = _qualify(world)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("COMPETENCE_PROVENANCE_MISMATCH", result["blocking_reasons"])

    def test_12_missing_required_evidence_is_blocked(self):
        world = _world()
        result = _qualify(world, claims=(), data_quality=None)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("MISSING_REQUIRED_EVIDENCE", result["blocking_reasons"])

    def test_13_degraded_data_quality_is_blocked(self):
        world = _world()
        result = _qualify(world, data_quality={"state": "DEGRADED"})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn(REASON_DATA_QUALITY_NOT_VALID, result["blocking_reasons"])

    def test_14_tampered_internal_publication_hash_is_blocked(self):
        world = _world()
        tampered = dict(world["publication"])
        tampered["assembled_estimate"] = 0.01
        world["publication"] = tampered
        result = _qualify(world)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("INTEGRITY_FAILURE", result["blocking_reasons"])

    def test_15_tampered_qualification_policy_is_blocked(self):
        world = _world()
        policy = dict(build_benjamin_publication_policy_v1())
        thresholds = dict(POLICY_THRESHOLDS)
        thresholds["minimum_total_scored_samples"] = 1
        policy["thresholds"] = thresholds
        result = _qualify(world, policy=policy)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("INTEGRITY_FAILURE", result["blocking_reasons"])

    def test_16_future_information_is_blocked(self):
        world = _world()
        result = _qualify(world, qualification_cutoff_ns=PUBLISHED - 10)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("FUTURE_INFORMATION_LEAKAGE", result["blocking_reasons"])

    def test_17_duplicate_qualification_is_idempotent(self):
        world = _world()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            runtime.publish(world["publication"], occurred_at_ns=PUBLISHED)
            first = runtime.qualify_for_benjamin(
                world["publication"], world["assembly"], world["memory"], world["context"],
                qualification_cutoff_ns=CUTOFF, claims=world["claims"], data_quality=world["data_quality"],
            )
            second = runtime.qualify_for_benjamin(
                world["publication"], world["assembly"], world["memory"], world["context"],
                qualification_cutoff_ns=CUTOFF, claims=world["claims"], data_quality=world["data_quality"],
            )
            self.assertTrue(second["idempotent"])
            self.assertEqual(first["qualification"]["integrity"]["content_hash"], second["qualification"]["integrity"]["content_hash"])
            self.assertEqual(first["handoff"]["integrity"]["content_hash"], second["handoff"]["integrity"]["content_hash"])
            self.assertEqual(runtime.state()["event_count"], 3)

    def test_18_conflicting_duplicate_identity_fails(self):
        world = _world()
        result = _qualify(world)
        handoff = build_benjamin_handoff(
            world["publication"], result, world["assembly"], world["memory"], world["context"], world["claims"], created_at_ns=CUTOFF
        )
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            runtime.record_handoff(handoff, occurred_at_ns=CUTOFF)
            conflict = dict(handoff)
            conflict["created_at_ns"] = CUTOFF + 1
            body = {key: value for key, value in conflict.items() if key != "integrity"}
            conflict["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
            with self.assertRaises(IntelligenceRuntimeError):
                runtime.record_handoff(conflict, occurred_at_ns=CUTOFF + 1)

    def test_19_buy_sell_hold_field_is_rejected(self):
        world = _world()
        result = _qualify(world)
        handoff = build_benjamin_handoff(
            world["publication"], result, world["assembly"], world["memory"], world["context"], world["claims"], created_at_ns=CUTOFF
        )
        for field in ("buy", "sell", "hold"):
            tampered = dict(handoff)
            tampered[field] = True
            with self.assertRaises(BenjaminPublicationGateError):
                validate_benjamin_handoff(tampered)

    def test_20_position_size_field_is_rejected(self):
        world = _world()
        result = _qualify(world)
        handoff = dict(build_benjamin_handoff(
            world["publication"], result, world["assembly"], world["memory"], world["context"], world["claims"], created_at_ns=CUTOFF
        ))
        handoff["position_size"] = 1
        with self.assertRaises(BenjaminPublicationGateError):
            validate_benjamin_handoff(handoff)

    def test_21_capital_allocation_field_is_rejected(self):
        world = _world()
        result = _qualify(world)
        handoff = dict(build_benjamin_handoff(
            world["publication"], result, world["assembly"], world["memory"], world["context"], world["claims"], created_at_ns=CUTOFF
        ))
        handoff["capital_allocation"] = {"usd": 1}
        with self.assertRaises(BenjaminPublicationGateError):
            validate_benjamin_handoff(handoff)

    def test_22_risk_authorization_field_is_rejected(self):
        world = _world()
        result = _qualify(world)
        handoff = dict(build_benjamin_handoff(
            world["publication"], result, world["assembly"], world["memory"], world["context"], world["claims"], created_at_ns=CUTOFF
        ))
        payload = dict(handoff)
        payload["risk_authorization"] = "APPROVED"
        with self.assertRaises(BenjaminPublicationGateError):
            validate_benjamin_handoff(payload)

    def test_23_execution_and_provider_order_fields_are_rejected(self):
        world = _world()
        result = _qualify(world)
        handoff = dict(build_benjamin_handoff(
            world["publication"], result, world["assembly"], world["memory"], world["context"], world["claims"], created_at_ns=CUTOFF
        ))
        for field in ("execution_instruction", "provider_order"):
            tampered = dict(handoff)
            tampered[field] = {"venue": "coinbase"}
            with self.assertRaises(BenjaminPublicationGateError):
                validate_benjamin_handoff(tampered)

    def test_24_internal_publication_cannot_masquerade_as_benjamin_handoff(self):
        world = _world()
        tampered = dict(world["publication"])
        tampered["handoff_type"] = "BENJAMIN_QUALIFIED_INTELLIGENCE"
        with self.assertRaises(Exception):
            validate_intelligence_publication(tampered)
        self.assertEqual(world["publication"]["publication_type"], "ZLJ_INTERNAL_INTELLIGENCE")
        self.assertNotEqual(world["publication"]["publication_type"], "BENJAMIN_QUALIFIED_INTELLIGENCE")

    def test_25_blocked_publication_cannot_create_benjamin_handoff(self):
        world = _world(samples=3)
        result = _qualify(world)
        self.assertEqual(result["status"], "BLOCKED")
        with self.assertRaises(BenjaminPublicationGateError):
            build_benjamin_handoff(
                world["publication"], result, world["assembly"], world["memory"], world["context"], world["claims"], created_at_ns=CUTOFF
            )

    def test_26_historical_replay_remains_deterministic(self):
        world = _world()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = IntelligenceRuntime(root)
            runtime.publish(world["publication"], occurred_at_ns=PUBLISHED)
            first = runtime.qualify_for_benjamin(
                world["publication"], world["assembly"], world["memory"], world["context"],
                qualification_cutoff_ns=CUTOFF, claims=world["claims"], data_quality=world["data_quality"],
            )
            later_claim = _claim(world["contracts"][0], 0.9, "evidence:later", cutoff_ns=20_000_000_000)
            later_score = score_expert_claim(world["contracts"][0], later_claim, True, resolved_at_ns=32_000_000_000, context=CONTEXT)
            runtime.record_claim(world["contracts"][0], later_claim, occurred_at_ns=20_000_000_001)
            runtime.record_score(later_score, occurred_at_ns=32_000_000_001)
            runtime.rebuild_competence(known_at_ns=40_000_000_000)
            replayed = project_runtime(runtime.events()[:3])
            self.assertEqual(replayed["qualifications"][0]["integrity"]["content_hash"], first["qualification"]["integrity"]["content_hash"])
            self.assertEqual(replayed["handoffs"][0]["integrity"]["content_hash"], first["handoff"]["integrity"]["content_hash"])
            self.assertEqual(replayed["qualifications"][0]["status"], "ELIGIBLE")
            projection = expert_intelligence_projection(root)
            self.assertEqual(projection["runtime"]["benjamin"]["eligibility_status"], "ELIGIBLE")
            self.assertEqual(projection["runtime"]["benjamin"]["handoff_count"], 1)
            self.assertTrue(projection["runtime"]["internal_intelligence_exists"])

    def test_policy_does_not_claim_empirical_optimality(self):
        policy = build_benjamin_publication_policy_v1()
        self.assertEqual(policy["empirical_status"], "NOT_CLAIMED_OPTIMAL")
        self.assertEqual(policy["thresholds"]["minimum_total_scored_samples"], 20)


if __name__ == "__main__":
    unittest.main()
