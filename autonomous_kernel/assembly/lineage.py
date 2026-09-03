from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from ..evaluation.competence import CompetenceProfile, build_competence_profiles
from ..models.registry import ModelRegistry, validate_model_registry
from ..prediction.contracts import Prediction
from ..prediction.journal import PredictionJournal, validate_prediction_journal
from .contracts import AssemblyReceipt
from .journal import AssemblyJournal, AssemblyJournalError


def _prediction_map(root: Path) -> Tuple[Dict[str, Prediction], List[str]]:
    errors = validate_prediction_journal(root)
    if errors:
        return {}, list(errors)
    output: Dict[str, Prediction] = {}
    for entry in PredictionJournal(root).entries():
        try:
            prediction = Prediction.from_wire(entry.get("prediction", {}))
        except (ValueError, TypeError) as exc:
            errors.append("prediction journal contains invalid prediction during assembly lineage check: %s" % exc)
            continue
        output[prediction.prediction_id] = prediction
    return output, list(errors)


def _registry_record_at(registry: ModelRegistry, model_ref: str, as_of_ns: int) -> Optional[Mapping[str, object]]:
    record: Optional[Dict[str, object]] = None
    for event in registry.events():
        if str(event.get("model_ref", "")) != model_ref:
            continue
        occurred = int(event.get("occurred_at_ns", -1))
        if occurred > as_of_ns:
            continue
        payload = event.get("payload", {})
        if not isinstance(payload, Mapping):
            continue
        if event.get("event_type") == "MODEL_REGISTERED":
            record = {
                "state": "CANDIDATE",
                "event_hash": str(event.get("event_hash", "")),
                "definition_hash": str(payload.get("definition_hash", "")),
                "artifact_hash": str(payload.get("artifact_hash", "")),
            }
        elif event.get("event_type") == "MODEL_TRANSITION" and record is not None:
            record["state"] = str(payload.get("to_state", ""))
            record["event_hash"] = str(event.get("event_hash", ""))
    return record


def _matching_profile(
    profiles: Sequence[CompetenceProfile],
    prediction: Prediction,
    component_hash: str,
) -> Optional[CompetenceProfile]:
    matches = [
        profile
        for profile in profiles
        if profile.model_ref == prediction.model_refs[0]
        and profile.instrument_id == prediction.instrument.canonical_id
        and profile.horizon_ns == prediction.horizon_ns
        and profile.target_metric == prediction.target_metric
        and profile.evidence_class == prediction.evidence_class
        and component_hash not in profile.prediction_hashes
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: (item.as_of_ns, item.content_hash()))
    latest_as_of = matches[-1].as_of_ns
    latest = [item for item in matches if item.as_of_ns == latest_as_of]
    hashes = {item.content_hash() for item in latest}
    if len(hashes) != 1:
        return None
    return latest[-1]


