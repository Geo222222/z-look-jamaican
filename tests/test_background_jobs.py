import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.background_jobs import launch_due, status, validate_background_jobs


class BackgroundJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "state").mkdir()
        self.registry = {
            "schema_version": 1,
            "items": [{
                "id": "JOB-TEST-001", "enabled": True,
                "module": "experiments.microstream_qualification", "shell": False,
                "capital_effect": "NONE", "credentials_allowed": False,
                "timeout_seconds": 90,
                "runs": [{"id": "RUN-TEST-001", "not_before": "2026-01-01T00:00:00Z", "args": []}],
            }],
        }
        (self.root / "state/background_jobs.json").write_text(json.dumps(self.registry), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_registry_enforces_zero_effect_allowlist(self):
        self.assertEqual(validate_background_jobs(self.registry), [])
        bad = json.loads(json.dumps(self.registry))
        bad["items"][0]["credentials_allowed"] = True
        self.assertTrue(validate_background_jobs(bad))

    def test_preregistration_hash_is_enforced_when_root_is_supplied(self):
        artifact = self.root / "freeze.json"
        artifact.write_text("{}", encoding="utf-8")
        import hashlib
        self.registry["items"][0]["preregistration_path"] = "freeze.json"
        self.registry["items"][0]["preregistration_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        self.assertEqual(validate_background_jobs(self.registry, self.root), [])
        artifact.write_text('{"changed":true}', encoding="utf-8")
        self.assertTrue(any("hash mismatch" in error for error in validate_background_jobs(self.registry, self.root)))

    def test_status_is_read_only(self):
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = status(self.root, "2026-01-02T00:00:00Z")
        after = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(result["items"][0]["runs"][0]["state"], "READY")

    def test_due_launch_is_idempotently_claimed(self):
        calls = []
        launcher = lambda command, cwd: calls.append((command, cwd)) or 123
        first = launch_due(self.root, "2026-01-02T00:00:00Z", launcher)
        second = launch_due(self.root, "2026-01-02T00:00:00Z", launcher)
        self.assertEqual(first["launched"][0]["run_id"], "RUN-TEST-001")
        self.assertEqual(second["launched"], [])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
