import unittest
from unittest.mock import patch

import app as app_module


class MultiAgentApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_agent_config_routes_keep_independent_configs(self):
        res = self.client.post("/api/agents/config", json={
            "agents": {
                "pot": {"provider": "custom", "api_key": "pot-key", "model": "pot-model", "base_url": "https://relay.example/v1"},
                "pub": {"provider": "openai", "api_key": "pub-key", "model": "pub-model"},
            }
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["agents"]["pot"]["has_api_key"])
        self.assertTrue(data["agents"]["pub"]["has_api_key"])
        self.assertEqual(data["agents"]["pot"]["model"], "pot-model")
        self.assertEqual(data["agents"]["pub"]["model"], "pub-model")

    def test_scanner_status_route(self):
        res = self.client.get("/api/scanner/status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("warmup_done", data)
        self.assertIn("in_memory_snapshot_symbols", data)


if __name__ == "__main__":
    unittest.main()
