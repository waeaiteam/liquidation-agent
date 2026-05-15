import unittest
import json
from pathlib import Path

from services.liquidation_maps import fuse_liquidation_maps, normalize_liquidation_map


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "claw402"


def load_fixture(name):
    with open(FIXTURE_DIR / name, "r", encoding="utf-8") as fh:
        return json.load(fh)


class LiquidationMapTests(unittest.TestCase):
    def test_normalize_price_matrix_response(self):
        raw = load_fixture("liq_map_matrix.json")

        normalized = normalize_liquidation_map(raw, symbol="BTCUSDT", exchange="binance", interval="1h")

        self.assertTrue(normalized["has_data"])
        self.assertEqual(normalized["price_axis"], [81000.0, 82000.0, 83000.0])
        self.assertEqual(len(normalized["points"]), 4)
        self.assertEqual(normalized["max_value"], 30.0)

    def test_normalize_rejects_empty_or_unrecognized_map(self):
        normalized = normalize_liquidation_map(load_fixture("empty.json"), symbol="BTCUSDT", exchange="binance", interval="1h")

        self.assertFalse(normalized["has_data"])
        self.assertIn("No liquidation price axis", normalized["error"])

    def test_normalize_rows_shape(self):
        normalized = normalize_liquidation_map(load_fixture("heat_map_rows.json"), symbol="BTCUSDT", exchange="binance", interval="1h")

        self.assertTrue(normalized["has_data"])
        self.assertEqual(normalized["shape"]["price_levels"], 3)
        self.assertEqual(normalized["max_value"], 1750000.0)
        self.assertGreaterEqual(len(normalized["clusters"]), 1)

    def test_normalize_price_axis_variant(self):
        normalized = normalize_liquidation_map(load_fixture("price_axis_variant.json"), symbol="BTCUSDT", exchange="binance", interval="1h")

        self.assertTrue(normalized["has_data"])
        self.assertEqual(normalized["price_axis"], [100.0, 101.0, 102.0])
        self.assertEqual(len(normalized["points"]), 3)

    def test_normalize_coinank_liq_heatmap_matrix(self):
        normalized = normalize_liquidation_map(load_fixture("liq_heatmap_matrix.json"), symbol="BTCUSDT", exchange="binance", interval="1m")

        self.assertTrue(normalized["has_data"])
        self.assertEqual(normalized["shape"]["time_count"], 4)
        self.assertEqual(normalized["shape"]["price_levels"], 4)
        self.assertEqual(normalized["max_liq_value"], 900.0)
        self.assertEqual(normalized["shape"]["matrix_cells"], 6)
        self.assertEqual(normalized["meta"]["orientation"], "sparse_time_price")
        self.assertTrue(all("time_index" in item and "price_index" in item for item in normalized["level_map"]))

    def test_normalize_nested_liq_heatmap_axes(self):
        raw = {
            "success": True,
            "code": "1",
            "data": {
                "tickSize": 25,
                "chartInterval": "5m",
                "start": 1762677300000,
                "liqHeatMap": {
                    "data": [["0", "1"], ["2", "0"]],
                    "chartTimeArray": [1762677300000, 1762677600000],
                    "priceArray": [97750, 97800],
                    "maxLiqValue": 102860717,
                },
                "end": 1762763700000,
            },
        }

        normalized = normalize_liquidation_map(raw, symbol="BTCUSDT", exchange="binance", interval="1d")

        self.assertTrue(normalized["has_data"])
        self.assertEqual(normalized["price_axis"], [97750.0, 97800.0])
        self.assertEqual(normalized["time_axis"], [1762677300000, 1762677600000])
        self.assertEqual(normalized["max_liq_value"], 102860717.0)

    def test_fuse_uses_agg_map_for_agent_and_heat_map_for_visual_levels(self):
        agg = normalize_liquidation_map({
            "data": {
                "prices": [100, 101, 102],
                "Binance": [0, 1000, 0],
                "Bybit": [0, 500, 200],
                "lastPrice": 101,
            }
        }, symbol="BTCUSDT", exchange="binance", interval="1h")
        visual = normalize_liquidation_map({
            "data": {
                "liqHeatMap": {
                    "data": [[0, 0, 10], [1, 2, 20]],
                    "chartTimeArray": [1, 2],
                    "priceArray": [99, 100, 101],
                    "maxLiqValue": 20,
                }
            }
        }, symbol="BTCUSDT", exchange="binance", interval="1h")

        fused = fuse_liquidation_maps(agg, visual)

        self.assertEqual(fused["source"], "claw402_coinank_fused")
        self.assertEqual(fused["price_axis"], [100.0, 101.0, 102.0])
        self.assertEqual(fused["time_axis"], [1, 2])
        self.assertEqual(len(fused["level_map"]), 2)
        self.assertGreaterEqual(len(fused["points"]), 2)

    def test_fuse_attaches_liq_map_leverage_points_without_heatmap(self):
        agg = normalize_liquidation_map({
            "data": {
                "prices": [100, 101, 102],
                "Binance": [0, 1000, 0],
                "Bybit": [0, 500, 200],
                "lastPrice": 101,
            }
        }, symbol="BTCUSDT", exchange="binance", interval="1h")
        leverage = normalize_liquidation_map({
            "data": {
                "prices": [100, 101, 102],
                "x5": [10, 0, 0],
                "x25": [0, 30, 0],
                "x100": [0, 0, 50],
                "lastPrice": 101,
            }
        }, symbol="BTCUSDT", exchange="binance", interval="1h")

        fused = fuse_liquidation_maps(agg, leverage_map=leverage)

        self.assertEqual(fused["source"], "claw402_coinank_fused")
        self.assertEqual(fused["meta"]["agent_source"], "claw402_coinank")
        self.assertEqual(fused["meta"]["leverage_source"], "claw402_coinank")
        self.assertEqual(fused["leverage_last_price"], 101.0)
        self.assertEqual(fused["shape"]["leverage_point_count"], 3)
        self.assertEqual(len(fused["leverage_points"]), 3)

    def test_leverage_series_are_bucketed_for_chart_colors(self):
        normalized = normalize_liquidation_map({
            "data": {
                "prices": [100, 101, 102, 103],
                "x30": [10, 0, 0, 0],
                "x40": [0, 20, 0, 0],
                "x90": [0, 0, 30, 0],
                "x100": [0, 0, 0, 40],
                "lastPrice": 101,
            }
        }, symbol="BTCUSDT", exchange="binance", interval="1h")

        buckets = {point["series"]: point["leverage_bucket"] for point in normalized["points"]}

        self.assertEqual(buckets["x30"], "x50")
        self.assertEqual(buckets["x40"], "x50")
        self.assertEqual(buckets["x90"], "x100")
        self.assertEqual(buckets["x100"], "x100")
        self.assertEqual(normalized["scope"], "exchange_leverage")


if __name__ == "__main__":
    unittest.main()
