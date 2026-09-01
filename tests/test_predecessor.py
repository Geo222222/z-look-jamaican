import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.predecessor import PredecessorVerificationError, verify_manifest


class PredecessorVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.sample = self.source / "primitive.py"
        self.sample.write_text("VALUE = 1\n", encoding="utf-8")
        digest = hashlib.sha256(self.sample.read_bytes()).hexdigest()
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps({
            "schema_version": 1,
            "manifest_id": "PREDECESSOR-TEST-001",
            "files": [{"path": "primitive.py", "sha256": digest}],
        }), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_manifest_verifies_read_only(self):
        before = self.sample.read_bytes()
        result = verify_manifest(self.manifest, self.source)
        self.assertEqual("verified", result["status"])
        self.assertFalse(result["writes_performed"])
        self.assertFalse(result["code_executed_from_predecessor"])
        self.assertEqual(before, self.sample.read_bytes())

    def test_drift_fails_closed(self):
        self.sample.write_text("VALUE = 2\n", encoding="utf-8")
        with self.assertRaises(PredecessorVerificationError):
            verify_manifest(self.manifest, self.source)

    def test_parent_path_is_rejected(self):
        payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        payload["files"][0]["path"] = "../outside.py"
        self.manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(PredecessorVerificationError):
            verify_manifest(self.manifest, self.source)


if __name__ == "__main__":
    unittest.main()
