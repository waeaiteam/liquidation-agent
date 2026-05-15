import unittest

import app as app_module
from services.heatmap_manager import HeatmapSnapshotManager
from strategy.models import StrategyConfig


class FakeLiquidationClient:
    def __init__(self):
        self.agg_calls = []
        self.liq_calls = []

    def agg_liq_map(self, base_coin=None, interval=None):
        self.agg_calls.append({"base_coin": base_coin, "interval": interval})
        return {
            "data": {
                "prices": [100, 101, 102],
                "Binance": [0, 10, 0],
                "Bybit": [0, 0, 20],
                "lastPrice": 101,
            }
        }

    def liq_map(self, **kwargs):
        self.liq_calls.append(kwargs)
        raise AssertionError("liq_map must not be called for aggregate maps")


class FakeCoinAnkClient:
    def __init__(self):
        self.coinank = type("CoinAnk", (), {})()
        self.coinank.liquidation = FakeLiquidationClient()


class FakeState:
    def __init__(self):
        self.snapshots = []
        self.costs = []
        self.raw_samples = []

    def latest_heatmap_snapshot(self, symbol, interval):
        for snap in self.snapshots:
            if snap.get("symbol") == symbol and snap.get("interval") == interval:
                return snap
        return None

    def heatmap_snapshot_age_seconds(self, snapshot):
        return 10**9 if snapshot is None else 0

    def can_spend_api_budget(self, *args, **kwargs):
        return True

    def record_claw402_raw_sample(self, kind, symbol, exchange, interval, raw):
        self.raw_samples.append((kind, symbol, exchange, interval, raw))

    def record_api_cost(self, kind, cost, symbol):
        self.costs.append((kind, cost, symbol))

    def record_heatmap_snapshot(self, snapshot, limit):
        self.snapshots.insert(0, snapshot)


class AggregateLiqMapTests(unittest.TestCase):
    def test_liqmap_endpoint_only_calls_aggregate_map(self):
        fake_client = FakeCoinAnkClient()
        original_create_client = app_module.create_client
        original_can_spend = app_module.agent_state.can_spend_api_budget
        original_record_raw = app_module.agent_state.record_claw402_raw_sample
        original_record_cost = app_module.agent_state.record_api_cost
        original_record_snapshot = app_module.agent_state.record_heatmap_snapshot
        original_status = app_module.agent_state.status
        original_add_event = app_module.agent_state.add_event
        app_module.create_client = lambda pk: (fake_client, "0xunit", "")
        app_module.agent_state.can_spend_api_budget = lambda *args, **kwargs: True
        app_module.agent_state.record_claw402_raw_sample = lambda *args, **kwargs: None
        app_module.agent_state.record_api_cost = lambda *args, **kwargs: None
        app_module.agent_state.record_heatmap_snapshot = lambda *args, **kwargs: None
        app_module.agent_state.status = lambda: {"heatmap": {"snapshots": []}}
        app_module.agent_state.add_event = lambda *args, **kwargs: None
        try:
            res = app_module.app.test_client().post(
                "/api/liqmap",
                json={"pk": "0xunit", "coin": "ETH", "symbol": "ETHUSDT", "exchange": "aggregate", "interval": "1d"},
            )
        finally:
            app_module.create_client = original_create_client
            app_module.agent_state.can_spend_api_budget = original_can_spend
            app_module.agent_state.record_claw402_raw_sample = original_record_raw
            app_module.agent_state.record_api_cost = original_record_cost
            app_module.agent_state.record_heatmap_snapshot = original_record_snapshot
            app_module.agent_state.status = original_status
            app_module.agent_state.add_event = original_add_event

        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(fake_client.coinank.liquidation.agg_calls, [{"base_coin": "ETH", "interval": "1d"}])
        self.assertEqual(fake_client.coinank.liquidation.liq_calls, [])
        self.assertEqual(body["snapshot"]["symbol"], "ETHUSDT")
        self.assertEqual(body["snapshot"]["interval"], "1d")
        self.assertEqual(body["snapshot"]["cost_usdc"], 0.001)
        self.assertNotIn("raw_liq_map", body)
        self.assertNotIn("leverage_error", body)
        self.assertNotIn("leverage_exchange", body["snapshot"])

    def test_heatmap_manager_uses_only_aggregate_map_and_one_cost(self):
        fake_client = FakeCoinAnkClient()
        fake_state = FakeState()
        config = StrategyConfig(coin="ETH", symbol="ETHUSDT", exchange="aggregate", interval="1d")

        result = HeatmapSnapshotManager().get_for_decision(fake_client, fake_state, config, price=101, force=True)

        self.assertTrue(result["usable"])
        self.assertEqual(fake_client.coinank.liquidation.agg_calls, [{"base_coin": "ETH", "interval": "1d"}])
        self.assertEqual(fake_client.coinank.liquidation.liq_calls, [])
        self.assertEqual(fake_state.costs[0], ("liq_map", 0.001, "ETHUSDT"))
        self.assertEqual(fake_state.snapshots[0]["symbol"], "ETHUSDT")
        self.assertNotIn("leverage_exchange", fake_state.snapshots[0])

    def test_latest_heatmap_snapshot_matches_symbol_and_interval(self):
        fake_state = FakeState()
        fake_state.snapshots = [
            {"symbol": "BTCUSDT", "interval": "1d"},
            {"symbol": "ETHUSDT", "interval": "12h"},
            {"symbol": "ETHUSDT", "interval": "1d"},
        ]

        self.assertEqual(fake_state.latest_heatmap_snapshot("ETHUSDT", "1d")["symbol"], "ETHUSDT")
        self.assertEqual(fake_state.latest_heatmap_snapshot("ETHUSDT", "1d")["interval"], "1d")
        self.assertIsNone(fake_state.latest_heatmap_snapshot("SOLUSDT", "1d"))


if __name__ == "__main__":
    unittest.main()
