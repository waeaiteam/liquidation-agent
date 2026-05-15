import unittest

from services.coinank import coinank_exchange


class CoinAnkClientTests(unittest.TestCase):
    def test_coinank_exchange_casing_matches_claw402_marketplace(self):
        self.assertEqual(coinank_exchange("binance"), "Binance")
        self.assertEqual(coinank_exchange("BINANCE"), "Binance")
        self.assertEqual(coinank_exchange("okx"), "OKX")
        self.assertEqual(coinank_exchange("Bybit"), "Bybit")
        self.assertEqual(coinank_exchange("custom"), "custom")


if __name__ == "__main__":
    unittest.main()
