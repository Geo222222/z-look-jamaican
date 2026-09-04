import copy
import unittest

from autonomous_kernel.experiments import validate_experiment_registry
from autonomous_kernel.store import load_json, repository_root


class ExperimentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.root = repository_root()
        self.document = load_json(self.root / "state/experiments.json")

    def test_current_registry_has_immutable_lineage_and_valid_hashes(self):
        self.assertEqual([], validate_experiment_registry(self.document, self.root))

    def test_preregistration_hash_drift_fails(self):
        changed = copy.deepcopy(self.document)
        changed["items"][0]["preregistration_sha256"] = "0" * 64
        errors = validate_experiment_registry(changed, self.root)
        self.assertTrue(any("hash mismatch" in item for item in errors))

    def test_running_experiment_requires_restart_command(self):
        changed = copy.deepcopy(self.document)
        running = next(item for item in changed["items"] if item["status"] == "RUNNING")
        running["resume_command"] = None
        errors = validate_experiment_registry(changed, self.root)
        self.assertTrue(any("needs resume_command" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
