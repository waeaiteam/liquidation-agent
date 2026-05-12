from __future__ import annotations

import os
from uuid import uuid4

from strategy.models import Order, RiskDecision, Signal, StrategyConfig, utc_now


class PaperExecutionAdapter:
    def execute(self, signal: Signal, decision: RiskDecision, config: StrategyConfig) -> Order:
        qty = round(decision.notional_usd / max(signal.price, 1), 8)
        return Order(
            id=str(uuid4()),
            signal_id=signal.id,
            timestamp=utc_now(),
            mode="paper",
            exchange=config.exchange,
            symbol=signal.symbol,
            side=signal.side,
            action=signal.action,
            qty=qty,
            notional_usd=decision.notional_usd,
            entry_price=signal.price,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            leverage=decision.leverage,
            status="OPEN",
            reason="paper execution",
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
