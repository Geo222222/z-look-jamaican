from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..context.contracts import MarketContextFrame
from ..operations import canonical_hash
from .contracts import ExperienceTimescale


MARKET_WIDE_EXPERIENCE_SCHEMA_VERSION = "1.0"
MARKET_WIDE_STATUSES = {"QUALIFIED", "DEGRADED", "UNAVAILABLE"}
BUILDER_VERSION = "market-wide-experience-v1"


class MarketWideExperienceError(ValueError):
    pass


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise MarketWideExperienceError("market-wide numeric value is not decimal-compatible") from exc


def _text(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else format(value, "f")


def _trend(first: Optional[Decimal], last: Optional[Decimal], epsilon: Decimal) -> str:
    if first is None or last is None:
        return "UNAVAILABLE"
    delta = last - first
    if delta > epsilon:
        return "RISING"
    if delta < -epsilon:
        return "FALLING"
    return "STABLE"


def _metric(context: MarketContextFrame, field: str) -> Optional[Decimal]:
    market = context.state.get("market")
    if not isinstance(market, Mapping):
        return None
    return _decimal(market.get(field))


def _member_return(context: MarketContextFrame, instrument_id: str) -> Optional[Decimal]:
    members = context.state.get("members")
    if not isinstance(members, Mapping):
        return None
    member = members.get(instrument_id)
    if not isinstance(member, Mapping):
        return None
    return _decimal(member.get("latest_return_bps"))


def _leader(context: MarketContextFrame) -> Optional[str]:
    members = context.state.get("members")
    if not isinstance(members, Mapping):
        return None
    candidates = []
    for instrument_id in sorted(str(key) for key in members):
        value = _member_return(context, instrument_id)
        if value is not None:
            candidates.append((value, instrument_id))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[1]


def _laggard(context: MarketContextFrame) -> Optional[str]:
    members = context.state.get("members")
    if not isinstance(members, Mapping):
        return None
    candidates = []
    for instrument_id in sorted(str(key) for key in members):
        value = _member_return(context, instrument_id)
        if value is not None:
            candidates.append((value, instrument_id))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[1]


def _regime(context: MarketContextFrame, family: str) -> str:
    regimes = context.state.get("regimes")
    if not isinstance(regimes, Mapping):
        return "UNAVAILABLE"
    return str(regimes.get(family, "UNAVAILABLE"))


def _quality(context: MarketContextFrame, family: str) -> str:
    qualities = context.state.get("feature_quality")
    if not isinstance(qualities, Mapping):
        return "UNAVAILABLE"
    item = qualities.get(family)
    if not isinstance(item, Mapping):
        return "UNAVAILABLE"
    value = str(item.get("status", "UNAVAILABLE"))
    return value if value in MARKET_WIDE_STATUSES else "UNAVAILABLE"


def _aggregate_quality(contexts: Sequence[MarketContextFrame], family: str) -> str:
    statuses = tuple(_quality(item, family) for item in contexts)
    if statuses and all(item == "QUALIFIED" for item in statuses):
        return "QUALIFIED"
    if any(item in {"QUALIFIED", "DEGRADED"} for item in statuses):
        return "DEGRADED"
    return "UNAVAILABLE"


@dataclass(frozen=True)
class MarketWideExperienceState:
    market_wide_experience_id: str
    timescale: ExperienceTimescale
    window_start_ns: int
    cutoff_at_ns: int
    known_at_ns: int
    status: str
    builder_version: str
    source_context_ids: Tuple[str, ...]
    source_context_hashes: Tuple[str, ...]
    state: Mapping[str, Any]
    parameters: Mapping[str, Any]
    schema_version: str = MARKET_WIDE_EXPERIENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MARKET_WIDE_EXPERIENCE_SCHEMA_VERSION:
            raise MarketWideExperienceError("unsupported market-wide experience schema")
        if not self.market_wide_experience_id:
            raise MarketWideExperienceError("market_wide_experience_id is required")
        if self.window_start_ns < 0 or self.cutoff_at_ns < self.window_start_ns:
            raise MarketWideExperienceError("market-wide experience window is invalid")
        if self.known_at_ns < self.window_start_ns or self.known_at_ns > self.cutoff_at_ns:
            raise MarketWideExperienceError("market-wide experience known_at is invalid")
        if self.status not in MARKET_WIDE_STATUSES:
            raise MarketWideExperienceError("market-wide experience status is invalid")
        if not self.builder_version:
            raise MarketWideExperienceError("market-wide experience builder_version is required")
        if not self.source_context_ids or len(self.source_context_ids) != len(self.source_context_hashes):
            raise MarketWideExperienceError("market-wide experience source lineage must be non-empty and aligned")
        if len(set(self.source_context_ids)) != len(self.source_context_ids):
            raise MarketWideExperienceError("market-wide source context ids must be unique")
        for digest in self.source_context_hashes:
            if len(digest) != 64:
                raise MarketWideExperienceError("market-wide source context hash must be SHA-256 hex")
            try:
                int(digest, 16)
            except ValueError as exc:
                raise MarketWideExperienceError("market-wide source context hash must be hexadecimal") from exc
        if not isinstance(self.state, Mapping) or not isinstance(self.parameters, Mapping):
            raise MarketWideExperienceError("market-wide state and parameters must be mappings")

    def source_set_hash(self) -> str:
        return canonical_hash(
            {
                "context_ids": list(self.source_context_ids),
                "context_hashes": list(self.source_context_hashes),
            }
        )

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "market_wide_experience_id": self.market_wide_experience_id,
            "timescale": self.timescale.value,
            "window_start_ns": self.window_start_ns,
            "cutoff_at_ns": self.cutoff_at_ns,
            "known_at_ns": self.known_at_ns,
            "status": self.status,
            "builder_version": self.builder_version,
            "parameters": dict(self.parameters),
            "state": dict(self.state),
            "lineage": {
                "source_context_ids": list(self.source_context_ids),
                "source_context_hashes": list(self.source_context_hashes),
                "source_set_hash": self.source_set_hash(),
            },
            "truth_boundary": {
                "source_truth_owner": "Z9_MARKET_CONTEXT",
                "causality_claim": False,
                "capital_decision": False,
                "risk_authorization": False,
                "external_execution": False,
            },
        }

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        value = self.body()
        value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}
        return value

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "MarketWideExperienceState":
        lineage = value.get("lineage")
        if not isinstance(lineage, Mapping):
            raise MarketWideExperienceError("market-wide lineage is malformed")
        item = cls(
            schema_version=str(value.get("schema_version", "")),
            market_wide_experience_id=str(value.get("market_wide_experience_id", "")),
            timescale=ExperienceTimescale(str(value.get("timescale", ""))),
            window_start_ns=int(value.get("window_start_ns", -1)),
            cutoff_at_ns=int(value.get("cutoff_at_ns", -1)),
            known_at_ns=int(value.get("known_at_ns", -1)),
            status=str(value.get("status", "")),
            builder_version=str(value.get("builder_version", "")),
            source_context_ids=tuple(str(item) for item in lineage.get("source_context_ids", [])),
            source_context_hashes=tuple(str(item) for item in lineage.get("source_context_hashes", [])),
            state=value.get("state") if isinstance(value.get("state"), Mapping) else {},
            parameters=value.get("parameters") if isinstance(value.get("parameters"), Mapping) else {},
        )
        if lineage.get("source_set_hash") != item.source_set_hash():
            raise MarketWideExperienceError("market-wide source_set_hash mismatch")
        truth = value.get("truth_boundary")
        if not isinstance(truth, Mapping) or truth.get("causality_claim") is not False:
            raise MarketWideExperienceError("market-wide truth boundary is invalid")
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("content_hash") != item.content_hash():
            raise MarketWideExperienceError("market-wide experience content hash mismatch")
        return item


