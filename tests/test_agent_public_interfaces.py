import unittest

import app as app_module


class AgentPublicInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_reports_endpoint_shape(self):
        report = {
            "timestamp": "unit",
            "final_action": "WAIT",
            "opportunity_score": {"score": 1, "passed": False},
            "blockers": ["unit"],
        }
        app_module.agent_state.record_decision_report(report)

        res = self.client.get("/api/agent/reports?limit=1")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(len(body["reports"]), 1)
        self.assertEqual(body["last"]["final_action"], "WAIT")

    def test_replay_is_deterministic_and_offline(self):
        payload = {
            "config": {
                "min_liquidation_usd": 100,
                "dominance_ratio": 1.1,
                "entry_mode": "fast",
                "use_heatmap_confirmation": False,
                "min_opportunity_score": 10,
            },
            "snapshots": [
                {
                    "price": 100,
                    "intervals": {"h1Long": 1000, "h1Short": 100, "h24Total": 2000},
                    "history": [{"longLiquidationUsd": 100, "shortLiquidationUsd": 50}],
                    "market": {"source": "unit"},
                }
            ],
        }

        res = self.client.post("/api/agent/replay", json=payload)
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["reports"][0]["safety_mode"], "observe")
        self.assertIn("opportunity_score", body["reports"][0])

    def test_diagnostics_endpoint_uses_status_shape(self):
        original_fetch = app_module.market_data_service.fetch_snapshot
        app_module.market_data_service.fetch_snapshot = lambda *args, **kwargs: {"price": 100, "source": "unit"}
        try:
            res = self.client.get("/api/agent/diagnostics")
        finally:
            app_module.market_data_service.fetch_snapshot = original_fetch

        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertIn(body["overall"], {"pass", "warn", "fail"})
        self.assertTrue(any(check["name"] == "worker_heartbeat" for check in body["checks"]))


if __name__ == "__main__":
    unittest.main()
