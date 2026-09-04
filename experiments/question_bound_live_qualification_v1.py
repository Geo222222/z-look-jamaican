from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import websockets

from autonomous_kernel.context import build_market_context
from autonomous_kernel.evaluation import QuestionOutcomeJournal, validate_question_outcome_journal
from autonomous_kernel.evaluation.liquidity_resolver import (
    LIQUIDITY_RESOLVER_IMPLEMENTATION_REF,
    resolve_liquidity_question,
)
from autonomous_kernel.evaluation.question_resolvers import (
    MIDPOINT_RESOLVER_IMPLEMENTATION_REF,
    resolve_midpoint_question,
)
from autonomous_kernel.experience import (
    EconomicInstrumentGraph,
    EconomicInstrumentNode,
    ExperienceTimescale,
    InstrumentRole,
    TimescaleSpec,
    build_market_experience,
)
from autonomous_kernel.market_observer import REQUIRED_CHANNELS, SUPPORTED_ENDPOINT
from autonomous_kernel.microstream import StreamJournal
from autonomous_kernel.observation import ProviderRecord, adapt_coinbase_advanced_trade
from autonomous_kernel.operations import canonical_hash
from autonomous_kernel.prediction import (
    PredictionArtifactRef,
    QuestionPredictionJournal,
    build_question_bound_prediction,
    validate_question_prediction_journal,
)
from autonomous_kernel.questions import (
    build_learning_journal_commitment,
    default_question_registry_v1,
    question_catalog_v1,
)
from autonomous_kernel.questions.readiness import build_resolver_ready_registry
from autonomous_kernel.representation import RepresentationFrame, build_instrument_state


EXPERIMENT_ID = "EXP-QUESTION-LIVE-001"
PREREG_REF = "artifacts/evidence/market/exp-question-live-001-preregistration.json"
PROVIDER = "coinbase_advanced_trade_public_websocket"
INSTRUMENT = "BTC-USD"
ECONOMIC_ROOT = "ASSET.BTC"
MODEL_REF = "MECHANISM_PROBE_V1:NO_MODEL_QUALIFICATION"


class QualificationError(RuntimeError):
    pass


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise QualificationError("expected JSON object: %s" % path)
    return value


def _question_map() -> Mapping[str, Any]:
    return {question.question_id: question for question in question_catalog_v1()}


def _copy_raw_bundle(root: Path, stream_id: str, artifact_dir: Path) -> Mapping[str, str]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    refs = {
        "journal": root / "runtime/market_stream" / (stream_id + ".jsonl"),
        "compressed": root / "artifacts/market_data/streams" / (stream_id + ".jsonl.gz"),
        "manifest": root / "artifacts/market_data/streams" / (stream_id + ".manifest.json"),
        "observation": root / "artifacts/market_data/observations" / ("OBS-" + stream_id + ".json"),
    }
    copied: Dict[str, str] = {}
    for name, source in refs.items():
        if not source.is_file():
            raise QualificationError("required raw evidence missing after finalize: %s" % source)
        destination = artifact_dir / source.name
        shutil.copyfile(str(source), str(destination))
        copied[name] = destination.name
    return copied


def _graph(frame: RepresentationFrame, known_at_ns: int) -> EconomicInstrumentGraph:
    return EconomicInstrumentGraph(
        graph_id="GRAPH-LIVE-BTC-SPOT-V1",
        graph_version="1.0.0",
        effective_at_ns=known_at_ns,
        known_at_ns=known_at_ns,
        nodes=(
            EconomicInstrumentNode(
                node_id="NODE-BTC-USD-SPOT",
                instrument=frame.instrument,
                role=InstrumentRole.SPOT,
                economic_root_id=ECONOMIC_ROOT,
                quote_family_id="QUOTE.USD",
            ),
        ),
        relationships=(),
    )


def _build_live_experience(
    frames: Sequence[RepresentationFrame],
    current: RepresentationFrame,
    graph: EconomicInstrumentGraph,
):
    history = tuple(frame for frame in frames[-12:] if frame.cutoff_at_ns <= current.cutoff_at_ns)
    if len(history) < 3:
        raise QualificationError("insufficient qualified representation history for context")
    context = build_market_context(
        history,
        cutoff_at_ns=current.cutoff_at_ns,
        minimum_core_instruments=1,
        minimum_history_points=2,
        maximum_member_age_ns=10_000_000_000,
        liquidity_depth_band_bps=10,
    )
    if context.status != "QUALIFIED":
        raise QualificationError("live context is not qualified: %s" % context.status)
    lookback = current.cutoff_at_ns - current.window_start_ns
    if lookback <= 0:
        raise QualificationError("live representation has no causal lookback")
    experience = build_market_experience(
        economic_root_id=ECONOMIC_ROOT,
        graph=graph,
        context=context,
        timescale_frames={ExperienceTimescale.MICRO: (current,)},
        timescale_specs=(TimescaleSpec(ExperienceTimescale.MICRO, lookback),),
        cutoff_at_ns=current.cutoff_at_ns,
    )
    if experience.status != "QUALIFIED":
        raise QualificationError("live Market Experience is not qualified: %s" % experience.status)
    return experience


