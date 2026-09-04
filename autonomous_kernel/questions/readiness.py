from __future__ import annotations

from typing import Mapping, Optional, Tuple

from .contracts import (
    QuestionContractError,
    QuestionRegistryEntry,
    QuestionRegistrySnapshot,
    build_question_registry_snapshot,
)


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
