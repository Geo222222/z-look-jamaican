from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from autonomous_kernel.assembly import (
    AdaptiveAssemblyError,
    AssemblyJournal,
    AssemblyReceipt,
    CertifiedAssemblyError,
    assemble_and_record,
    validate_assembly_journal,
    validate_assembly_lineage,
    validate_assembly_receipt_lineage,
)
from autonomous_kernel.evaluation import OutcomeJournal, resolve_prediction
from autonomous_kernel.models import ModelDefinition, ModelRegistry
from autonomous_kernel.observation import default_instrument_registry
from autonomous_kernel.prediction import PredictionJournal, create_prediction
from autonomous_kernel.representation import RepresentationFrame


PROVIDER = "coinbase_advanced_trade_public_websocket"
INSTRUMENT = default_instrument_registry().resolve(PROVIDER, "BTC-USD")
TARGET = "ZLJ_AGGREGATE_MIDPOINT_RETURN_BPS_V1"
HORIZON = 100


def frame(frame_id: str, known_at_ns: int, midpoint: object) -> RepresentationFrame:
    mid = Decimal(str(midpoint))
    digest = hashlib.sha256(frame_id.encode("utf-8")).hexdigest()
    return RepresentationFrame(
        frame_id=frame_id,
        representation_type="INSTRUMENT_STATE",
        instrument=INSTRUMENT,
        window_start_ns=max(0, known_at_ns - 10),
        cutoff_at_ns=known_at_ns,
        known_at_ns=known_at_ns,
        latest_source_event_at_ns=max(0, known_at_ns - 1),
        status="QUALIFIED",
        builder_version="z8-test-v1",
        parameters={"test": True},
        state={
            "venue_states": {},
            "aggregate": {
                "cross_venue_book_state": "NORMAL",
                "cross_venue_best_bid": format(mid - Decimal("0.01"), "f"),
                "cross_venue_best_ask": format(mid + Decimal("0.01"), "f"),
                "mean_venue_midpoint": format(mid, "f"),
            },
            "input_quality": {"status_counts": {"VALID": 1}, "degraded_reasons": []},
        },
        source_observation_ids=("OBS-%s" % frame_id,),
        source_content_hashes=(digest,),
        source_providers=(PROVIDER,),
        source_venues=("COINBASE",),
    )


def definition(model_id: str, horizons=(HORIZON,)) -> ModelDefinition:
    return ModelDefinition(
        model_id=model_id,
        version="1.0.0",
        family="Z8_TEST",
        lifecycle_state="CANDIDATE",
        required_representation_type="INSTRUMENT_STATE",
        target_metric=TARGET,
        supported_horizons_ns=tuple(int(value) for value in horizons),
        parameters={"test": model_id},
    )


def register_shadow(registry: ModelRegistry, model: ModelDefinition, *, start_ns: int) -> None:
    registry.register(
        model,
        artifact_hash=hashlib.sha256(model.model_ref.encode("utf-8")).hexdigest(),
        code_ref="tests/test_adaptive_assembly.py",
        training_data_refs=("TEST-DATA-%s" % model.model_id,),
        occurred_at_ns=start_ns,
    )
    registry.transition(
        model.model_ref,
        "REPLAY_QUALIFIED",
        evidence_kind="REPLAY_EVALUATION",
        evidence_refs=("REPLAY-%s" % model.model_id,),
        occurred_at_ns=start_ns + 1,
    )
    registry.transition(
        model.model_ref,
        "WALK_FORWARD_QUALIFIED",
        evidence_kind="WALK_FORWARD_EVALUATION",
        evidence_refs=("WF-%s" % model.model_id,),
        occurred_at_ns=start_ns + 2,
    )
    registry.transition(
        model.model_ref,
        "SHADOW",
        evidence_kind="SHADOW_EVALUATION",
        evidence_refs=("SHADOW-%s" % model.model_id,),
        occurred_at_ns=start_ns + 3,
    )