def _artifact_ref(experience) -> PredictionArtifactRef:
    micro = [view for view in experience.views if view.timescale is ExperienceTimescale.MICRO]
    if len(micro) != 1:
        raise QualificationError("live experience does not contain exactly one MICRO view")
    return PredictionArtifactRef(
        artifact_type="MARKET_EXPERIENCE",
        artifact_id=experience.experience_id,
        content_hash=experience.content_hash(),
        known_at_ns=experience.known_at_ns,
        status=experience.status,
        timescales=(ExperienceTimescale.MICRO,),
        feature_families=("SPOT_MICROSTRUCTURE",),
        subject_ids=(ECONOMIC_ROOT,),
    )


def _prediction_summary(prediction) -> Mapping[str, Any]:
    return {
        "prediction_id": prediction.prediction_id,
        "question_ref": prediction.question_ref,
        "subject_id": prediction.subject_id,
        "cutoff_at_ns": prediction.cutoff_at_ns,
        "resolves_at_ns": prediction.resolves_at_ns,
        "answer": dict(prediction.answer),
        "content_hash": prediction.content_hash(),
    }


def _outcome_summary(outcome) -> Mapping[str, Any]:
    return {
        "outcome_id": outcome.outcome_id,
        "prediction_id": outcome.prediction_id,
        "question_ref": outcome.question_ref,
        "subject_id": outcome.subject_id,
        "status": outcome.status,
        "realized_answer": outcome.realized_answer,
        "decided_at_ns": outcome.decided_at_ns,
        "evidence": [item.to_wire() for item in outcome.resolution_evidence],
        "content_hash": outcome.content_hash(),
    }


