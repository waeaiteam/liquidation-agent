import unittest

from services.backtest import run_simple_backtest


class BacktestTests(unittest.TestCase):
    def test_backtest_uses_paper_broker_and_returns_equity_curve(self):
        klines = [
            {"close_time": 1, "close": 100},
            {"close_time": 2, "close": 102},
            {"close_time": 3, "close": 104},
            {"close_time": 4, "close": 106},
        ]

        result = run_simple_backtest(klines, symbol="BTCUSDT", seed_usd=10_000, notional_usd=1000)

        self.assertIn("summary", result)
        self.assertGreaterEqual(result["summary"]["trades"], 1)
        self.assertGreater(len(result["equity_curve"]), 0)


if __name__ == "__main__":
    unittest.main()
