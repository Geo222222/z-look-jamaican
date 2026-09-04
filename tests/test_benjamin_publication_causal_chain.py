from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.intelligence import (
    IntelligenceRuntime,
    IntelligenceRuntimeError,
    build_benjamin_handoff,
    project_runtime,
)
from autonomous_kernel.operations import canonical_hash
try:
    from test_benjamin_publication_gate import (
        CUTOFF,
        PUBLISHED,
        _journal_chain,
        _qualify,
        _qualify_runtime,
        _world,
    )
except ModuleNotFoundError:
    from tests.test_benjamin_publication_gate import (
        CUTOFF,
        PUBLISHED,
        _journal_chain,
        _qualify,
        _qualify_runtime,
        _world,
    )


class BenjaminPublicationCausalChainTests(unittest.TestCase):
    def test_01_qualification_fails_if_publication_was_never_journaled(self):
        world = _world()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            _journal_chain(runtime, world, publication=False)
            with self.assertRaisesRegex(IntelligenceRuntimeError, "recorded internal publication"):
                _qualify_runtime(runtime, world)

    def test_02_qualification_fails_if_assembly_was_never_journaled(self):
        world = _world()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            _journal_chain(runtime, world, assembly=False)
            with self.assertRaisesRegex(IntelligenceRuntimeError, "recorded assembly"):
                _qualify_runtime(runtime, world)

    def test_03_qualification_fails_if_competence_was_never_journaled(self):
        world = _world()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            _journal_chain(runtime, world, competence=False)
            with self.assertRaisesRegex(IntelligenceRuntimeError, "recorded competence memory"):
                _qualify_runtime(runtime, world)

    def test_04_qualification_fails_if_one_contributing_claim_was_never_journaled(self):
        world = _world()
        omitted = world["claims"][1]["integrity"]["content_hash"]
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            _journal_chain(runtime, world, competence=False, omit_claim_hashes=(omitted,))
            with self.assertRaisesRegex(IntelligenceRuntimeError, "recorded expert claim"):
                _qualify_runtime(runtime, world)

    def test_05_qualification_succeeds_only_after_all_exact_prerequisites_are_journaled(self):
        world = _world()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            with self.assertRaises(IntelligenceRuntimeError):
                _qualify_runtime(runtime, world)
            _journal_chain(runtime, world)
            result = _qualify_runtime(runtime, world)
            self.assertEqual(result["qualification"]["status"], "ELIGIBLE")
            self.assertIsNotNone(result["handoff"])
            types = [event["event_type"] for event in runtime.events()]
            self.assertIn("EXPERT_CLAIM_RECORDED", types)
            self.assertIn("EXPERT_SCORE_RECORDED", types)
            self.assertIn("COMPETENCE_REBUILT", types)
            self.assertIn("EXPERT_ASSEMBLY_RECORDED", types)
            self.assertIn("INTELLIGENCE_PUBLISHED", types)
            self.assertIn("BENJAMIN_PUBLICATION_QUALIFIED", types)
            self.assertIn("BENJAMIN_HANDOFF_PUBLISHED", types)

    def test_06_detached_structurally_valid_publication_cannot_be_qualified(self):
        world = _world()
        self.assertEqual(_qualify(world)["status"], "ELIGIBLE")
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            with self.assertRaisesRegex(IntelligenceRuntimeError, "recorded"):
                _qualify_runtime(runtime, world)

    def test_07_detached_structurally_valid_handoff_cannot_be_recorded(self):
        world = _world()
        receipt = _qualify(world)
        handoff = build_benjamin_handoff(
            world["publication"], receipt, world["assembly"], world["memory"], world["context"], world["claims"], created_at_ns=CUTOFF
        )
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            with self.assertRaisesRegex(IntelligenceRuntimeError, "recorded eligible qualification"):
                runtime.record_handoff(handoff, occurred_at_ns=CUTOFF)

    def test_08_handoff_fails_when_qualification_does_not_exist(self):
        world = _world()
        receipt = _qualify(world)
        handoff = build_benjamin_handoff(
            world["publication"], receipt, world["assembly"], world["memory"], world["context"], world["claims"], created_at_ns=CUTOFF
        )
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            _journal_chain(runtime, world)
            with self.assertRaisesRegex(IntelligenceRuntimeError, "recorded eligible qualification"):
                runtime.record_handoff(handoff, occurred_at_ns=CUTOFF)

    def test_09_handoff_fails_when_matching_qualification_is_blocked(self):
        blocked_world = _world(samples=3)
        eligible_world = _world()
        eligible = _qualify(eligible_world)
        handoff = dict(build_benjamin_handoff(
            eligible_world["publication"], eligible, eligible_world["assembly"], eligible_world["memory"],
            eligible_world["context"], eligible_world["claims"], created_at_ns=CUTOFF,
        ))
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            _journal_chain(runtime, blocked_world)
            blocked = _qualify_runtime(runtime, blocked_world)
            self.assertEqual(blocked["qualification"]["status"], "BLOCKED")
            self.assertIsNone(blocked["handoff"])
            handoff["qualification_result_id"] = blocked["qualification"]["qualification_id"]
            handoff["qualification_result_hash"] = blocked["qualification"]["integrity"]["content_hash"]
            body = {key: value for key, value in handoff.items() if key != "integrity"}
            handoff["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
            with self.assertRaisesRegex(IntelligenceRuntimeError, "blocked qualification"):
                runtime.record_handoff(handoff, occurred_at_ns=CUTOFF)

    def test_10_handoff_fails_when_qualification_hash_mismatches(self):
        world = _world()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            _journal_chain(runtime, world)
            result = _qualify_runtime(runtime, world)
            tampered = dict(result["handoff"])
            tampered["qualification_result_hash"] = "b" * 64
            body = {key: value for key, value in tampered.items() if key != "integrity"}
            tampered["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
            with self.assertRaisesRegex(IntelligenceRuntimeError, "qualification hash mismatch"):
                runtime.record_handoff(tampered, occurred_at_ns=CUTOFF)

    def test_11_handoff_fails_when_publication_lineage_mismatches(self):
        world = _world()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            _journal_chain(runtime, world)
            result = _qualify_runtime(runtime, world)
            tampered = dict(result["handoff"])
            tampered["internal_publication_hash"] = "c" * 64
            body = {key: value for key, value in tampered.items() if key != "integrity"}
            tampered["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
            with self.assertRaisesRegex(IntelligenceRuntimeError, "publication lineage mismatch"):
                runtime.record_handoff(tampered, occurred_at_ns=CUTOFF)

    def test_12_handoff_fails_when_assembly_lineage_mismatches(self):
        world = _world()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            _journal_chain(runtime, world)
            result = _qualify_runtime(runtime, world)
            tampered = dict(result["handoff"])
            tampered["assembly_hash"] = "d" * 64
            body = {key: value for key, value in tampered.items() if key != "integrity"}
            tampered["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
            with self.assertRaisesRegex(IntelligenceRuntimeError, "assembly lineage mismatch"):
                runtime.record_handoff(tampered, occurred_at_ns=CUTOFF)

    def test_13_exact_eligible_chain_remains_idempotent(self):
        world = _world()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            _journal_chain(runtime, world)
            first = _qualify_runtime(runtime, world)
            second = _qualify_runtime(runtime, world)
            third = runtime.record_handoff(first["handoff"], occurred_at_ns=CUTOFF)
            self.assertTrue(second["idempotent"])
            self.assertEqual(first["handoff"]["integrity"]["content_hash"], second["handoff"]["integrity"]["content_hash"])
            self.assertEqual(first["handoff"]["integrity"]["content_hash"], third["integrity"]["content_hash"])

    def test_14_conflicting_qualification_identity_still_fails(self):
        world = _world()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            _journal_chain(runtime, world)
            _qualify_runtime(runtime, world)
            with self.assertRaisesRegex(IntelligenceRuntimeError, "conflicting qualification identity reuse"):
                _qualify_runtime(runtime, world, data_quality={"state": "DEGRADED"})

    def test_15_conflicting_handoff_identity_still_fails(self):
        world = _world()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            _journal_chain(runtime, world)
            result = _qualify_runtime(runtime, world)
            conflict = dict(result["handoff"])
            conflict["known_at_ns"] = CUTOFF + 1
            body = {key: value for key, value in conflict.items() if key != "integrity"}
            conflict["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
            with self.assertRaisesRegex(IntelligenceRuntimeError, "conflicting handoff identity reuse"):
                runtime.record_handoff(conflict, occurred_at_ns=CUTOFF)

    def test_16_historical_replay_remains_deterministic(self):
        world = _world()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            _journal_chain(runtime, world)
            first = _qualify_runtime(runtime, world)
            events = runtime.events()
            rebuilt = project_runtime(events)
            self.assertEqual(rebuilt["qualifications"][0]["integrity"]["content_hash"], first["qualification"]["integrity"]["content_hash"])
            self.assertEqual(rebuilt["handoffs"][0]["integrity"]["content_hash"], first["handoff"]["integrity"]["content_hash"])
            again = project_runtime(runtime.events())
            self.assertEqual(again, rebuilt)

    def test_17_later_evidence_cannot_retroactively_create_a_historical_handoff(self):
        world = _world()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            _journal_chain(runtime, world, publication=False)
            with self.assertRaises(IntelligenceRuntimeError):
                _qualify_runtime(runtime, world, qualification_cutoff_ns=CUTOFF)
            runtime.publish(world["publication"], occurred_at_ns=CUTOFF + 1_000)
            with self.assertRaises(IntelligenceRuntimeError):
                _qualify_runtime(runtime, world, qualification_cutoff_ns=CUTOFF)
            later = _qualify_runtime(runtime, world, qualification_cutoff_ns=CUTOFF + 2_000)
            self.assertEqual(later["qualification"]["status"], "ELIGIBLE")
            historical = [event for event in runtime.events() if int(event["occurred_at_ns"]) <= CUTOFF]
            self.assertFalse(any(event["event_type"] == "BENJAMIN_HANDOFF_PUBLISHED" for event in historical))
            self.assertFalse(any(event["event_type"] == "BENJAMIN_PUBLICATION_QUALIFIED" for event in historical))

    def test_18_no_capital_risk_execution_authority_is_introduced(self):
        world = _world()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = IntelligenceRuntime(Path(temporary))
            _journal_chain(runtime, world)
            result = _qualify_runtime(runtime, world)
            authority = result["handoff"]["authority"]
            self.assertEqual(authority["may_be_consumed_by"], "BENJAMIN")
            self.assertEqual(authority["economic_decision_remains_with"], "BENJAMIN")
            self.assertEqual(authority["risk_authorization_remains_with"], "WATCHMAN")
            self.assertEqual(authority["execution_remains_with"], "THE_HAND")
            self.assertFalse(authority["capital_allocation"])
            self.assertFalse(authority["risk_authorization"])
            self.assertFalse(authority["external_execution"])
            self.assertFalse(authority["provider_order_creation"])
            denies = result["handoff"]["denies"]
            self.assertTrue(denies["capital_allocation"])
            self.assertTrue(denies["risk_authorization"])
            self.assertTrue(denies["external_execution"])
            self.assertTrue(denies["provider_order_creation"])
            for field in ("buy", "sell", "hold", "position_size", "capital_allocation", "risk_authorization", "provider_order"):
                self.assertNotIn(field, result["handoff"])
                self.assertNotIn(field, result["qualification"])


if __name__ == "__main__":
    unittest.main()
