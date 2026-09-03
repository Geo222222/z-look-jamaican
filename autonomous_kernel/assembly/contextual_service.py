from __future__ import annotations

from pathlib import Path
from typing import Sequence, Tuple

from ..context.contracts import MarketContextFrame
from ..context.store import MarketContextStore
from ..models.registry import ModelRegistry
from ..prediction.contracts import Prediction
from ..prediction.journal import PredictionJournal
from ..representation.contracts import RepresentationFrame
from .context_profiles import ModelContextProfileRegistry, ModelContextProfileRegistryError
from .contextual import ContextualAssemblyError, ContextualAssemblyReceipt, contextualize_prediction
from .contextual_journal import ContextualAssemblyJournal
from .contextual_lineage import validate_contextual_receipt_lineage
from .service import CertifiedAssemblyError, assemble_and_record


class CertifiedContextualAssemblyError(RuntimeError):
    pass


def contextual_assemble_and_record(root: Path, frame: RepresentationFrame, component_predictions: Sequence[Prediction], registry: ModelRegistry, context: MarketContextFrame, *, assembly_at_ns: int) -> Tuple[Prediction, ContextualAssemblyReceipt]:
    """Canonical durable Z8+Z9 entrypoint.

    Context profiles are resolved from governed durable state. A profile must
    have been activated strictly before the assembly timestamp so a later event
    cannot retroactively reinterpret an assembly made at the same nanosecond.
    """
    root = root.resolve(); assembly_at = int(assembly_at_ns)
    if assembly_at <= 0:
        raise CertifiedContextualAssemblyError("contextual assembly time must be positive so policy authority can be strictly prior")
    try:
        durable_context = MarketContextStore(root).load(context.context_id)
    except Exception as exc:
        raise CertifiedContextualAssemblyError("Z9 context must be durably persisted before use: %s" % exc) from exc
    if durable_context.to_wire() != context.to_wire():
        raise CertifiedContextualAssemblyError("supplied Z9 context differs from durable artifact")
    model_refs = []
    for prediction in component_predictions:
        if len(prediction.model_refs) != 1:
            raise CertifiedContextualAssemblyError("contextual components must each bind exactly one model_ref")
        model_refs.append(prediction.model_refs[0])
    try:
        profiles = ModelContextProfileRegistry(root).active_profiles(model_refs, as_of_ns=assembly_at - 1)
        base_prediction, base_receipt = assemble_and_record(root, frame, component_predictions, registry, assembly_at_ns=assembly_at)
        final_prediction, receipt = contextualize_prediction(frame, component_predictions, base_prediction, base_receipt, context, profiles, assembly_at_ns=assembly_at)
    except (CertifiedAssemblyError, ContextualAssemblyError, ModelContextProfileRegistryError) as exc:
        raise CertifiedContextualAssemblyError(str(exc)) from exc
    PredictionJournal(root).append(final_prediction, journaled_at_ns=assembly_at)
    lineage_errors = validate_contextual_receipt_lineage(root, receipt)
    if lineage_errors:
        raise CertifiedContextualAssemblyError("contextual lineage invalid before receipt append: " + "; ".join(lineage_errors))
    ContextualAssemblyJournal(root).append(receipt)
    return final_prediction, receipt
