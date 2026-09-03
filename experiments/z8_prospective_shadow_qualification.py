from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import websockets

from autonomous_kernel.assembly import AssemblyJournal, assemble_and_record, validate_assembly_lineage
from autonomous_kernel.evaluation import OutcomeJournal, OutcomePendingError, PredictionOutcome, resolve_prediction
from autonomous_kernel.evaluation.journal import validate_outcome_journal
from autonomous_kernel.market_observer import REQUIRED_CHANNELS, SUPPORTED_ENDPOINT
from autonomous_kernel.microstream import StreamJournal
from autonomous_kernel.models import ModelRegistry, baseline_model_set, run_baseline_models
from autonomous_kernel.observation import ProviderRecord, adapt_coinbase_advanced_trade
from autonomous_kernel.operations import canonical_hash
from autonomous_kernel.prediction import Prediction, PredictionJournal
from autonomous_kernel.prediction.journal import validate_prediction_journal
from autonomous_kernel.representation import RepresentationFrame, build_instrument_state


EXPERIMENT_ID = "EXP-Z8-PROSPECTIVE-001"
PREREG_REF = "artifacts/evidence/market/exp-z8-prospective-001-preregistration.json"
PROVIDER = "coinbase_advanced_trade_public_websocket"
INSTRUMENT = "BTC-USD"


class ProspectiveQualificationError(RuntimeError):
    pass


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProspectiveQualificationError("expected JSON object: %s" % path)
    return value


def _register_shadow_harness(registry: ModelRegistry, models: Sequence[Any], start_ns: int) -> None:
    # This registry is experiment-local. These transitions exercise the serving
    # contract only; they are not durable claims that the baseline models have
    # earned economic qualification outside this prospective mechanism test.
    base = max(0, int(start_ns) - 20)
    for index, model in enumerate(models):
        artifact = hashlib.sha256((model.definition.content_hash() + "|prospective-mechanism-harness").encode("utf-8")).hexdigest()
        at = base + index * 4
        registry.register(
            model.definition,
            artifact_hash=artifact,
            code_ref="autonomous_kernel/models/baselines.py",
            training_data_refs=("MECHANISM_ONLY:NO_PRODUCTION_QUALIFICATION",),
            occurred_at_ns=at,
        )
        registry.transition(
            model.definition.model_ref,
            "REPLAY_QUALIFIED",
            evidence_kind="REPLAY_EVALUATION",
            evidence_refs=("MECHANISM-CERT:Z4-Z8",),
            occurred_at_ns=at + 1,
        )
        registry.transition(
            model.definition.model_ref,
            "WALK_FORWARD_QUALIFIED",
            evidence_kind="WALK_FORWARD_EVALUATION",
            evidence_refs=("MECHANISM-CERT:POINT-IN-TIME",),
            occurred_at_ns=at + 2,
        )
        registry.transition(
            model.definition.model_ref,
            "SHADOW",
            evidence_kind="SHADOW_EVALUATION",
            evidence_refs=("EXP-Z8-PROSPECTIVE-001:HARNESS",),
            occurred_at_ns=at + 3,
        )


def _outcomes(root: Path) -> Dict[str, PredictionOutcome]:
    result: Dict[str, PredictionOutcome] = {}
    for entry in OutcomeJournal(root).entries():
        outcome = PredictionOutcome.from_wire(entry["outcome"])
        result[outcome.prediction_id] = outcome
    return result


def _resolve_available(root: Path, prediction_ids: Sequence[str], frames: Sequence[RepresentationFrame], now_ns: int) -> None:
    known = _outcomes(root)
    journal = OutcomeJournal(root)
    for prediction_id in prediction_ids:
        if prediction_id in known:
            continue
        try:
            outcome = resolve_prediction(root, prediction_id, frames, now_at_ns=int(now_ns))
        except OutcomePendingError:
            continue
        journal.append(outcome)
        known[prediction_id] = outcome


def _prediction_map(root: Path) -> Dict[str, Prediction]:
    output: Dict[str, Prediction] = {}
    for entry in PredictionJournal(root).entries():
        prediction = Prediction.from_wire(entry["prediction"])
        output[prediction.prediction_id] = prediction
    return output


