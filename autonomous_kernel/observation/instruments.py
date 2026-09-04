from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


class InstrumentIdentityError(ValueError):
    pass


@dataclass(frozen=True)
class CanonicalInstrument:
    canonical_id: str
    asset_class: str
    market_type: str
    base_asset: str
    quote_asset: str
    settlement_asset: Optional[str] = None
    expiry: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("canonical_id", "asset_class", "market_type", "base_asset", "quote_asset"):
            if not str(getattr(self, name)).strip():
                raise InstrumentIdentityError("%s is required" % name)
        if self.market_type == "FUTURE" and not self.expiry:
            raise InstrumentIdentityError("dated futures require expiry")
        if self.market_type != "FUTURE" and self.expiry is not None:
            raise InstrumentIdentityError("expiry is only valid for dated futures")

    def to_wire(self) -> Dict[str, Optional[str]]:
        return {
            "canonical_id": self.canonical_id,
            "asset_class": self.asset_class,
            "market_type": self.market_type,
            "base_asset": self.base_asset,
            "quote_asset": self.quote_asset,
            "settlement_asset": self.settlement_asset,
            "expiry": self.expiry,
        }


class InstrumentRegistry:
    """Provider-symbol aliases mapped onto stable market identities.

    Provider symbols are deliberately not used as canonical identifiers. A
    later spot/futures or cross-venue representation can therefore join markets
    without guessing whether venue-specific symbols mean the same economic
    instrument. Quote assets remain part of identity: BTC-USD and BTC-USDT may
    share an economic root later, but are not interchangeable spot expressions
    unless an explicit quote-normalization contract earns that comparison.
    """

    def __init__(self) -> None:
        self._aliases: Dict[Tuple[str, str], CanonicalInstrument] = {}

    def register(self, provider: str, provider_symbol: str, instrument: CanonicalInstrument) -> None:
        key = (str(provider).strip().lower(), str(provider_symbol).strip().upper())
        if not key[0] or not key[1]:
            raise InstrumentIdentityError("provider and provider_symbol are required")
        existing = self._aliases.get(key)
        if existing is not None and existing != instrument:
            raise InstrumentIdentityError("provider symbol already maps to another canonical instrument")
        self._aliases[key] = instrument

    def resolve(self, provider: str, provider_symbol: str) -> CanonicalInstrument:
        key = (str(provider).strip().lower(), str(provider_symbol).strip().upper())
        try:
            return self._aliases[key]
        except KeyError as exc:
            raise InstrumentIdentityError(
                "unregistered provider instrument: %s %s" % (provider, provider_symbol)
            ) from exc


def default_instrument_registry() -> InstrumentRegistry:
    registry = InstrumentRegistry()
    btc_usd_spot = CanonicalInstrument(
        canonical_id="CRYPTO.SPOT.BTC-USD",
        asset_class="CRYPTO",
        market_type="SPOT",
        base_asset="BTC",
        quote_asset="USD",
        settlement_asset="USD",
    )
    eth_usd_spot = CanonicalInstrument(
        canonical_id="CRYPTO.SPOT.ETH-USD",
        asset_class="CRYPTO",
        market_type="SPOT",
        base_asset="ETH",
        quote_asset="USD",
        settlement_asset="USD",
    )
    btc_usdt_spot = CanonicalInstrument(
        canonical_id="CRYPTO.SPOT.BTC-USDT",
        asset_class="CRYPTO",
        market_type="SPOT",
        base_asset="BTC",
        quote_asset="USDT",
        settlement_asset="USDT",
    )
    eth_usdt_spot = CanonicalInstrument(
        canonical_id="CRYPTO.SPOT.ETH-USDT",
        asset_class="CRYPTO",
        market_type="SPOT",
        base_asset="ETH",
        quote_asset="USDT",
        settlement_asset="USDT",
    )
    for symbol in ("BTC-USD",):
        registry.register("coinbase_advanced_trade_public_websocket", symbol, btc_usd_spot)
    for symbol in ("ETH-USD",):
        registry.register("coinbase_advanced_trade_public_websocket", symbol, eth_usd_spot)
    for symbol in ("BTC/USD", "XBT/USD"):
        registry.register("kraken_websocket_v2", symbol, btc_usd_spot)
    for symbol in ("ETH/USD",):
        registry.register("kraken_websocket_v2", symbol, eth_usd_spot)
    registry.register("binance_spot_public_websocket", "BTCUSDT", btc_usdt_spot)
    registry.register("binance_spot_public_websocket", "ETHUSDT", eth_usdt_spot)
    return registry
