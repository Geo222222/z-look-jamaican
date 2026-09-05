"""Bounded Direction 10s predict → resolve → score → competence experiment.

Uses durable canonical Coinbase batches already on disk, or an optional fresh
public observer capture. Does not train models, assemble beliefs, or allocate capital.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from autonomous_kernel.evaluation.question_journal import QuestionOutcomeJournal
from autonomous_kernel.experts.sync import sync_expert_learning
from autonomous_kernel.intelligence.runtime import IntelligenceRuntime
from autonomous_kernel.learning.direction_loop import (
    DIRECTION_QUESTION_REF,
    HORIZON_NS,
    process_canonical_direction_batches,
    question_learning_projection,
)
from autonomous_kernel.operator import operator_snapshot
from autonomous_kernel.prediction.question_journal import QuestionPredictionJournal


def _canonical_batches(root: Path) -> Sequence[str]:
    directory = root / "artifacts/market_data/canonical"
    if not directory.is_dir():
        return ()
    names = []
    for path in sorted(directory.glob("CAN-COINBASE-BTC-USD-OBS-*.manifest.json")):
        names.append(path.name[: -len(".manifest.json")])
    return tuple(names)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded Direction competence loop")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--batch-id", action="append")
    parser.add_argument("--proof", type=Path, default=Path("runtime/prediction-competence-proof/result.json"))
    args = parser.parse_args()
    root = args.root.resolve()
    batch_ids = tuple(args.batch_id) if args.batch_id else tuple(_canonical_batches(root)[-2:])
    if not batch_ids:
        raise SystemExit("no canonical Coinbase BTC-USD batches are available")

    result = process_canonical_direction_batches(root, batch_ids)
    predictions = list(QuestionPredictionJournal(root).entries())
    outcomes = list(QuestionOutcomeJournal(root).entries())
    prediction_bytes = (root / "memory/question_predictions.jsonl").read_bytes()
    outcome_bytes = (root / "memory/question_outcomes.jsonl").read_bytes()
    QuestionPredictionJournal(root).rebuild_state()
    QuestionOutcomeJournal(root).rebuild_state()
    restarted_predictions = (root / "memory/question_predictions.jsonl").read_bytes()
    restarted_outcomes = (root / "memory/question_outcomes.jsonl").read_bytes()
    known_at = int(result.get("sync", {}).get("known_at_ns") or 0)
    replay = dict(sync_expert_learning(root, known_at_ns=known_at)) if known_at else {}
    competence = IntelligenceRuntime(root).state().get("competence")
    snapshot = operator_snapshot(root)
    chain = None
    if result.get("predictions") and result.get("outcomes"):
        pred = result["predictions"][0]
        out = next((item for item in result["outcomes"] if item.get("prediction_id") == pred["prediction_id"]), None)
        chain = {
            "question_ref": DIRECTION_QUESTION_REF,
            "horizon_ns": HORIZON_NS,
            "prediction": pred,
            "outcome": out,
        }
    proof = {
        "status": result.get("status"),
        "question_ref": DIRECTION_QUESTION_REF,
        "horizon_ns": HORIZON_NS,
        "batch_ids": list(batch_ids),
        "counts": result.get("counts"),
        "prospective_shadow_blocked_reason": result.get("prospective_shadow_blocked_reason"),
        "contextual_competence_status": result.get("contextual_competence_status"),
        "causal_chain_example": chain,
        "sync": {key: value for key, value in dict(result.get("sync") or {}).items() if key != "competence"},
        "replay_sync": replay,
        "restart": {
            "prediction_journal_bytes_unchanged": prediction_bytes == restarted_predictions,
            "outcome_journal_bytes_unchanged": outcome_bytes == restarted_outcomes,
            "duplicate_scores_on_replay": int(replay.get("scores_recorded") or 0),
            "duplicate_claims_on_replay": int(replay.get("claims_recorded") or 0),
            "prediction_count": len(predictions),
            "outcome_count": len(outcomes),
        },
        "competence": None
        if not isinstance(competence, Mapping)
        else {
            "known_at_ns": competence.get("known_at_ns"),
            "content_hash": (competence.get("integrity") or {}).get("content_hash"),
            "entry_count": competence.get("entry_count"),
            "entries": competence.get("entries"),
            "mastery_claim": False,
        },
        "operator": {
            "question_learning": snapshot.get("question_learning"),
            "z3": next((stage for stage in snapshot.get("stages") or [] if stage.get("id") == "Z3"), None),
            "z6": next((stage for stage in snapshot.get("stages") or [] if stage.get("id") == "Z6"), None),
            "z7": next((stage for stage in snapshot.get("stages") or [] if stage.get("id") == "Z7"), None),
            "earned_competence": ((snapshot.get("expert_intelligence") or {}).get("qualification") or {}).get("earned_competence"),
            "benjamin_eligibility": ((snapshot.get("expert_intelligence") or {}).get("qualification") or {}).get("benjamin_eligibility"),
            "adaptive_assembly_count": ((snapshot.get("expert_intelligence") or {}).get("runtime") or {}).get("assembly_count"),
        },
        "projection": question_learning_projection(root),
        "authority": result.get("authority"),
    }
    _write_json(root / args.proof, proof)
    print(json.dumps({"status": "OK", "proof": str((root / args.proof).as_posix()), "counts": result.get("counts")}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