def _adaptive_metrics(root: Path, adaptive_ids: Sequence[str]) -> Mapping[str, Any]:
    predictions = _prediction_map(root)
    outcomes = _outcomes(root)
    pairs = []
    for prediction_id in adaptive_ids:
        prediction = predictions.get(prediction_id)
        outcome = outcomes.get(prediction_id)
        if prediction is not None and outcome is not None and outcome.status == "RESOLVED":
            pairs.append((prediction, outcome))
    if not pairs:
        return {"resolved_count": 0, "mae_bps": None, "brier_score": None, "directional_accuracy": None}
    abs_errors: List[Decimal] = []
    briers: List[Decimal] = []
    hits: List[Decimal] = []
    for prediction, outcome in pairs:
        realized = Decimal(str(outcome.realized_return_bps))
        expected = Decimal(prediction.expected_move_bps)
        actual = Decimal(int(outcome.actual_positive))
        probability = Decimal(prediction.probability_positive)
        abs_errors.append(abs(realized - expected))
        briers.append((probability - actual) ** 2)
        hits.append(Decimal("1") if (expected > 0) == bool(outcome.actual_positive) else Decimal("0"))
    divisor = Decimal(len(pairs))
    return {
        "resolved_count": len(pairs),
        "mae_bps": format(sum(abs_errors, Decimal("0")) / divisor, "f"),
        "brier_score": format(sum(briers, Decimal("0")) / divisor, "f"),
        "directional_accuracy": format(sum(hits, Decimal("0")) / divisor, "f"),
    }


