import tempfile
import unittest
from pathlib import Path

from experiments.crypto_market_replay import Candle, read_candles, replay, write_candles


class CryptoMarketReplayTests(unittest.TestCase):
    def candles(self, count=800):
        return [
            Candle(1_700_000_000 + i * 300, 99 + i * 0.01, 101 + i * 0.01, 100 + i * 0.01, 100.1 + i * 0.01, 10 + i % 7)
            for i in range(count)
        ]

    def test_gzip_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candles.csv.gz"
            write_candles(path, self.candles(5))
            self.assertEqual(read_candles(path), self.candles(5))

    def test_costs_cannot_improve_return(self):
        candles = self.candles()
        cheap = replay("BTC-USD", candles, "trend", 5.0)
        expensive = replay("BTC-USD", candles, "trend", 40.0)
        self.assertLessEqual(expensive["net_compounded_return"], cheap["net_compounded_return"])
        self.assertEqual(cheap["latency_candles"], 1)

    def test_requires_sufficient_history(self):
        with self.assertRaises(ValueError):
            replay("BTC-USD", self.candles(100), "trend", 20.0)


if __name__ == "__main__":
    unittest.main()
