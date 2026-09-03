from __future__ import annotations

import unittest
from pathlib import Path

from experiments.z9_certification_status import z9_certification_status


class Z9CertificationStatusTests(unittest.TestCase):
    def test_repo_truthfully_reports_construction_complete_but_empirical_gates_blocked(self):
        root = Path(__file__).resolve().parent.parent; value = z9_certification_status(root)
        self.assertEqual("CONSTRUCTED", value["construction"]["status"]); self.assertFalse(value["construction"]["base_z8_rewritten"]); self.assertEqual("DATA_BLOCKED", value["market_wide_historical"]["status"]); self.assertEqual("DATA_BLOCKED", value["spot_derivatives"]["status"]); self.assertEqual("DATA_BLOCKED", value["contextual_assembly"]["status"]); self.assertEqual("NONE", value["capital_effect"]); self.assertFalse(value["live_execution"])


if __name__ == "__main__": unittest.main()
