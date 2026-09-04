from __future__ import annotations

from typing import Dict, Mapping

from .contracts import (
    QuestionContractError,
    QuestionRegistryEntry,
    QuestionRegistrySnapshot,
    build_question_registry_snapshot,
)
from .evolution import build_reversal_v1_1_registry, build_reversal_v1_2_registry


# Exact implementation identities that have executable resolver contracts in
# the original v1 question-bound learning architecture. This mapping preserves
# the historical boundary in which reversal v1.0 remained unearned.
RESOLVER_READY_IMPLEMENTATIONS_V1 = {
    "ECONOMIC_ROOT_DIRECTION_10S": "autonomous_kernel.evaluation.question_resolvers.midpoint_v1",
    "ECONOMIC_ROOT_MAGNITUDE_30S": "autonomous_kernel.evaluation.question_resolvers.midpoint_v1",
    "ECONOMIC_ROOT_VOLATILITY_60S": "autonomous_kernel.evaluation.question_path_resolvers.fixed_grid_v1",
    "ECONOMIC_ROOT_FRAGILITY_MAE_60S": "autonomous_kernel.evaluation.question_path_resolvers.fixed_grid_v1",
    "ECONOMIC_ROOT_LIQUIDITY_DETERIORATION_30S": "autonomous_kernel.evaluation.liquidity_resolver.liquidity_v1",
    "SPOT_DERIVATIVE_BASIS_CHANGE_5M": "autonomous_kernel.evaluation.relationship_resolver.basis_v1",
    "SPOT_DERIVATIVE_RELATIVE_VALUE_CONVERGENCE_5M": "autonomous_kernel.evaluation.relationship_resolver.basis_v1",
    "MARKET_DIRECTION_REGIME_15M": "autonomous_kernel.evaluation.regime_resolver.endpoint_v1",
    "MARKET_REGIME_PERSISTENCE_5M": "autonomous_kernel.evaluation.regime_resolver.persistence_v1",
}

# Historical v1 boundary. The logical reversal family later evolves to a new
# question version rather than receiving a retroactive resolver here.
UNRESOLVED_QUESTION_IDS_V1 = ("ECONOMIC_ROOT_REVERSAL_60S",)


def build_resolver_ready_registry(
    base: QuestionRegistrySnapshot,
    *,
    version: str,
    known_at_ns: int,
    effective_at_ns: int,
    resolver_implementations: Mapping[str, str],
) -> QuestionRegistrySnapshot:
    """Create a new registry snapshot with explicit resolver-ready transitions.

    `resolver_implementations` is keyed by exact `question_ref`. Questions not
    present in the mapping preserve their prior lifecycle state. This function
    cannot create QUALIFIED claims; resolver readiness and empirical/model
    qualification remain separate evidence stages.
    """
    if not str(version).strip():
        raise QuestionContractError("resolver-ready registry version is required")
    if known_at_ns < base.known_at_ns or effective_at_ns < known_at_ns:
        raise QuestionContractError("resolver-ready registry timing is invalid")
    known_refs = {entry.definition.question_ref for entry in base.entries}
    unknown = set(str(ref) for ref in resolver_implementations).difference(known_refs)
    if unknown:
        raise QuestionContractError("resolver readiness references unknown question: %s" % ", ".join(sorted(unknown)))

    entries = []
    for entry in base.entries:
        question_ref = entry.definition.question_ref
        implementation = resolver_implementations.get(question_ref)
        if implementation is None:
            entries.append(entry)
            continue
        if not str(implementation).strip():
            raise QuestionContractError("resolver implementation ref is required")
        if entry.lifecycle_state not in {"DEFINED", "RESOLVER_READY"}:
            raise QuestionContractError("resolver readiness cannot overwrite %s lifecycle" % entry.lifecycle_state)
        if entry.lifecycle_state == "RESOLVER_READY" and entry.resolver_implementation_ref != implementation:
            raise QuestionContractError("resolver-ready question cannot silently change implementation")
        entries.append(
            QuestionRegistryEntry(
                definition=entry.definition,
                lifecycle_state="RESOLVER_READY",
                registered_at_ns=entry.registered_at_ns,
                effective_at_ns=effective_at_ns,
                resolver_implementation_ref=str(implementation),
            )
        )
    return build_question_registry_snapshot(
        registry_id=base.registry_id,
        version=str(version),
        entries=tuple(entries),
        known_at_ns=int(known_at_ns),
        effective_at_ns=int(effective_at_ns),
    )


