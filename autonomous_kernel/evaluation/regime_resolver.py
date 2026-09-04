from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

from ..context.contracts import MarketContextFrame
from ..context.store import MarketContextStore, validate_market_context_store
from ..experience.market_wide import MarketWideExperienceState
from .question_outcome import QuestionBoundOutcome, ResolutionEvidenceRef, build_question_outcome_id
from .question_resolvers import QuestionOutcomePendingError, QuestionResolverError, _journaled_prediction


REGIME_ENDPOINT_POLICY_ID = "FIRST_QUALIFIED_MARKET_CONTEXT_AT_OR_AFTER_TARGET_V1"
REGIME_PERSISTENCE_POLICY_ID = "QUALIFIED_MARKET_CONTEXT_INTERVAL_V1"
REGIME_ENDPOINT_IMPLEMENTATION_REF = "autonomous_kernel.evaluation.regime_resolver.endpoint_v1"
REGIME_PERSISTENCE_IMPLEMENTATION_REF = "autonomous_kernel.evaluation.regime_resolver.persistence_v1"
REGIME_QUESTION_ID = "MARKET_DIRECTION_REGIME_15M"
PERSISTENCE_QUESTION_ID = "MARKET_REGIME_PERSISTENCE_5M"


class RegimeContractDiscontinuityError(QuestionResolverError):
    pass


def _members(context: MarketContextFrame) -> Tuple[str, ...]:
    members = context.state.get("members")
    if not isinstance(members, Mapping) or not members:
        raise QuestionResolverError("market regime context requires member summaries")
    return tuple(sorted(str(item) for item in members))


def _direction(context: MarketContextFrame) -> str:
    regimes = context.state.get("regimes")
    if not isinstance(regimes, Mapping):
        raise QuestionResolverError("market regime context lacks regimes")
    direction = str(regimes.get("direction", "UNAVAILABLE"))
    if direction not in {"RISK_ON", "RISK_OFF", "NEUTRAL", "MIXED"}:
        raise QuestionResolverError("market direction regime is unavailable or invalid")
    return direction


def _bound_baseline(
    prediction,
    market_wide: MarketWideExperienceState,
    store: MarketContextStore,
) -> MarketContextFrame:
    refs = [
        ref
        for ref in prediction.artifact_refs
        if ref.artifact_type == "MARKET_WIDE_EXPERIENCE"
        and ref.artifact_id == market_wide.market_wide_experience_id
    ]
    if len(refs) != 1:
        raise QuestionResolverError("prediction does not bind supplied market-wide experience")
    ref = refs[0]
    restored = MarketWideExperienceState.from_wire(market_wide.to_wire())
    if ref.content_hash != restored.content_hash():
        raise QuestionResolverError("market-wide experience hash differs from prediction lineage")
    if restored.cutoff_at_ns != prediction.cutoff_at_ns:
        raise QuestionResolverError("market-wide experience cutoff differs from prediction cutoff")
    if prediction.mode == "PROSPECTIVE_SHADOW" and restored.status != "QUALIFIED":
        raise QuestionResolverError("prospective regime resolution requires qualified market-wide experience")
    current = restored.state.get("current")
    if not isinstance(current, Mapping):
        raise QuestionResolverError("market-wide experience lacks current Z9 context")
    context_id = str(current.get("context_id", ""))
    context_hash = str(current.get("context_hash", ""))
    if not context_id or not context_hash:
        raise QuestionResolverError("market-wide current context identity is incomplete")
    try:
        baseline = store.load(context_id)
    except (FileNotFoundError, RuntimeError, ValueError, TypeError) as exc:
        raise QuestionResolverError("prediction-bound baseline context is not durably recoverable: %s" % exc) from exc
    if baseline.content_hash() != context_hash:
        raise QuestionResolverError("baseline context hash differs from market-wide experience lineage")
    if baseline.cutoff_at_ns > prediction.cutoff_at_ns or baseline.known_at_ns > prediction.cutoff_at_ns:
        raise QuestionResolverError("baseline market context exceeds prediction cutoff")
    if prediction.mode == "PROSPECTIVE_SHADOW" and baseline.status != "QUALIFIED":
        raise QuestionResolverError("prospective regime resolution requires qualified baseline context")
    _direction(baseline)
    return baseline


