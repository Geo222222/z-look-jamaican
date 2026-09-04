"""Z1 canonical market-observation plane.

Raw provider evidence remains authoritative source material. This package
creates deterministic, provider-neutral observations for downstream market
representation, model, assembly, and learning systems.
"""

from .adapters import (
    ProviderRecord,
    adapt_binance_spot,
    adapt_coinbase_advanced_trade,
    adapt_kraken_v2,
)
from .contracts import CanonicalObservation, ObservationContractError
from .instruments import CanonicalInstrument, InstrumentRegistry, default_instrument_registry
from .public_sources import (
    PublicSourceCaptureError,
    PublicSourceSpec,
    RawPublicSourceJournal,
    binance_spot_source,
    canonicalize_public_record,
    capture_public_source_window,
    kraken_spot_source,
)
from .store import CanonicalBatchStore, validate_canonical_market_data_store

__all__ = [
    "CanonicalBatchStore",
    "CanonicalInstrument",
    "CanonicalObservation",
    "InstrumentRegistry",
    "ObservationContractError",
    "ProviderRecord",
    "PublicSourceCaptureError",
    "PublicSourceSpec",
    "RawPublicSourceJournal",
    "adapt_binance_spot",
    "adapt_coinbase_advanced_trade",
    "adapt_kraken_v2",
    "binance_spot_source",
    "canonicalize_public_record",
    "capture_public_source_window",
    "default_instrument_registry",
    "kraken_spot_source",
    "validate_canonical_market_data_store",
]
