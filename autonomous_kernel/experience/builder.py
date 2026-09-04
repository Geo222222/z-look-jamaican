from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Mapping, Sequence

from ..context.contracts import MarketContextFrame
from ..representation.contracts import RepresentationFrame
from .contracts import (
    ExperienceRelationshipStateRef,
    ExperienceSourceFrame,
    ExperienceTimescale,
    ExperienceView,
    MarketExperienceError,
    MarketExperienceFrame,
)
from .economic_graph import EconomicInstrumentGraph
from .relationships import EconomicRelationshipState


BUILDER_VERSION = "market-experience-v1"


@dataclass(frozen=True)
class TimescaleSpec:
    timescale: ExperienceTimescale
    lookback_ns: int

    def __post_init__(self) -> None:
        if self.lookback_ns <= 0:
            raise MarketExperienceError("timescale lookback_ns must be positive")


def _experience_id(
    economic_root_id: str,
    cutoff_at_ns: int,
    graph_hash: str,
    context_hash: str,
    source_hashes: Sequence[str],
    timescale_specs: Sequence[TimescaleSpec],
    builder_version: str,
) -> str:
    specification = [
        "%s:%d" % (spec.timescale.value, spec.lookback_ns)
        for spec in sorted(timescale_specs, key=lambda item: item.timescale.value)
    ]
    material = "|".join(
        [economic_root_id, str(cutoff_at_ns), graph_hash, context_hash, builder_version]
        + specification
        + sorted(source_hashes)
    )
    return "EXP-%s" % hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _frame_ref(frame: RepresentationFrame) -> ExperienceSourceFrame:
    return ExperienceSourceFrame(
        frame_id=frame.frame_id,
        frame_hash=frame.content_hash(),
        representation_type=frame.representation_type,
        instrument_id=frame.instrument.canonical_id,
        market_type=frame.instrument.market_type,
        window_start_ns=frame.window_start_ns,
        cutoff_at_ns=frame.cutoff_at_ns,
        known_at_ns=frame.known_at_ns,
        status=frame.status,
    )


def _relationship_ref(state: EconomicRelationshipState) -> ExperienceRelationshipStateRef:
    return ExperienceRelationshipStateRef(
        relationship_state_id=state.relationship_state_id,
        relationship_state_hash=state.content_hash(),
        relationship_id=state.relationship_id,
        relationship_type=state.relationship_type,
        economic_root_id=state.economic_root_id,
        graph_hash=state.graph_hash,
        cutoff_at_ns=state.cutoff_at_ns,
        known_at_ns=state.known_at_ns,
        status=state.status,
    )


def _aggregate_status(items: Sequence[RepresentationFrame], *, incomplete: bool = False) -> str:
    if not items:
        return "UNAVAILABLE"
    if incomplete:
        return "DEGRADED" if any(item.status in {"QUALIFIED", "DEGRADED"} for item in items) else "UNAVAILABLE"
    if all(item.status == "QUALIFIED" for item in items):
        return "QUALIFIED"
    if any(item.status in {"QUALIFIED", "DEGRADED"} for item in items):
        return "DEGRADED"
    return "UNAVAILABLE"


def _aggregate_family_status(statuses: Sequence[str]) -> str:
    values = tuple(status for status in statuses if status in {"QUALIFIED", "DEGRADED", "UNAVAILABLE"})
    if not values:
        return "UNAVAILABLE"
    if all(status == "QUALIFIED" for status in values):
        return "QUALIFIED"
    if any(status in {"QUALIFIED", "DEGRADED"} for status in values):
        return "DEGRADED"
    return "UNAVAILABLE"


def _derivative_family_status(
    frames: Sequence[RepresentationFrame],
    family: str,
    *,
    incomplete: bool,
) -> str:
    statuses = []
    for frame in frames:
        feature_status = frame.state.get("feature_family_status")
        if not isinstance(feature_status, Mapping):
            statuses.append("UNAVAILABLE")
            continue
        status = str(feature_status.get(family, "UNAVAILABLE"))
        if status not in {"QUALIFIED", "DEGRADED", "UNAVAILABLE"}:
            status = "UNAVAILABLE"
        if (frame.status == "DEGRADED" or incomplete) and status == "QUALIFIED":
            status = "DEGRADED"
        if frame.status == "UNAVAILABLE":
            status = "UNAVAILABLE"
        statuses.append(status)
    return _aggregate_family_status(statuses)


