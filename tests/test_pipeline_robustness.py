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


if __name__ == "__main__":
    unittest.main()
