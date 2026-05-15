import tempfile
import unittest
import os

from services.potential_scanner import (
    BinancePublicClient,
    PotentialScanner,
    PotentialStore,
    ScannerConfig,
    funding_confirmed_positive,
    oi_segments,
)
import app as app_module


class FakeClient(BinancePublicClient):
    def __init__(self):
        self.premium_calls = 0

    def premium_index(self):
        self.premium_calls += 1
        rates = {"BTCUSDT": -0.0003, "ETHUSDT": 0.0002}
        return [{"symbol": k, "lastFundingRate": v} for k, v in rates.items()]

    def exchange_info(self):
        return ["BTCUSDT", "ETHUSDT"]

    def tickers_24hr(self):
        return {
            "BTCUSDT": {"symbol": "BTCUSDT", "quoteVolume": "100000000", "lastPrice": "80000", "priceChangePercent": "-2"},
            "ETHUSDT": {"symbol": "ETHUSDT", "quoteVolume": "100000000", "lastPrice": "3000", "priceChangePercent": "1"},
        }

    def funding_history(self, symbol, limit=8):
        return [{"fundingRate": "0.0001", "fundingTime": i} for i in range(limit)]

    def open_interest_hist(self, symbol, period="1h", limit=24):
        values = [100] * 6 + [110] * 6 + [120] * 6 + [130] * 6
        return [{"sumOpenInterestValue": str(v), "timestamp": i} for i, v in enumerate(values)]

    def spot_symbols(self):
        return {"BTC", "ETH"}

    def market_caps(self):
        return {"BTC": 1_000_000_000}

    def square_discussion(self, coin):
        return 100, 10000


class PotentialScannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = PotentialStore(os.path.join(self.tmp.name, "potential.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_funding_confirmed_positive(self):
        ok, count, recent = funding_confirmed_positive([{"fundingRate": "0.1"}, {"fundingRate": "0.2"}], 2)
        self.assertTrue(ok)
        self.assertEqual(count, 2)
        ok, _, _ = funding_confirmed_positive([{"fundingRate": "-0.1"}, {"fundingRate": "0.2"}], 2)
        self.assertFalse(ok)

    def test_oi_segments_require_four_rising_segments(self):
        values = [{"sumOpenInterestValue": str(v)} for v in ([100] * 6 + [110] * 6 + [120] * 6 + [130] * 6)]
        segs, change, rising = oi_segments(values)
        self.assertEqual(segs, [100, 110, 120, 130])
        self.assertAlmostEqual(change, 30)
        self.assertTrue(rising)
        bad = [{"sumOpenInterestValue": str(v)} for v in ([100] * 6 + [90] * 6 + [120] * 6 + [130] * 6)]
        _, _, rising = oi_segments(bad)
        self.assertFalse(rising)

    def test_watchlist_accumulates_confidence_before_signal(self):
        scanner = PotentialScanner(self.store, FakeClient())
        first = scanner.scan_once()
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["inserted"], 0)
        self.assertEqual(first["watch_candidates"], 1)
        watch = self.store.get_watchlist_item("BTCUSDT")
        self.assertIsNotNone(watch)
        self.assertEqual(watch["status"], "watching")

        self.store.update_scanner_config({"watch_confidence_threshold": 7})
        second = scanner.scan_once()
        self.assertEqual(second["inserted"], 1)
        self.assertEqual(second["signals"][0]["symbol"], "BTCUSDT")
        self.assertEqual(self.store.get_watchlist_item("BTCUSDT")["status"], "analyzing")
        third = scanner.scan_once()
        self.assertEqual(third["inserted"], 0)

    def test_scanner_config_exposes_risk_controls(self):
        cfg = ScannerConfig.from_dict({
            "max_concurrent_positions": 0,
            "paper_slippage_pct": 1,
            "entry_confirmation_mode": "unknown",
            "publish_mode": "bad",
            "watch_confidence_threshold": 500,
            "watch_timeout_ticks": 0,
        })
        self.assertEqual(cfg.max_concurrent_positions, 3)
        self.assertEqual(cfg.paper_slippage_pct, 0.05)
        self.assertEqual(cfg.entry_confirmation_mode, "aggressive")
        self.assertEqual(cfg.publish_mode, "manual")
        self.assertEqual(cfg.watch_confidence_threshold, 100)
        self.assertEqual(cfg.watch_timeout_ticks, 480)

    def test_pot_oi_declining_detects_recent_drop(self):
        class OiClient:
            def open_interest_hist(self, symbol, period="1h", limit=12):
                values = [120] * 6 + [90] * 6
                return [{"sumOpenInterestValue": str(v)} for v in values]
        old = app_module.potential_scanner.client
        try:
            app_module.potential_scanner.client = OiClient()
            self.assertTrue(app_module._pot_oi_declining("BTCUSDT"))
        finally:
            app_module.potential_scanner.client = old


if __name__ == "__main__":
    unittest.main()
