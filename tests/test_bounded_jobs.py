from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.jobs import (
    BoundedJobError,
    build_job_spec,
    execute_job_run,
    job_status,
    persist_job_spec,
)


class BoundedJobsTests(unittest.TestCase):
    def _root_and_spec(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        prereg = root / "artifacts/evidence/jobs/prereg.json"
        prereg.parent.mkdir(parents=True, exist_ok=True)
        prereg.write_bytes(b'{"experiment":"X"}\n')
        digest = hashlib.sha256(prereg.read_bytes()).hexdigest()
        spec = build_job_spec(
            job_id="JOB-EXPERT-SYNC-001",
            action="EXPERT_SYNC",
            action_args={"known_at_ns": 100},
            preregistration_path="artifacts/evidence/jobs/prereg.json",
            preregistration_sha256=digest,
            run_ids=("RUN-001",),
            not_before_ns=(90,),
            timeout_seconds=30,
            created_at_ns=80,
        )
        return temporary, root, spec

    def test_job_requires_hash_pinned_preregistration_and_zero_authority(self):
        temporary, root, spec = self._root_and_spec()
        try:
            persist_job_spec(root, spec)
            self.assertEqual("NONE", spec["authority"]["capital_effect"])
            self.assertFalse(spec["authority"]["credentials_allowed"])
            self.assertFalse(spec["authority"]["external_execution"])
            status = job_status(root, known_at_ns=89)
            self.assertEqual("SCHEDULED", status["items"][0]["runs"][0]["state"])
            status = job_status(root, known_at_ns=90)
            self.assertEqual("READY", status["items"][0]["runs"][0]["state"])
        finally:
            temporary.cleanup()

    def test_unallowlisted_action_is_rejected(self):
        with self.assertRaisesRegex(BoundedJobError, "identity/action"):
            build_job_spec(
                job_id="JOB-BAD",
                action="ORDER_PLACEMENT",
                action_args={},
                preregistration_path="x.json",
                preregistration_sha256="a" * 64,
                run_ids=("RUN",),
                not_before_ns=(1,),
                timeout_seconds=1,
                created_at_ns=0,
            )

    def test_preregistration_tampering_fails_before_execution(self):
        temporary, root, spec = self._root_and_spec()
        try:
            persist_job_spec(root, spec)
            prereg = root / "artifacts/evidence/jobs/prereg.json"
            prereg.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(BoundedJobError, "hash mismatch"):
                job_status(root, known_at_ns=100)
        finally:
            temporary.cleanup()

    def test_execution_is_idempotent_and_uses_fixed_action_mapping(self):
        temporary, root, spec = self._root_and_spec()
        calls = []
        try:
            persist_job_spec(root, spec)

            def executor(command, cwd, timeout_seconds):
                calls.append((list(command), cwd, timeout_seconds))
                return 0, "ok", ""

            first = execute_job_run(root, job_id=spec["job_id"], run_id="RUN-001", known_at_ns=100, executor=executor)
            second = execute_job_run(root, job_id=spec["job_id"], run_id="RUN-001", known_at_ns=101, executor=executor)
            self.assertEqual("SUCCEEDED", first["status"])
            self.assertEqual(first, second)
            self.assertEqual(1, len(calls))
            command = calls[0][0]
            self.assertIn("expert_sync", command)
            self.assertIn("--known-at-ns", command)
            self.assertFalse(first["credentials_used"])
            self.assertFalse(first["shell_used"])
        finally:
            temporary.cleanup()

    def test_not_due_run_fails_closed(self):
        temporary, root, spec = self._root_and_spec()
        try:
            persist_job_spec(root, spec)
            with self.assertRaisesRegex(BoundedJobError, "not due"):
                execute_job_run(root, job_id=spec["job_id"], run_id="RUN-001", known_at_ns=89, executor=lambda *_: (0, "", ""))
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
