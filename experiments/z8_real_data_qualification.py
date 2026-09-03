from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from autonomous_kernel.assembly import AssemblyJournal, assemble_and_record, validate_assembly_lineage
from autonomous_kernel.evaluation import OutcomeJournal, OutcomePendingError, PredictionOutcome, resolve_prediction
from autonomous_kernel.evaluation.journal import validate_outcome_journal
from autonomous_kernel.models import ModelRegistry, baseline_model_set, run_baseline_models
from autonomous_kernel.observation.materialize import materialize_coinbase_stream
from autonomous_kernel.operations import canonical_hash
from autonomous_kernel.prediction import Prediction, PredictionJournal, create_prediction
from autonomous_kernel.prediction.journal import validate_prediction_journal
from autonomous_kernel.representation import RepresentationFrame, build_instrument_state
from autonomous_kernel.representation.materialize import load_canonical_batch


EXPERIMENT_ID = "EXP-Z8-HIST-REAL-001"
PREREG_REF = "artifacts/evidence/market/exp-z8-hist-real-001-preregistration.json"
STREAM_ID = "COINBASE-BTC-USD-MICROSTREAM-004"
AUDIT_REF = "evidence/audits/EXP-MICROSTREAM-004.json"
MANIFEST_REF = "artifacts/market_data/streams/COINBASE-BTC-USD-MICROSTREAM-004.manifest.json"
COMPRESSED_REF = "artifacts/market_data/streams/COINBASE-BTC-USD-MICROSTREAM-004.jsonl.gz"
EQUAL_MODEL_REF = "EQUAL-WEIGHT-ENSEMBLE@1.0.0"


class RealDataQualificationError(RuntimeError):
    pass


