from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.learning.direction_loop import process_canonical_direction_batches
from autonomous_kernel.prediction.question_journal import QuestionPredictionJournal


REPO = Path(__file__).resolve().parents[1]


def _copy_canonical(src_root: Path, dest_root: Path, batch_id: str) -> None:
    relative_dir = Path("artifacts/market_data/canonical")
    (dest_root / relative_dir).mkdir(parents=True, exist_ok=True)
    for suffix in (".jsonl.gz", ".manifest.json"):
        src = src_root / relative_dir / (batch_id + suffix)
        if not src.is_file():
            raise FileNotFoundError(src)
        shutil.copy2(src, dest_root / relative_dir / (batch_id + suffix))


class RealCoinbaseDirectionLoopTests(unittest.TestCase):
    def test_public_coinbase_batches_complete_predict_resolve_score_loop(self):
        manifests = sorted((REPO / "artifacts/market_data/canonical").glob("CAN-COINBASE-BTC-USD-OBS-*.manifest.json"))
        self.assertGreaterEqual(len(manifests), 1, "real Coinbase canonical batches are required")
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
            proof_dir = REPO / "runtime/prediction-competence-proof"
            proof_dir.mkdir(parents=True, exist_ok=True)
            proof = {
                "batch_ids": batch_ids,
                "counts": result["counts"],
                "skipped_unqualified_cutoffs": result.get("skipped_unqualified_cutoffs"),
                "question_ref": result["question_ref"],
                "horizon_ns": result["horizon_ns"],
                "contextual_competence_status": result["contextual_competence_status"],
                "prospective_shadow_blocked_reason": result["prospective_shadow_blocked_reason"],
                "causal_chain_example": {
                    "prediction": first,
                    "outcome": matching,
                },
                "sync": {key: value for key, value in dict(result.get("sync") or {}).items() if key != "competence"},
                "competence": (result.get("sync") or {}).get("competence"),
                "restart": {
                    "duplicate_scores_on_second_sync": process_canonical_direction_batches(root, tuple(batch_ids))["sync"]["scores_recorded"],
                },
            }
            (proof_dir / "result.json").write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            for name in (
                "memory/question_predictions.jsonl",
                "memory/question_outcomes.jsonl",
                "state/question_prediction_journal.json",
                "state/question_outcome_journal.json",
                "memory/expert_intelligence.jsonl",
                "state/expert_intelligence.json",
            ):
                src = root / name
                if src.is_file():
                    dest = REPO / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)


if __name__ == "__main__":
    unittest.main()