async def run_live(source_root: Path, artifact_dir: Path) -> Mapping[str, Any]:
    prereg = _load_json(source_root / PREREG_REF)
    if prereg.get("experiment_id") != EXPERIMENT_ID or prereg.get("status") != "PREREGISTERED_BEFORE_LIVE_DATA":
        raise QualificationError("preregistration identity/status mismatch")
    if prereg.get("base_head") != "794629c0a5c6820eeaab81b2d249a641c544d3c1":
        raise QualificationError("preregistration base-head mismatch")
    policy = prereg.get("session_policy")
    if not isinstance(policy, Mapping):
        raise QualificationError("session policy is missing")
    capture_seconds = int(policy["capture_seconds"])
    warmup_seconds = int(policy["warmup_seconds"])
    frame_interval = float(policy["frame_interval_seconds"])
    anchor_offsets = tuple(int(item) for item in policy["prediction_anchor_offsets_seconds"])
    depth_bands = tuple(int(item) for item in policy["depth_bands_bps"])
    minimum_resolved = int(policy["minimum_resolved_predictions"])

    artifact_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="zlj-question-live-") as directory:
        root = Path(directory)
        start_ns = time.time_ns()
        stream_id = "COINBASE-BTC-USD-QUESTION-LIVE-%d" % start_ns
        raw_journal = StreamJournal(root, stream_id)
        prediction_journal = QuestionPredictionJournal(root)
        outcome_journal = QuestionOutcomeJournal(root)

        questions = _question_map()
        direction = questions["ECONOMIC_ROOT_DIRECTION_10S"]
        liquidity = questions["ECONOMIC_ROOT_LIQUIDITY_DETERIORATION_30S"]
        base_registry = default_question_registry_v1(
            registered_at_ns=start_ns,
            effective_at_ns=start_ns,
        )
        registry = build_resolver_ready_registry(
            base_registry,
            version="LIVE-001",
            known_at_ns=start_ns + 1,
            effective_at_ns=start_ns + 1,
            resolver_implementations={
                direction.question_ref: MIDPOINT_RESOLVER_IMPLEMENTATION_REF,
                liquidity.question_ref: LIQUIDITY_RESOLVER_IMPLEMENTATION_REF,
            },
        )

        observations: List[Any] = []
        frames: List[RepresentationFrame] = []
        anchors: Dict[str, Mapping[str, Any]] = {}
        predictions: List[Any] = []
        first_live_message_ns: Optional[int] = None
        reader_error: List[str] = []
        done = asyncio.Event()
        graph_holder: List[EconomicInstrumentGraph] = []
        anchor_targets = [start_ns + offset * 1_000_000_000 for offset in anchor_offsets]
        anchor_index = 0

        async def reader() -> None:
            nonlocal first_live_message_ns
            deadline = time.monotonic() + capture_seconds
            try:
                async with websockets.connect(
                    SUPPORTED_ENDPOINT,
                    open_timeout=20,
                    max_size=8_000_000,
                    ping_interval=20,
                    ping_timeout=20,
                ) as socket:
                    for channel in REQUIRED_CHANNELS:
                        await socket.send(json.dumps({
                            "type": "subscribe",
                            "product_ids": [INSTRUMENT],
                            "channel": channel,
                        }, separators=(",", ":")))
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
                            raise QualificationError("provider error: %s" % message)
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
            except Exception as exc:
                reader_error.append("%s: %s" % (type(exc).__name__, exc))
            finally:
                done.set()

        async def worker() -> None:
            nonlocal anchor_index
            while not done.is_set():
                await asyncio.sleep(frame_interval)
                if not observations:
                    continue
                cutoff_ns = time.time_ns()
                try:
                    frame = await asyncio.to_thread(
                        build_instrument_state,
                        tuple(observations),
                        cutoff_at_ns=cutoff_ns,
                        depth_bands_bps=depth_bands,
                    )
                except (RuntimeError, ValueError):
                    continue
                if frame.status != "QUALIFIED":
                    continue
                frames.append(frame)
                if not graph_holder:
                    graph_holder.append(_graph(frame, start_ns))
                while anchor_index < len(anchor_targets) and cutoff_ns >= anchor_targets[anchor_index]:
                    if cutoff_ns < start_ns + warmup_seconds * 1_000_000_000 or len(frames) < 3:
                        break
                    try:
                        experience = _build_live_experience(frames, frame, graph_holder[0])
                    except (QualificationError, ValueError):
                        break
                    artifact = _artifact_ref(experience)
                    created_at = max(cutoff_ns, experience.known_at_ns)
                    direction_prediction = build_question_bound_prediction(
                        registry=registry,
                        question=direction,
                        subject_id=ECONOMIC_ROOT,
                        mode="PROSPECTIVE_SHADOW",
                        evidence_class="FORWARD_EVALUABLE",
                        cutoff_at_ns=frame.cutoff_at_ns,
                        created_at_ns=created_at,
                        answer={"value": 1, "probability_1": "0.5"},
                        model_refs=(MODEL_REF,),
                        artifact_refs=(artifact,),
                    )
                    liquidity_prediction = build_question_bound_prediction(
                        registry=registry,
                        question=liquidity,
                        subject_id=ECONOMIC_ROOT,
                        mode="PROSPECTIVE_SHADOW",
                        evidence_class="FORWARD_EVALUABLE",
                        cutoff_at_ns=frame.cutoff_at_ns,
                        created_at_ns=created_at,
                        answer={"value": 0, "probability_1": "0.5"},
                        model_refs=(MODEL_REF,),
                        artifact_refs=(artifact,),
                    )
                    for prediction in (direction_prediction, liquidity_prediction):
                        prediction_journal.append(prediction, journaled_at_ns=created_at + 1)
                        predictions.append(prediction)
                    anchor_key = "anchor_%d" % anchor_index
                    anchors[anchor_key] = {
                        "target_at_ns": anchor_targets[anchor_index],
                        "actual_cutoff_at_ns": frame.cutoff_at_ns,
                        "experience": experience,
                        "baseline_frame": frame,
                        "prediction_ids": (
                            direction_prediction.prediction_id,
                            liquidity_prediction.prediction_id,
                        ),
                    }
                    anchor_index += 1

        await asyncio.gather(reader(), worker())
        if reader_error:
            raise QualificationError(reader_error[0])

        if len(anchors) != len(anchor_targets):
            raise QualificationError("not all preregistered prediction anchors were materialized")
        if not frames:
            raise QualificationError("no qualified representation frames were captured")

        now_ns = time.time_ns() + 10_000_000_000
        outcomes = []
        for anchor in anchors.values():
            experience = anchor["experience"]
            baseline = anchor["baseline_frame"]
            direction_id, liquidity_id = anchor["prediction_ids"]
            direction_outcome = resolve_midpoint_question(
                root,
                direction_id,
                baseline_experience=experience,
                baseline_frames=(baseline,),
                forward_frames=tuple(frames),
                now_at_ns=now_ns,
            )
            liquidity_outcome = resolve_liquidity_question(
                root,
                liquidity_id,
                baseline_experience=experience,
                baseline_frames=(baseline,),
                forward_frames=tuple(frames),
                now_at_ns=now_ns,
            )
            for outcome in (direction_outcome, liquidity_outcome):
                outcome_journal.append(outcome)
                outcomes.append(outcome)

        finalized = raw_journal.finalize([50, 90, 99])
        raw_refs = _copy_raw_bundle(root, stream_id, artifact_dir / "raw")
        prediction_errors = validate_question_prediction_journal(root)
        outcome_errors = validate_question_outcome_journal(root)
        prediction_records = prediction_journal.entries()
        outcome_records = outcome_journal.entries()
        prediction_commitment = build_learning_journal_commitment(
            journal_name="ZLJ.QUESTION_PREDICTIONS.v1",
            records=prediction_records,
        )
        outcome_commitment = build_learning_journal_commitment(
            journal_name="ZLJ.QUESTION_OUTCOMES.v1",
            records=outcome_records,
        )

        summary = finalized["manifest"]["summary"]
        reasons: List[str] = []
        if finalized["observation"]["quality"]["status"] != "VALID":
            reasons.append("market_data_quality_not_valid")
        if summary.get("gaps"):
            reasons.append("sequence_gap")
        if int(summary.get("duplicate_count", 0)) != 0:
            reasons.append("duplicate_message")
        if summary.get("out_of_order"):
            reasons.append("out_of_order")
        if prediction_errors:
            reasons.append("prediction_journal_integrity_error")
        if outcome_errors:
            reasons.append("outcome_journal_integrity_error")
        resolved = [outcome for outcome in outcomes if outcome.status == "RESOLVED"]
        if len(resolved) < minimum_resolved:
            reasons.append("resolved_predictions_below_preregistered_minimum")
        late = []
        for record in prediction_records:
            prediction = record.get("prediction", {})
            timing = prediction.get("timing", {}) if isinstance(prediction, Mapping) else {}
            if int(record.get("journaled_at_ns", -1)) >= int(timing.get("resolves_at_ns", -1)):
                late.append(str(prediction.get("prediction_id", "UNKNOWN")))
        if late:
            reasons.append("prospective_prediction_journaled_too_late")

        decision = "SINGLE_SESSION_QUESTION_BOUND_LEARNING_MECHANISM_SUPPORTED" if not reasons else "NOT_EARNED"
        body: Dict[str, Any] = {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "preregistration_ref": PREREG_REF,
            "scope": prereg["scope"],
            "session": {
                "stream_id": stream_id,
                "first_live_message_ns": first_live_message_ns,
                "capture_seconds": capture_seconds,
                "canonical_observation_count": len(observations),
                "qualified_frame_count": len(frames),
                "prediction_anchor_count": len(anchors),
                "prediction_count": len(predictions),
                "outcome_count": len(outcomes),
                "resolved_outcome_count": len(resolved),
                "raw_stream_manifest_hash": finalized["manifest"]["integrity"]["content_hash"],
                "raw_stream_summary": summary,
                "raw_bundle_files": raw_refs,
            },
            "predictions": [_prediction_summary(item) for item in predictions],
            "outcomes": [_outcome_summary(item) for item in outcomes],
            "learning_commitments": {
                "predictions": prediction_commitment.body(),
                "outcomes": outcome_commitment.body(),
            },
            "integrity": {
                "prediction_journal_errors": prediction_errors,
                "outcome_journal_errors": outcome_errors,
                "prediction_journal_entry_count": len(prediction_records),
                "outcome_journal_entry_count": len(outcome_records),
                "late_prediction_ids": late,
                "registry_hash": registry.content_hash(),
            },
            "qualification": {
                "decision": decision,
                "reasons": reasons,
                "model_performance_claim": False,
                "model_qualification": False,
                "capital_effect": "NONE",
                "live_execution": False,
            },
        }
        result = dict(body)
        result["integrity"] = dict(body["integrity"])
        result["integrity"]["result_content_hash"] = canonical_hash(body)
        result_path = artifact_dir / "result.json"
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (artifact_dir / "preregistration.json").write_text(
            json.dumps(prereg, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if decision != "SINGLE_SESSION_QUESTION_BOUND_LEARNING_MECHANISM_SUPPORTED":
            raise QualificationError("qualification not earned: %s" % ", ".join(reasons))
        return result


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run preregistered question-bound live market qualification")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = asyncio.run(run_live(args.root.resolve(), args.artifact_dir.resolve()))
    print(json.dumps({
        "experiment_id": result["experiment_id"],
        "decision": result["qualification"]["decision"],
        "prediction_count": result["session"]["prediction_count"],
        "resolved_outcome_count": result["session"]["resolved_outcome_count"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
