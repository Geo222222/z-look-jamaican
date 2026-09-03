from __future__ import annotations

from pathlib import Path
from typing import Sequence, Tuple

from ..context.contracts import MarketContextFrame
from ..context.materialize import MarketContextMaterializationError, verify_materialized_context
from ..context.store import MarketContextStore
from ..models.registry import ModelRegistry
from ..prediction.contracts import Prediction
from ..prediction.journal import PredictionJournal
from ..representation.contracts import RepresentationFrame
from .context_profile_registry import ModelContextProfileRegistry, ModelContextProfileRegistryError
from .contextual import ContextualAssemblyError, ContextualAssemblyReceipt, contextualize_prediction
from .contextual_journal import ContextualAssemblyJournal
from .contextual_lineage import validate_contextual_receipt_lineage
from .service import CertifiedAssemblyError, assemble_and_record


class CertifiedContextualAssemblyError(RuntimeError):
    pass


def contextual_assemble_and_record(
    root: Path,
    frame: RepresentationFrame,
    component_predictions: Sequence[Prediction],
    registry: ModelRegistry,
    context: MarketContextFrame,
    *,
    assembly_at_ns: int,
) -> Tuple[Prediction, ContextualAssemblyReceipt]:
    """Canonical durable Z8+Z9 entrypoint.

    The runtime refuses caller-supplied context policies. The supplied Z9 frame must
    have been produced by the canonical durable materializer, and every component
    model's ModelContextProfile is resolved from the immutable governed registry as
    of the assembly cutoff.
    """
    root = root.resolve()
    assembly_at = int(assembly_at_ns)
    try:
        durable_context = MarketContextStore(root).load(context.context_id)
        verify_materialized_context(root, context.context_id)
    except Exception as exc:
        raise CertifiedContextualAssemblyError("Z9 context must be canonically materialized and durable before use: %s" % exc) from exc
    if durable_context.to_wire() != context.to_wire():
        raise CertifiedContextualAssemblyError("supplied Z9 context differs from canonical durable artifact")

    model_refs = []
    for component in component_predictions:
        if len(component.model_refs) != 1:
            raise CertifiedContextualAssemblyError("each contextual component prediction must bind exactly one model_ref")
        model_refs.append(component.model_refs[0])
    if not model_refs or len(model_refs) != len(set(model_refs)):
        raise CertifiedContextualAssemblyError("contextual component model_refs must be unique and non-empty")
    try:
        profiles = ModelContextProfileRegistry(root).resolve(model_refs, as_of_ns=assembly_at)
    except ModelContextProfileRegistryError as exc:
        raise CertifiedContextualAssemblyError("governed ModelContextProfile resolution failed: %s" % exc) from exc

    try:
        base_prediction, base_receipt = assemble_and_record(root, frame, component_predictions, registry, assembly_at_ns=assembly_at)
        final_prediction, receipt = contextualize_prediction(frame, component_predictions, base_prediction, base_receipt, durable_context, profiles, assembly_at_ns=assembly_at)
    except (CertifiedAssemblyError, ContextualAssemblyError) as exc:
        raise CertifiedContextualAssemblyError(str(exc)) from exc
    PredictionJournal(root).append(final_prediction, journaled_at_ns=assembly_at)
    lineage_errors = validate_contextual_receipt_lineage(root, receipt)
    if lineage_errors:
        raise CertifiedContextualAssemblyError("contextual lineage invalid before receipt append: " + "; ".join(lineage_errors))
    ContextualAssemblyJournal(root).append(receipt)
    return final_prediction, receipt
