from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from monitor.app import main as mainmod
from monitor.app.read_model import (
    build_health,
    slice_benjamin_handoff,
    slice_intelligence,
    snapshot_digest,
)
from autonomous_kernel.operator import operator_snapshot


REPO = Path(__file__).resolve().parents[1]


class OperatorLocalApiTests(unittest.TestCase):
    def setUp(self):
        mainmod.ROOT = REPO
        self.client = TestClient(mainmod.app)

    def test_health_endpoint_is_online_and_non_economic(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["connectivity"], "BACKEND_ONLINE")
        self.assertIn(payload["status"], {"BACKEND_ONLINE", "BACKEND_DEGRADED"})
        self.assertEqual(payload["runtime_mode"], "SHADOW_ONLY")
        self.assertEqual(payload["live_execution"], "LOCKED")
        self.assertEqual(payload["capital_authority"], "NONE")
        self.assertFalse(payload["authority"]["capital_allocation"])
        self.assertFalse(payload["authority"]["external_execution"])
        self.assertNotIn("buy", payload)
        self.assertNotIn("position_size", payload)

    def test_read_only_api_surfaces(self):
        for path in (
            "/api/system", "/api/overview", "/api/market", "/api/context", "/api/questions",
            "/api/experts", "/api/outcomes", "/api/competence", "/api/assembly", "/api/research",
            "/api/jobs", "/api/intelligence", "/api/benjamin-handoff",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_no_new_mutation_routes(self):
        mutating = []
        for route in mainmod.app.routes:
            methods = getattr(route, "methods", None) or set()
            path = getattr(route, "path", "")
            if methods & {"POST", "PUT", "PATCH", "DELETE"}:
                mutating.append((path, sorted(methods)))
        self.assertEqual(mutating, [("/api/control/execute", ["POST"])])
        denied = self.client.post("/api/intelligence", json={"buy": True})
        self.assertEqual(denied.status_code, 405)

    def test_operator_snapshot_correctness_and_zero_runtime_defaults(self):
        snapshot = operator_snapshot(REPO)
        intel = slice_intelligence(snapshot)
        benjamin = slice_benjamin_handoff(snapshot)
        self.assertEqual(intel["publication_count"], 0)
        self.assertFalse(intel["internal_intelligence_exists"])
        self.assertEqual(benjamin["eligibility_status"], "NONE")
        self.assertEqual(benjamin["handoff_count"], 0)
        self.assertEqual(benjamin["authority"]["economic_decision_remains_with"], "BENJAMIN")
        self.assertFalse(benjamin["authority"]["capital_allocation"])
        self.assertIsNone(benjamin["latest_handoff"])
        health = build_health(REPO)
        self.assertEqual(health["connectivity"], "BACKEND_ONLINE")
        self.assertNotIn(health["status"], {None, "UNKNOWN", "demo"})

    def test_invalid_intelligence_journal_is_degraded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = root / "memory/expert_intelligence.jsonl"
            journal.parent.mkdir(parents=True)
            journal.write_text("{not json\n", encoding="utf-8")
            health = build_health(root)
            self.assertEqual(health["connectivity"], "BACKEND_ONLINE")
            self.assertEqual(health["status"], "BACKEND_DEGRADED")
            self.assertEqual(health["intelligence_journal_validity"], "INVALID")

    def test_sse_snapshot_digest_changes_only_when_payload_changes(self):
        first = {"contract": {"name": "zlj-operator-console"}, "n": 1}
        second = {"contract": {"name": "zlj-operator-console"}, "n": 1}
        third = {"contract": {"name": "zlj-operator-console"}, "n": 2}
        self.assertEqual(snapshot_digest(first), snapshot_digest(second))
        self.assertNotEqual(snapshot_digest(first), snapshot_digest(third))
        sse_routes = [getattr(route, "path", "") for route in mainmod.app.routes if getattr(route, "path", "") == "/api/events"]
        self.assertEqual(sse_routes, ["/api/events"])


if __name__ == "__main__":
    unittest.main()
