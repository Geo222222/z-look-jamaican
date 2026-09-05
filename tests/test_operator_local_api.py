from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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
            "/api/forecasts", "/api/experts", "/api/outcomes", "/api/competence", "/api/assembly", "/api/research",
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

    def test_console_page_owns_a_single_event_source(self):
        web = REPO / "monitor/web"
        product = (web / "product-app.js").read_text(encoding="utf-8")
        expert = (web / "expert-console.js").read_text(encoding="utf-8")
        resolver = (web / "resolver-console.js").read_text(encoding="utf-8")
        index = (web / "index.html").read_text(encoding="utf-8")
        self.assertEqual(1, product.count("new EventSource"))
        self.assertIn("closeEvents", product)
        self.assertIn("pagehide", product)
        self.assertIn("zlj-operator-snapshot", product)
        self.assertNotIn("new EventSource", expert)
        self.assertNotIn("new EventSource", resolver)
        self.assertIn("zlj-operator-snapshot", expert)
        self.assertIn("zlj-operator-snapshot", resolver)
        self.assertIn("product-app.js", index)
        self.assertNotIn('src="/assets/app.js"', index)

    def test_health_stays_responsive_while_sse_clients_share_one_snapshot(self):
        calls = {"count": 0}

        def fake_snapshot():
            calls["count"] += 1
            time.sleep(0.3)
            return {
                "contract": {"name": "zlj-operator-console", "schema_version": "1.2", "read_only": True},
                "stages": [],
                "system": {"capital_authority": "NONE", "live_execution": "LOCKED_FALSE"},
            }

        class FakeRequest:
            def __init__(self):
                self.checks = 0

            async def is_disconnected(self):
                self.checks += 1
                return self.checks >= 2

        build_health(REPO)
        started = time.perf_counter()
        response = self.client.get("/api/health")
        health_ms = (time.perf_counter() - started) * 1000
        self.assertEqual(200, response.status_code)
        self.assertLess(health_ms, 250)
        self.assertFalse(response.json()["authority"]["capital_allocation"])

        previous = (mainmod._sse_latest, mainmod._sse_digest, mainmod._sse_generation)
        try:
            with patch.object(mainmod, "_snapshot", side_effect=fake_snapshot):
                asyncio.run(mainmod._publish_snapshot())
                self.assertEqual(1, calls["count"])
                self.assertIsNotNone(mainmod._sse_latest)
                self.assertTrue(asyncio.run(mainmod._disconnected_within(FakeRequest(), 1.0)))
        finally:
            mainmod._sse_latest, mainmod._sse_digest, mainmod._sse_generation = previous


if __name__ == "__main__":
    unittest.main()
