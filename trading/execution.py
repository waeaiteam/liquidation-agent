from __future__ import annotations

import os
from uuid import uuid4

from strategy.models import Order, RiskDecision, Signal, StrategyConfig, utc_now
from trading.paper_broker import PaperBroker


class PaperExecutionAdapter:
    def __init__(self):
        self.broker = PaperBroker()

    def execute(self, signal: Signal, decision: RiskDecision, config: StrategyConfig) -> Order:
        self._sync_config(config)
        order = self.broker.open_position(
            symbol=signal.symbol,
            side=signal.side,
            price=signal.price,
            notional_usd=decision.notional_usd,
            leverage=decision.leverage,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            signal_id=signal.id,
        )
        return self._order_from_dict(order, config.exchange, signal.action)

    def mark_to_market(self, symbol: str, price: float):
        self.broker.mark_to_market(symbol, price)

    def load_orders(self, orders: list[dict]):
        if self.broker.orders or not orders:
            return
        self.broker.orders = [dict(order) for order in orders if order.get("mode") == "paper"]

    def _sync_config(self, config: StrategyConfig):
        if self.broker.account["seed_usd"] != float(config.paper_seed_usd):
            self.broker = PaperBroker(
                seed_usd=config.paper_seed_usd,
                fee_rate=config.paper_fee_rate,
                slippage_bps=config.paper_slippage_bps,
            )
        else:
            self.broker.fee_rate = config.paper_fee_rate
            self.broker.slippage_bps = config.paper_slippage_bps

    def _order_from_dict(self, data: dict, exchange: str, action: str) -> Order:
        return Order(
            id=data["id"],
            signal_id=data["signal_id"],
            timestamp=data["timestamp"],
            mode="paper",
            exchange=exchange,
            symbol=data["symbol"],
            side=data["side"],
            action=action,
            qty=data["qty"],
            notional_usd=data["notional_usd"],
            entry_price=data["entry_price"],
            stop_loss=data["stop_loss"],
            take_profit=data["take_profit"],
            leverage=data["leverage"],
            status=data["status"],
            exit_price=data.get("exit_price", 0),
            pnl_usd=data.get("pnl_usd", 0),
            reason=data.get("reason", ""),
        )


class BinanceExecutionAdapter:
    def execute(self, signal: Signal, decision: RiskDecision, config: StrategyConfig) -> Order:
        api_key = os.getenv("BINANCE_API_KEY")
        api_secret = os.getenv("BINANCE_API_SECRET")
        if not api_key or not api_secret:
            raise RuntimeError("BINANCE_API_KEY and BINANCE_API_SECRET are required for live mode")
        qty = round(decision.notional_usd / max(signal.price, 1), 8)
        return Order(
            id=str(uuid4()),
            signal_id=signal.id,
            timestamp=utc_now(),
            mode="live",
            exchange="binance",
            symbol=signal.symbol,
            side=signal.side,
            action=signal.action,
            qty=qty,
            notional_usd=decision.notional_usd,
            entry_price=signal.price,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            leverage=decision.leverage,
            status="REJECTED",
            reason="Binance live adapter is armed but real order placement is not implemented in this build",
        )
