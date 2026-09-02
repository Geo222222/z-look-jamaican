import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from experiments.market_observer import _guarded_tick


class MarketObserverEntrypointTests(unittest.TestCase):
    def test_successful_capture_runs_join_then_compaction(self):
        captured = {
            "status": "CAPTURED",
            "observer_id": "PUBLIC-MICROSTRUCTURE-OBSERVER-001",
            "window": {
                "window_id": "PUBLIC-MICROSTRUCTURE-WINDOW-TEST",
                "stream_id": "STREAM-TEST",
                "observation_id": "OBS-STREAM-TEST",
                "quality": "VALID",
            },
        }
        joined = {
            "status": "JOINED_NEUTRAL_PERCEPTION",
            "decision_id": "JOIN-PUBLIC-MICROSTRUCTURE-WINDOW-TEST",
        }
        compacted = {"status": "COMPACTED", "stream_id": "STREAM-TEST"}
        storage = {"allowed": True, "used_bytes": 0}

        with patch("experiments.market_observer.observer_storage_status", return_value=storage), patch(
            "experiments.market_observer.run_observer_once", new=AsyncMock(return_value=captured)
        ) as run_once, patch(
            "experiments.market_observer.join_observer_window", return_value=joined
        ) as join_window, patch(
            "experiments.market_observer.compact_successful_raw_journal", return_value=compacted
        ) as compact:
            result = asyncio.run(_guarded_tick(__import__("pathlib").Path(".").resolve()))

        run_once.assert_awaited_once()
        join_window.assert_called_once()
        compact.assert_called_once()
        self.assertEqual(joined, result["joined_shadow_handoff"])
        self.assertEqual(compacted, result["raw_journal_cleanup"])
        self.assertEqual(storage, result["storage_before"])

    def test_handoff_error_is_reported_without_erasing_capture_or_skipping_cleanup(self):
        captured = {
            "status": "CAPTURED",
            "observer_id": "PUBLIC-MICROSTRUCTURE-OBSERVER-001",
            "window": {
                "window_id": "PUBLIC-MICROSTRUCTURE-WINDOW-TEST",
                "stream_id": "STREAM-TEST",
                "observation_id": "OBS-STREAM-TEST",
                "quality": "VALID",
            },
        }
        storage = {"allowed": True, "used_bytes": 0}
        with patch("experiments.market_observer.observer_storage_status", return_value=storage), patch(
            "experiments.market_observer.run_observer_once", new=AsyncMock(return_value=captured)
        ), patch(
            "experiments.market_observer.join_observer_window", side_effect=RuntimeError("handoff defect")
        ), patch(
            "experiments.market_observer.compact_successful_raw_journal",
            return_value={"status": "COMPACTED", "stream_id": "STREAM-TEST"},
        ) as compact:
            result = asyncio.run(_guarded_tick(__import__("pathlib").Path(".").resolve()))

        self.assertEqual("CAPTURED", result["status"])
        self.assertEqual("ERROR", result["joined_shadow_handoff"]["status"])
        self.assertEqual("RuntimeError", result["joined_shadow_handoff"]["error_type"])
        self.assertIn("handoff defect", result["joined_shadow_handoff"]["error"])
        compact.assert_called_once()


if __name__ == "__main__":
    unittest.main()
