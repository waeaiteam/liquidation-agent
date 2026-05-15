from __future__ import annotations

from typing import Any

from services.paper_trading import SharedPaperTrading
from services.potential_scanner import PotentialStore
from trading.exchange_api import BinanceSignedClient


class UnifiedTradeExecutor:
    def __init__(self, store: PotentialStore):
        self.store = store
        self.paper = SharedPaperTrading(store)

    def execute_entry(self, *, mode: str, live_enabled: bool, signal: dict[str, Any], stop_price: float, api_key: str = "", api_secret: str = "") -> dict[str, Any]:
        mode = str(mode or "paper").lower()
        notional = float(signal.get("notional_usdt") or 100)
        if mode == "live":
            if not live_enabled:
                return {"status": "blocked", "reason": "live mode requires explicit live_enabled"}
            client = BinanceSignedClient(api_key, api_secret)
            health = client.health_check()
            if not health.get("ok"):
                return {"status": "blocked", "reason": "live exchange health check failed", "health": health}
            return client.create_order(
                symbol=str(signal.get("symbol") or "").upper(),
                side="BUY",
                type="MARKET",
                quoteOrderQty=round(notional, 2),
            )
        return self.paper.open_order(
            agent_type=str(signal.get("agent_type") or "pot"),
            symbol=str(signal.get("symbol") or ""),
            side=str(signal.get("side") or "long"),
            entry_price=float(signal.get("price") or 0),
            notional_usdt=notional,
            stop_price=float(stop_price or 0),
            signal_id=signal.get("signal_id"),
            trajectory_id=signal.get("trajectory_id"),
        )
