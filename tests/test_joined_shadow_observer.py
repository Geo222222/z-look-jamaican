import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.joined_shadow_observer import JoinedShadowPolicy, join_observer_window
from autonomous_kernel.market_data import MarketDataStore, validate_market_data_store
from autonomous_kernel.market_data_quality import classify_market_data
from autonomous_kernel.market_observation_qualification import QUALIFIED, qualification_snapshot
from autonomous_kernel.operations import canonical_hash
from autonomous_kernel.qualified_shadow import STATE_RELATIVE_PATH


def write_policy(root: Path, **overrides):
    document = {
        "schema_version": 1,
        "program_id": "QUALIFIED-MARKET-SHADOW-V1",
        "handoff_mode": "PERCEPTION_ACCEPTANCE_ONLY",
        "target_position": 0,
        "strategy_id": "PERCEPTION-PIPELINE-QUALIFICATION-V1",
        "rationale_code": "NO_TRADING_SIGNAL_PERCEPTION_ACCEPTANCE",
        "actionable_delay_seconds": 1,
        "max_event_age_seconds": 30,
        "max_transport_age_seconds": 30,
        "capital_effect": "NONE",
        "execution_authority": False,
    }
    document.update(overrides)
    path = root / "config/qualified_shadow.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


def stream_observation():
    raw = {
        "provider": "provider-stream",
        "instrument": "BTC-USD",
        "channel": "microstructure_stream",
        "provider_payload": {},
        "source_event_at": 1000,
        "received_at": 1000,
    }
    normalized = {
        "schema_version": 1,
        "type": "microstructure_stream_summary",
        "instrument": "BTC-USD",
        "raw_observation_id": "OBS-STREAM-1",
        "stream_id": "STREAM-1",
        "summary": {
            "schema_version": 1,
            "record_count": 10,
            "unique_message_count": 10,
            "duplicate_count": 0,
            "channels": ["heartbeats", "level2", "market_trades"],
            "sequence_scope": "CONNECTION_GLOBAL",
            "gaps": [],
            "out_of_order": [],
            "level2_snapshot_count": 1,
            "level2_update_count": 8,
            "market_trade_message_count": 1,
            "heartbeat_message_count": 1,
            "final_book_hash": "book-hash",
        },
        "truth_class": "OBSERVED_PUBLIC_MARKET_DATA",
    }
    quality = classify_market_data(
        provider=raw["provider"],
        source_event_at=1000,
        received_at=1000,
        observed_at=1000,
        max_event_age_seconds=30,
        max_transport_age_seconds=30,
    ).to_dict()
    content = {"raw": raw, "normalized": normalized, "quality": quality}
    return {
        "schema_version": 1,
        "observation_id": "OBS-STREAM-1",
        "observed_at": 1000,
        "raw": raw,
        "normalized": normalized,
        "quality": quality,
        "integrity": {"algorithm": "sha256", "content_hash": canonical_hash(content)},
    }


def window():
    return {
        "window_id": "PUBLIC-MICROSTRUCTURE-WINDOW-TEST-1",
        "stream_id": "STREAM-1",
        "quality": "VALID",
        "observation_id": "OBS-STREAM-1",
    }


class JoinedShadowObserverTests(unittest.TestCase):
    def test_fresh_window_creates_neutral_join_and_earns_certification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            MarketDataStore(root).persist(stream_observation())

            result = join_observer_window(root, window(), consumed_at=1005)

            self.assertEqual("JOINED_NEUTRAL_PERCEPTION", result["status"])
            self.assertEqual(0, result["target_position"])
            self.assertEqual("NONE", result["capital_effect"])
            self.assertFalse(result["execution_authority"])
            self.assertTrue((root / STATE_RELATIVE_PATH).is_file())
            snapshot = qualification_snapshot(root, {"decisions": []})
            self.assertEqual(QUALIFIED, snapshot["shadow_evidence"]["certification_state"])
            self.assertEqual(1, snapshot["shadow_evidence"]["successor_qualified_count"])

    def test_retry_returns_existing_join_even_after_freshness_window_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            MarketDataStore(root).persist(stream_observation())
            first = join_observer_window(root, window(), consumed_at=1005)
            retry = join_observer_window(root, window(), consumed_at=1100)
            self.assertEqual("JOINED_NEUTRAL_PERCEPTION", first["status"])
            self.assertEqual("ALREADY_JOINED_NEUTRAL_PERCEPTION", retry["status"])
            self.assertEqual(first["decision_id"], retry["decision_id"])
            self.assertEqual(1005, retry["consumed_at"])
            self.assertEqual(first["market_evidence_bond"], retry["market_evidence_bond"])

    def test_corrupted_existing_join_is_rejected_on_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            MarketDataStore(root).persist(stream_observation())
            join_observer_window(root, window(), consumed_at=1005)

            state_path = root / STATE_RELATIVE_PATH
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["decisions"][0]["target_position"] = 1
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "failed validation"):
                join_observer_window(root, window(), consumed_at=1100)

    def test_stale_first_handoff_is_skipped_without_successor_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            MarketDataStore(root).persist(stream_observation())
            result = join_observer_window(root, window(), consumed_at=1100)
            self.assertEqual("SKIPPED_NOT_FRESH_OR_QUALIFIED", result["status"])
            self.assertFalse((root / STATE_RELATIVE_PATH).exists())

    def test_stream_identity_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            MarketDataStore(root).persist(stream_observation())
            bad = dict(window())
            bad["stream_id"] = "DIFFERENT-STREAM"
            with self.assertRaisesRegex(ValueError, "stream_id"):
                join_observer_window(root, bad, consumed_at=1005)

    def test_policy_cannot_enable_position_or_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = write_policy(root, target_position=1)
            with self.assertRaisesRegex(ValueError, "neutral target 0"):
                JoinedShadowPolicy.load(root, policy_path)

            write_policy(root, execution_authority=True)
            with self.assertRaisesRegex(ValueError, "financial or execution authority"):
                JoinedShadowPolicy.load(root)

    def test_full_kernel_requires_successor_state_and_handoff_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            state.mkdir(parents=True, exist_ok=True)
            (state / "current_state.json").write_text("{}\n", encoding="utf-8")
            MarketDataStore(root).rebuild_index()

            errors = validate_market_data_store(root)

            self.assertIn(
                "missing required successor shadow state: state/qualified_market_shadow.json",
                errors,
            )
            self.assertIn(
                "missing required joined-shadow policy: config/qualified_shadow.json",
                errors,
            )


if __name__ == "__main__":
    unittest.main()
