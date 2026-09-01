"""Execute the preregistered complete zero-capital shadow lifecycle."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from autonomous_kernel.operations import validate_execution_receipts
from autonomous_kernel.shadow_lifecycle import ExecutionAssumptions, ShadowLifecycle, TypedDecision
from autonomous_kernel.store import load_json
from experiments.market_data_capture import capture


FROZEN_AT_EPOCH = 1788273780


def run(root: Path) -> dict:
    captured = capture(root)
    observation = next(item for item in captured if item["normalized"]["instrument"] == "BTC-USD")
    if int(observation["raw"]["source_event_at"]) <= FROZEN_AT_EPOCH:
        raise RuntimeError("captured candle is not prospective to preregistration")
    capabilities = load_json(root / "state/capabilities.json")["items"]
    capability = next(item for item in capabilities if item["id"] == "CAP-ZERO-EXPOSURE-EXECUTION-001")
    governor = load_json(root / "state/current_state.json")["governor"]
    suffix = str(observation["raw"]["source_event_at"])
    decision = TypedDecision(
        f"DEC-EXP-SHADOW-LIFECYCLE-001-{suffix}", capability["id"], observation["observation_id"],
        datetime.fromtimestamp(int(observation["observed_at"]), timezone.utc).isoformat().replace("+00:00", "Z"),
        "BUY", "0.001", "MARKET", None, "PREREGISTERED_ZERO_CAPITAL_LIFECYCLE",
    )
    assumptions = ExecutionAssumptions(
        "ASSUME-EXP-SHADOW-LIFECYCLE-001", "20", "2", "3", 150, "1", "0.5",
        "0.0001", "0.01", "10", "0.01",
    )
    processed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    receipt = ShadowLifecycle(root).run(
        decision=decision, observation=observation, capability=capability, governor=governor,
        assumptions=assumptions, processed_at=processed_at,
    )
    errors = validate_execution_receipts(root)
    if errors:
        raise RuntimeError("; ".join(errors))
    return {"experiment_id": "EXP-SHADOW-LIFECYCLE-001", "observation_id": observation["observation_id"], "receipt_id": receipt["receipt_id"], "result": receipt["execution_result"]["status"], "reconciliation": receipt["accounting"]["reconciliation_state"], "actual_financial_exposure_usd": receipt["accounting"]["financial_exposure_usd"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(json.dumps(run(args.root.resolve()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
