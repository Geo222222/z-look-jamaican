"""Venue-neutral, deterministic quality gate for market observations.

This module has no network, state, wallet, signer, or execution dependencies.
It classifies already-observed metadata and fails closed on missing or invalid
timestamp provenance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


VALID = "VALID"
DEGRADED = "DEGRADED"
STALE = "STALE"
UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class MarketDataQuality:
    status: str
    provider: str | None
    source_event_at: int | None
    received_at: int | None
    observed_at: int
    event_age_seconds: int | None
    transport_age_seconds: int | None
    action_permitted: bool
    reasons: tuple[str, ...]
    schema_version: int = 1

    def to_dict(self) -> Mapping[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


def classify_market_data(
    *,
    provider: str | None,
    source_event_at: int | None,
    received_at: int | None,
    observed_at: int,
    max_event_age_seconds: int,
    max_transport_age_seconds: int,
) -> MarketDataQuality:
    """Classify timestamp/provenance quality; only VALID permits an action."""
    reasons: list[str] = []
    provider_value = str(provider).strip() if provider is not None else ""
    if not provider_value:
        reasons.append("provider_unavailable")
    if source_event_at is None:
        reasons.append("source_event_timestamp_unavailable")
    if received_at is None:
        reasons.append("receive_timestamp_unavailable")
    if reasons:
        return MarketDataQuality(
            UNAVAILABLE, provider_value or None, source_event_at, received_at, int(observed_at),
            None, None, False, tuple(reasons)
        )

    source = int(source_event_at)
    received = int(received_at)
    observed = int(observed_at)
    event_age = observed - source
    transport_age = received - source
    if source > received:
        reasons.append("source_event_after_receive")
    if received > observed:
        reasons.append("receive_after_observation")
    if event_age < 0 or transport_age < 0:
        reasons.append("negative_timestamp_age")
    if reasons:
        return MarketDataQuality(
            UNAVAILABLE, provider_value, source, received, observed,
            event_age, transport_age, False, tuple(reasons)
        )
    if event_age > int(max_event_age_seconds):
        return MarketDataQuality(
            STALE, provider_value, source, received, observed,
            event_age, transport_age, False, ("event_age_limit_exceeded",)
        )
    if transport_age > int(max_transport_age_seconds):
        return MarketDataQuality(
            DEGRADED, provider_value, source, received, observed,
            event_age, transport_age, False, ("transport_age_limit_exceeded",)
        )
    return MarketDataQuality(
        VALID, provider_value, source, received, observed,
        event_age, transport_age, True, ()
    )
