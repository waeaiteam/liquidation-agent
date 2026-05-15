import os
import tempfile
import unittest
from unittest.mock import patch

from services.potential_scanner import PotentialStore
from services.square_publisher import SquarePublisher


class SquarePublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = PotentialStore(os.path.join(self.tmp.name, "potential.db"))
        self.publisher = SquarePublisher(self.store)
        signal_id = self.store.insert_signal({
            "symbol": "BTCUSDT",
            "potential_score": 80,
            "fr_current": -0.0002,
            "fr_previous": 0.0001,
            "fr_positive_periods": 2,
            "oi_change_pct": 12,
            "oi_segments": [1, 2, 3, 4],
            "oi_consecutive_rising": True,
            "volume_24h": 100000000,
            "price": 80000,
            "price_change_24h": 1,
        })
        content = (
            "BTC 出现 POTagent 潜力币观察信号，预测费率刚转负，OI 四段连续放大。"
            "这类结构代表合约资金正在重新定价，但仍需要观察成交量是否持续、价格是否突破信号高点，以及 BTC 是否配合。"
            "风险在于低流动性、假突破和大盘共振下跌。后续如果费率重新转正或 OI 均值开始下降，需要重新评估该信号。"
            "如果价格已经明显拉升，应该降低追单冲动，等待回踩或二次确认；如果成交量没有延续，也要把它当成观察信号而不是交易指令。"
            "AI建议仅供参考，不构成投资建议。#BTC #Crypto"
        )
        self.draft_id = self.store.create_publish_draft(signal_id, content)

    def tearDown(self):
        self.tmp.cleanup()

    def test_business_error_code_is_reported(self):
        self.assertEqual(
            self.publisher._business_error({"code": "220003", "message": "missing key"}),
            {"error": "API Key无效或未找到", "code": "220003"},
        )
        self.assertIsNone(self.publisher._business_error({"code": "000000", "data": {"postId": "1"}}))

    def test_publish_handles_square_business_error(self):
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"code":"20022","message":"bad word"}'

        with patch("services.square_publisher.urlopen", return_value=FakeResp()):
            result = self.publisher.publish(self.draft_id, "square-key")
        self.assertFalse(result["published"])
        self.assertEqual(result["code"], "20022")
        self.assertIn("敏感词", result["error"])

    def test_safety_blocks_url_and_sensitive_words(self):
        bad_id = self.store.create_publish_draft(
            1,
            "稳赚内容 https://example.com AI建议仅供参考，不构成投资建议。" * 8,
        )
        result = self.publisher.publish(bad_id, "square-key")
        self.assertFalse(result["published"])
        self.assertIn("content must not contain URLs", result["error"])
        self.assertIn("sensitive words", result["error"])


if __name__ == "__main__":
    unittest.main()
