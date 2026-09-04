from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping, Sequence, Tuple

from ..evaluation.competence import build_competence_profiles
from ..models.registry import ModelRegistry
from ..operations import canonical_hash
from ..prediction.contracts import Prediction
from ..prediction.journal import PredictionJournal, validate_prediction_journal
from ..representation.contracts import RepresentationFrame
from .adaptive import assemble_prediction
from .contracts import AssemblyReceipt, INTERVAL_POLICY_ID, WEIGHT_POLICY_ID
from .journal import AssemblyJournal
from .lineage import validate_assembly_receipt_lineage


class CertifiedAssemblyError(RuntimeError):
    pass


def _journaled_predictions(root: Path) -> Dict[str, Tuple[Prediction, int]]:
    errors = validate_prediction_journal(root)
    if errors:
        raise CertifiedAssemblyError("prediction journal invalid: " + "; ".join(errors))
    output: Dict[str, Tuple[Prediction, int]] = {}
    for entry in PredictionJournal(root).entries():
        prediction = Prediction.from_wire(entry.get("prediction", {}))
        output[prediction.prediction_id] = (prediction, int(entry.get("journaled_at_ns", -1)))
    return output


def _require_durable_components(
    root: Path,
    components: Sequence[Prediction],
    *,
    assembly_at_ns: int,
) -> None:
    durable = _journaled_predictions(root)
    for component in components:
        existing = durable.get(component.prediction_id)
        if existing is None:
            raise CertifiedAssemblyError(
                "component prediction %s must be durably journaled before assembly" % component.prediction_id
            )
        journaled, journaled_at_ns = existing
        if journaled.to_wire() != component.to_wire():
            raise CertifiedAssemblyError(
                "component prediction %s differs from its durable journal record" % component.prediction_id
            )
        if journaled_at_ns > assembly_at_ns:
            raise CertifiedAssemblyError(
                "component prediction %s was journaled after assembly time" % component.prediction_id
            )


def _with_competence_cutoff(receipt: AssemblyReceipt, cutoff_ns: int) -> AssemblyReceipt:
    contributors = tuple(
        dict(contributor, competence_cutoff_ns=int(cutoff_ns))
        for contributor in receipt.contributors
    )
    receipt_material = {
        "assembly_at_ns": receipt.assembly_at_ns,
        "assembled_prediction_hash": receipt.assembled_prediction_content_hash,
        "contributors": [dict(item) for item in contributors],
        "weight_policy_id": WEIGHT_POLICY_ID,
        "interval_policy_id": INTERVAL_POLICY_ID,
    }
    receipt_id = "ASM-%s" % canonical_hash(receipt_material)[:32]
    return AssemblyReceipt(
        receipt_id=receipt_id,
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
        contributors=contributors,
    )


def assemble_and_record(
    root: Path,
    frame: RepresentationFrame,
    component_predictions: Sequence[Prediction],
    registry: ModelRegistry,
    *,
    assembly_at_ns: int,
) -> Tuple[Prediction, AssemblyReceipt]:
    """Canonical Z8 entrypoint grounded only in durable prior evidence.

    Component predictions must already be in the Z3 journal. Competence is
    reconstructed from Z3/Z6 evidence strictly before the earliest current
    component was created, which prevents self-evidence and caller-fabricated
    competence from influencing prospective weights.

    Durability is recoverable by order: the assembled Z3 prediction is appended
    first, then its Z8 receipt. If the process stops between those writes, a retry
    recomputes the same deterministic prediction and appends the missing receipt.
    """

    root = root.resolve()
    assembly_at = int(assembly_at_ns)
    if assembly_at < 0:
        raise CertifiedAssemblyError("assembly_at_ns must be non-negative")
    components = tuple(component_predictions)
    if not components:
        raise CertifiedAssemblyError("component predictions are required")
    _require_durable_components(root, components, assembly_at_ns=assembly_at)

    earliest_created = min(component.created_at_ns for component in components)
    competence_cutoff = max(0, earliest_created - 1)
    if competence_cutoff > assembly_at:
        raise CertifiedAssemblyError("competence cutoff cannot exceed assembly time")
    profiles = build_competence_profiles(root, as_of_ns=competence_cutoff)

    assembled, raw_receipt = assemble_prediction(
        frame,
        components,
        profiles,
        registry,
        assembly_at_ns=assembly_at,
    )
    receipt = _with_competence_cutoff(raw_receipt, competence_cutoff)

    PredictionJournal(root).append(assembled, journaled_at_ns=assembly_at)
    lineage_errors = validate_assembly_receipt_lineage(root, receipt)
    if lineage_errors:
        raise CertifiedAssemblyError("assembly lineage invalid before receipt append: " + "; ".join(lineage_errors))
    AssemblyJournal(root).append(receipt)
    return assembled, receipt
