import unittest

from strategy.models import StrategyConfig


class StrategyConfigLlmSettingsTests(unittest.TestCase):
    def test_main_llm_settings_are_preserved(self):
        config = StrategyConfig.from_dict(
            {
                "llm_provider": "OpenRouter",
                "llm_model": "anthropic/claude-sonnet-4",
                "llm_base_url": "https://openrouter.ai/api/v1",
                "llm_context_length": 200000,
            }
        )

        self.assertEqual(config.llm_provider, "openrouter")
        self.assertEqual(config.llm_model, "anthropic/claude-sonnet-4")
        self.assertEqual(config.llm_base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(config.llm_context_length, 200000)


if __name__ == "__main__":
    unittest.main()
