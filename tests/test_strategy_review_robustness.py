import unittest

from services import strategy_agent
from services.strategy_agent import _sanitize_review


class StrategyReviewRobustnessTests(unittest.TestCase):
    def test_sanitize_review_rejects_non_object_json(self):
        with self.assertRaisesRegex(ValueError, "LLM review JSON must be an object"):
            _sanitize_review("wait")

    def test_review_uses_dedicated_system_prompt(self):
        captured = {}

        def fake_call(base_url, api_key, model, payload, user_prompt):
            captured["payload"] = payload
            return '{"decision":"approve","confidence":0.8,"reason":"ok","notional_multiplier":1,"adjustments":{}}'

        original = strategy_agent._call_openai_compatible
        strategy_agent._call_openai_compatible = fake_call
        try:
            review = strategy_agent.review_trade_with_llm(
                "deepseek",
                "test-key",
                "deepseek-chat",
                {"signal": {"action": "BUY"}},
            )
        finally:
            strategy_agent._call_openai_compatible = original

        self.assertEqual(review["decision"], "approve")
        self.assertIn("system_override", captured["payload"])
        self.assertIn("清算反向短线交易 agent 的审查员", captured["payload"]["system_override"])


if __name__ == "__main__":
    unittest.main()
