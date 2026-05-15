import unittest
from unittest.mock import patch

from trading.exchange_api import BinanceSignedClient
from trading.executor import UnifiedTradeExecutor
from services.potential_scanner import PotentialStore
import tempfile
import os


class ExchangeExecutorTests(unittest.TestCase):
    def test_signed_request_uses_digest_hex_signature(self):
        client = BinanceSignedClient("k", "s")
        with patch("trading.exchange_api.urlopen") as mocked:
            mocked.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'
            client.signed_request("GET", "/fapi/v2/account", {"recvWindow": 5000})
        req = mocked.call_args.args[0]
        self.assertIn("signature=", req.full_url)

    def test_live_executor_blocks_when_health_check_fails(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        try:
            executor = UnifiedTradeExecutor(PotentialStore(os.path.join(tmp.name, "p.db")))
            with patch("trading.executor.BinanceSignedClient.health_check", return_value={"ok": False, "reason": "bad key"}):
                result = executor.execute_entry(
                    mode="live",
                    live_enabled=True,
                    signal={"symbol": "BTCUSDT", "price": 100, "notional_usdt": 10},
                    stop_price=90,
                    api_key="k",
                    api_secret="s",
                )
            self.assertEqual(result["status"], "blocked")
            self.assertIn("health", result)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
