from __future__ import annotations

from uuid import uuid4

from strategy.models import utc_now


class PaperBroker:
    def __init__(self, *, seed_usd: float = 100_000, fee_rate: float = 0.0004, slippage_bps: float = 1.0):
        self.account = {
            "seed_usd": float(seed_usd),
            "cash_usd": float(seed_usd),
            "equity_usd": float(seed_usd),
            "realized_pnl_usd": 0.0,
            "unrealized_pnl_usd": 0.0,
            "fees_usd": 0.0,
        }
        self.fee_rate = float(fee_rate)
        self.slippage_bps = float(slippage_bps)
        self.orders: list[dict] = []
        self.fills: list[dict] = []
        self.equity_curve: list[dict] = []

    def open_position(
        self,
        *,
        symbol: str,
        side: str,
        price: float,
        notional_usd: float,
        leverage: int,
        stop_loss: float,
        take_profit: float,
        signal_id: str,
    ) -> dict:
        side = side.upper()
        fill_price = self._apply_slippage(float(price), side, opening=True)
        qty = round(float(notional_usd) / max(fill_price, 1e-9), 8)
        fee = round(float(notional_usd) * self.fee_rate, 6)
        self.account["cash_usd"] -= fee
        self.account["fees_usd"] += fee
        order = {
            "id": str(uuid4()),
            "signal_id": signal_id,
            "timestamp": utc_now(),
            "mode": "paper",
            "exchange": "paper",
            "symbol": symbol.upper(),
            "side": side,
            "action": "BUY" if side == "LONG" else "SELL",
            "qty": qty,
            "notional_usd": round(float(notional_usd), 6),
            "entry_price": round(fill_price, 8),
            "stop_loss": float(stop_loss or 0),
            "take_profit": float(take_profit or 0),
            "leverage": int(leverage),
            "status": "OPEN",
            "exit_price": 0,
            "pnl_usd": 0,
            "unrealized_pnl_usd": 0,
            "fee_usd": fee,
            "reason": "paper market fill",
        }
        self.orders.insert(0, order)
        self.fills.insert(0, self._fill(order, fill_price, qty, fee, "OPEN"))
        self._record_equity()
        return order

    def mark_to_market(self, symbol: str, price: float):
        symbol = symbol.upper()
        price = float(price)
        unrealized = 0.0
        for order in self.orders:
            if order.get("symbol") != symbol or order.get("status") != "OPEN":
                continue
            pnl = self._pnl(order, price)
            order["unrealized_pnl_usd"] = round(pnl, 6)
            should_close = self._should_close(order, price)
            if should_close:
                self.close_position(order["id"], price, should_close)
            else:
                unrealized += pnl
        self.account["unrealized_pnl_usd"] = round(unrealized, 6)
        self.account["equity_usd"] = round(self.account["cash_usd"] + self.account["realized_pnl_usd"] + unrealized, 6)
        self._record_equity()

    def close_position(self, order_id: str, price: float, reason: str = "manual close") -> dict:
        order = next((o for o in self.orders if o.get("id") == order_id), None)
        if not order or order.get("status") != "OPEN":
            raise ValueError("open paper order not found")
        fill_price = self._apply_slippage(float(price), order["side"], opening=False)
        gross = self._pnl(order, fill_price)
        fee = round(float(order["notional_usd"]) * self.fee_rate, 6)
        net = round(gross - fee, 6)
        self.account["cash_usd"] -= fee
        self.account["fees_usd"] += fee
        self.account["realized_pnl_usd"] += net
        order.update({
            "status": "CLOSED",
            "exit_price": round(fill_price, 8),
            "pnl_usd": net,
            "unrealized_pnl_usd": 0,
            "close_fee_usd": fee,
            "closed_at": utc_now(),
            "reason": reason,
        })
        self.fills.insert(0, self._fill(order, fill_price, order["qty"], fee, "CLOSE"))
        self._record_equity()
        return order

    def _pnl(self, order: dict, price: float) -> float:
        qty = float(order.get("qty") or 0)
        entry = float(order.get("entry_price") or 0)
        if order.get("side") == "LONG":
            return (price - entry) * qty
        return (entry - price) * qty

    def _should_close(self, order: dict, price: float) -> str:
        side = order.get("side")
        stop = float(order.get("stop_loss") or 0)
        take = float(order.get("take_profit") or 0)
        if side == "LONG":
            if stop and price <= stop:
                return "paper stop loss hit"
            if take and price >= take:
                return "paper take profit hit"
        if side == "SHORT":
            if stop and price >= stop:
                return "paper stop loss hit"
            if take and price <= take:
                return "paper take profit hit"
        return ""

    def _apply_slippage(self, price: float, side: str, *, opening: bool) -> float:
        bps = self.slippage_bps / 10_000
        adverse = (side == "LONG" and opening) or (side == "SHORT" and not opening)
        return price * (1 + bps if adverse else 1 - bps)

    def _fill(self, order: dict, price: float, qty: float, fee: float, kind: str) -> dict:
        return {
            "id": str(uuid4()),
            "order_id": order["id"],
            "timestamp": utc_now(),
            "symbol": order["symbol"],
            "side": order["side"],
            "kind": kind,
            "price": round(price, 8),
            "qty": qty,
            "fee_usd": fee,
        }

    def _record_equity(self):
        self.equity_curve.insert(0, {
            "timestamp": utc_now(),
            "equity_usd": round(self.account["equity_usd"], 6),
            "cash_usd": round(self.account["cash_usd"], 6),
            "realized_pnl_usd": round(self.account["realized_pnl_usd"], 6),
            "unrealized_pnl_usd": round(self.account["unrealized_pnl_usd"], 6),
        })
        self.equity_curve = self.equity_curve[:2000]
