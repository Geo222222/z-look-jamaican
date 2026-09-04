import io
import zipfile
import unittest

from experiments.historical_mechanisms import (
    AlignedBar,
    FAMILIES,
    Kline,
    Trade,
    _breakout_flags,
    _inspect_zip,
    _trade_return,
    align_market_rows,
    funding_between,
    funding_prefix,
    mechanism_signal,
    normalize_timestamp_ms,
    summarize_trades,
)


class HistoricalMechanismTests(unittest.TestCase):
    def test_normalizes_millisecond_and_microsecond_timestamps(self):
        self.assertEqual(normalize_timestamp_ms("1782864000000"), 1782864000000)
        self.assertEqual(normalize_timestamp_ms("1782864000000000"), 1782864000000)
        with self.assertRaises(ValueError):
            normalize_timestamp_ms("123")

    def test_breakout_uses_only_prior_lookback_values(self):
        closes = [1.0, 2.0, 3.0, 2.5, 4.0]
        self.assertEqual(_breakout_flags(closes, lookback=3), [False, False, False, False, True])

    def test_alignment_rejects_same_exchange_timestamp_mismatch(self):
        spot = [Kline(1_700_000_000_000, 10, 11, 9, 10, 100, 2, 60)]
        perpetual = [Kline(1_700_000_300_000, 10, 11, 9, 10, 100, 2, 60)]
        premium = [Kline(1_700_000_000_000, 0.1, 0.2, 0.0, 0.1, 0, 1, 0)]
        with self.assertRaisesRegex(ValueError, "timestamp mismatch"):
            align_market_rows(spot, perpetual, premium)

    def test_archive_rejects_unexpected_or_traversing_member(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr("../BTCUSDT.csv", "1782864000000,1\n")
        with self.assertRaises(ValueError):
            _inspect_zip(raw.getvalue(), "BTCUSDT.csv")

    def test_confirmed_flow_requires_both_market_legs(self):
        row = AlignedBar(
            1_700_000_000_000,
            100,
            101,
            0.01,
            0.8,
            1_000,
            100,
            101,
            0.01,
            0.7,
            2_000,
            0.001,
            0.0,
            0.0,
            False,
            False,
        )
        thresholds = {
            "spot_imbalance_q80": 0.5,
            "perpetual_imbalance_q80": 0.5,
            "spot_volume_q50": 500,
            "perpetual_volume_q50": 500,
        }
        self.assertTrue(mechanism_signal("CONFIRMED-FLOW-CONTINUATION-30M", row, thresholds))
        self.assertFalse(
            mechanism_signal(
                "CONFIRMED-FLOW-CONTINUATION-30M",
                row._replace(perpetual_imbalance=0.4),
                thresholds,
            )
        )

    def test_funding_is_strictly_after_entry_and_inclusive_of_exit(self):
        timestamps = [1000, 2000, 3000]
        prefix = funding_prefix([0.01, 0.02, 0.03])
        self.assertAlmostEqual(funding_between(timestamps, prefix, 1000, 3000), 0.05)
        self.assertAlmostEqual(funding_between(timestamps, prefix, 2000, 2000), 0.0)

    def test_perpetual_and_pair_returns_apply_funding_direction(self):
        entry = AlignedBar(1000, 100, 100, 0, 0, 1, 100, 100, 0, 0, 1, 0, 0, 0, False, False)
        exit_bar = entry._replace(timestamp_ms=2000, spot_open=102, perpetual_open=103)
        self.assertAlmostEqual(_trade_return("perpetual", entry, exit_bar, 0.001), 0.029)
        self.assertAlmostEqual(_trade_return("long_spot_short_perpetual", entry, exit_bar, 0.001), -0.009)

    def test_costs_charge_two_directional_and_four_paired_sides(self):
        directional = summarize_trades([Trade("FOLD-01", 1, 2, 0.01, 0)], ["FOLD-01"], "spot", 20)
        paired = summarize_trades(
            [Trade("FOLD-01", 1, 2, 0.01, 0)], ["FOLD-01"], "long_spot_short_perpetual", 20
        )
        self.assertAlmostEqual(directional["mean_net_return"], 0.006)
        self.assertAlmostEqual(paired["mean_net_return"], 0.002)
        self.assertEqual(directional["trading_sides"], 2)
        self.assertEqual(paired["trading_sides"], 4)

    def test_frozen_design_contains_fourteen_asset_target_streams(self):
        targets_per_asset = sum(len(specification["targets"]) for specification in FAMILIES.values())
        self.assertEqual(targets_per_asset * 2, 14)


if __name__ == "__main__":
    unittest.main()
