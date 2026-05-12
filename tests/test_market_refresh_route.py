import unittest

import app as app_module


class MarketRefreshRouteTests(unittest.TestCase):
    def setUp(self):
        self.old_service = app_module.market_data_service
        app_module.agent_state.update_config({"coin": "BTC", "symbol": "BTCUSDT", "exchange": "binance", "interval": "1m"})

    def tearDown(self):
        app_module.market_data_service = self.old_service
        app_module.agent_state.update_config({"coin": "BTC", "symbol": "BTCUSDT", "exchange": "binance", "interval": "1m"})

    def test_market_refresh_uses_requested_exchange_not_stale_config(self):
        class FakeMarketData:
            def fetch_snapshot(self, symbol, *, exchange, interval, limit=120):
                assert symbol == "BTCUSDT"
                assert exchange == "okx"
                assert interval == "1h"
                return {
                    "source": "okx_swap",
                    "exchange": "okx",
                    "symbol": "BTCUSDT",
                    "price": 80300.0,
                    "funding_rate": 0.0001,
                    "open_interest": 123.0,
                    "klines": [],
                }

        app_module.market_data_service = FakeMarketData()
        client = app_module.app.test_client()

        resp = client.post("/api/market/refresh", json={"symbol": "BTCUSDT", "exchange": "okx", "interval": "1h"})

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["market"]["exchange"], "okx")
        self.assertEqual(data["status"]["config"]["exchange"], "okx")


if __name__ == "__main__":
    unittest.main()