def component(
    source: RepresentationFrame,
    model_ref: str,
    prediction_id: str,
    *,
    prediction_at_ns: int,
    created_at_ns: int,
    expected: object,
    probability: object,
    low: object = "-30",
    high: object = "30",
    mode: str = "PROSPECTIVE_SHADOW",
    horizon_ns: int = HORIZON,
):
    return create_prediction(
        source,
        mode=mode,
        prediction_at_ns=prediction_at_ns,
        created_at_ns=created_at_ns,
        horizon_ns=horizon_ns,
        expected_move_bps=expected,
        probability_positive=probability,
        interval_low_bps=low,
        interval_high_bps=high,
        model_refs=(model_ref,),
        prediction_id=prediction_id,
    )


def journal_prediction(root: Path, prediction, journaled_at_ns: int) -> None:
    PredictionJournal(root).append(prediction, journaled_at_ns=journaled_at_ns)


def add_resolved_case(
    root: Path,
    model_ref: str,
    case_id: str,
    *,
    prediction_at_ns: int,
    expected: object,
    probability: object,
    realized_bps: object,
    mode: str = "PROSPECTIVE_SHADOW",
) -> None:
    source = frame("REP-SRC-%s" % case_id, prediction_at_ns, "100")
    created = prediction_at_ns if mode == "PROSPECTIVE_SHADOW" else prediction_at_ns + 500
    prediction = component(
        source,
        model_ref,
        "PRED-%s" % case_id,
        prediction_at_ns=prediction_at_ns,
        created_at_ns=created,
        expected=expected,
        probability=probability,
        low="-200",
        high="200",
        mode=mode,
    )
    journal_prediction(root, prediction, created + 1)
    realized_midpoint = Decimal("100") * (
        Decimal("1") + Decimal(str(realized_bps)) / Decimal("10000")
    )
    resolution = frame(
        "REP-RES-%s" % case_id,
        prediction.resolves_at_ns + 1,
        realized_midpoint,
    )
    now_at = max(created + 2, prediction.resolves_at_ns + 2)
    outcome = resolve_prediction(root, prediction.prediction_id, (resolution,), now_at_ns=now_at)
    OutcomeJournal(root).append(outcome)


def current_pair(root: Path, model_a: ModelDefinition, model_b: ModelDefinition, *, created_at_ns: int = 10_001):
    source = frame("REP-CURRENT-%d" % created_at_ns, created_at_ns - 1, "100")
    prediction_at = created_at_ns - 1
    first = component(
        source,
        model_a.model_ref,
        "PRED-CURRENT-A-%d" % created_at_ns,
        prediction_at_ns=prediction_at,
        created_at_ns=created_at_ns,
        expected="20",
        probability="0.7",
        low="-5",
        high="30",
    )
    second = component(
        source,
        model_b.model_ref,
        "PRED-CURRENT-B-%d" % created_at_ns,
        prediction_at_ns=prediction_at,
        created_at_ns=created_at_ns,
        expected="-20",
        probability="0.3",
        low="-40",
        high="5",
    )
    journal_prediction(root, first, created_at_ns)
    journal_prediction(root, second, created_at_ns)
    return source, first, second


