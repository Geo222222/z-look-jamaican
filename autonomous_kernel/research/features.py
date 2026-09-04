from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Sequence

from ..context.contracts import MarketContextFrame
from ..operations import canonical_hash
from ..representation.contracts import RepresentationFrame
from .contracts import ResearchContractError


FEATURE_SCHEMA_VERSION = "1.0"


def _decimal(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return float(number) if number.is_finite() else None


def _mean(values: Sequence[float]) -> Optional[float]:
    clean = [float(value) for value in values]
    return None if not clean else sum(clean) / len(clean)


def extract_instrument_features(frame: RepresentationFrame) -> Mapping[str, Any]:
    """Extract a stable, model-neutral feature vector from one qualified Z2 frame."""
    if frame.status != "QUALIFIED":
        raise ResearchContractError("training feature extraction requires QUALIFIED representation")
    aggregate = frame.state.get("aggregate")
    venues = frame.state.get("venue_states")
    if not isinstance(aggregate, Mapping) or not isinstance(venues, Mapping):
        raise ResearchContractError("representation state is malformed")

    imbalances = []
    spreads = []
    depths = []
    for state in venues.values():
        if not isinstance(state, Mapping):
            continue
        book = state.get("book")
        if not isinstance(book, Mapping) or book.get("status") != "QUALIFIED":
            continue
        spread = _decimal(book.get("spread_bps"))
        if spread is not None:
            spreads.append(spread)
        band = (book.get("depth_bands_bps") or {}).get("10") if isinstance(book.get("depth_bands_bps"), Mapping) else None
        if isinstance(band, Mapping):
            imbalance = _decimal(band.get("quote_notional_imbalance"))
            bid = _decimal(band.get("bid_quote_notional"))
            ask = _decimal(band.get("ask_quote_notional"))
            if imbalance is not None:
                imbalances.append(imbalance)
            if bid is not None and ask is not None:
                depths.append(bid + ask)

    flow = aggregate.get("trade_flow") if isinstance(aggregate.get("trade_flow"), Mapping) else {}
    buy = _decimal(flow.get("reported_buy_quote_notional")) or 0.0
    sell = _decimal(flow.get("reported_sell_quote_notional")) or 0.0
    denominator = buy + sell
    flow_imbalance = 0.0 if denominator <= 0 else (buy - sell) / denominator

    features = {
        "aggregate.cross_venue_spread_bps": _decimal(aggregate.get("cross_venue_spread_bps")),
        "aggregate.venue_midpoint_dispersion_bps": _decimal(aggregate.get("venue_midpoint_dispersion_bps")),
        "aggregate.qualified_book_venue_count": int(aggregate.get("qualified_book_venue_count", 0) or 0),
        "aggregate.venue_count": int(aggregate.get("venue_count", 0) or 0),
        "microstructure.mean_venue_spread_bps": _mean(spreads),
        "microstructure.mean_depth10_quote_notional": _mean(depths),
        "microstructure.mean_depth10_imbalance": _mean(imbalances),
        "flow.reported_quote_notional_imbalance": flow_imbalance,
        "flow.trade_count": int(flow.get("trade_count", 0) or 0),
    }
    return {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "subject_id": frame.instrument.canonical_id,
        "cutoff_at_ns": int(frame.cutoff_at_ns),
        "known_at_ns": int(frame.known_at_ns),
        "features": features,
        "source_ref": {
            "artifact_type": "REPRESENTATION_FRAME",
            "artifact_id": frame.frame_id,
            "content_hash": frame.content_hash(),
            "known_at_ns": int(frame.known_at_ns),
            "role": "FEATURE",
        },
        "integrity": {
            "algorithm": "sha256",
            "content_hash": canonical_hash({
                "schema_version": FEATURE_SCHEMA_VERSION,
                "subject_id": frame.instrument.canonical_id,
                "cutoff_at_ns": int(frame.cutoff_at_ns),
                "known_at_ns": int(frame.known_at_ns),
                "features": features,
                "source_frame_hash": frame.content_hash(),
            }),
        },
    }


def extract_context_features(context: MarketContextFrame) -> Mapping[str, Any]:
    """Flatten stable Z9 context dimensions without interpreting them economically."""
    if context.status != "QUALIFIED":
        raise ResearchContractError("context feature extraction requires QUALIFIED context")
    state = context.state
    features: Dict[str, Any] = {}
    for key in (
        "regime",
        "volatility_state",
        "liquidity_state",
        "breadth_state",
        "basis_state",
        "correlation_state",
        "spot_confirmation_state",
        "futures_state",
        "microstructure_state",
        "freshness_state",
    ):
        if key in state:
            value = state.get(key)
            if isinstance(value, (str, int, float, bool)) or value is None:
                features["context.%s" % key] = value
    summary = state.get("summary")
    if isinstance(summary, Mapping):
        for key, value in summary.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                features["context.summary.%s" % key] = value
    return {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "cutoff_at_ns": int(context.cutoff_at_ns),
        "known_at_ns": int(context.known_at_ns),
        "features": dict(sorted(features.items())),
        "source_ref": {
            "artifact_type": "MARKET_CONTEXT_FRAME",
            "artifact_id": context.context_id,
            "content_hash": context.content_hash(),
            "known_at_ns": int(context.known_at_ns),
            "role": "FEATURE",
        },
    }


def build_training_row(
    *,
    row_id: str,
    question_ref: str,
    question_definition_hash: str,
    instrument_features: Mapping[str, Any],
    label: Any,
    label_artifact_id: str,
    label_content_hash: str,
    label_known_at_ns: int,
    context_features: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Any]:
    if not row_id or not label_artifact_id:
        raise ResearchContractError("training row identity is required")
    cutoff = int(instrument_features.get("cutoff_at_ns", -1))
    known = int(instrument_features.get("known_at_ns", -1))
    if cutoff < 0 or known < 0 or known > cutoff:
        raise ResearchContractError("instrument feature timing invalid")
    features = dict(instrument_features.get("features") or {})
    refs = [dict(instrument_features.get("source_ref") or {})]
    context: Dict[str, Any] = {}
    if context_features is not None:
        context_cutoff = int(context_features.get("cutoff_at_ns", -1))
        context_known = int(context_features.get("known_at_ns", -1))
        if context_cutoff != cutoff or context_known > cutoff:
            raise ResearchContractError("context features must be point-in-time aligned with instrument cutoff")
        context = dict(context_features.get("features") or {})
        refs.append(dict(context_features.get("source_ref") or {}))
    refs.append({
        "artifact_type": "QUESTION_OUTCOME",
        "artifact_id": str(label_artifact_id),
        "content_hash": str(label_content_hash),
        "known_at_ns": int(label_known_at_ns),
        "role": "LABEL",
    })
    return {
        "row_id": str(row_id),
        "question_ref": str(question_ref),
        "question_definition_hash": str(question_definition_hash).lower(),
        "subject_id": str(instrument_features.get("subject_id", "")),
        "cutoff_at_ns": cutoff,
        "feature_known_at_ns": max([known] + ([int(context_features.get("known_at_ns", -1))] if context_features is not None else [])),
        "label_known_at_ns": int(label_known_at_ns),
        "features": features,
        "context": context,
        "label": label,
        "source_refs": refs,
    }
