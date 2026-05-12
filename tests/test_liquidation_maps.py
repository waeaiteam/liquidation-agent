import unittest

from services.liquidation_maps import normalize_liquidation_map


class LiquidationMapTests(unittest.TestCase):
    def test_normalize_price_matrix_response(self):
        raw = {
            "data": {
                "prices": [81000, 82000, 83000],
                "long": [10, 20, 0],
                "short": [0, 15, 30],
            }
        }

        normalized = normalize_liquidation_map(raw, symbol="BTCUSDT", exchange="binance", interval="1h")

        self.assertTrue(normalized["has_data"])
        self.assertEqual(normalized["price_axis"], [81000.0, 82000.0, 83000.0])
        self.assertEqual(len(normalized["points"]), 4)
        self.assertEqual(normalized["max_value"], 30.0)

    def test_normalize_rejects_empty_or_unrecognized_map(self):
        normalized = normalize_liquidation_map({"data": {"unexpected": "shape"}}, symbol="BTCUSDT", exchange="binance", interval="1h")

        self.assertFalse(normalized["has_data"])
        self.assertIn("No liquidation price axis", normalized["error"])


if __name__ == "__main__":
    unittest.main()