def _feature_status(
    frames: Sequence[RepresentationFrame],
    context: MarketContextFrame,
    *,
    incomplete: bool,
) -> Dict[str, str]:
    spot_micro = [
        frame
        for frame in frames
        if frame.representation_type == "INSTRUMENT_STATE" and frame.instrument.market_type == "SPOT"
    ]
    derivative_micro = [
        frame
        for frame in frames
        if frame.representation_type == "INSTRUMENT_STATE"
        and frame.instrument.market_type in {"PERPETUAL", "FUTURE"}
    ]
    derivative_state = [
        frame
        for frame in frames
        if frame.representation_type == "DERIVATIVE_STATE"
        and frame.instrument.market_type in {"PERPETUAL", "FUTURE"}
    ]
    market_wide = context.status if context.status in {"QUALIFIED", "DEGRADED", "UNAVAILABLE"} else "UNAVAILABLE"
    if incomplete and market_wide == "QUALIFIED":
        market_wide = "DEGRADED"
    return {
        "SPOT_MICROSTRUCTURE": _aggregate_status(spot_micro, incomplete=incomplete),
        "DERIVATIVE_MICROSTRUCTURE": _aggregate_status(derivative_micro, incomplete=incomplete),
        "DERIVATIVE_POSITIONING": _derivative_family_status(
            derivative_state, "OPEN_INTEREST", incomplete=incomplete
        ),
        "DERIVATIVE_FINANCING": _derivative_family_status(
            derivative_state, "FUNDING", incomplete=incomplete
        ),
        "DERIVATIVE_LIQUIDATIONS": _derivative_family_status(
            derivative_state, "LIQUIDATIONS", incomplete=incomplete
        ),
        "MARK_INDEX_DIVERGENCE": _derivative_family_status(
            derivative_state, "MARK_INDEX", incomplete=incomplete
        ),
        # Cross-contract curve state is not inferred from one derivative frame.
        # A later empirical relationship/term-structure phase must earn it.
        "TERM_STRUCTURE": "UNAVAILABLE",
        "MARKET_WIDE_CONTEXT": market_wide,
    }


