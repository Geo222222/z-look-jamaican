import unittest

from experiments.crypto_market_replay import Candle
from experiments.crypto_market_shadow import make_decision


class CryptoMarketShadowTests(unittest.TestCase):
    def test_decision_is_prospective(self):
        candles = [Candle(1_700_000_000 + i * 300, 99, 101, 100, 100 + i * 0.01, 100) for i in range(60)]
        observed_at = candles[-1].timestamp + 301
        decision = make_decision("BTC-USD", candles, observed_at)
        self.assertGreater(decision["actionable_at"], observed_at)

    def test_weekend_cannot_be_eligible(self):
        # 2023-11-18 was Saturday.
        start = 1_700_265_600
        candles = [Candle(start + i * 300, 99, 101, 100, 100 + i, 10000) for i in range(60)]
        decision = make_decision("BTC-USD", candles, candles[-1].timestamp + 301)
        self.assertFalse(decision["weekday"])
        self.assertEqual(decision["target_position"], 0)


if __name__ == "__main__":
    unittest.main()
