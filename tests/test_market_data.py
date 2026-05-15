import json
import unittest
from io import BytesIO
from urllib.error import HTTPError

from services.market_data import BinanceMarketDataService, MarketDataError, MarketDataRestrictedError


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
            BinanceMarketDataService().fetch_snapshot("BTCUSDT", exchange="unknown")

    def test_okx_snapshot_uses_okx_swap_payloads(self):
        calls = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                url = calls[-1]
                if "market/ticker" in url:
                    return json.dumps({"code": "0", "data": [{
                        "last": "80279.9",
                        "open24h": "81630.2",
                        "high24h": "82100",
                        "low24h": "79779",
                        "volCcy24h": "79213.549",
                    }]}).encode()
                if "funding-rate" in url:
                    return json.dumps({"code": "0", "data": [{"fundingRate": "-0.000056"}]}).encode()
                if "open-interest" in url:
                    return json.dumps({"code": "0", "data": [{"oiCcy": "12000"}]}).encode()
                if "market/candles" in url:
                    return json.dumps({"code": "0", "data": [
                        ["2", "80500", "82100", "80400", "82000.5", "20", "1620000"],
                        ["1", "80000", "81000", "79000", "80500", "10", "805000"],
                    ]}).encode()
                return b"{}"

        def fake_urlopen(req, timeout=10):
            calls.append(req.full_url)
            return FakeResponse()

        service = BinanceMarketDataService(urlopen_fn=fake_urlopen)
        snapshot = service.fetch_snapshot("BTCUSDT", exchange="okx", interval="1H", limit=2)

        self.assertEqual(snapshot["source"], "okx_swap")
        self.assertEqual(snapshot["exchange"], "okx")
        self.assertEqual(snapshot["inst_id"], "BTC-USDT-SWAP")
        self.assertEqual(snapshot["price"], 80279.9)
        self.assertEqual(snapshot["funding_rate"], -0.000056)
        self.assertEqual(snapshot["open_interest"], 12000)
        self.assertEqual(len(snapshot["klines"]), 2)
        self.assertIn("bar=1H", calls[-1])

    def test_binance_451_is_classified_as_restricted_location(self):
        def fake_urlopen(req, timeout=10):
            raise HTTPError(
                req.full_url,
                451,
                "Unavailable For Legal Reasons",
                hdrs=None,
                fp=BytesIO(b'{"msg":"Service unavailable from a restricted location"}'),
            )

        service = BinanceMarketDataService(urlopen_fn=fake_urlopen)
        with self.assertRaises(MarketDataRestrictedError):
            service.fetch_snapshot("BTCUSDT", exchange="binance")

    def test_lists_binance_usdt_perpetual_symbols(self):
        calls = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return json.dumps({
                    "symbols": [
                        {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
                        {"symbol": "ETHUSDC", "baseAsset": "ETH", "quoteAsset": "USDC", "contractType": "PERPETUAL", "status": "TRADING"},
                        {"symbol": "OLDUSDT", "baseAsset": "OLD", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "BREAK"},
                        {"symbol": "SOLUSDT", "baseAsset": "SOL", "quoteAsset": "USDT", "contractType": "CURRENT_QUARTER", "status": "TRADING"},
                        {"symbol": "WIFUSDT", "baseAsset": "WIF", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
                    ]
                }).encode()

        def fake_urlopen(req, timeout=10):
            calls.append(req.full_url)
            return FakeResponse()

        symbols = BinanceMarketDataService(urlopen_fn=fake_urlopen).list_binance_usdt_perpetual_symbols()

        self.assertEqual([item["symbol"] for item in symbols], ["BTCUSDT", "WIFUSDT"])
        self.assertIn("/fapi/v1/exchangeInfo", calls[0])


if __name__ == "__main__":
    unittest.main()
