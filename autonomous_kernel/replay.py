"""Deterministic restartable replay over immutable market observations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .market_data import _atomic_json, validate_observation
from .shadow_lifecycle import ExecutionAssumptions, ShadowLifecycle, TypedDecision


class ReplayEngine:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def run(
        self, *, replay_id: str, observations: Sequence[Mapping[str, Any]],
        decisions: Mapping[str, TypedDecision], capability: Mapping[str, Any],
        governor: Mapping[str, Any], assumptions: ExecutionAssumptions,
        processed_at: str, fail_after_receipts: int | None = None,
    ) -> Mapping[str, Any]:
        if not replay_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in replay_id):
            raise ValueError("unsafe replay_id")
        ids = [str(item.get("observation_id", "")) for item in observations]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate market observation ID")
        for item in observations:
            errors = validate_observation(item)
            if errors:
                raise ValueError("invalid replay observation: " + "; ".join(errors))
        ordered = sorted(observations, key=lambda item: (int(item["raw"]["source_event_at"]), str(item["normalized"]["instrument"]), str(item["observation_id"])))
        ordered_ids = [str(item["observation_id"]) for item in ordered]
        checkpoint_path = self.root / "runtime/replays" / f"{replay_id}.json"
        if checkpoint_path.exists():
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if checkpoint.get("ordered_observation_ids") != ordered_ids:
                raise RuntimeError("replay input lineage changed")
        else:
            gaps = self._gaps(ordered)
            checkpoint = {"schema_version": 1, "replay_id": replay_id, "ordered_observation_ids": ordered_ids, "processed_observation_ids": [], "receipt_paths": [], "gaps": gaps, "status": "RUNNING"}
            _atomic_json(checkpoint_path, checkpoint)
        lifecycle = ShadowLifecycle(self.root)
        for item in ordered:
            observation_id = str(item["observation_id"])
            if observation_id in checkpoint["processed_observation_ids"]:
                continue
            decision = decisions.get(observation_id)
            if decision is None:
                checkpoint["processed_observation_ids"].append(observation_id)
                _atomic_json(checkpoint_path, checkpoint)
                continue
            receipt = lifecycle.run(decision=decision, observation=item, capability=capability, governor=governor, assumptions=assumptions, processed_at=processed_at)
            receipt_path = f"receipts/execution/{receipt['request']['request_id']}.json"
            checkpoint["processed_observation_ids"].append(observation_id)
            if receipt_path not in checkpoint["receipt_paths"]:
                checkpoint["receipt_paths"].append(receipt_path)
            _atomic_json(checkpoint_path, checkpoint)
            if fail_after_receipts is not None and len(checkpoint["receipt_paths"]) >= fail_after_receipts:
                raise RuntimeError("injected replay interruption")
        checkpoint["status"] = "COMPLETE"
        checkpoint["timestamp_ordered"] = True
        checkpoint["duplicate_safe"] = True
        checkpoint["randomness"] = "NONE"
        checkpoint["provenance_preserved"] = True
        _atomic_json(checkpoint_path, checkpoint)
        return checkpoint

    @staticmethod
    def _gaps(ordered: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        gaps = []
        previous: dict[str, Mapping[str, Any]] = {}
        for item in ordered:
            instrument = str(item["normalized"]["instrument"])
            prior = previous.get(instrument)
            if prior is not None:
                expected = int(prior["raw"]["source_event_at"]) + int(item["normalized"]["interval_seconds"])
                actual = int(item["raw"]["source_event_at"])
                if actual != expected:
                    gaps.append({"instrument": instrument, "after_observation_id": prior["observation_id"], "before_observation_id": item["observation_id"], "expected_source_event_at": expected, "actual_source_event_at": actual, "missing_intervals": max(0, (actual - expected) // int(item["normalized"]["interval_seconds"]))})
            previous[instrument] = item
        return gaps
