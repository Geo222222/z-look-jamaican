import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autonomous_kernel.market_observer import (
    ObserverBusyError,
    ObserverConfig,
    ObserverLease,
    run_observer_once,
)
from autonomous_kernel.microstructure_features import public_microstructure_distributions


class MarketObserverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir()
        (self.root / "state").mkdir()
        self.config = {
            "schema_version": 1,
            "observer_id": "TEST-OBSERVER",
            "provider": "coinbase_advanced_trade_public_websocket",
            "endpoint": "wss://advanced-trade-ws.coinbase.com",
            "instrument": "BTC-USD",
            "channels": ["level2", "market_trades", "heartbeats"],
            "cadence_seconds": 15,
            "minimum_window_separation_seconds": 15,
            "capture_seconds": 2,
            "message_idle_timeout_seconds": 1,
            "maximum_messages": 100,
            "maximum_uncompressed_bytes": 100000,
            "maximum_source_clock_ahead_seconds": 1,
            "distribution_percentiles": [50, 90, 99, 100],
            "lease_timeout_seconds": 20,
            "maximum_consecutive_failures_before_degraded": 2,
            "maximum_window_history": 10,
            "network_policy": "PUBLIC_READ_ONLY",
            "authentication_allowed": False,
            "orders_allowed": False,
            "wallets_allowed": False,
            "capital_used_usd": "0.00",
        }
        self._write_config()
        (self.root / "state/market_observer.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "observer_id": "TEST-OBSERVER",
                    "status": "IDLE",
                    "updated_at": None,
                    "active_window": None,
                    "last_attempt_at": None,
                    "last_success_at": None,
                    "next_eligible_at": None,
                    "consecutive_failures": 0,
                    "windows": [],
                    "failures": [],
                    "authority": "test",
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _write_config(self):
        (self.root / "config/market_observer.json").write_text(
            json.dumps(self.config), encoding="utf-8"
        )

    def test_config_fails_closed_if_authentication_is_enabled(self):
        self.config["authentication_allowed"] = True
        self._write_config()
        with self.assertRaisesRegex(ValueError, "authentication"):
            ObserverConfig.load(self.root)

    def test_lease_blocks_overlap_and_recovers_stale_owner(self):
        now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
        first = ObserverLease(self.root, 20, now).acquire()
        try:
            with self.assertRaises(ObserverBusyError):
                ObserverLease(self.root, 20, now + timedelta(seconds=1)).acquire()
        finally:
            first.release()

        stale_path = self.root / "runtime/market_observer/observer.lock"
        stale_path.parent.mkdir(parents=True, exist_ok=True)
        stale_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "token": "old",
                    "pid": 1,
                    "acquired_at": "2026-09-02T11:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        recovered = ObserverLease(self.root, 20, now).acquire()
        recovered.release()
        self.assertFalse(stale_path.exists())
        self.assertTrue(list((self.root / "runtime/market_observer").glob("observer.stale.*.lock")))

    def test_success_preregisters_persists_audit_and_enforces_separation(self):
        now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
        calls = []

        async def fake_capture(root, config, identity):
            preregistrations = list(
                (root / "artifacts/evidence/market/observer").glob("*-preregistration.json")
            )
            self.assertEqual(len(preregistrations), 1)
            calls.append(identity["window_id"])
            return {
                "manifest": {
                    "summary": {
                        "unique_message_count": 20,
                        "level2_update_count": 12,
                        "market_trade_message_count": 4,
                        "spread_bps_percentiles": {"50": "0.5", "90": "0.8"},
                    }
                },
                "observation": {
                    "observation_id": "OBS-TEST",
                    "quality": {"status": "VALID"},
                    "raw": {
                        "provider_payload": {
                            "manifest_path": "artifacts/market_data/streams/test.manifest.json"
                        }
                    },
                },
                "microstructure_features": {
                    "depth_sample_count": 10,
                    "total_depth_10bps_base_percentiles": {"50": "1.5"},
                    "book_imbalance_10bps_percentiles": {"50": "0.1"},
                    "book_impact_proxy": {
                        "truth_class": "PUBLIC_ORDER_BOOK_PROXY_NOT_ACTUAL_FILL"
                    },
                },
            }

        first = asyncio.run(
            run_observer_once(self.root, now=now, capture_fn=fake_capture)
        )
        self.assertEqual(first["status"], "CAPTURED")
        self.assertEqual(len(calls), 1)
        state = json.loads(
            (self.root / "state/market_observer.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["status"], "IDLE")
        self.assertEqual(state["consecutive_failures"], 0)
        self.assertEqual(len(state["windows"]), 1)
        self.assertFalse(state["windows"][0]["public_features"]["actual_fill_truth_available"])
        audit_path = self.root / state["windows"][0]["audit_path"]
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        self.assertEqual(audit["outcome"], "VALID_PUBLIC_OBSERVATION_WINDOW")
        self.assertEqual(audit["safety"]["capital_used_usd"], "0.00")
        self.assertFalse(audit["safety"]["capability_promoted"])

        async def should_not_run(root, config, identity):
            raise AssertionError("capture ran before next eligible window")

        second = asyncio.run(
            run_observer_once(
                self.root, now=now + timedelta(seconds=5), capture_fn=should_not_run
            )
        )
        self.assertEqual(second["status"], "NOT_DUE")

    def test_failure_is_preserved_without_reusing_window(self):
        now = datetime(2026, 9, 2, 13, 0, tzinfo=timezone.utc)

        async def failing_capture(root, config, identity):
            raise RuntimeError("provider unavailable")

        result = asyncio.run(
            run_observer_once(self.root, now=now, capture_fn=failing_capture)
        )
        self.assertEqual(result["status"], "FAILED")
        state = json.loads(
            (self.root / "state/market_observer.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["consecutive_failures"], 1)
        self.assertIsNone(state["active_window"])
        self.assertEqual(len(state["failures"]), 1)
        audit = json.loads(
            next((self.root / "evidence/audits/market_observer").glob("*.json")).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(audit["outcome"], "REJECTED_PUBLIC_OBSERVATION_WINDOW")
        self.assertFalse(audit["safety"]["capability_promoted"])

    def test_public_feature_replay_measures_depth_and_labels_fill_proxy(self):
        records = [
            {
                "message": {
                    "channel": "level2",
                    "sequence_num": 0,
                    "timestamp": "2026-09-02T12:00:00Z",
                    "events": [
                        {
                            "type": "snapshot",
                            "updates": [
                                {
                                    "side": "bid",
                                    "price_level": "99900",
                                    "new_quantity": "2",
                                },
                                {
                                    "side": "offer",
                                    "price_level": "100100",
                                    "new_quantity": "2",
                                },
                            ],
                        }
                    ],
                }
            },
            {
                "message": {
                    "channel": "level2",
                    "sequence_num": 1,
                    "timestamp": "2026-09-02T12:00:01Z",
                    "events": [
                        {
                            "type": "update",
                            "updates": [
                                {
                                    "side": "bid",
                                    "price_level": "99950",
                                    "new_quantity": "1",
                                },
                                {
                                    "side": "offer",
                                    "price_level": "100050",
                                    "new_quantity": "1",
                                },
                            ],
                        }
                    ],
                }
            },
        ]
        features = public_microstructure_distributions(records)
        self.assertEqual(features["depth_sample_count"], 2)
        self.assertIn("50", features["total_depth_10bps_base_percentiles"])
        proxy = features["book_impact_proxy"]
        self.assertEqual(proxy["truth_class"], "PUBLIC_ORDER_BOOK_PROXY_NOT_ACTUAL_FILL")
        self.assertIn("100", proxy["buy_slippage_bps_by_quote_notional"])
        self.assertIn("100", proxy["sell_slippage_bps_by_quote_notional"])


if __name__ == "__main__":
    unittest.main()
