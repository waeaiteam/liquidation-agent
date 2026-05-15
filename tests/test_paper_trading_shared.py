import tempfile
import unittest
import os

from services.paper_trading import SharedPaperTrading
from services.potential_scanner import PotentialStore
from trading.position_manager import DynamicPositionRules


class SharedPaperTradingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = PotentialStore(os.path.join(self.tmp.name, "potential.db"))
        self.paper = SharedPaperTrading(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_paper_order_lifecycle(self):
        order = self.paper.open_order(
            agent_type="pot",
            symbol="BTCUSDT",
            side="long",
            entry_price=100,
            notional_usdt=1000,
            stop_price=90,
            signal_id=1,
        )
        self.assertEqual(order["status"], "open")
        self.paper.update_mark(order["id"], 120, trailing_stop=105)
        closed = self.paper.close_order(order["id"], 120, "manual", reduce_pct=100)
        self.assertEqual(closed["status"], "closed")
        self.assertGreater(closed["pnl_usdt"], 0)
        stats = self.paper.stats("pot")
        self.assertEqual(stats["total_trades"], 1)
        self.assertEqual(stats["win_rate"], 100)

    def test_trailing_stop_never_moves_down_for_long(self):
        rules = DynamicPositionRules()
        first = rules.trailing_stop(120, 5, 90, side="long")
        second = rules.trailing_stop(110, 5, first, side="long")
        self.assertGreaterEqual(second, first)

    def test_paper_long_entry_applies_slippage(self):
        order = self.paper.open_order(
            agent_type="pot",
            symbol="BTCUSDT",
            side="long",
            entry_price=100,
            notional_usdt=1000,
            stop_price=90,
            slippage_pct=0.003,
        )
        self.assertAlmostEqual(order["entry_price"], 100.3)

    def test_trailing_stop_tightens_as_profit_expands(self):
        rules = DynamicPositionRules()
        loose = rules.trailing_stop(102, 1, 90, side="long", entry_price=100)
        tight = rules.trailing_stop(104, 1, 90, side="long", entry_price=100)
        self.assertEqual(loose, 100)
        self.assertEqual(tight, 102.5)


if __name__ == "__main__":
    unittest.main()
