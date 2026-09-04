from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional, Sequence, Tuple

from ..experience.relationship_recovery import recover_economic_relationship_state
from ..experience.relationships import EconomicRelationshipState, RelationshipStateError
from .question_outcome import QuestionBoundOutcome, ResolutionEvidenceRef, build_question_outcome_id
from .question_resolvers import QuestionOutcomePendingError, QuestionResolverError, _journaled_prediction


RELATIONSHIP_RESOLVER_POLICY_ID = "FIRST_QUALIFIED_RELATIONSHIP_STATE_AT_OR_AFTER_TARGET_V1"
RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF = "autonomous_kernel.evaluation.relationship_resolver.basis_v1"
SUPPORTED_RELATIONSHIP_QUESTIONS = {
    "SPOT_DERIVATIVE_BASIS_CHANGE_5M",
    "SPOT_DERIVATIVE_RELATIVE_VALUE_CONVERGENCE_5M",
}


def _decimal(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise QuestionResolverError("%s must be decimal-compatible" % field) from exc
    if not number.is_finite():
        raise QuestionResolverError("%s must be finite" % field)
    return number


def _verified(state: EconomicRelationshipState) -> EconomicRelationshipState:
    try:
        return recover_economic_relationship_state(state.to_wire())
    except RelationshipStateError as exc:
        raise QuestionResolverError("relationship state failed recovery verification: %s" % exc) from exc


def _basis_bps(state: EconomicRelationshipState) -> Decimal:
    basis = state.state.get("basis")
    air_gap = state.state.get("unit_air_gap")
    if not isinstance(basis, dict) or not isinstance(air_gap, dict):
        raise QuestionResolverError("relationship state lacks basis/unit air-gap evidence")
    if state.relationship_type != "SPOT_DERIVATIVE":
        raise QuestionResolverError("relationship resolver requires SPOT_DERIVATIVE state")
    if basis.get("status") != "QUALIFIED" or basis.get("basis_bps") is None:
        raise QuestionResolverError("relationship basis is not qualified")
    if air_gap.get("price_basis_directly_comparable") is not True:
        raise QuestionResolverError("relationship basis quote units are not directly comparable")
    if basis.get("spot_quote_unit") != basis.get("derivative_quote_unit"):
        raise QuestionResolverError("relationship basis quote units differ")
    return _decimal(basis.get("basis_bps"), "basis_bps")


def _bound_baseline(prediction, baseline: EconomicRelationshipState) -> Tuple[EconomicRelationshipState, Decimal]:
    item = _verified(baseline)
    refs = [
        ref
        for ref in prediction.artifact_refs
        if ref.artifact_type == "ECONOMIC_RELATIONSHIP_STATE"
        and ref.artifact_id == item.relationship_state_id
    ]
    if len(refs) != 1:
        raise QuestionResolverError("prediction does not bind supplied relationship state")
    if refs[0].content_hash != item.content_hash():
        raise QuestionResolverError("relationship state content hash differs from prediction lineage")
    if item.relationship_id != prediction.subject_id:
        raise QuestionResolverError("relationship state differs from prediction subject")
    if item.cutoff_at_ns != prediction.cutoff_at_ns:
        raise QuestionResolverError("relationship-state cutoff differs from prediction cutoff")
    if prediction.mode == "PROSPECTIVE_SHADOW" and item.status != "QUALIFIED":
        raise QuestionResolverError("prospective relationship resolution requires qualified baseline state")
    return item, _basis_bps(item)


def _same_frozen_relationship(baseline: EconomicRelationshipState, candidate: EconomicRelationshipState) -> bool:
    return (
        candidate.relationship_id == baseline.relationship_id
        and candidate.relationship_type == baseline.relationship_type
        and candidate.economic_root_id == baseline.economic_root_id
        and candidate.graph_hash == baseline.graph_hash
        and candidate.source_node_id == baseline.source_node_id
        and candidate.target_node_id == baseline.target_node_id
    )


def _select_forward_state(
    prediction,
    baseline: EconomicRelationshipState,
    states: Sequence[EconomicRelationshipState],
) -> Optional[Tuple[EconomicRelationshipState, Decimal]]:
    upper = prediction.resolves_at_ns + prediction.max_resolution_lag_ns
    ordered = sorted(
        (
            state
            for state in states
            if state.relationship_id == baseline.relationship_id
            and state.known_at_ns >= prediction.resolves_at_ns
            and state.known_at_ns <= upper
        ),
        key=lambda item: (item.known_at_ns, item.cutoff_at_ns, item.relationship_state_id, item.content_hash()),
    )
    for raw in ordered:
        item = _verified(raw)
        if item.status != "QUALIFIED" or not _same_frozen_relationship(baseline, item):
            continue
        try:
            basis = _basis_bps(item)
        except QuestionResolverError:
            continue
        return item, basis
    return None


def _realized_value(question_id: str, baseline_basis: Decimal, forward_basis: Decimal) -> Decimal:
    if question_id == "SPOT_DERIVATIVE_BASIS_CHANGE_5M":
        return forward_basis - baseline_basis
    if question_id == "SPOT_DERIVATIVE_RELATIVE_VALUE_CONVERGENCE_5M":
        return abs(baseline_basis) - abs(forward_basis)
    raise QuestionResolverError("unsupported relationship question")


def resolve_relationship_question(
    root: Path,
    prediction_id: str,
    *,
    baseline_state: EconomicRelationshipState,
    forward_states: Sequence[EconomicRelationshipState],
    now_at_ns: int,
) -> QuestionBoundOutcome:
    """Resolve basis/relative-value outcomes from exact stored relationship facts.

    The resolver never recomputes spot/futures basis from raw provider amounts.
    It consumes only relationship states whose direct price-basis comparability
    was already qualified, freezes graph/node semantics at T, and selects the
    first same-relationship qualified state within the declared future window.
    """
    root = root.resolve()
    prediction, entry_hash, journaled_at_ns = _journaled_prediction(root, prediction_id)
    question_id = prediction.question_ref.split("@", 1)[0]
    if question_id not in SUPPORTED_RELATIONSHIP_QUESTIONS:
        raise QuestionResolverError("prediction question is not supported by relationship resolver")
    if prediction.resolver_policy_id != RELATIONSHIP_RESOLVER_POLICY_ID:
        raise QuestionResolverError("prediction resolver policy differs from relationship resolver")
    if prediction.mode == "PROSPECTIVE_SHADOW" and journaled_at_ns >= prediction.resolves_at_ns:
        raise QuestionResolverError("late-journaled prospective prediction cannot become forward evidence")
    now = int(now_at_ns)
    if now < 0:
        raise QuestionResolverError("now_at_ns must be non-negative")

    baseline, baseline_basis = _bound_baseline(prediction, baseline_state)
    selected = _select_forward_state(prediction, baseline, forward_states)
    outcome_id = build_question_outcome_id(
        prediction.prediction_id,
        prediction.resolver_policy_id,
        RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF,
    )
    if selected is not None:
        forward, forward_basis = selected
        if now < forward.known_at_ns:
            raise QuestionOutcomePendingError("selected forward relationship state is not knowable at now_at_ns")
        value = _realized_value(question_id, baseline_basis, forward_basis)
        return QuestionBoundOutcome(
            outcome_id=outcome_id,
            prediction_id=prediction.prediction_id,
            prediction_content_hash=prediction.content_hash(),
            prediction_journal_entry_hash=entry_hash,
            question_ref=prediction.question_ref,
            question_definition_hash=prediction.question_definition_hash,
            question_registry_hash=prediction.question_registry_hash,
            subject_id=prediction.subject_id,
            answer_kind=prediction.answer_kind,
            outcome_metric_id=prediction.outcome_metric_id,
            resolver_policy_id=prediction.resolver_policy_id,
            resolver_implementation_ref=RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF,
            status="RESOLVED",
            cutoff_at_ns=prediction.cutoff_at_ns,
            target_resolves_at_ns=prediction.resolves_at_ns,
            max_resolution_lag_ns=prediction.max_resolution_lag_ns,
            decided_at_ns=forward.known_at_ns,
            realized_answer={"value": format(value, "f")},
            resolution_evidence=(
                ResolutionEvidenceRef(
                    evidence_family="ECONOMIC_RELATIONSHIP_STATE",
                    artifact_type="ECONOMIC_RELATIONSHIP_STATE",
                    artifact_id=baseline.relationship_state_id,
                    content_hash=baseline.content_hash(),
                    known_at_ns=baseline.known_at_ns,
                    role="BASELINE",
                    subject_ids=(prediction.subject_id, baseline.economic_root_id),
                ),
                ResolutionEvidenceRef(
                    evidence_family="ECONOMIC_RELATIONSHIP_STATE",
                    artifact_type="ECONOMIC_RELATIONSHIP_STATE",
                    artifact_id=forward.relationship_state_id,
                    content_hash=forward.content_hash(),
                    known_at_ns=forward.known_at_ns,
                    role="FORWARD",
                    subject_ids=(prediction.subject_id, forward.economic_root_id),
                ),
            ),
        )

    window_closes = prediction.resolves_at_ns + prediction.max_resolution_lag_ns
    if now <= window_closes:
        raise QuestionOutcomePendingError("relationship resolution window remains open and no eligible state exists")
    return QuestionBoundOutcome(
        outcome_id=outcome_id,
        prediction_id=prediction.prediction_id,
        prediction_content_hash=prediction.content_hash(),
        prediction_journal_entry_hash=entry_hash,
        question_ref=prediction.question_ref,
        question_definition_hash=prediction.question_definition_hash,
        question_registry_hash=prediction.question_registry_hash,
        subject_id=prediction.subject_id,
        answer_kind=prediction.answer_kind,
        outcome_metric_id=prediction.outcome_metric_id,
        resolver_policy_id=prediction.resolver_policy_id,
        resolver_implementation_ref=RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF,
        status="UNRESOLVABLE",
        cutoff_at_ns=prediction.cutoff_at_ns,
        target_resolves_at_ns=prediction.resolves_at_ns,
        max_resolution_lag_ns=prediction.max_resolution_lag_ns,
        decided_at_ns=window_closes + 1,
        realized_answer=None,
        resolution_evidence=(),
    )
