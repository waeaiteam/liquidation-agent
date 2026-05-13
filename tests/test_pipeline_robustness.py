import unittest

from services import x_pipeline


class PipelineRobustnessTests(unittest.TestCase):
    def test_openai_compatible_accepts_plain_string_response(self):
        service = x_pipeline.TweetPipelineService()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return b'"plain reply"'

        original = x_pipeline.urlopen
        x_pipeline.urlopen = lambda _req, timeout=60: FakeResponse()
        try:
            result = service._call_openai_compatible(
                "https://example.test/v1",
                "test-key",
                "test-model",
                "hello",
            )
        finally:
            x_pipeline.urlopen = original

        self.assertEqual(result, "plain reply")

    def test_collect_context_ignores_non_dict_state_fields(self):
        class State:
            last_snapshot = "not a snapshot"
            last_signal = "not a signal"
            last_risk = "not risk"
            agent_phase = "SCANNING"

        service = x_pipeline.TweetPipelineService()
        context = service._collect_context(State(), None)

        self.assertEqual(context["market"], {})
        self.assertEqual(context["signal"]["action"], "wait")
        self.assertEqual(context["signal"]["risk_approved"], False)

    def test_collect_context_normalizes_list_market_fields(self):
        class State:
            last_snapshot = {
                "symbol": "BTCUSDT",
                "price": "82000",
                "funding": [{"rate": "0.0001"}],
                "oi": [{"openInterest": "12345"}],
                "market": {"change_24h_pct": "1.2", "volume_24h_quote": "999999"},
            }
            last_signal = {"action": "wait"}
            last_risk = {}
            agent_phase = "SCANNING"

        service = x_pipeline.TweetPipelineService()
        context = service._collect_context(State(), None)
        prompt = service._build_prompt_safe(context)

        self.assertEqual(context["market"]["funding_rate"], 0.0001)
        self.assertEqual(context["market"]["open_interest"], 12345.0)
        self.assertIn("持仓量", prompt)

    def test_fallback_image_cards_match_candidates(self):
        service = x_pipeline.TweetPipelineService()
        context = {
            "market": {"symbol": "BTCUSDT", "price": 82000},
            "signal": {"action": "wait"},
            "sentiment": {"score": 50, "label": "neutral"},
        }
        cards = service._fallback_image_cards(context, [{"text": "a"}, {"text": "b"}])

        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0]["metric_value"], "$82,000.00")
        self.assertIn("alt_text", cards[0])


if __name__ == "__main__":
    unittest.main()
