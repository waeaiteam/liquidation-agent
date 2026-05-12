import unittest

from trading.paper_broker import PaperBroker
from trading.execution import PaperExecutionAdapter


class PaperBrokerTests(unittest.TestCase):
    def test_opens_marks_and_closes_long_position(self):
        broker = PaperBroker(seed_usd=10_000, slippage_bps=0)

        order = broker.open_position(
            symbol="BTCUSDT",
            side="LONG",
            price=100.0,
            notional_usd=1000.0,
            leverage=2,
            stop_loss=95.0,
            take_profit=110.0,
            signal_id="sig-1",
        )
        self.assertEqual(order["status"], "OPEN")
        self.assertEqual(order["qty"], 10.0)

        broker.mark_to_market("BTCUSDT", 111.0)
        closed = broker.orders[0]

        self.assertEqual(closed["status"], "CLOSED")
        self.assertEqual(closed["exit_price"], 111.0)
        self.assertGreater(closed["pnl_usd"], 0)
        self.assertGreater(broker.account["equity_usd"], 10_000)

    def test_execution_adapter_restores_saved_paper_orders(self):
        adapter = PaperExecutionAdapter()
        adapter.load_orders([
            {
                "id": "order-1",
                "mode": "paper",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry_price": 100.0,
                "qty": 10.0,
                "notional_usd": 1000.0,
                "leverage": 2,
                "stop_loss": 95.0,
                "take_profit": 110.0,
                "status": "OPEN",
                "fee_open": 0.0,
                "timestamp": "2026-01-01T00:00:00Z",
            }
        ])

        adapter.mark_to_market("BTCUSDT", 111.0)

        self.assertEqual(adapter.broker.orders[0]["status"], "CLOSED")
        self.assertGreater(adapter.broker.orders[0]["exit_price"], 110.0)


if __name__ == "__main__":
    unittest.main()
