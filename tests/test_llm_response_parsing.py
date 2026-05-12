import unittest

from urllib.request import Request

from services import llm


class LlmResponseParsingTests(unittest.TestCase):
    def test_openai_compatible_returns_plain_string_response(self):
        original = llm._request_json
        llm._request_json = lambda _req: "plain reply"
        try:
            result = llm._call_openai_compatible(
                "https://example.test/v1",
                "test-key",
                "test-model",
                {},
                "hello",
            )
        finally:
            llm._request_json = original

        self.assertEqual(result, "plain reply")

    def test_request_json_rejects_non_object_and_non_string_json(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return b"[1, 2, 3]"

        original = llm.urlopen
        llm.urlopen = lambda _req, timeout=60: FakeResponse()
        try:
            with self.assertRaisesRegex(RuntimeError, "Unexpected LLM provider JSON"):
                llm._request_json(Request("https://example.test"))
        finally:
            llm.urlopen = original


if __name__ == "__main__":
    unittest.main()