def resolver_ready_refs_v1(base: QuestionRegistrySnapshot) -> Dict[str, str]:
    """Return exact question-ref -> resolver-ref mappings for implemented v1 truth resolvers.

    The function fails closed if the registry is missing an implementation-bound
    question or if an expected unresolved question disappeared. This prevents a
    partial/custom registry from being mistaken for the canonical v1 readiness
    surface.
    """
    by_id = {entry.definition.question_id: entry.definition.question_ref for entry in base.entries}
    required = set(RESOLVER_READY_IMPLEMENTATIONS_V1)
    unresolved = set(UNRESOLVED_QUESTION_IDS_V1)
    missing = required.difference(by_id)
    if missing:
        raise QuestionContractError(
            "canonical resolver-ready registry is missing questions: %s" % ", ".join(sorted(missing))
        )
    missing_unresolved = unresolved.difference(by_id)
    if missing_unresolved:
        raise QuestionContractError(
            "canonical unresolved-question boundary is missing questions: %s" % ", ".join(sorted(missing_unresolved))
        )
    return {
        by_id[question_id]: implementation
        for question_id, implementation in sorted(RESOLVER_READY_IMPLEMENTATIONS_V1.items())
    }


def build_resolver_ready_registry_v1(
    base: QuestionRegistrySnapshot,
    *,
    version: str,
    known_at_ns: int,
    effective_at_ns: int,
) -> QuestionRegistrySnapshot:
    """Promote only the mechanically implemented original-v1 question resolvers.

    `ECONOMIC_ROOT_REVERSAL_60S@1.0.0` remains DEFINED. No question is promoted
    to QUALIFIED by this function.
    """
    return build_resolver_ready_registry(
        base,
        version=version,
        known_at_ns=known_at_ns,
        effective_at_ns=effective_at_ns,
        resolver_implementations=resolver_ready_refs_v1(base),
    )


def build_complete_resolver_ready_registry_v1_1(
    base: QuestionRegistrySnapshot,
    *,
    version: str,
    known_at_ns: int,
    effective_at_ns: int,
) -> QuestionRegistrySnapshot:
    """Build the sign-reversal-era registry retained for historical replay."""
    original_ready = build_resolver_ready_registry_v1(
        base,
        version=str(version) + "-original-v1-ready",
        known_at_ns=int(known_at_ns),
        effective_at_ns=int(effective_at_ns),
    )
    return build_reversal_v1_1_registry(
        original_ready,
        version=str(version),
        known_at_ns=int(known_at_ns),
        effective_at_ns=int(effective_at_ns),
    )


def build_complete_resolver_ready_registry_v1_2(
    base: QuestionRegistrySnapshot,
    *,
    version: str,
    known_at_ns: int,
    effective_at_ns: int,
) -> QuestionRegistrySnapshot:
    """Build the final resolver campaign registry with material reversal active.

    The nine original questions become RESOLVER_READY, reversal v1.0 remains
    DEFINED, sign-only reversal v1.1 is retained as RETIRED historical truth,
    and material reversal v1.2 becomes the single active RESOLVER_READY reversal
    examination. No model or capital authority is granted.
    """
    sign_registry = build_complete_resolver_ready_registry_v1_1(
        base,
        version=str(version) + "-sign-reversal-stage",
        known_at_ns=int(known_at_ns),
        effective_at_ns=int(effective_at_ns),
    )
    return build_reversal_v1_2_registry(
        sign_registry,
        version=str(version),
        known_at_ns=int(known_at_ns),
        effective_at_ns=int(effective_at_ns),
    )