def _load_json(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise RealDataQualificationError("required evidence is missing: %s" % path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RealDataQualificationError("expected JSON object: %s" % path)
    return value


def _verify_preregistered_source(source_root: Path) -> Mapping[str, Any]:
    prereg = _load_json(source_root / PREREG_REF)
    if prereg.get("experiment_id") != EXPERIMENT_ID or prereg.get("status") != "PREREGISTERED_BEFORE_RESULT":
        raise RealDataQualificationError("qualification preregistration identity/status mismatch")
    dataset = prereg.get("dataset")
    if not isinstance(dataset, Mapping) or dataset.get("included_stream_id") != STREAM_ID:
        raise RealDataQualificationError("preregistered dataset identity mismatch")

    audit = _load_json(source_root / AUDIT_REF)
    if audit.get("outcome") != dataset.get("audit_outcome_required"):
        raise RealDataQualificationError("source stream did not earn the preregistered audit outcome")
    if audit.get("decision") != dataset.get("audit_decision_required"):
        raise RealDataQualificationError("source stream did not earn the preregistered audit decision")

    manifest = _load_json(source_root / MANIFEST_REF)
    if manifest.get("stream_id") != STREAM_ID:
        raise RealDataQualificationError("source stream manifest identity mismatch")
    if manifest.get("journal_sha256") != dataset.get("journal_sha256"):
        raise RealDataQualificationError("source journal hash differs from preregistration")
    if manifest.get("compressed_sha256") != dataset.get("compressed_sha256"):
        raise RealDataQualificationError("source compressed hash differs from preregistration")
    if manifest.get("integrity", {}).get("content_hash") != dataset.get("manifest_content_hash"):
        raise RealDataQualificationError("source manifest content hash differs from preregistration")
    summary = manifest.get("summary", {})
    expected = {
        "unique_message_count": int(dataset.get("expected_unique_messages")),
        "last_global_sequence": int(dataset.get("expected_sequence_last")),
        "duplicate_count": int(dataset.get("expected_duplicates")),
    }
    for field, value in expected.items():
        if summary.get(field) != value:
            raise RealDataQualificationError("source manifest %s differs from preregistration" % field)
    if len(summary.get("gaps", [])) != int(dataset.get("expected_gaps")):
        raise RealDataQualificationError("source gap count differs from preregistration")
    if len(summary.get("out_of_order", [])) != int(dataset.get("expected_out_of_order")):
        raise RealDataQualificationError("source out-of-order count differs from preregistration")

    compressed_path = source_root / COMPRESSED_REF
    compressed_hash = hashlib.sha256(compressed_path.read_bytes()).hexdigest()
    if compressed_hash != dataset.get("compressed_sha256"):
        raise RealDataQualificationError("checked-out compressed evidence hash differs from preregistration")
    return prereg


def _prepare_workspace(source_root: Path, work_root: Path) -> None:
    for ref in (MANIFEST_REF, COMPRESSED_REF):
        source = source_root / ref
        destination = work_root / ref
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(source), str(destination))


def _grid(start: int, stop: int, step: int) -> Tuple[int, ...]:
    if step <= 0:
        raise RealDataQualificationError("grid step must be positive")
    if stop < start:
        return ()
    return tuple(range(int(start), int(stop) + 1, int(step)))


def _qualified_frame(observations: Sequence[Any], cutoff: int, depth_bands: Tuple[int, ...]) -> Optional[RepresentationFrame]:
    selected = tuple(item for item in observations if item.known_at_ns <= cutoff)
    if not selected:
        return None
    frame = build_instrument_state(selected, cutoff_at_ns=cutoff, depth_bands_bps=depth_bands)
    return frame if frame.status == "QUALIFIED" else None


def _register_baselines(registry: ModelRegistry, *, occurred_at_ns: int, training_ref: str) -> Tuple[Any, ...]:
    models = baseline_model_set()
    for model in models:
        material = model.definition.content_hash() + "|autonomous_kernel/models/baselines.py|" + training_ref
        registry.register(
            model.definition,
            artifact_hash=hashlib.sha256(material.encode("utf-8")).hexdigest(),
            code_ref="autonomous_kernel/models/baselines.py",
            training_data_refs=(training_ref,),
            occurred_at_ns=occurred_at_ns,
        )
    return models


def _equal_weight_prediction(frame: RepresentationFrame, components: Sequence[Prediction], *, at_ns: int, horizon_ns: int) -> Prediction:
    count = Decimal(len(components))
    expected = sum((Decimal(item.expected_move_bps) for item in components), Decimal("0")) / count
    probability = sum((Decimal(item.probability_positive) for item in components), Decimal("0")) / count
    lows = [Decimal(item.interval_low_bps) for item in components if item.interval_low_bps is not None]
    highs = [Decimal(item.interval_high_bps) for item in components if item.interval_high_bps is not None]
    low = min(lows) if len(lows) == len(components) else None
    high = max(highs) if len(highs) == len(components) else None
    material = {
        "frame_hash": frame.content_hash(),
        "at_ns": int(at_ns),
        "horizon_ns": int(horizon_ns),
        "component_hashes": [item.content_hash() for item in components],
        "policy": "ARITHMETIC_EQUAL_WEIGHT_V1",
    }
    return create_prediction(
        frame,
        mode="HISTORICAL_REPLAY",
        prediction_at_ns=at_ns,
        created_at_ns=at_ns,
        horizon_ns=horizon_ns,
        expected_move_bps=expected,
        probability_positive=probability,
        interval_low_bps=low,
        interval_high_bps=high,
        model_refs=(EQUAL_MODEL_REF,),
        prediction_id="PRED-EQ-%s" % canonical_hash(material)[:32],
    )


def _prediction_map(root: Path) -> Dict[str, Prediction]:
    output: Dict[str, Prediction] = {}
    for entry in PredictionJournal(root).entries():
        prediction = Prediction.from_wire(entry["prediction"])
        output[prediction.prediction_id] = prediction
    return output


def _outcome_map(root: Path) -> Dict[str, PredictionOutcome]:
    output: Dict[str, PredictionOutcome] = {}
    for entry in OutcomeJournal(root).entries():
        outcome = PredictionOutcome.from_wire(entry["outcome"])
        output[outcome.prediction_id] = outcome
    return output


def _resolve_available(root: Path, prediction_ids: Sequence[str], frames: Sequence[RepresentationFrame], *, now_at_ns: int) -> None:
    outcomes = _outcome_map(root)
    journal = OutcomeJournal(root)
    for prediction_id in prediction_ids:
        if prediction_id in outcomes:
            continue
        try:
            outcome = resolve_prediction(root, prediction_id, frames, now_at_ns=now_at_ns)
        except OutcomePendingError:
            continue
        journal.append(outcome)
        outcomes[prediction_id] = outcome


def _sqrt_decimal(value: Decimal) -> Decimal:
    if value < 0:
        raise RealDataQualificationError("cannot sqrt negative metric")
    return value.sqrt()


def _metrics(predictions: Sequence[Prediction], outcomes: Mapping[str, PredictionOutcome]) -> Mapping[str, Any]:
    final = [outcomes[item.prediction_id] for item in predictions if item.prediction_id in outcomes]
    resolved_pairs = [(item, outcomes[item.prediction_id]) for item in predictions if item.prediction_id in outcomes and outcomes[item.prediction_id].status == "RESOLVED"]
    unresolvable = sum(1 for item in final if item.status == "UNRESOLVABLE")
    if not resolved_pairs:
        return {
            "prediction_count": len(predictions),
            "resolved_count": 0,
            "unresolvable_count": unresolvable,
            "pending_count": len(predictions) - len(final),
            "mean_absolute_error_bps": None,
            "root_mean_squared_error_bps": None,
            "mean_forecast_bias_bps": None,
            "directional_accuracy": None,
            "brier_score": None,
            "calibration_gap": None,
            "interval_coverage": None,
        }

    errors: List[Decimal] = []
    absolute: List[Decimal] = []
    squared: List[Decimal] = []
    direction_hits: List[Decimal] = []
    brier_terms: List[Decimal] = []
    probabilities: List[Decimal] = []
    actuals: List[Decimal] = []
    interval_hits: List[Decimal] = []
    for prediction, outcome in resolved_pairs:
        realized = Decimal(str(outcome.realized_return_bps))
        expected = Decimal(prediction.expected_move_bps)
        error = realized - expected
        errors.append(error)
        absolute.append(abs(error))
        squared.append(error * error)
        actual = Decimal(int(outcome.actual_positive))
        probability = Decimal(prediction.probability_positive)
        probabilities.append(probability)
        actuals.append(actual)
        direction_hits.append(Decimal("1") if (expected > 0) == bool(outcome.actual_positive) else Decimal("0"))
        brier_terms.append((probability - actual) ** 2)
        if prediction.interval_low_bps is not None and prediction.interval_high_bps is not None:
            low = Decimal(prediction.interval_low_bps)
            high = Decimal(prediction.interval_high_bps)
            interval_hits.append(Decimal("1") if low <= realized <= high else Decimal("0"))

    count = Decimal(len(resolved_pairs))
    mean = lambda values: sum(values, Decimal("0")) / Decimal(len(values))
    mean_probability = mean(probabilities)
    actual_rate = mean(actuals)
    return {
        "prediction_count": len(predictions),
        "resolved_count": len(resolved_pairs),
        "unresolvable_count": unresolvable,
        "pending_count": len(predictions) - len(final),
        "mean_absolute_error_bps": format(mean(absolute), "f"),
        "root_mean_squared_error_bps": format(_sqrt_decimal(mean(squared)), "f"),
        "mean_forecast_bias_bps": format(mean(errors), "f"),
        "directional_accuracy": format(mean(direction_hits), "f"),
        "brier_score": format(mean(brier_terms), "f"),
        "calibration_gap": format(abs(mean_probability - actual_rate), "f"),
        "interval_coverage": None if not interval_hits else format(mean(interval_hits), "f"),
    }


def _classify_predictions(root: Path) -> Mapping[str, Tuple[Prediction, ...]]:
    predictions = tuple(_prediction_map(root).values())
    adaptive_ids = {
        AssemblyJournal(root).entries()[index]["receipt"]["assembled_prediction"]["prediction_id"]
        for index in range(len(AssemblyJournal(root).entries()))
    }
    groups: Dict[str, List[Prediction]] = {
        "NULL-PRIOR@1.0.0": [],
        "BOOK-IMBALANCE-LINEAR@1.0.0": [],
        "REPORTED-FLOW-LINEAR@1.0.0": [],
        "EQUAL_WEIGHT_ENSEMBLE_V1": [],
        "Z8_ADAPTIVE_ASSEMBLY_V1": [],
    }
    for prediction in predictions:
        if prediction.prediction_id in adaptive_ids:
            groups["Z8_ADAPTIVE_ASSEMBLY_V1"].append(prediction)
        elif prediction.model_refs == (EQUAL_MODEL_REF,):
            groups["EQUAL_WEIGHT_ENSEMBLE_V1"].append(prediction)
        elif len(prediction.model_refs) == 1 and prediction.model_refs[0] in groups:
            groups[prediction.model_refs[0]].append(prediction)
    return {key: tuple(sorted(value, key=lambda item: (item.prediction_at_ns, item.prediction_id))) for key, value in groups.items()}


def _adaptive_weight_change_count(root: Path) -> int:
    count = 0
    for entry in AssemblyJournal(root).entries():
        contributors = entry.get("receipt", {}).get("contributors", [])
        if not contributors:
            continue
        matched = any(item.get("competence_status") == "MATCHED" for item in contributors)
        weights = [Decimal(str(item.get("normalized_weight"))) for item in contributors]
        equal = Decimal("1") / Decimal(len(weights))
        if matched and any(weight != equal for weight in weights):
            count += 1
    return count


def _qualification_decision(prereg: Mapping[str, Any], metrics: Mapping[str, Mapping[str, Any]], weight_changes: int, integrity_errors: Sequence[str]) -> Tuple[str, Tuple[str, ...]]:
    minimum = prereg["minimum_evidence"]
    gates = prereg["performance_gates"]
    adaptive = metrics["Z8_ADAPTIVE_ASSEMBLY_V1"]
    equal = metrics["EQUAL_WEIGHT_ENSEMBLE_V1"]
    reasons: List[str] = []

    if adaptive["resolved_count"] < int(minimum["minimum_resolved_adaptive_predictions"]):
        reasons.append("adaptive_resolved_count_below_minimum")
    if equal["resolved_count"] < int(minimum["minimum_resolved_equal_weight_predictions"]):
        reasons.append("equal_weight_resolved_count_below_minimum")
    if weight_changes < int(minimum["minimum_post_prior_adaptive_weight_changes"]):
        reasons.append("adaptive_weights_never_changed_after_prior_evidence")
    if integrity_errors:
        reasons.append("lineage_or_integrity_error")
    if reasons:
        return "INSUFFICIENT_DATA", tuple(reasons)

    if Decimal(str(adaptive["mean_absolute_error_bps"])) > Decimal(str(equal["mean_absolute_error_bps"])):
        reasons.append("adaptive_mae_worse_than_equal_weight")
    if Decimal(str(adaptive["brier_score"])) > Decimal(str(equal["brier_score"])):
        reasons.append("adaptive_brier_worse_than_equal_weight")
    shortfall = Decimal(str(equal["directional_accuracy"])) - Decimal(str(adaptive["directional_accuracy"]))
    if shortfall > Decimal(str(gates["adaptive_directional_accuracy_max_allowed_shortfall_vs_equal"])):
        reasons.append("adaptive_directional_accuracy_shortfall_exceeded")
    if reasons:
        return "NOT_EARNED", tuple(reasons)
    return str(gates["qualification_if_all_gates_pass"]), ()


def run_qualification(source_root: Path) -> Mapping[str, Any]:
    source_root = source_root.resolve()
    prereg = _verify_preregistered_source(source_root)
    frame_policy = prereg["frame_policy"]
    horizon_ns = int(frame_policy["horizon_ns"])
    step_ns = int(frame_policy["grid_step_ns"])
    depth_bands = tuple(int(value) for value in frame_policy["depth_bands_bps"])

    with tempfile.TemporaryDirectory(prefix="z8-real-data-") as directory:
        root = Path(directory)
        _prepare_workspace(source_root, root)
        canonical_manifest = materialize_coinbase_stream(root, STREAM_ID, default_symbol="BTC-USD")
        _, observations = load_canonical_batch(root, "CAN-" + STREAM_ID)
        relevant = tuple(item for item in observations if item.instrument.canonical_id == "CRYPTO.SPOT.BTC-USD")
        if not relevant:
            raise RealDataQualificationError("canonical stream produced no BTC spot observations")

        first_known = min(item.known_at_ns for item in relevant)
        last_known = max(item.known_at_ns for item in relevant)
        resolution_cutoffs = _grid(first_known, last_known, step_ns)
        prediction_first = first_known + int(frame_policy["first_prediction_offset_from_first_known_ns"])
        prediction_last = last_known - int(frame_policy["latest_prediction_buffer_from_last_known_ns"])
        prediction_cutoffs = _grid(prediction_first, prediction_last, step_ns)
        all_cutoffs = tuple(sorted(set(resolution_cutoffs + prediction_cutoffs)))

        frames_by_cutoff: Dict[int, RepresentationFrame] = {}
        for cutoff in all_cutoffs:
            frame = _qualified_frame(relevant, cutoff, depth_bands)
            if frame is not None:
                frames_by_cutoff[cutoff] = frame
        all_frames = tuple(frames_by_cutoff[key] for key in sorted(frames_by_cutoff))
        if not all_frames:
            raise RealDataQualificationError("real stream produced no qualified Z2 frames")

        registry = ModelRegistry(root)
        models = _register_baselines(
            registry,
            occurred_at_ns=max(0, first_known - 1),
            training_ref="STREAM:%s:%s" % (STREAM_ID, prereg["dataset"]["journal_sha256"]),
        )
        pending_ids: List[str] = []
        prediction_journal = PredictionJournal(root)
        assembly_receipts: List[str] = []

        for cutoff in prediction_cutoffs:
            frame = frames_by_cutoff.get(cutoff)
            if frame is None:
                continue
            _resolve_available(root, tuple(pending_ids), all_frames, now_at_ns=cutoff)

            components = run_baseline_models(
                frame,
                mode="HISTORICAL_REPLAY",
                prediction_at_ns=cutoff,
                created_at_ns=cutoff,
                horizon_ns=horizon_ns,
                models=models,
            )
            for prediction in components:
                prediction_journal.append(prediction, journaled_at_ns=cutoff)
                pending_ids.append(prediction.prediction_id)

            equal = _equal_weight_prediction(frame, components, at_ns=cutoff, horizon_ns=horizon_ns)
            prediction_journal.append(equal, journaled_at_ns=cutoff)
            pending_ids.append(equal.prediction_id)

            adaptive, receipt = assemble_and_record(
                root,
                frame,
                components,
                registry,
                assembly_at_ns=cutoff + 1,
            )
            pending_ids.append(adaptive.prediction_id)
            assembly_receipts.append(receipt.receipt_id)

        final_now = last_known + int(prereg["pipeline"]["z6_max_resolution_lag_ns"]) + 1
        _resolve_available(root, tuple(pending_ids), all_frames, now_at_ns=final_now)

        integrity_errors: List[str] = []
        integrity_errors.extend(validate_prediction_journal(root))
        integrity_errors.extend(validate_outcome_journal(root))
        integrity_errors.extend(validate_assembly_lineage(root))

        groups = _classify_predictions(root)
        outcomes = _outcome_map(root)
        metric_report = {key: _metrics(value, outcomes) for key, value in groups.items()}
        weight_changes = _adaptive_weight_change_count(root)
        decision, reasons = _qualification_decision(prereg, metric_report, weight_changes, integrity_errors)

        report_body: Dict[str, Any] = {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "preregistration_ref": PREREG_REF,
            "scope": {
                "mode": "HISTORICAL_REPLAY",
                "evidence_class": "RESEARCH_ONLY",
                "claim_ceiling": prereg["scope"]["claim_ceiling"],
                "capital_effect": "NONE",
                "live_execution": false,
            },
            "dataset": {
                "stream_id": STREAM_ID,
                "journal_sha256": prereg["dataset"]["journal_sha256"],
                "compressed_sha256": prereg["dataset"]["compressed_sha256"],
                "manifest_content_hash": prereg["dataset"]["manifest_content_hash"],
                "canonical_batch_content_hash": canonical_manifest["integrity"]["content_hash"],
                "canonical_record_count": canonical_manifest["record_count"],
                "first_known_at_ns": first_known,
                "last_known_at_ns": last_known,
                "window_duration_ns": last_known - first_known,
                "excluded_stream_ids": list(prereg["dataset"]["excluded_stream_ids"]),
            },
            "representation": {
                "candidate_cutoff_count": len(all_cutoffs),
                "qualified_frame_count": len(all_frames),
                "prediction_cutoff_count": len(prediction_cutoffs),
                "qualified_prediction_frame_count": sum(1 for cutoff in prediction_cutoffs if cutoff in frames_by_cutoff),
                "builder_version": "instrument-state-v1",
                "depth_bands_bps": list(depth_bands),
            },
            "prediction_policy": {
                "horizon_ns": horizon_ns,
                "grid_step_ns": step_ns,
                "resolution_policy": prereg["pipeline"]["z6_resolution_policy"],
                "max_resolution_lag_ns": prereg["pipeline"]["z6_max_resolution_lag_ns"],
            },
            "metrics": metric_report,
            "adaptive_evidence": {
                "assembly_count": len(assembly_receipts),
                "post_prior_non_equal_weight_assembly_count": weight_changes,
                "assembly_receipt_set_hash": canonical_hash({"receipt_ids": assembly_receipts}),
            },
            "integrity": {
                "errors": integrity_errors,
                "prediction_journal_entries": len(PredictionJournal(root).entries()),
                "outcome_journal_entries": len(OutcomeJournal(root).entries()),
                "assembly_journal_entries": len(AssemblyJournal(root).entries()),
            },
            "qualification": {
                "decision": decision,
                "reasons": list(reasons),
                "broad_historical_qualification": false,
                "walk_forward_qualification": false,
                "prospective_shadow_qualification": false,
                "contextual_qualification": false,
            },
        }
        report = dict(report_body)
        report["integrity"]["result_content_hash"] = canonical_hash(report_body)
        return report


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run preregistered Z8 real-data historical qualification")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = run_qualification(args.root)
    text = json.dumps(result, sort_keys=True, separators=(",", ":"))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