def _stream_quality(manifest: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    summary = manifest.get("summary", {})
    reasons: List[str] = []
    channels = set(summary.get("channels", []))
    if not set(REQUIRED_CHANNELS).issubset(channels):
        reasons.append("required_channel_missing")
    if int(summary.get("level2_snapshot_count", 0)) < 1:
        reasons.append("level2_snapshot_missing")
    if int(summary.get("level2_update_count", 0)) < 1:
        reasons.append("level2_updates_missing")
    if int(summary.get("market_trade_message_count", 0)) < 1:
        reasons.append("trades_missing")
    if int(summary.get("heartbeat_message_count", 0)) < 1:
        reasons.append("heartbeats_missing")
    if summary.get("gaps"):
        reasons.append("sequence_gap")
    if summary.get("out_of_order"):
        reasons.append("out_of_order")
    return not reasons, reasons


async def run_live(source_root: Path) -> Mapping[str, Any]:
    prereg = _load_json(source_root / PREREG_REF)
    if prereg.get("experiment_id") != EXPERIMENT_ID or prereg.get("status") != "PREREGISTERED_BEFORE_LIVE_DATA":
        raise ProspectiveQualificationError("preregistration identity/status mismatch")
    policy = prereg["session_policy"]
    capture_seconds = int(policy["capture_seconds"])
    warmup_ns = int(policy["warmup_seconds"]) * 1_000_000_000
    interval_ns = int(policy["prediction_interval_seconds"]) * 1_000_000_000
    horizon_ns = int(policy["prediction_horizon_seconds"]) * 1_000_000_000
    max_lag_ns = int(policy["max_resolution_lag_seconds"]) * 1_000_000_000

    with tempfile.TemporaryDirectory(prefix="z8-prospective-") as directory:
        root = Path(directory)
        start_ns = time.time_ns()
        stream_id = "COINBASE-BTC-USD-PROSPECTIVE-%d" % start_ns
        raw_journal = StreamJournal(root, stream_id)
        models = baseline_model_set()
        registry = ModelRegistry(root)
        _register_shadow_harness(registry, models, start_ns)
        prediction_journal = PredictionJournal(root)

        observations: List[Any] = []
        frames: List[RepresentationFrame] = []
        pending_ids: List[str] = []
        adaptive_ids: List[str] = []
        receipts: List[str] = []
        first_live_message_ns: Optional[int] = None
        next_prediction_ns = start_ns + warmup_ns
        stop_predicting_ns = start_ns + capture_seconds * 1_000_000_000 - horizon_ns - max_lag_ns
        deadline = time.monotonic() + capture_seconds

        async with websockets.connect(SUPPORTED_ENDPOINT, open_timeout=20, max_size=8_000_000, ping_interval=20, ping_timeout=20) as socket:
            for channel in REQUIRED_CHANNELS:
                await socket.send(json.dumps({"type": "subscribe", "product_ids": [INSTRUMENT], "channel": channel}, separators=(",", ":")))
            while time.monotonic() < deadline:
                remaining = max(0.1, deadline - time.monotonic())
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=min(5.0, remaining))
                except asyncio.TimeoutError:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                received_ns = time.time_ns()
                message = json.loads(raw)
                if message.get("type") == "error" or message.get("channel") == "errors":
                    raise ProspectiveQualificationError("provider error: %s" % message)
                if first_live_message_ns is None:
                    first_live_message_ns = received_ns
                raw_journal.ingest(message, received_ns)

                channel = str(message.get("channel", ""))
                if channel in {"level2", "l2_data", "market_trades"}:
                    record = ProviderRecord(
                        provider=PROVIDER,
                        stream_id=stream_id,
                        received_at_ns=received_ns,
                        message=message,
                        message_hash=canonical_hash(message),
                        raw_ref="runtime/market_stream/%s.jsonl" % stream_id,
                    )
                    observations.extend(adapt_coinbase_advanced_trade(record, default_symbol=INSTRUMENT))

                if observations:
                    try:
                        frame = build_instrument_state(tuple(observations), cutoff_at_ns=received_ns, depth_bands_bps=(1, 5, 10))
                    except (RuntimeError, ValueError):
                        frame = None
                    if frame is not None and frame.status == "QUALIFIED":
                        if not frames or frame.content_hash() != frames[-1].content_hash():
                            frames.append(frame)
                        _resolve_available(root, tuple(pending_ids), tuple(frames), received_ns)

                        if received_ns >= next_prediction_ns and received_ns <= stop_predicting_ns:
                            at_ns = max(received_ns, frame.known_at_ns)
                            components = run_baseline_models(
                                frame,
                                mode="PROSPECTIVE_SHADOW",
                                prediction_at_ns=at_ns,
                                created_at_ns=at_ns,
                                horizon_ns=horizon_ns,
                                models=models,
                            )
                            for prediction in components:
                                prediction_journal.append(prediction, journaled_at_ns=at_ns)
                                pending_ids.append(prediction.prediction_id)
                            adaptive, receipt = assemble_and_record(
                                root,
                                frame,
                                components,
                                registry,
                                assembly_at_ns=at_ns + 1,
                            )
                            pending_ids.append(adaptive.prediction_id)
                            adaptive_ids.append(adaptive.prediction_id)
                            receipts.append(receipt.receipt_id)
                            while next_prediction_ns <= received_ns:
                                next_prediction_ns += interval_ns

        final_now_ns = time.time_ns() + max_lag_ns + 1
        _resolve_available(root, tuple(pending_ids), tuple(frames), final_now_ns)
        finalized = raw_journal.finalize([50, 90, 99])
        stream_ok, stream_reasons = _stream_quality(finalized["manifest"])

        errors: List[str] = []
        errors.extend(validate_prediction_journal(root))
        errors.extend(validate_outcome_journal(root))
        errors.extend(validate_assembly_lineage(root))
        metrics = _adaptive_metrics(root, adaptive_ids)
        minimum = int(policy["minimum_resolved_adaptive_predictions"])
        reasons: List[str] = []
        if not stream_ok:
            reasons.extend(stream_reasons)
        if int(metrics["resolved_count"]) < minimum:
            reasons.append("resolved_adaptive_predictions_below_minimum")
        if errors:
            reasons.append("lineage_or_integrity_error")

        decision = "SINGLE_SESSION_PROSPECTIVE_MECHANISM_SUPPORTED" if not reasons else "NOT_EARNED"
        body: Dict[str, Any] = {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "preregistration_ref": PREREG_REF,
            "scope": {
                "mode": "PROSPECTIVE_SHADOW",
                "evidence_class": "FORWARD_EVALUABLE",
                "claim_ceiling": prereg["scope"]["claim_ceiling"],
                "capital_effect": "NONE",
                "live_execution": False,
                "registry_scope": "EXPERIMENT_LOCAL_MECHANISM_ONLY",
            },
            "session": {
                "stream_id": stream_id,
                "first_live_message_ns": first_live_message_ns,
                "capture_seconds": capture_seconds,
                "canonical_observation_count": len(observations),
                "qualified_frame_count": len(frames),
                "component_prediction_count": len(adaptive_ids) * len(models),
                "adaptive_prediction_count": len(adaptive_ids),
                "assembly_receipt_count": len(receipts),
                "raw_stream_manifest_hash": finalized["manifest"]["integrity"]["content_hash"],
                "raw_stream_summary": finalized["manifest"]["summary"],
            },
            "adaptive_metrics": metrics,
            "integrity": {
                "errors": errors,
                "prediction_journal_entries": len(PredictionJournal(root).entries()),
                "outcome_journal_entries": len(OutcomeJournal(root).entries()),
                "assembly_journal_entries": len(AssemblyJournal(root).entries()),
                "assembly_receipt_set_hash": canonical_hash({"receipt_ids": receipts}),
            },
            "qualification": {
                "decision": decision,
                "reasons": reasons,
                "broad_historical_qualification": False,
                "walk_forward_qualification": False,
                "contextual_qualification": False,
            },
        }
        report = dict(body)
        report["integrity"] = dict(body["integrity"])
        report["integrity"]["result_content_hash"] = canonical_hash(body)
        return report


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run preregistered Z8 prospective shadow mechanism qualification")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = asyncio.run(run_live(args.root.resolve()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