def build_market_wide_experience(
    contexts: Sequence[MarketContextFrame],
    *,
    timescale: ExperienceTimescale,
    window_start_ns: int,
    cutoff_at_ns: int,
    trend_epsilon: Any = "0",
    minimum_contexts: int = 3,
    builder_version: str = BUILDER_VERSION,
) -> MarketWideExperienceState:
    source = tuple(contexts)
    if not source:
        raise MarketWideExperienceError("market-wide experience requires Market Context history")
    if minimum_contexts <= 0:
        raise MarketWideExperienceError("minimum_contexts must be positive")
    if window_start_ns < 0 or cutoff_at_ns < window_start_ns:
        raise MarketWideExperienceError("market-wide experience window is invalid")
    if len({item.context_id for item in source}) != len(source):
        raise MarketWideExperienceError("duplicate Market Context id")
    epsilon = _decimal(trend_epsilon)
    if epsilon is None or epsilon < 0:
        raise MarketWideExperienceError("trend_epsilon must be non-negative")
    ordered = tuple(sorted(source, key=lambda item: (item.cutoff_at_ns, item.known_at_ns, item.context_id)))
    for context in ordered:
        if context.cutoff_at_ns < window_start_ns:
            raise MarketWideExperienceError("source context precedes market-wide experience window")
        if context.cutoff_at_ns > cutoff_at_ns or context.known_at_ns > cutoff_at_ns:
            raise MarketWideExperienceError("lookahead Market Context rejected")
    latest = ordered[-1]
    incomplete = latest.cutoff_at_ns < cutoff_at_ns or ordered[0].cutoff_at_ns > window_start_ns

    fields = (
        "aggregate_return_bps",
        "breadth_positive",
        "cross_sectional_return_dispersion_bps",
        "median_realized_volatility_bps",
        "median_spread_bps",
        "liquidity_concentration_hhi",
        "median_absolute_pairwise_correlation",
    )
    trajectories: Dict[str, Mapping[str, Any]] = {}
    for field in fields:
        series = tuple(_metric(item, field) for item in ordered)
        known = tuple(item for item in series if item is not None)
        first = next((item for item in series if item is not None), None)
        last = next((item for item in reversed(series) if item is not None), None)
        trajectories[field] = {
            "status": "QUALIFIED" if len(known) == len(series) else "DEGRADED" if known else "UNAVAILABLE",
            "first": _text(first),
            "last": _text(last),
            "delta": _text(None if first is None or last is None else last - first),
            "trend": _trend(first, last, epsilon),
            "sample_count": len(known),
        }

    regime_families = ("direction", "volatility", "liquidity", "correlation", "derivatives", "structure")
    regime_history: Dict[str, Mapping[str, Any]] = {}
    for family in regime_families:
        values = tuple(_regime(item, family) for item in ordered)
        transitions = sum(1 for left, right in zip(values[:-1], values[1:]) if left != right)
        regime_history[family] = {
            "current": values[-1],
            "transition_count": transitions,
            "observed_states": list(dict.fromkeys(values)),
        }

    leaders = tuple(_leader(item) for item in ordered)
    leaders_known = tuple(item for item in leaders if item is not None)
    leader_transitions = sum(1 for left, right in zip(leaders_known[:-1], leaders_known[1:]) if left != right)
    current_leader = _leader(latest)
    current_laggard = _laggard(latest)

    feature_quality = {
        family: _aggregate_quality(ordered, family)
        for family in ("CORE_MARKET", "CROSS_ASSET", "LIQUIDITY", "CORRELATION", "DERIVATIVES", "LEAD_LAG")
    }
    if all(item.status == "QUALIFIED" for item in ordered) and len(ordered) >= minimum_contexts and not incomplete:
        status = "QUALIFIED"
    elif any(item.status in {"QUALIFIED", "DEGRADED"} for item in ordered):
        status = "DEGRADED"
    else:
        status = "UNAVAILABLE"

    state = {
        "current": {
            "context_id": latest.context_id,
            "context_hash": latest.content_hash(),
            "market": dict(latest.state.get("market", {})) if isinstance(latest.state.get("market"), Mapping) else {},
            "regimes": dict(latest.state.get("regimes", {})) if isinstance(latest.state.get("regimes"), Mapping) else {},
        },
        "trajectory": trajectories,
        "regime_history": regime_history,
        "leadership": {
            "current_leader": current_leader,
            "current_laggard": current_laggard,
            "leader_transition_count": leader_transitions,
            "leader_history": list(leaders),
            "truth_class": "POINT_IN_TIME_CROSS_SECTIONAL_RETURN_LEADERSHIP_NOT_CAUSALITY",
        },
        "feature_quality": feature_quality,
        "coverage": {
            "context_count": len(ordered),
            "minimum_contexts": minimum_contexts,
            "window_complete": not incomplete,
        },
    }
    parameters = {
        "trend_epsilon": format(epsilon, "f"),
        "minimum_contexts": minimum_contexts,
        "lookahead_policy": "HARD_REJECT_CONTEXT_KNOWN_OR_CUTOFF_AFTER_EXPERIENCE_CUTOFF",
        "window_policy": "HARD_REJECT_CONTEXT_BEFORE_DECLARED_WINDOW",
        "trend_policy": "FIRST_TO_LAST_DELTA_WITH_EXPLICIT_EPSILON",
        "leadership_policy": "LATEST_CROSS_SECTIONAL_RETURN_RANK_NOT_CAUSALITY",
    }
    source_ids = tuple(item.context_id for item in ordered)
    source_hashes = tuple(item.content_hash() for item in ordered)
    material = {
        "timescale": timescale.value,
        "window_start_ns": window_start_ns,
        "cutoff_at_ns": cutoff_at_ns,
        "builder_version": builder_version,
        "source": list(zip(source_ids, source_hashes)),
        "parameters": parameters,
        "state": state,
    }
    return MarketWideExperienceState(
        market_wide_experience_id="MWEXP-%s" % canonical_hash(material)[:32],
        timescale=timescale,
        window_start_ns=window_start_ns,
        cutoff_at_ns=cutoff_at_ns,
        known_at_ns=max(item.known_at_ns for item in ordered),
        status=status,
        builder_version=builder_version,
        source_context_ids=source_ids,
        source_context_hashes=source_hashes,
        state=state,
        parameters=parameters,
    )