def validate_assembly_receipt_lineage(root: Path, receipt: AssemblyReceipt) -> List[str]:
    """Validate one Z8 receipt against the durable Z3/Z5/Z6/Z7 evidence it cites."""

    root = root.resolve()
    predictions, errors = _prediction_map(root)
    registry_errors = validate_model_registry(root, require_state=False)
    errors.extend(registry_errors)
    if errors:
        return errors

    assembled = predictions.get(receipt.assembled_prediction_id)
    if assembled is None:
        errors.append("assembly %s cites an assembled prediction absent from the prediction journal" % receipt.receipt_id)
        return errors
    if assembled.content_hash() != receipt.assembled_prediction_content_hash:
        errors.append("assembly %s assembled prediction hash mismatch" % receipt.receipt_id)
    if assembled.representation_frame_id != receipt.representation_frame_id or assembled.representation_content_hash != receipt.representation_content_hash:
        errors.append("assembly %s representation lineage mismatch" % receipt.receipt_id)
    if assembled.prediction_at_ns != receipt.prediction_at_ns or assembled.horizon_ns != receipt.horizon_ns or assembled.resolves_at_ns != receipt.resolves_at_ns:
        errors.append("assembly %s prediction timing mismatch" % receipt.receipt_id)
    if assembled.target_metric != receipt.target_metric or assembled.mode != receipt.mode or assembled.evidence_class != receipt.evidence_class:
        errors.append("assembly %s prediction contract mismatch" % receipt.receipt_id)

    contributor_refs = tuple(str(item.get("model_ref", "")) for item in receipt.contributors)
    if assembled.model_refs != contributor_refs:
        errors.append("assembly %s contributor model refs do not match assembled prediction" % receipt.receipt_id)

    registry = ModelRegistry(root)
    competence_cache: Dict[int, Tuple[CompetenceProfile, ...]] = {}
    for contributor in receipt.contributors:
        model_ref = str(contributor.get("model_ref", ""))
        component_id = str(contributor.get("component_prediction_id", ""))
        component = predictions.get(component_id)
        if component is None:
            errors.append("assembly %s contributor %s is absent from the prediction journal" % (receipt.receipt_id, model_ref))
            continue
        component_hash = component.content_hash()
        if component_hash != str(contributor.get("component_prediction_hash", "")):
            errors.append("assembly %s contributor %s prediction hash mismatch" % (receipt.receipt_id, model_ref))
        if component.model_refs != (model_ref,):
            errors.append("assembly %s contributor %s model ownership mismatch" % (receipt.receipt_id, model_ref))
        if component.representation_frame_id != receipt.representation_frame_id or component.representation_content_hash != receipt.representation_content_hash:
            errors.append("assembly %s contributor %s representation mismatch" % (receipt.receipt_id, model_ref))
        if component.mode != receipt.mode or component.evidence_class != receipt.evidence_class:
            errors.append("assembly %s contributor %s evidence class mismatch" % (receipt.receipt_id, model_ref))
        if component.prediction_at_ns != receipt.prediction_at_ns or component.horizon_ns != receipt.horizon_ns or component.target_metric != receipt.target_metric:
            errors.append("assembly %s contributor %s prediction contract mismatch" % (receipt.receipt_id, model_ref))

        registry_record = _registry_record_at(registry, model_ref, receipt.assembly_at_ns)
        if registry_record is None:
            errors.append("assembly %s contributor %s was not registered by assembly time" % (receipt.receipt_id, model_ref))
        else:
            if registry_record.get("state") != contributor.get("registry_state"):
                errors.append("assembly %s contributor %s registry state mismatch" % (receipt.receipt_id, model_ref))
            if registry_record.get("event_hash") != contributor.get("registry_event_hash"):
                errors.append("assembly %s contributor %s registry event mismatch" % (receipt.receipt_id, model_ref))
            if registry_record.get("definition_hash") != contributor.get("model_definition_hash"):
                errors.append("assembly %s contributor %s model definition mismatch" % (receipt.receipt_id, model_ref))
            if registry_record.get("artifact_hash") != contributor.get("model_artifact_hash"):
                errors.append("assembly %s contributor %s model artifact mismatch" % (receipt.receipt_id, model_ref))

        cutoff_value = contributor.get("competence_cutoff_ns")
        if not isinstance(cutoff_value, int) or cutoff_value < 0 or cutoff_value > receipt.assembly_at_ns:
            errors.append("assembly %s contributor %s competence cutoff invalid" % (receipt.receipt_id, model_ref))
            continue
        cutoff = int(cutoff_value)
        if cutoff not in competence_cache:
            competence_cache[cutoff] = build_competence_profiles(root, as_of_ns=cutoff)
        profile = _matching_profile(competence_cache[cutoff], component, component_hash)
        cited_hash = contributor.get("competence_profile_hash")
        if profile is None:
            if cited_hash is not None or contributor.get("competence_status") != "NO_PRIOR_MATCHED_COMPETENCE":
                errors.append("assembly %s contributor %s falsely cites competence evidence" % (receipt.receipt_id, model_ref))
        else:
            if cited_hash != profile.content_hash() or contributor.get("competence_status") != "MATCHED":
                errors.append("assembly %s contributor %s competence profile mismatch" % (receipt.receipt_id, model_ref))
            if contributor.get("competence_as_of_ns") != profile.as_of_ns:
                errors.append("assembly %s contributor %s competence as-of mismatch" % (receipt.receipt_id, model_ref))

    return errors


def validate_assembly_lineage(root: Path) -> List[str]:
    """Validate every durable assembly receipt against its cited evidence graph."""

    root = root.resolve()
    try:
        records = AssemblyJournal(root).entries()
    except AssemblyJournalError as exc:
        return [str(exc)]
    errors: List[str] = []
    for index, record in enumerate(records):
        try:
            receipt = AssemblyReceipt.from_wire(record.get("receipt", {}))
        except (ValueError, TypeError) as exc:
            errors.append("assembly sequence %d receipt invalid during lineage validation: %s" % (index, exc))
            continue
        for error in validate_assembly_receipt_lineage(root, receipt):
            errors.append("assembly sequence %d: %s" % (index, error))
    return errors
