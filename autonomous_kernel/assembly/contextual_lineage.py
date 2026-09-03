from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from ..context.store import MarketContextStore
from ..prediction.contracts import Prediction
from ..prediction.journal import PredictionJournal, validate_prediction_journal
from .context_profiles import ModelContextProfileRegistry, ModelContextProfileRegistryError, profile_set_hash, validate_context_profile_registry
from .contracts import AssemblyReceipt
from .contextual import ContextualAssemblyReceipt
from .contextual_journal import ContextualAssemblyJournal, ContextualAssemblyJournalError, validate_contextual_assembly_journal
from .journal import AssemblyJournal, AssemblyJournalError, validate_assembly_journal


def _predictions(root: Path) -> Dict[str, Prediction]:
    return {Prediction.from_wire(entry.get("prediction", {})).prediction_id: Prediction.from_wire(entry.get("prediction", {})) for entry in PredictionJournal(root).entries()}


def _base_receipts(root: Path) -> Dict[str, AssemblyReceipt]:
    output: Dict[str, AssemblyReceipt] = {}
    for entry in AssemblyJournal(root).entries():
        receipt = AssemblyReceipt.from_wire(entry.get("receipt", {})); output[receipt.receipt_id] = receipt
    return output


def validate_contextual_receipt_lineage(root: Path, receipt: ContextualAssemblyReceipt) -> List[str]:
    errors: List[str] = []
    prediction_errors = validate_prediction_journal(root)
    if prediction_errors: return ["prediction journal invalid: " + "; ".join(prediction_errors)]
    try: predictions = _predictions(root)
    except Exception as exc: return ["prediction lineage unreadable: %s" % exc]
    final = predictions.get(receipt.final_prediction_id)
    if final is None: errors.append("contextual final prediction missing from Z3 journal")
    elif final.content_hash() != receipt.final_prediction_hash: errors.append("contextual final prediction hash mismatch")
    base_prediction = predictions.get(receipt.base_prediction_id)
    if base_prediction is None: errors.append("base Z8 prediction missing from Z3 journal")
    elif base_prediction.content_hash() != receipt.base_prediction_hash: errors.append("base Z8 prediction hash mismatch")
    base_errors = validate_assembly_journal(root)
    if base_errors: errors.append("base assembly journal invalid: " + "; ".join(base_errors))
    else:
        try: base_receipt = _base_receipts(root).get(receipt.base_assembly_receipt_id)
        except (AssemblyJournalError, ValueError, TypeError) as exc: base_receipt = None; errors.append("base receipt lineage unreadable: %s" % exc)
        if base_receipt is None: errors.append("base Z8 receipt missing")
        elif base_receipt.content_hash() != receipt.base_assembly_receipt_hash: errors.append("base Z8 receipt hash mismatch")
    try: context = MarketContextStore(root).load(receipt.context_id)
    except Exception as exc: context = None; errors.append("Z9 context lineage unavailable: %s" % exc)
    if context is not None and context.content_hash() != receipt.context_content_hash: errors.append("Z9 context hash mismatch")
    for contributor in receipt.contributors:
        prediction_id = str(contributor.get("component_prediction_id", "")); prediction = predictions.get(prediction_id)
        if prediction is None: errors.append("component prediction missing: %s" % prediction_id)
        elif prediction.content_hash() != contributor.get("component_prediction_hash"): errors.append("component prediction hash mismatch: %s" % prediction_id)

    registry_errors = validate_context_profile_registry(root)
    if registry_errors:
        errors.append("context-profile registry invalid: " + "; ".join(registry_errors))
    else:
        model_refs = [str(item.get("model_ref", "")) for item in receipt.contributors]
        try:
            profiles = ModelContextProfileRegistry(root).active_profiles(model_refs, as_of_ns=receipt.assembly_at_ns)
        except ModelContextProfileRegistryError as exc:
            profiles = (); errors.append("context-profile lineage unavailable: %s" % exc)
        if profiles:
            expected_hashes = {profile.model_ref: profile.content_hash() for profile in profiles}
            if profile_set_hash(profiles) != receipt.context_profile_set_hash:
                errors.append("context-profile set hash mismatch")
            for contributor in receipt.contributors:
                model_ref = str(contributor.get("model_ref", ""))
                if contributor.get("context_profile_hash") != expected_hashes.get(model_ref):
                    errors.append("context-profile hash mismatch for %s" % model_ref)
    return errors


def validate_contextual_assembly_lineage(root: Path) -> List[str]:
    errors = validate_contextual_assembly_journal(root)
    if errors: return errors
    try: records = ContextualAssemblyJournal(root).entries()
    except ContextualAssemblyJournalError as exc: return [str(exc)]
    for index, entry in enumerate(records):
        try: receipt = ContextualAssemblyReceipt.from_wire(entry.get("receipt", {}))
        except Exception as exc: errors.append("sequence %d contextual receipt invalid: %s" % (index, exc)); continue
        errors.extend("sequence %d: %s" % (index, error) for error in validate_contextual_receipt_lineage(root, receipt))
    return errors
