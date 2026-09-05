from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomous_kernel.learning.direction_loop import process_canonical_direction_batches
from autonomous_kernel.prediction.question_journal import QuestionPredictionJournal


REPO = Path(__file__).resolve().parents[1]
REAL_COINBASE_OPT_IN_ENV = "ZLOOK_RUN_REAL_COINBASE_DIRECTION_TEST"
CHECKOUT_GUARD_PATHS = (
    "runtime/prediction-competence-proof",
    "memory/question_predictions.jsonl",
    "memory/question_outcomes.jsonl",
    "memory/expert_intelligence.jsonl",
    "state/question_prediction_journal.json",
    "state/question_outcome_journal.json",
    "state/expert_intelligence.json",
)


def _opt_in_requested() -> bool:
    return os.environ.get(REAL_COINBASE_OPT_IN_ENV, "").strip() == "1"


def _local_coinbase_manifests(root: Path) -> list:
    directory = Path(root) / "artifacts/market_data/canonical"
    if not directory.is_dir():
        return []
    return sorted(directory.glob("CAN-COINBASE-BTC-USD-OBS-*.manifest.json"))


def require_opt_in_local_coinbase_captures(root: Path) -> list:
    if not _opt_in_requested():
        raise unittest.SkipTest(
            "real Coinbase Direction proof is opt-in; set %s=1 with local CAN-COINBASE-BTC-USD-OBS-* captures"
            % REAL_COINBASE_OPT_IN_ENV
        )
    manifests = _local_coinbase_manifests(root)
    if not manifests:
        raise AssertionError(
            "%s=1 but no artifacts/market_data/canonical/CAN-COINBASE-BTC-USD-OBS-*.manifest.json captures are present"
            % REAL_COINBASE_OPT_IN_ENV
        )
    return manifests


def _copy_canonical(src_root: Path, dest_root: Path, batch_id: str) -> None:
    relative_dir = Path("artifacts/market_data/canonical")
    (dest_root / relative_dir).mkdir(parents=True, exist_ok=True)
    for suffix in (".jsonl.gz", ".manifest.json"):
        src = src_root / relative_dir / (batch_id + suffix)
        if not src.is_file():
            raise FileNotFoundError(src)
        shutil.copy2(src, dest_root / relative_dir / (batch_id + suffix))


def _checkout_guard(root: Path) -> tuple:
    records = []
    for relative in CHECKOUT_GUARD_PATHS:
        path = Path(root) / relative
        if not path.exists():
            records.append((relative, None))
            continue
        if path.is_file():
            stat = path.stat()
            records.append((relative, stat.st_size, stat.st_mtime_ns))
            continue
        for child in sorted(path.rglob("*")):
            if not child.is_file():
                continue
            stat = child.stat()
            records.append((str(child.relative_to(root)).replace("\\", "/"), stat.st_size, stat.st_mtime_ns))
    return tuple(records)


class RealCoinbaseDirectionGateTests(unittest.TestCase):
    def test_skips_without_opt_in_and_does_not_mutate_checkout(self):
        before = _checkout_guard(REPO)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(REAL_COINBASE_OPT_IN_ENV, None)
            with self.assertRaises(unittest.SkipTest):
                require_opt_in_local_coinbase_captures(REPO)
        self.assertEqual(before, _checkout_guard(REPO))

    def test_opt_in_without_local_captures_fails_clearly(self):
        before = _checkout_guard(REPO)
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory)
            with patch.dict(os.environ, {REAL_COINBASE_OPT_IN_ENV: "1"}):
                with self.assertRaisesRegex(AssertionError, r"%s=1 but no .*captures are present" % REAL_COINBASE_OPT_IN_ENV):
                    require_opt_in_local_coinbase_captures(empty)
        self.assertEqual(before, _checkout_guard(REPO))


class RealCoinbaseDirectionLoopTests(unittest.TestCase):
    def test_public_coinbase_batches_complete_predict_resolve_score_loop(self):
        before = _checkout_guard(REPO)
        manifests = require_opt_in_local_coinbase_captures(REPO)
        batch_ids = [path.name[: -len(".manifest.json")] for path in manifests[-2:]]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for batch_id in batch_ids:
                _copy_canonical(REPO, root, batch_id)
            result = process_canonical_direction_batches(root, tuple(batch_ids))
            self.assertGreaterEqual(result["counts"]["predicted"], 6)
            self.assertGreater(result["counts"]["resolved"] + result["counts"]["unresolvable"], 0)
            self.assertEqual(result["question_ref"], "ECONOMIC_ROOT_DIRECTION_10S@1.0.0")
            self.assertEqual(result["horizon_ns"], 10_000_000_000)
            self.assertFalse(result["authority"]["capital_allocation"])
            self.assertEqual(result["counts"]["predicted"], result["sync"]["journal_prediction_count"])
            self.assertEqual(result["counts"]["unresolvable"], result["sync"]["unresolvable_predictions"])
            self.assertEqual(
                result["counts"]["resolved"] + result["counts"]["unresolvable"],
                result["sync"]["journal_outcome_count"],
            )
            self.assertEqual(result["counts"]["pending"], result["sync"]["awaiting_outcome_predictions"])
            self.assertEqual(
                result["counts"]["predicted"],
                result["counts"]["resolved"] + result["counts"]["unresolvable"] + result["counts"]["pending"],
            )
            predictions = QuestionPredictionJournal(root).entries()
            self.assertEqual(len(predictions), result["counts"]["predicted"])
            first = result["predictions"][0]
            matching = next(item for item in result["outcomes"] if item.get("prediction_id") == first["prediction_id"])
            if matching.get("status") == "RESOLVED":
                self.assertGreater(int(matching["decided_at_ns"]), int(first["cutoff_at_ns"]))
            self.assertIsNotNone(result.get("sync"))
            self.assertFalse((root / "runtime/prediction-competence-proof/result.json").is_file())
        self.assertEqual(
            before,
            _checkout_guard(REPO),
            "real Coinbase Direction test must not write runtime/, memory/, or state/ in the repository checkout",
        )


if __name__ == "__main__":
    unittest.main()
