from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.experience.bridge import (
    MarketWideExperienceBridgeError,
    contexts_in_window,
    materialize_market_wide_experience,
)
from autonomous_kernel.experience.contracts import ExperienceTimescale
from autonomous_kernel.experience.market_wide_store import MarketWideExperienceStore
from autonomous_kernel.learning.direction_loop import materialize_cutoff_frame, _experience_for_cutoff
from tests.test_direction_competence_loop import T, SECOND, _write_span
from tests.test_market_wide_experience import history


class MarketWideExperienceBridgeTests(unittest.TestCase):
    def test_bridge_persists_degraded_single_instrument_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_id = _write_span(root, seconds=20)
            cutoff = T + 10 * SECOND
            frame = materialize_cutoff_frame(root, batch_id, cutoff)
            _experience_for_cutoff(root, frame)
            experience = materialize_market_wide_experience(
                root,
                cutoff_at_ns=cutoff,
                window_start_ns=cutoff - 30 * SECOND,
                timescale=ExperienceTimescale.SHORT,
            )
            self.assertEqual("DEGRADED", experience.status)
            self.assertGreaterEqual(experience.known_at_ns, cutoff - 30 * SECOND)
            self.assertLessEqual(experience.known_at_ns, cutoff)
            restored = MarketWideExperienceStore(root).load(experience.market_wide_experience_id)
            self.assertEqual(experience.content_hash(), restored.content_hash())
            window = contexts_in_window(root, window_start_ns=cutoff - 30 * SECOND, cutoff_at_ns=cutoff)
            self.assertGreaterEqual(len(window), 1)
            self.assertTrue(all(item.known_at_ns <= cutoff for item in window))

    def test_bridge_rejects_empty_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(MarketWideExperienceBridgeError, "no Market Context"):
                materialize_market_wide_experience(
                    root,
                    cutoff_at_ns=T,
                    window_start_ns=T - SECOND,
                )

    def test_builder_lookahead_still_hard_rejects(self):
        from autonomous_kernel.experience.market_wide import MarketWideExperienceError, build_market_wide_experience
        from tests.test_market_wide_experience import context

        source = history() + (
            context(
                "CTX-FUTURE",
                3_000_001,
                aggregate_return="1",
                breadth="0.5",
                dispersion="1",
                volatility="1",
                spread="1",
                liquidity_hhi="0.5",
                correlation="0.5",
                btc_return="1",
                eth_return="1",
                direction="NEUTRAL",
                correlation_regime="NORMAL",
            ),
        )
        with self.assertRaisesRegex(MarketWideExperienceError, "lookahead"):
            build_market_wide_experience(
                source,
                timescale=ExperienceTimescale.SHORT,
                window_start_ns=1_000_000,
                cutoff_at_ns=3_000_000,
            )


if __name__ == "__main__":
    unittest.main()