class AdaptiveAssemblyCertificationTests(unittest.TestCase):
    def test_certified_service_is_input_order_deterministic_idempotent_and_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ModelRegistry(root)
            model_a = definition("MODEL-A")
            model_b = definition("MODEL-B")
            register_shadow(registry, model_a, start_ns=1)
            register_shadow(registry, model_b, start_ns=10)
            source, first, second = current_pair(root, model_a, model_b)

            assembled_1, receipt_1 = assemble_and_record(
                root,
                source,
                (second, first),
                registry,
                assembly_at_ns=10_002,
            )
            assembled_2, receipt_2 = assemble_and_record(
                root,
                source,
                (first, second),
                registry,
                assembly_at_ns=10_002,
            )

            self.assertEqual(assembled_1.to_wire(), assembled_2.to_wire())
            self.assertEqual(receipt_1.to_wire(), receipt_2.to_wire())
            self.assertEqual(3, len(PredictionJournal(root).entries()))
            self.assertEqual(1, len(AssemblyJournal(root).entries()))
            self.assertEqual([], validate_assembly_journal(root))
            self.assertEqual([], validate_assembly_lineage(root))
            self.assertEqual(
                [model_a.model_ref, model_b.model_ref],
                [item["model_ref"] for item in receipt_1.contributors],
            )
            self.assertEqual(
                Decimal("1"),
                sum(Decimal(str(item["normalized_weight"])) for item in receipt_1.contributors),
            )

    def test_certified_service_requires_every_component_to_be_durably_journaled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ModelRegistry(root)
            model_a = definition("MODEL-A")
            model_b = definition("MODEL-B")
            register_shadow(registry, model_a, start_ns=1)
            register_shadow(registry, model_b, start_ns=10)
            source = frame("REP-CURRENT", 10_000, "100")
            first = component(
                source,
                model_a.model_ref,
                "PRED-A",
                prediction_at_ns=10_000,
                created_at_ns=10_001,
                expected="10",
                probability="0.6",
            )
            second = component(
                source,
                model_b.model_ref,
                "PRED-B",
                prediction_at_ns=10_000,
                created_at_ns=10_001,
                expected="-10",
                probability="0.4",
            )
            journal_prediction(root, first, 10_001)
            with self.assertRaisesRegex(CertifiedAssemblyError, "must be durably journaled"):
                assemble_and_record(root, source, (first, second), registry, assembly_at_ns=10_002)

    def test_registry_state_is_evaluated_as_of_assembly_and_quarantine_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ModelRegistry(root)
            model_a = definition("MODEL-A")
            model_b = definition("MODEL-B")
            register_shadow(registry, model_a, start_ns=1)
            register_shadow(registry, model_b, start_ns=10)
            registry.transition(
                model_b.model_ref,
                "QUARANTINED",
                evidence_kind="INTEGRITY_EVIDENCE",
                evidence_refs=("INTEGRITY-B",),
                occurred_at_ns=100,
            )
            source = frame("REP-ASOF", 20, "100")
            first = component(
                source,
                model_a.model_ref,
                "PRED-ASOF-A",
                prediction_at_ns=20,
                created_at_ns=21,
                expected="10",
                probability="0.6",
                horizon_ns=1000,
            )
            second = component(
                source,
                model_b.model_ref,
                "PRED-ASOF-B",
                prediction_at_ns=20,
                created_at_ns=21,
                expected="-10",
                probability="0.4",
                horizon_ns=1000,
            )
            # Definitions must explicitly support the longer horizon for this test.
            # Re-registering the same refs with different definitions is forbidden,
            # so use predictions on the registered 100ns horizon and keep the
            # post-quarantine assembly inside that resolution window instead.
            first = create_prediction(
                source,
                mode="PROSPECTIVE_SHADOW",
                prediction_at_ns=20,
                created_at_ns=21,
                horizon_ns=HORIZON,
                expected_move_bps="10",
                probability_positive="0.6",
                interval_low_bps="-20",
                interval_high_bps="20",
                model_refs=(model_a.model_ref,),
                prediction_id="PRED-ASOF-A",
            )
            second = create_prediction(
                source,
                mode="PROSPECTIVE_SHADOW",
                prediction_at_ns=20,
                created_at_ns=21,
                horizon_ns=HORIZON,
                expected_move_bps="-10",
                probability_positive="0.4",
                interval_low_bps="-20",
                interval_high_bps="20",
                model_refs=(model_b.model_ref,),
                prediction_id="PRED-ASOF-B",
            )
            journal_prediction(root, first, 21)
            journal_prediction(root, second, 21)

            assemble_and_record(root, source, (first, second), registry, assembly_at_ns=50)
            with self.assertRaisesRegex(AdaptiveAssemblyError, "not eligible"):
                assemble_and_record(root, source, (first, second), registry, assembly_at_ns=110)
            self.assertEqual(3, len(PredictionJournal(root).entries()))
            self.assertEqual(1, len(AssemblyJournal(root).entries()))

    def test_registered_model_contract_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ModelRegistry(root)
            model_a = definition("MODEL-A")
            model_b = definition("MODEL-B", horizons=(200,))
            register_shadow(registry, model_a, start_ns=1)
            register_shadow(registry, model_b, start_ns=10)
            source = frame("REP-MISMATCH", 10_000, "100")
            first = component(
                source,
                model_a.model_ref,
                "PRED-MISMATCH-A",
                prediction_at_ns=10_000,
                created_at_ns=10_001,
                expected="10",
                probability="0.6",
            )
            second = component(
                source,
                model_b.model_ref,
                "PRED-MISMATCH-B",
                prediction_at_ns=10_000,
                created_at_ns=10_001,
                expected="-10",
                probability="0.4",
            )
            journal_prediction(root, first, 10_001)
            journal_prediction(root, second, 10_001)
            with self.assertRaisesRegex(AdaptiveAssemblyError, "does not support this prediction horizon"):
                assemble_and_record(root, source, (first, second), registry, assembly_at_ns=10_002)

    def test_research_only_competence_cannot_weight_prospective_assembly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ModelRegistry(root)
            model_a = definition("MODEL-A")
            model_b = definition("MODEL-B")
            register_shadow(registry, model_a, start_ns=1)
            register_shadow(registry, model_b, start_ns=10)

            for index in range(3):
                add_resolved_case(
                    root,
                    model_a.model_ref,
                    "RESEARCH-A-%d" % index,
                    prediction_at_ns=100 + index * 200,
                    expected="20",
                    probability="0.9",
                    realized_bps="20",
                    mode="HISTORICAL_REPLAY",
                )

            source, first, second = current_pair(root, model_a, model_b)
            _, receipt = assemble_and_record(root, source, (first, second), registry, assembly_at_ns=10_002)
            weights = {item["model_ref"]: Decimal(str(item["normalized_weight"])) for item in receipt.contributors}
            statuses = {item["model_ref"]: item["competence_status"] for item in receipt.contributors}
            self.assertEqual(Decimal("0.5"), weights[model_a.model_ref])
            self.assertEqual(Decimal("0.5"), weights[model_b.model_ref])
            self.assertEqual("NO_PRIOR_MATCHED_COMPETENCE", statuses[model_a.model_ref])
            self.assertEqual("NO_PRIOR_MATCHED_COMPETENCE", statuses[model_b.model_ref])

    def test_forward_competence_changes_weights_but_cannot_monopolize_assembly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ModelRegistry(root)
            model_a = definition("MODEL-A")
            model_b = definition("MODEL-B")
            register_shadow(registry, model_a, start_ns=1)
            register_shadow(registry, model_b, start_ns=10)

            for index in range(8):
                base = 100 + index * 250
                add_resolved_case(
                    root,
                    model_a.model_ref,
                    "GOOD-A-%d" % index,
                    prediction_at_ns=base,
                    expected="20",
                    probability="0.9",
                    realized_bps="20",
                )
                add_resolved_case(
                    root,
                    model_b.model_ref,
                    "BAD-B-%d" % index,
                    prediction_at_ns=base + 120,
                    expected="20",
                    probability="0.9",
                    realized_bps="-20",
                )

            source, first, second = current_pair(root, model_a, model_b)
            assembled, receipt = assemble_and_record(root, source, (first, second), registry, assembly_at_ns=10_002)
            contributors = {item["model_ref"]: item for item in receipt.contributors}
            weight_a = Decimal(str(contributors[model_a.model_ref]["normalized_weight"]))
            weight_b = Decimal(str(contributors[model_b.model_ref]["normalized_weight"]))
            self.assertGreater(weight_a, weight_b)
            self.assertLessEqual(weight_a, Decimal("0.75"))
            self.assertGreaterEqual(weight_b, Decimal("0.25"))
            for contributor in receipt.contributors:
                raw = Decimal(str(contributor["raw_weight_score"]))
                self.assertGreaterEqual(raw, Decimal("0.5"))
                self.assertLessEqual(raw, Decimal("1.5"))
                self.assertEqual(10_000, contributor["competence_cutoff_ns"])
                self.assertEqual("MATCHED", contributor["competence_status"])
            self.assertGreater(Decimal(assembled.expected_move_bps), Decimal("0"))

    def test_assembled_interval_is_conservative_component_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ModelRegistry(root)
            model_a = definition("MODEL-A")
            model_b = definition("MODEL-B")
            register_shadow(registry, model_a, start_ns=1)
            register_shadow(registry, model_b, start_ns=10)
            source, first, second = current_pair(root, model_a, model_b)
            assembled, _ = assemble_and_record(root, source, (first, second), registry, assembly_at_ns=10_002)
            self.assertEqual(Decimal("-40"), Decimal(str(assembled.interval_low_bps)))
            self.assertEqual(Decimal("30"), Decimal(str(assembled.interval_high_bps)))

    def test_lineage_rejects_forged_registry_artifact_even_with_valid_receipt_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ModelRegistry(root)
            model_a = definition("MODEL-A")
            model_b = definition("MODEL-B")
            register_shadow(registry, model_a, start_ns=1)
            register_shadow(registry, model_b, start_ns=10)
            source, first, second = current_pair(root, model_a, model_b)
            _, receipt = assemble_and_record(root, source, (first, second), registry, assembly_at_ns=10_002)

            contributors = [dict(item) for item in receipt.contributors]
            contributors[0]["model_artifact_hash"] = "f" * 64
            forged = AssemblyReceipt(
                receipt_id="ASM-FORGED",
                assembly_at_ns=receipt.assembly_at_ns,
                mode=receipt.mode,
                evidence_class=receipt.evidence_class,
                representation_frame_id=receipt.representation_frame_id,
                representation_content_hash=receipt.representation_content_hash,
                prediction_at_ns=receipt.prediction_at_ns,
                horizon_ns=receipt.horizon_ns,
                resolves_at_ns=receipt.resolves_at_ns,
                target_metric=receipt.target_metric,
                assembled_prediction_id=receipt.assembled_prediction_id,
                assembled_prediction_content_hash=receipt.assembled_prediction_content_hash,
                contributors=tuple(contributors),
            )
            errors = validate_assembly_receipt_lineage(root, forged)
            self.assertTrue(any("model artifact mismatch" in error for error in errors))

    def test_assembly_journal_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ModelRegistry(root)
            model_a = definition("MODEL-A")
            model_b = definition("MODEL-B")
            register_shadow(registry, model_a, start_ns=1)
            register_shadow(registry, model_b, start_ns=10)
            source, first, second = current_pair(root, model_a, model_b)
            assemble_and_record(root, source, (first, second), registry, assembly_at_ns=10_002)

            journal_path = root / "memory/assemblies.jsonl"
            value = json.loads(journal_path.read_text(encoding="utf-8").strip())
            value["previous_hash"] = "FORGED"
            journal_path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            self.assertTrue(validate_assembly_journal(root))

    def test_prediction_identity_includes_creation_time(self):
        source = frame("REP-ID", 100, "100")
        first = create_prediction(
            source,
            mode="PROSPECTIVE_SHADOW",
            prediction_at_ns=100,
            created_at_ns=101,
            horizon_ns=HORIZON,
            expected_move_bps="10",
            probability_positive="0.6",
            interval_low_bps="-20",
            interval_high_bps="20",
            model_refs=("MODEL-A@1.0.0",),
        )
        second = create_prediction(
            source,
            mode="PROSPECTIVE_SHADOW",
            prediction_at_ns=100,
            created_at_ns=102,
            horizon_ns=HORIZON,
            expected_move_bps="10",
            probability_positive="0.6",
            interval_low_bps="-20",
            interval_high_bps="20",
            model_refs=("MODEL-A@1.0.0",),
        )
        self.assertNotEqual(first.prediction_id, second.prediction_id)


if __name__ == "__main__":
    unittest.main()
