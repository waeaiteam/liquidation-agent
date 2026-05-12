import unittest

from services.strategy_agent import _sanitize_review


class StrategyReviewRobustnessTests(unittest.TestCase):
    def test_sanitize_review_rejects_non_object_json(self):
        with self.assertRaisesRegex(ValueError, "LLM review JSON must be an object"):
            _sanitize_review("wait")


if __name__ == "__main__":
    unittest.main()
