from __future__ import annotations

import unittest

from autonomous_kernel.experience import (
    ContractConvention,
    EconomicAmountSemantics,
    NativeAmountKind,
    normalized_amounts_comparable,
    same_native_series_compatible,
)


RULE_A = "a" * 64
RULE_B = "b" * 64
RULE_C = "c" * 64


def _spot_base() -> EconomicAmountSemantics:
    return EconomicAmountSemantics(
        instrument_id="CRYPTO.SPOT.BTC-USD",
        market_type="SPOT",
        provider="SPOT_A",
        venue="SPOT_A",
        native_kind=NativeAmountKind.BASE_ASSET,
        native_unit="BTC",
    )


def _linear_contracts(provider: str = "DERIV_A", rule_hash: str = RULE_A) -> EconomicAmountSemantics:
    return EconomicAmountSemantics(
        instrument_id="CRYPTO.PERP.BTC-USD",
        market_type="PERPETUAL",
        provider=provider,
        venue=provider,
        native_kind=NativeAmountKind.CONTRACTS,
        native_unit="CONTRACT",
        contract_convention=ContractConvention.LINEAR_BASE,
        contract_multiplier="0.001",
        multiplier_unit="BTC_PER_CONTRACT",
        normalized_unit="USD_QUOTE_NOTIONAL",
        normalization_status="QUALIFIED",
        conversion_rule_id=f"{provider}-BTC-PERP-v1",
        conversion_rule_hash=rule_hash,
    )


def _inverse_contracts(provider: str = "DERIV_B", rule_hash: str = RULE_B) -> EconomicAmountSemantics:
    return EconomicAmountSemantics(
        instrument_id="CRYPTO.PERP.BTC-USD-INVERSE",
        market_type="PERPETUAL",
        provider=provider,
        venue=provider,
        native_kind=NativeAmountKind.CONTRACTS,
        native_unit="CONTRACT",
        contract_convention=ContractConvention.INVERSE_QUOTE,
        contract_multiplier="100",
        multiplier_unit="USD_PER_CONTRACT",
        normalized_unit="USD_QUOTE_NOTIONAL",
        normalization_status="QUALIFIED",
        conversion_rule_id=f"{provider}-BTC-INVERSE-v1",
        conversion_rule_hash=rule_hash,
    )


class EconomicAmountSemanticsTests(unittest.TestCase):
    def test_same_number_spot_btc_and_futures_contracts_are_not_comparable(self) -> None:
        spot = _spot_base()
        futures = EconomicAmountSemantics(
            instrument_id="CRYPTO.PERP.BTC-USD",
            market_type="PERPETUAL",
            provider="DERIV_A",
            venue="DERIV_A",
            native_kind=NativeAmountKind.CONTRACTS,
            native_unit="CONTRACT",
            contract_convention=ContractConvention.LINEAR_BASE,
            contract_multiplier="0.001",
            multiplier_unit="BTC_PER_CONTRACT",
        )
        self.assertFalse(normalized_amounts_comparable(spot, futures))
        self.assertFalse(same_native_series_compatible(spot, futures))

    def test_linear_and_inverse_contract_counts_are_not_same_native_series(self) -> None:
        linear = _linear_contracts()
        inverse = _inverse_contracts()
        self.assertFalse(same_native_series_compatible(linear, inverse))

    def test_different_exchange_contract_rules_break_native_series_even_for_same_symbol(self) -> None:
        first = EconomicAmountSemantics(
            instrument_id="CRYPTO.PERP.BTC-USD",
            market_type="PERPETUAL",
            provider="EXCHANGE_A",
            venue="EXCHANGE_A",
            native_kind=NativeAmountKind.CONTRACTS,
            native_unit="CONTRACT",
            contract_convention=ContractConvention.LINEAR_BASE,
            contract_multiplier="0.001",
            multiplier_unit="BTC_PER_CONTRACT",
        )
        second = EconomicAmountSemantics(
            instrument_id="CRYPTO.PERP.BTC-USD",
            market_type="PERPETUAL",
            provider="EXCHANGE_B",
            venue="EXCHANGE_B",
            native_kind=NativeAmountKind.CONTRACTS,
            native_unit="CONTRACT",
            contract_convention=ContractConvention.LINEAR_BASE,
            contract_multiplier="1",
            multiplier_unit="BTC_PER_CONTRACT",
        )
        self.assertFalse(same_native_series_compatible(first, second))

    def test_same_exchange_rule_is_compatible_for_within_series_change(self) -> None:
        first = EconomicAmountSemantics(
            instrument_id="CRYPTO.PERP.BTC-USD",
            market_type="PERPETUAL",
            provider="EXCHANGE_A",
            venue="EXCHANGE_A",
            native_kind=NativeAmountKind.PROVIDER_NATIVE,
            native_unit="OPEN_INTEREST_NATIVE",
            contract_convention=ContractConvention.PROVIDER_NATIVE,
        )
        second = EconomicAmountSemantics(
            instrument_id="CRYPTO.PERP.BTC-USD",
            market_type="PERPETUAL",
            provider="EXCHANGE_A",
            venue="EXCHANGE_A",
            native_kind=NativeAmountKind.PROVIDER_NATIVE,
            native_unit="OPEN_INTEREST_NATIVE",
            contract_convention=ContractConvention.PROVIDER_NATIVE,
        )
        self.assertTrue(same_native_series_compatible(first, second))

    def test_normalized_amounts_can_compare_only_after_both_have_proof_to_same_unit(self) -> None:
        linear = _linear_contracts(rule_hash=RULE_A)
        inverse = _inverse_contracts(rule_hash=RULE_B)
        self.assertTrue(normalized_amounts_comparable(linear, inverse))
        self.assertFalse(same_native_series_compatible(linear, inverse))

    def test_different_normalized_units_remain_air_gapped(self) -> None:
        quote_notional = _linear_contracts()
        base_equivalent = EconomicAmountSemantics(
            instrument_id="CRYPTO.PERP.BTC-USD",
            market_type="PERPETUAL",
            provider="DERIV_C",
            venue="DERIV_C",
            native_kind=NativeAmountKind.CONTRACTS,
            native_unit="CONTRACT",
            contract_convention=ContractConvention.LINEAR_BASE,
            contract_multiplier="0.01",
            multiplier_unit="BTC_PER_CONTRACT",
            normalized_unit="BTC_BASE_EQUIVALENT",
            normalization_status="QUALIFIED",
            conversion_rule_id="DERIV_C-BTC-PERP-v1",
            conversion_rule_hash=RULE_C,
        )
        self.assertFalse(normalized_amounts_comparable(quote_notional, base_equivalent))


if __name__ == "__main__":
    unittest.main()
