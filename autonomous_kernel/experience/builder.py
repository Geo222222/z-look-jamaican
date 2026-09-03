from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Mapping, Sequence

from ..context.contracts import MarketContextFrame
from ..representation.contracts import RepresentationFrame
from .contracts import (
    ExperienceSourceFrame,
    ExperienceTimescale,
    ExperienceView,
    MarketExperienceError,
    MarketExperienceFrame,
)
from .economic_graph import EconomicInstrumentGraph


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
        instrument_id=frame.instrument.canonical_id,
        market_type=frame.instrument.market_type,
        window_start_ns=frame.window_start_ns,
        cutoff_at_ns=frame.cutoff_at_ns,
        known_at_ns=frame.known_at_ns,
        status=frame.status,
    )


def _feature_status(frames: Sequence[RepresentationFrame], context: MarketContextFrame) -> Dict[str, str]:
    spot = [frame for frame in frames if frame.instrument.market_type == "SPOT"]
    derivatives = [frame for frame in frames if frame.instrument.market_type in {"PERPETUAL", "FUTURE"}]

    def aggregate(items: Sequence[RepresentationFrame]) -> str:
        if not items:
            return "UNAVAILABLE"
        if all(item.status == "QUALIFIED" for item in items):
            return "QUALIFIED"
        if any(item.status in {"QUALIFIED", "DEGRADED"} for item in items):
            return "DEGRADED"
        return "UNAVAILABLE"

    market_wide = context.status if context.status in {"QUALIFIED", "DEGRADED", "UNAVAILABLE"} else "UNAVAILABLE"
    return {
        "SPOT_MICROSTRUCTURE": aggregate(spot),
        "DERIVATIVE_MICROSTRUCTURE": aggregate(derivatives),
        # Funding/OI/liquidation/term-structure are deliberately not inferred
        # from order-book/trade frames. A later derivative-state capture phase
        # must earn these families explicitly.
        "DERIVATIVE_POSITIONING": "UNAVAILABLE",
        "DERIVATIVE_FINANCING": "UNAVAILABLE",
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
    builder_version: str = BUILDER_VERSION,
) -> MarketExperienceFrame:
    """Build a deterministic causal market-experience manifest at cutoff T.

    The builder rejects rather than filters future-known source frames, contexts,
    or graph versions. Future realized paths/outcomes belong in a separate
    outcome object and must never mutate this frame.
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

    specs = tuple(timescale_specs)
    if not specs:
        raise MarketExperienceError("at least one timescale spec is required")
    if len({spec.timescale for spec in specs}) != len(specs):
        raise MarketExperienceError("timescale specs must be unique")
    if any(spec.lookback_ns > cutoff_at_ns for spec in specs):
        raise MarketExperienceError("timescale lookback begins before epoch")

    views = []
    all_hashes = []
    known_values = [graph.known_at_ns, context.known_at_ns]
    overall_states = []
    for spec in specs:
        frames = tuple(timescale_frames.get(spec.timescale, ()))
        ids = [frame.frame_id for frame in frames]
        if len(ids) != len(set(ids)):
            raise MarketExperienceError("duplicate source frame id in timescale")
        for frame in frames:
            if frame.instrument.canonical_id not in allowed_instruments:
                raise MarketExperienceError("source frame instrument is outside economic root graph")
            if frame.cutoff_at_ns > cutoff_at_ns or frame.known_at_ns > cutoff_at_ns:
                raise MarketExperienceError("lookahead source frame rejected")
            # A source representation may include a longer observation history;
            # the view lookback is an experience/selection contract and does not
            # fabricate a shorter Z2 frame than the caller actually supplied.
            all_hashes.append(frame.content_hash())
            known_values.append(frame.known_at_ns)

        family_status = _feature_status(frames, context)
        if not frames:
            status = "UNAVAILABLE"
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
                window_start_ns=cutoff_at_ns - spec.lookback_ns,
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

    graph_hash = graph.content_hash()
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
    )
