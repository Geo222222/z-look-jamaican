from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.models import (
    ModelDefinition,
    ModelQualificationError,
    ModelRegistry,
    QualificationEvidenceStore,
    apply_transition_proposal,
    build_evaluation_receipt,
    build_transition_proposal,
)


QUESTION = "ECONOMIC_ROOT_DIRECTION_10S@1.0.0"


def _definition():
    return ModelDefinition(
        model_id="BRIDGE-MODEL",
        version="1.0.0",
        family="TEST",
        lifecycle_state="CANDIDATE",
        required_representation_type="INSTRUMENT_STATE",
        target_metric="AGGREGATE_MIDPOINT_DIRECTION_10S_V1",
        supported_horizons_ns=(10_000_000_000,),
        parameters={},
    )


def _register(root):
    registry = ModelRegistry(root)
    item = _definition()
    record = registry.register(
        item,
        artifact_hash="a" * 64,
        code_ref="git:abc:autonomous_kernel/models/bridge.py",
        training_data_refs=("dataset:direction:v1",),
        occurred_at_ns=100,
    )
    return registry, record


def _receipt(record, target, *, receipt_id=None, verdict="SUPPORTED", at=200):
    kwargs = {}
    if target == "WALK_FORWARD_QUALIFIED":
        kwargs = {
            "dataset_hash": "c" * 64,
            "walk_forward_hash": "d" * 64,
            "experiment_hash": "e" * 64,
        }
    return build_evaluation_receipt(
        receipt_id=receipt_id or ("REC-%s" % target),
        model_ref=record["model_ref"],
        model_definition_hash=record["definition_hash"],
        model_artifact_hash=record["artifact_hash"],
        target_state=target,
        question_refs=(QUESTION,),
        evaluation_artifact_refs=("evidence:%s" % target,),
        evaluation_artifact_hashes=("b" * 64,),
        sample_count=250,
        metrics={"score": 0.8},
        thresholds={"minimum_score": 0.7},
        verdict=verdict,
        evaluated_at_ns=at,
        **kwargs
    )


class ModelQualificationBridgeTests(unittest.TestCase):
    def _persist_and_apply(self, root, registry, record, target, at):
        receipt = _receipt(record, target, at=at)
        proposal = build_transition_proposal(registry, receipt, proposed_at_ns=at + 1)
        store = QualificationEvidenceStore(root)
        store.persist_receipt(receipt)
        store.persist_proposal(proposal)
        result = apply_transition_proposal(
            root,
            proposal_hash=proposal["integrity"]["content_hash"],
            occurred_at_ns=at + 2,
        )
        return result, receipt, proposal

    def test_full_qualification_path_requires_persisted_exact_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry, record = _register(root)
            stages = (
                "REPLAY_QUALIFIED",
                "WALK_FORWARD_QUALIFIED",
                "SHADOW",
                "QUALIFIED",
            )
            for index, target in enumerate(stages):
                record, receipt, proposal = self._persist_and_apply(root, registry, record, target, 200 + index * 10)
                self.assertEqual(record["state"], target)
                self.assertEqual(record["last_evidence_refs"], ["qualification-receipt:%s" % receipt["integrity"]["content_hash"]])
                self.assertFalse(proposal["authority"]["model_self_certification"])
            self.assertTrue(registry.eligible(record["model_ref"], "QUALIFIED_SERVING"))

    def test_unsupported_evaluation_cannot_propose_promotion(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry, record = _register(Path(temporary))
            receipt = _receipt(record, "REPLAY_QUALIFIED", verdict="NOT_SUPPORTED")
            with self.assertRaisesRegex(ModelQualificationError, "only SUPPORTED"):
                build_transition_proposal(registry, receipt, proposed_at_ns=201)
            self.assertEqual(registry.state()["models"][record["model_ref"]]["state"], "CANDIDATE")

    def test_illegal_skip_is_rejected_before_proposal_is_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry, record = _register(Path(temporary))
            receipt = _receipt(record, "QUALIFIED")
            with self.assertRaisesRegex(ModelQualificationError, "illegal lifecycle edge"):
                build_transition_proposal(registry, receipt, proposed_at_ns=201)

    def test_walk_forward_receipt_requires_dataset_plan_and_experiment(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, record = _register(Path(temporary))
            with self.assertRaisesRegex(ModelQualificationError, "requires dataset"):
                build_evaluation_receipt(
                    receipt_id="REC-WF-BAD",
                    model_ref=record["model_ref"],
                    model_definition_hash=record["definition_hash"],
                    model_artifact_hash=record["artifact_hash"],
                    target_state="WALK_FORWARD_QUALIFIED",
                    question_refs=(QUESTION,),
                    evaluation_artifact_refs=("evidence:wf",),
                    evaluation_artifact_hashes=("b" * 64,),
                    sample_count=100,
                    metrics={},
                    thresholds={},
                    verdict="SUPPORTED",
                    evaluated_at_ns=200,
                )

    def test_model_identity_drift_blocks_proposal(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry, record = _register(Path(temporary))
            receipt = dict(_receipt(record, "REPLAY_QUALIFIED"))
            receipt["model_artifact_hash"] = "f" * 64
            body = {key: value for key, value in receipt.items() if key != "integrity"}
            from autonomous_kernel.operations import canonical_hash
            receipt["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
            with self.assertRaisesRegex(ModelQualificationError, "artifact identity mismatch"):
                build_transition_proposal(registry, receipt, proposed_at_ns=201)

    def test_apply_requires_both_persisted_receipt_and_proposal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry, record = _register(root)
            receipt = _receipt(record, "REPLAY_QUALIFIED")
            proposal = build_transition_proposal(registry, receipt, proposed_at_ns=201)
            store = QualificationEvidenceStore(root)
            store.persist_proposal(proposal)
            with self.assertRaisesRegex(ModelQualificationError, "evaluation receipt not found"):
                apply_transition_proposal(root, proposal_hash=proposal["integrity"]["content_hash"], occurred_at_ns=202)
            self.assertEqual(registry.state()["models"][record["model_ref"]]["state"], "CANDIDATE")

    def test_stale_proposal_cannot_override_new_registry_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry, record = _register(root)
            receipt = _receipt(record, "REPLAY_QUALIFIED")
            proposal = build_transition_proposal(registry, receipt, proposed_at_ns=201)
            store = QualificationEvidenceStore(root)
            store.persist_receipt(receipt)
            store.persist_proposal(proposal)
            registry.transition(
                record["model_ref"],
                "QUARANTINED",
                evidence_kind="INTEGRITY_EVIDENCE",
                evidence_refs=("integrity:external",),
                occurred_at_ns=202,
            )
            with self.assertRaisesRegex(ModelQualificationError, "lifecycle moved"):
                apply_transition_proposal(root, proposal_hash=proposal["integrity"]["content_hash"], occurred_at_ns=203)

    def test_persisted_evidence_is_tamper_evident(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry, record = _register(root)
            receipt = _receipt(record, "REPLAY_QUALIFIED")
            proposal = build_transition_proposal(registry, receipt, proposed_at_ns=201)
            store = QualificationEvidenceStore(root)
            store.persist_receipt(receipt)
            store.persist_proposal(proposal)
            path = store.receipt_path(receipt["integrity"]["content_hash"])
            value = json.loads(path.read_text(encoding="utf-8"))
            value["verdict"] = "BLOCKED"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ModelQualificationError, "content hash mismatch"):
                apply_transition_proposal(root, proposal_hash=proposal["integrity"]["content_hash"], occurred_at_ns=202)


if __name__ == "__main__":
    unittest.main()
