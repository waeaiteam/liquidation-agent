import unittest

from services.x_sentiment import XSentimentService


class XSentimentRobustnessTests(unittest.TestCase):
    def test_extract_json_rejects_non_object_json(self):
        service = XSentimentService()

        result = service._extract_json('"plain reply"')

        self.assertEqual(result["sentiment"]["score"], 50)
        self.assertEqual(result["trending"], [])


if __name__ == "__main__":
    unittest.main()
