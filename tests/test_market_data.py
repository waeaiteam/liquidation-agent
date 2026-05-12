import json
import unittest

from services.market_data import BinanceMarketDataService, MarketDataError


class MarketDataTests(unittest.TestCase):
    def test_binance_snapshot_uses_real_ticker_payload(self):
        calls = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                url = calls[-1]
                if "ticker/24hr" in url:
                    return json.dumps({
                        "symbol": "BTCUSDT",
                        "lastPrice": "82000.5",
                        "priceChangePercent": "2.4",
                        "volume": "1000",
                        "quoteVolume": "82000000",
                        "highPrice": "83000",
                        "lowPrice": "80000",
                    }).encode()
                if "premiumIndex" in url:
                    return json.dumps({"lastFundingRate": "0.0001", "markPrice": "82001"}).encode()
                if "openInterest" in url:
                    return json.dumps({"openInterest": "12345"}).encode()
                if "klines" in url:
                    return json.dumps([
                        [1, "80000", "81000", "79000", "80500", "10", 2, "805000", 1, "5", "400000", "0"],
                        [2, "80500", "82100", "80400", "82000.5", "20", 3, "1620000", 1, "9", "730000", "0"],
                    ]).encode()
                return b"{}"

        def fake_urlopen(req, timeout=10):
            calls.append(req.full_url)
            return FakeResponse()

        service = BinanceMarketDataService(urlopen_fn=fake_urlopen)
        snapshot = service.fetch_snapshot("BTCUSDT", interval="1m", limit=2)

        self.assertEqual(snapshot["source"], "binance_futures")
        self.assertEqual(snapshot["symbol"], "BTCUSDT")
        self.assertEqual(snapshot["price"], 82000.5)
        self.assertEqual(snapshot["funding_rate"], 0.0001)
        self.assertEqual(len(snapshot["klines"]), 2)

    def test_unsupported_exchange_is_explicit_error(self):
        with self.assertRaises(MarketDataError):
            BinanceMarketDataService().fetch_snapshot("BTCUSDT", exchange="okx")


if __name__ == "__main__":
    unittest.main()
