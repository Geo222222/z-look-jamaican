import unittest

from monitor.app.contract import MonitorContractError, overview_view, validate_snapshot


def sample():
    def sec(data=None, state="available"):
        return {
            "provenance": {
                "source": "repository_files",
                "source_id": "X",
                "observed_at": "2026-08-31T22:00:00Z",
                "authoritative_at": "2026-08-31T21:59:00Z",
                "integrity": {"algorithm": "sha256", "by_path": {}},
            },
            "availability": {"state": state, "reason": None},
            "freshness": {"state": "fresh", "age_seconds": 3},
            "data": data or {},
        }

    return {
        "contract": {
            "name": "z-look-jamaican-monitor-snapshot",
            "schema_version": "1.0.0",
            "read_only": True,
            "observed_at": "2026-08-31T22:00:00Z",
        },
        "sections": {
            "system_health": sec({"system_id": "zlook", "root_state": "RESEARCH", "strategy_stage": "SHADOW", "validation_status": "ok", "heartbeat": {"age_seconds": 3, "state": "fresh"}}),
            "active_experiment": sec({"experiment_id": "EXP-MKT-002", "mode": "shadow", "summary": {}, "task_ids": []}),
            "decisions": sec({"counts": {"total": 8, "prospective": 4, "resolved": 4, "timestamp_violations": 0}}),
            "evidence_events": sec({"items": [{"id": "E1"}]}),
            "opportunities": sec({"items": []}),
            "economics": sec({"realized_totals": {"retained_revenue_usd": 0, "realized_profit_usd": 0}}, "not_earned"),
            "financial_exposure": sec({"recorded_current_exposure_usd": 0, "external_untracked_exposure": "unknown"}),
            "data_quality": sec({"repository_validation": "ok", "timestamp_violations": 0}),
            "goals_tasks": sec({"next_task_id": "TASK-3"}),
        },
    }


class MonitorContractTests(unittest.TestCase):
    def test_contract_and_overview(self):
        snap = validate_snapshot(sample())
        out = overview_view(snap)
        self.assertEqual(out["active_experiment"]["id"], "EXP-MKT-002")
        self.assertEqual(out["metrics"]["decisions_total"], 8)
        self.assertEqual(out["metrics"]["timestamp_violations"], 0)
        self.assertEqual(out["availability"]["economics"], "not_earned")

    def test_rejects_mutable_contract(self):
        payload = sample()
        payload["contract"]["read_only"] = False
        with self.assertRaises(MonitorContractError):
            validate_snapshot(payload)


if __name__ == "__main__":
    unittest.main()