def _durable_contexts(root: Path) -> Tuple[MarketContextFrame, ...]:
    errors = validate_market_context_store(root)
    if errors:
        raise QuestionResolverError("market-context store is invalid: " + "; ".join(errors))
    store = MarketContextStore(root)
    if not store.index_path.is_file():
        raise QuestionResolverError("market-context discovery index is missing")
    try:
        index = json.loads(store.index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QuestionResolverError("market-context discovery index is unreadable") from exc
    contexts = []
    for raw in index.get("items", []):
        if not isinstance(raw, Mapping):
            raise QuestionResolverError("market-context discovery item is malformed")
        context_id = str(raw.get("context_id", ""))
        if not context_id:
            raise QuestionResolverError("market-context discovery item lacks context_id")
        contexts.append(store.load(context_id))
    return tuple(sorted(contexts, key=lambda item: (item.known_at_ns, item.cutoff_at_ns, item.context_id)))


def _same_universe(left: MarketContextFrame, right: MarketContextFrame) -> bool:
    return _members(left) == _members(right)


def _same_regime_contract(left: MarketContextFrame, right: MarketContextFrame) -> bool:
    return (
        left.builder_version == right.builder_version
        and left.parameters == right.parameters
        and _same_universe(left, right)
    )


def _first_endpoint(
    prediction,
    baseline: MarketContextFrame,
    contexts: Sequence[MarketContextFrame],
) -> Optional[MarketContextFrame]:
    upper = prediction.resolves_at_ns + prediction.max_resolution_lag_ns
    same_universe = [
        context
        for context in contexts
        if context.context_id != baseline.context_id
        and context.status == "QUALIFIED"
        and _same_universe(baseline, context)
        and context.cutoff_at_ns >= prediction.resolves_at_ns
        and context.known_at_ns >= prediction.resolves_at_ns
        and context.cutoff_at_ns <= upper
        and context.known_at_ns <= upper
    ]
    if not same_universe:
        return None
    first = sorted(
        same_universe,
        key=lambda item: (item.known_at_ns, item.cutoff_at_ns, item.context_id, item.content_hash()),
    )[0]
    if not _same_regime_contract(baseline, first):
        raise RegimeContractDiscontinuityError(
            "first same-universe endpoint changed the regime-definition contract"
        )
    _direction(first)
    return first


def _outcome(
    prediction,
    entry_hash: str,
    implementation_ref: str,
    *,
    status: str,
    decided_at_ns: int,
    realized_answer,
    evidence: Sequence[ResolutionEvidenceRef],
) -> QuestionBoundOutcome:
    return QuestionBoundOutcome(
        outcome_id=build_question_outcome_id(
            prediction.prediction_id,
            prediction.resolver_policy_id,
            implementation_ref,
        ),
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
        resolver_implementation_ref=implementation_ref,
        status=status,
        cutoff_at_ns=prediction.cutoff_at_ns,
        target_resolves_at_ns=prediction.resolves_at_ns,
        max_resolution_lag_ns=prediction.max_resolution_lag_ns,
        decided_at_ns=decided_at_ns,
        realized_answer=realized_answer,
        resolution_evidence=tuple(evidence),
    )


def _evidence(context: MarketContextFrame, role: str, subject_id: str) -> ResolutionEvidenceRef:
    return ResolutionEvidenceRef(
        evidence_family="MARKET_WIDE_CONTEXT",
        artifact_type="MARKET_CONTEXT",
        artifact_id=context.context_id,
        content_hash=context.content_hash(),
        known_at_ns=context.known_at_ns,
        role=role,
        subject_ids=(subject_id,),
    )


def resolve_market_regime_question(
    root: Path,
    prediction_id: str,
    *,
    baseline_market_wide: MarketWideExperienceState,
    now_at_ns: int,
) -> QuestionBoundOutcome:
    root = root.resolve()
    prediction, entry_hash, journaled_at_ns = _journaled_prediction(root, prediction_id)
    if prediction.question_ref.split("@", 1)[0] != REGIME_QUESTION_ID:
        raise QuestionResolverError("prediction question is not supported by market regime endpoint resolver")
    if prediction.resolver_policy_id != REGIME_ENDPOINT_POLICY_ID:
        raise QuestionResolverError("prediction resolver policy differs from market regime endpoint resolver")
    if prediction.mode == "PROSPECTIVE_SHADOW" and journaled_at_ns >= prediction.resolves_at_ns:
        raise QuestionResolverError("late-journaled prospective prediction cannot become forward evidence")
    now = int(now_at_ns)
    if now < 0:
        raise QuestionResolverError("now_at_ns must be non-negative")

    store = MarketContextStore(root)
    baseline = _bound_baseline(prediction, baseline_market_wide, store)
    contexts = _durable_contexts(root)
    try:
        selected = _first_endpoint(prediction, baseline, contexts)
    except RegimeContractDiscontinuityError:
        window_closes = prediction.resolves_at_ns + prediction.max_resolution_lag_ns
        if now <= window_closes:
            raise QuestionOutcomePendingError("regime endpoint contract changed inside the open resolution window")
        selected = None

    if selected is not None:
        if now < selected.known_at_ns:
            raise QuestionOutcomePendingError("selected market regime endpoint is not knowable at now_at_ns")
        return _outcome(
            prediction,
            entry_hash,
            REGIME_ENDPOINT_IMPLEMENTATION_REF,
            status="RESOLVED",
            decided_at_ns=selected.known_at_ns,
            realized_answer={"value": _direction(selected)},
            evidence=(
                _evidence(baseline, "BASELINE", prediction.subject_id),
                _evidence(selected, "FORWARD", prediction.subject_id),
            ),
        )

    window_closes = prediction.resolves_at_ns + prediction.max_resolution_lag_ns
    if now <= window_closes:
        raise QuestionOutcomePendingError("market regime resolution window remains open and no comparable endpoint exists")
    return _outcome(
        prediction,
        entry_hash,
        REGIME_ENDPOINT_IMPLEMENTATION_REF,
        status="UNRESOLVABLE",
        decided_at_ns=window_closes + 1,
        realized_answer=None,
        evidence=(),
    )


def resolve_regime_persistence_question(
    root: Path,
    prediction_id: str,
    *,
    baseline_market_wide: MarketWideExperienceState,
    now_at_ns: int,
) -> QuestionBoundOutcome:
    root = root.resolve()
    prediction, entry_hash, journaled_at_ns = _journaled_prediction(root, prediction_id)
    if prediction.question_ref.split("@", 1)[0] != PERSISTENCE_QUESTION_ID:
        raise QuestionResolverError("prediction question is not supported by regime persistence resolver")
    if prediction.resolver_policy_id != REGIME_PERSISTENCE_POLICY_ID:
        raise QuestionResolverError("prediction resolver policy differs from regime persistence resolver")
    if prediction.mode == "PROSPECTIVE_SHADOW" and journaled_at_ns >= prediction.resolves_at_ns:
        raise QuestionResolverError("late-journaled prospective prediction cannot become forward evidence")
    now = int(now_at_ns)
    if now < 0:
        raise QuestionResolverError("now_at_ns must be non-negative")

    store = MarketContextStore(root)
    baseline = _bound_baseline(prediction, baseline_market_wide, store)
    baseline_direction = _direction(baseline)
    contexts = _durable_contexts(root)

    interval_universe = [
        context
        for context in contexts
        if context.context_id != baseline.context_id
        and context.status == "QUALIFIED"
        and _same_universe(baseline, context)
        and context.cutoff_at_ns > prediction.cutoff_at_ns
        and context.cutoff_at_ns <= prediction.resolves_at_ns + prediction.max_resolution_lag_ns
    ]
    interval_universe.sort(key=lambda item: (item.known_at_ns, item.cutoff_at_ns, item.context_id))

    for context in interval_universe:
        if context.cutoff_at_ns <= prediction.resolves_at_ns and not _same_regime_contract(baseline, context):
            window_closes = prediction.resolves_at_ns + prediction.max_resolution_lag_ns
            if now <= window_closes:
                raise QuestionOutcomePendingError("regime contract changed during persistence interval")
            return _outcome(
                prediction,
                entry_hash,
                REGIME_PERSISTENCE_IMPLEMENTATION_REF,
                status="UNRESOLVABLE",
                decided_at_ns=window_closes + 1,
                realized_answer=None,
                evidence=(),
            )

    try:
        endpoint = _first_endpoint(prediction, baseline, contexts)
    except RegimeContractDiscontinuityError:
        endpoint = None
    if endpoint is None:
        window_closes = prediction.resolves_at_ns + prediction.max_resolution_lag_ns
        if now <= window_closes:
            raise QuestionOutcomePendingError("persistence interval lacks a comparable terminal context")
        return _outcome(
            prediction,
            entry_hash,
            REGIME_PERSISTENCE_IMPLEMENTATION_REF,
            status="UNRESOLVABLE",
            decided_at_ns=window_closes + 1,
            realized_answer=None,
            evidence=(),
        )
    if now < endpoint.known_at_ns:
        raise QuestionOutcomePendingError("persistence terminal context is not knowable at now_at_ns")

    comparable_interval = [
        context
        for context in interval_universe
        if context.cutoff_at_ns <= prediction.resolves_at_ns and _same_regime_contract(baseline, context)
    ]
    observed = list(comparable_interval)
    if endpoint.context_id not in {item.context_id for item in observed}:
        observed.append(endpoint)
    observed.sort(key=lambda item: (item.cutoff_at_ns, item.known_at_ns, item.context_id))
    persistent = 1 if observed and all(_direction(item) == baseline_direction for item in observed) else 0
    evidence = [_evidence(baseline, "BASELINE", prediction.subject_id)]
    # The generic outcome schema distinguishes only BASELINE vs FORWARD. Every
    # post-cutoff context in the durable interval is therefore a FORWARD fact;
    # its timestamp and ordered artifact identity preserve interval position.
    evidence.extend(_evidence(item, "FORWARD", prediction.subject_id) for item in observed)
    return _outcome(
        prediction,
        entry_hash,
        REGIME_PERSISTENCE_IMPLEMENTATION_REF,
        status="RESOLVED",
        decided_at_ns=max(item.known_at_ns for item in observed),
        realized_answer={"value": persistent},
        evidence=evidence,
    )