def build_market_experience(
    *,
    economic_root_id: str,
    graph: EconomicInstrumentGraph,
    context: MarketContextFrame,
    timescale_frames: Mapping[ExperienceTimescale, Sequence[RepresentationFrame]],
    timescale_specs: Sequence[TimescaleSpec],
    cutoff_at_ns: int,
    relationship_states: Sequence[EconomicRelationshipState] = (),
    builder_version: str = BUILDER_VERSION,
) -> MarketExperienceFrame:
    """Build a deterministic causal market-experience manifest at cutoff T.

    The builder rejects rather than filters future-known source frames, contexts,
    graph versions, or empirical relationship states. Future realized paths and
    outcomes belong in separate objects and must never mutate this frame.

    A timescale may not be backed by a representation containing information
    from before that view's declared start; doing so would contaminate a short
    question with a longer history. Sources that start late or end before T are
    retained only as degraded/incomplete evidence.
    """
    if not economic_root_id:
        raise MarketExperienceError("economic_root_id is required")
    if cutoff_at_ns < 0:
        raise MarketExperienceError("cutoff_at_ns must be non-negative")
    if graph.known_at_ns > cutoff_at_ns:
        raise MarketExperienceError("lookahead graph rejected")
    if context.cutoff_at_ns > cutoff_at_ns or context.known_at_ns > cutoff_at_ns:
        raise MarketExperienceError("lookahead market context rejected")
    graph_nodes = graph.nodes_for_root(economic_root_id)
    if not graph_nodes:
        raise MarketExperienceError("economic root is absent from graph")
    allowed_instruments = {node.instrument.canonical_id for node in graph_nodes}

    relationships = tuple(relationship_states)
    relationship_ids = [item.relationship_state_id for item in relationships]
    if len(relationship_ids) != len(set(relationship_ids)):
        raise MarketExperienceError("duplicate relationship-state id")
    graph_hash = graph.content_hash()
    for state in relationships:
        if state.economic_root_id != economic_root_id:
            raise MarketExperienceError("relationship state economic root differs from experience")
        if state.graph_hash != graph_hash:
            raise MarketExperienceError("relationship state graph hash differs from experience graph")
        if state.cutoff_at_ns > cutoff_at_ns or state.known_at_ns > cutoff_at_ns:
            raise MarketExperienceError("lookahead relationship state rejected")

    specs = tuple(timescale_specs)
    if not specs:
        raise MarketExperienceError("at least one timescale spec is required")
    if len({spec.timescale for spec in specs}) != len(specs):
        raise MarketExperienceError("timescale specs must be unique")
    if any(spec.lookback_ns > cutoff_at_ns for spec in specs):
        raise MarketExperienceError("timescale lookback begins before epoch")

    views = []
    all_hashes = [item.content_hash() for item in relationships]
    known_values = [graph.known_at_ns, context.known_at_ns] + [item.known_at_ns for item in relationships]
    overall_states = []
    for spec in specs:
        view_start = cutoff_at_ns - spec.lookback_ns
        frames = tuple(timescale_frames.get(spec.timescale, ()))
        ids = [frame.frame_id for frame in frames]
        if len(ids) != len(set(ids)):
            raise MarketExperienceError("duplicate source frame id in timescale")
        incomplete = False
        for frame in frames:
            if frame.instrument.canonical_id not in allowed_instruments:
                raise MarketExperienceError("source frame instrument is outside economic root graph")
            if frame.cutoff_at_ns > cutoff_at_ns or frame.known_at_ns > cutoff_at_ns:
                raise MarketExperienceError("lookahead source frame rejected")
            if frame.window_start_ns < view_start:
                raise MarketExperienceError("source frame contains information before timescale view start")
            if frame.window_start_ns > view_start or frame.cutoff_at_ns < cutoff_at_ns:
                incomplete = True
            all_hashes.append(frame.content_hash())
            known_values.append(frame.known_at_ns)

        family_status = _feature_status(frames, context, incomplete=incomplete)
        if not frames:
            status = "UNAVAILABLE"
        elif incomplete:
            status = "DEGRADED" if any(frame.status in {"QUALIFIED", "DEGRADED"} for frame in frames) else "UNAVAILABLE"
        elif all(frame.status == "QUALIFIED" for frame in frames) and context.status == "QUALIFIED":
            status = "QUALIFIED"
        elif any(frame.status in {"QUALIFIED", "DEGRADED"} for frame in frames) and context.status != "UNAVAILABLE":
            status = "DEGRADED"
        else:
            status = "UNAVAILABLE"
        overall_states.append(status)
        views.append(
            ExperienceView(
                timescale=spec.timescale,
                lookback_ns=spec.lookback_ns,
                window_start_ns=view_start,
                cutoff_at_ns=cutoff_at_ns,
                status=status,
                source_frames=tuple(_frame_ref(frame) for frame in frames),
                feature_family_status=family_status,
            )
        )

    if all(status == "QUALIFIED" for status in overall_states):
        overall_status = "QUALIFIED"
    elif any(status in {"QUALIFIED", "DEGRADED"} for status in overall_states):
        overall_status = "DEGRADED"
    else:
        overall_status = "UNAVAILABLE"
    if relationships and overall_status == "QUALIFIED" and any(item.status != "QUALIFIED" for item in relationships):
        overall_status = "DEGRADED"

    context_hash = context.content_hash()
    experience_id = _experience_id(
        economic_root_id,
        cutoff_at_ns,
        graph_hash,
        context_hash,
        all_hashes,
        specs,
        builder_version,
    )
    return MarketExperienceFrame(
        experience_id=experience_id,
        economic_root_id=economic_root_id,
        cutoff_at_ns=cutoff_at_ns,
        known_at_ns=max(known_values),
        status=overall_status,
        builder_version=builder_version,
        graph_id=graph.graph_id,
        graph_version=graph.graph_version,
        graph_hash=graph_hash,
        context_id=context.context_id,
        context_hash=context_hash,
        context_status=context.status,
        views=tuple(views),
        relationship_states=tuple(_relationship_ref(item) for item in relationships),
    )
