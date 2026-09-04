import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.market_data import MarketDataStore, build_candle_observation, validate_market_data_store
from autonomous_kernel.market_observation_qualification import BLOCKED, QUALIFIED, qualification_snapshot
from autonomous_kernel.qualified_shadow import (
    STATE_RELATIVE_PATH,
    ShadowDecisionProposal,
    record_qualified_shadow_decision,
    validate_qualified_shadow_state,
)


def candle(observation_id="OBS-QUALIFIED-SHADOW-1"):
    return build_candle_observation(
        observation_id=observation_id,
        provider="provider-a",
        instrument="BTC-USD",
        interval_seconds=300,
        candle_start_at=1000,
        received_at=1302,
        observed_at=1305,
        open_price="100",
        high_price="102",
        low_price="99",
        close_price="101",
        volume="4.5",
        max_event_age_seconds=30,
        max_transport_age_seconds=30,
    )


def proposal(*, decision_id="QUAL-SHADOW-1", target=1, observed_at=1305):
    return ShadowDecisionProposal(
        decision_id=decision_id,
        product="BTC-USD",
        observed_at=observed_at,
        actionable_at=1500 if observed_at < 1500 else observed_at + 300,
        target_position=target,
        strategy_id="TEST-STRATEGY-V1",
        rationale_code="TEST_SIGNAL",
        signal_candle_timestamp=1000,
    )


class QualifiedShadowTests(unittest.TestCase):
    def test_successor_decision_is_separate_from_legacy_and_earns_joined_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_path = root / "state/market_shadow.json"
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "experiment_id": "EXP-MKT-002",
                        "decisions": [
                            {
                                "id": "LEGACY-1",
                                "product": "BTC-USD",
                                "observed_at": 900,
                                "actionable_at": 1200,
                                "status": "resolved",
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            legacy_before = legacy_path.read_bytes()
            item = candle()
            MarketDataStore(root).persist(item)

            bound = record_qualified_shadow_decision(
                root,
                proposal(),
                [item["observation_id"]],
                max_event_age_seconds=30,
                max_transport_age_seconds=30,
            )

            self.assertEqual(legacy_before, legacy_path.read_bytes())
            self.assertEqual(2, bound["market_evidence_bond"]["schema_version"])
            self.assertEqual([], validate_qualified_shadow_state(root))
            self.assertTrue((root / STATE_RELATIVE_PATH).is_file())

            snapshot = qualification_snapshot(root)
            evidence = snapshot["shadow_evidence"]
            self.assertEqual(QUALIFIED, evidence["certification_state"])
            self.assertEqual(1, evidence["legacy_unjoined_count"])
            self.assertEqual(1, evidence["successor_decision_count"])
            self.assertEqual(1, evidence["successor_joined_count"])
            self.assertEqual(1, evidence["successor_qualified_count"])
            self.assertEqual(0, evidence["successor_blocked_count"])

    def test_stale_evidence_cannot_create_successor_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = candle()
            MarketDataStore(root).persist(item)
            with self.assertRaisesRegex(ValueError, "not qualified"):
                record_qualified_shadow_decision(
                    root,
                    proposal(observed_at=1400),
                    [item["observation_id"]],
                    max_event_age_seconds=30,
                    max_transport_age_seconds=30,
                )
            self.assertFalse((root / STATE_RELATIVE_PATH).exists())

    def test_target_position_tampering_breaks_evidence_bond_and_canonical_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = candle()
            MarketDataStore(root).persist(item)
            record_qualified_shadow_decision(
                root,
                proposal(),
                [item["observation_id"]],
                max_event_age_seconds=30,
                max_transport_age_seconds=30,
            )
            state_path = root / STATE_RELATIVE_PATH
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["decisions"][0]["target_position"] = 0
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

            snapshot = qualification_snapshot(root, {"decisions": []})
            evidence = snapshot["shadow_evidence"]
            self.assertEqual(BLOCKED, evidence["certification_state"])
            self.assertEqual(1, evidence["successor_blocked_count"])
            reasons = evidence["decisions"][0]["reasons"]
            self.assertIn("market_evidence_bond_hash_mismatch", reasons)

            canonical_errors = validate_market_data_store(root)
            self.assertTrue(any("qualified shadow decision hash mismatch" in error for error in canonical_errors))

    def test_retry_is_idempotent_and_conflicting_decision_id_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = candle()
            MarketDataStore(root).persist(item)
            first = record_qualified_shadow_decision(
                root,
                proposal(),
                [item["observation_id"]],
                max_event_age_seconds=30,
                max_transport_age_seconds=30,
            )
            second = record_qualified_shadow_decision(
                root,
                proposal(),
                [item["observation_id"]],
                max_event_age_seconds=30,
                max_transport_age_seconds=30,
            )
            self.assertEqual(first, second)
            with self.assertRaisesRegex(RuntimeError, "ID conflict"):
                record_qualified_shadow_decision(
                    root,
                    proposal(target=0),
                    [item["observation_id"]],
                    max_event_age_seconds=30,
                    max_transport_age_seconds=30,
                )

    def test_duplicate_observation_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = candle()
            MarketDataStore(root).persist(item)
            with self.assertRaisesRegex(ValueError, "duplicate observation IDs"):
                record_qualified_shadow_decision(
                    root,
                    proposal(),
                    [item["observation_id"], item["observation_id"]],
                    max_event_age_seconds=30,
                    max_transport_age_seconds=30,
                )


if __name__ == "__main__":
    unittest.main()
