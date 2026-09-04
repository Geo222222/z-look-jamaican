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
from .store import CanonicalBatchStore, validate_canonical_market_data_store

__all__ = [
    "CanonicalBatchStore",
    "CanonicalInstrument",
    "CanonicalObservation",
    "InstrumentRegistry",
    "ObservationContractError",
    "ProviderRecord",
    "adapt_binance_spot",
    "adapt_coinbase_advanced_trade",
    "adapt_kraken_v2",
    "default_instrument_registry",
    "validate_canonical_market_data_store",
]
