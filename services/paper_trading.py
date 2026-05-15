from __future__ import annotations

import math
from typing import Any

from services.potential_scanner import PotentialStore, to_float, utc_iso


class SharedPaperTrading:
    def __init__(self, store: PotentialStore):
        self.store = store

    def open_order(
        self,
        *,
        agent_type: str,
        symbol: str,
        side: str,
        entry_price: float,
        notional_usdt: float,
        stop_price: float,
        signal_id: int | None = None,
        trajectory_id: str | None = None,
        slippage_pct: float = 0.0,
    ) -> dict[str, Any]:
        requested_entry = float(entry_price)
        side = side.lower()
        slippage_pct = max(0.0, float(slippage_pct or 0.0))
        entry_price = requested_entry * (1 + slippage_pct) if side == "long" else requested_entry * (1 - slippage_pct)
        notional_usdt = float(notional_usdt)
        qty = notional_usdt / max(entry_price, 1e-12)
        now = utc_iso()
        with self.store._lock, self.store.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO paper_balance(agent_type, initial_balance, current_balance, updated_at) VALUES (?, 10000, 10000, ?)",
                (agent_type, now),
            )
            cur = conn.execute(
                """
                INSERT INTO paper_orders(agent_type, symbol, side, entry_price, entry_time, quantity, status, stop_price, trailing_stop, notional_usdt, remaining_pct, signal_id, trajectory_id)
                VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, 100, ?, ?)
                """,
                (agent_type, symbol.upper(), side, entry_price, now, qty, stop_price, stop_price, notional_usdt, signal_id, trajectory_id),
            )
            order_id = int(cur.lastrowid)
        order = self.get_order(order_id)
        self.store.log_trajectory(
            agent_type,
            "entry",
            input_data={"signal_id": signal_id, "requested_entry": requested_entry, "slippage_pct": slippage_pct},
            decision=order,
            session_id=trajectory_id or str(signal_id or order_id),
        )
        return order

    def get_order(self, order_id: int) -> dict[str, Any]:
        with self.store._lock, self.store.connect() as conn:
            row = conn.execute("SELECT * FROM paper_orders WHERE id = ?", (int(order_id),)).fetchone()
        if not row:
            raise ValueError("paper order not found")
        return dict(row)

    def open_orders(self, agent_type: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM paper_orders WHERE status IN ('open', 'partial_close')"
        params = []
        if agent_type:
            sql += " AND agent_type = ?"
            params.append(agent_type)
        sql += " ORDER BY entry_time DESC"
        with self.store._lock, self.store.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def list_orders(self, agent_type: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM paper_orders"
        params = []
        if agent_type:
            sql += " WHERE agent_type = ?"
            params.append(agent_type)
        sql += " ORDER BY COALESCE(exit_time, entry_time) DESC LIMIT ?"
        params.append(max(1, min(int(limit or 200), 1000)))
        with self.store._lock, self.store.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def update_mark(self, order_id: int, price: float, trailing_stop: float | None = None) -> dict[str, Any]:
        order = self.get_order(order_id)
        pnl_pct, pnl_usdt = paper_pnl(order, price)
        max_profit = max(to_float(order.get("max_profit_pct")), pnl_pct)
        max_dd = min(to_float(order.get("max_drawdown_pct")), pnl_pct)
        with self.store._lock, self.store.connect() as conn:
            conn.execute(
                """
                UPDATE paper_orders
                SET max_profit_pct = ?, max_drawdown_pct = ?, trailing_stop = COALESCE(?, trailing_stop)
                WHERE id = ?
                """,
                (max_profit, max_dd, trailing_stop, int(order_id)),
            )
        return self.get_order(order_id)

    def close_order(self, order_id: int, price: float, reason: str, reduce_pct: float = 100.0) -> dict[str, Any]:
        order = self.get_order(order_id)
        if order.get("status") == "closed":
            return order
        reduce_pct = max(0.0, min(100.0, float(reduce_pct or 100)))
        remaining = max(0.0, float(order.get("remaining_pct") or 100) - reduce_pct)
        pnl_pct, pnl_usdt = paper_pnl(order, price, pct=reduce_pct)
        status = "closed" if remaining <= 0 else "partial_close"
        now = utc_iso() if status == "closed" else order.get("exit_time")
        with self.store._lock, self.store.connect() as conn:
            conn.execute(
                """
                UPDATE paper_orders
                SET status = ?, exit_price = ?, exit_time = ?, pnl_pct = COALESCE(pnl_pct, 0) + ?, pnl_usdt = COALESCE(pnl_usdt, 0) + ?,
                    exit_reason = ?, remaining_pct = ?
                WHERE id = ?
                """,
                (status, price, now, pnl_pct, pnl_usdt, reason, remaining, int(order_id)),
            )
            self._recompute_balance(conn, str(order.get("agent_type") or "pot"))
        closed = self.get_order(order_id)
        self.store.log_trajectory(order.get("agent_type") or "pot", "exit" if status == "closed" else "reduce", input_data=order, decision={"price": price, "reason": reason, "reduce_pct": reduce_pct}, outcome=closed, session_id=str(order.get("trajectory_id") or order_id))
        return closed

    def stats(self, agent_type: str | None = None) -> dict[str, Any]:
        orders = self.list_orders(agent_type=agent_type, limit=1000)
        closed = [o for o in orders if o.get("status") == "closed"]
        total = len(closed)
        wins = sum(1 for o in closed if to_float(o.get("pnl_usdt")) > 0)
        losses = [abs(to_float(o.get("pnl_usdt"))) for o in closed if to_float(o.get("pnl_usdt")) < 0]
        gains = [to_float(o.get("pnl_usdt")) for o in closed if to_float(o.get("pnl_usdt")) > 0]
        profit_factor = round(sum(gains) / max(sum(losses), 1e-9), 3) if closed else 0
        total_pnl = round(sum(to_float(o.get("pnl_usdt")) for o in closed), 4)
        max_dd = min([to_float(o.get("max_drawdown_pct")) for o in orders] or [0])
        return {
            "total_trades": total,
            "win_trades": wins,
            "win_rate": round(wins / total * 100, 2) if total else 0,
            "profit_factor": profit_factor,
            "total_pnl": total_pnl,
            "max_drawdown": round(max_dd, 2),
            "live_ready": bool(total >= 20 and wins / max(total, 1) > 0.55 and profit_factor > 1.5 and abs(max_dd) < 15),
            "open_orders": len([o for o in orders if o.get("status") in {"open", "partial_close"}]),
        }

    def equity_curve(self, agent_type: str | None = None) -> list[dict[str, Any]]:
        orders = [o for o in reversed(self.list_orders(agent_type=agent_type, limit=1000)) if o.get("status") == "closed"]
        equity = 10000.0
        curve = [{"timestamp": None, "equity": equity}]
        for order in orders:
            equity += to_float(order.get("pnl_usdt"))
            curve.append({"timestamp": order.get("exit_time"), "equity": round(equity, 4), "pnl": to_float(order.get("pnl_usdt"))})
        return curve

    def _recompute_balance(self, conn, agent_type: str) -> None:
        rows = conn.execute("SELECT * FROM paper_orders WHERE agent_type = ? AND status = 'closed'", (agent_type,)).fetchall()
        total_pnl = sum(to_float(row["pnl_usdt"]) for row in rows)
        total = len(rows)
        wins = sum(1 for row in rows if to_float(row["pnl_usdt"]) > 0)
        max_dd = min([to_float(row["max_drawdown_pct"]) for row in rows] or [0])
        conn.execute(
            """
            INSERT INTO paper_balance(agent_type, initial_balance, current_balance, total_trades, win_trades, total_pnl, max_drawdown, updated_at)
            VALUES (?, 10000, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_type) DO UPDATE SET current_balance=excluded.current_balance, total_trades=excluded.total_trades,
                win_trades=excluded.win_trades, total_pnl=excluded.total_pnl, max_drawdown=excluded.max_drawdown, updated_at=excluded.updated_at
            """,
            (agent_type, 10000 + total_pnl, total, wins, total_pnl, max_dd, utc_iso()),
        )


def paper_pnl(order: dict[str, Any], price: float, pct: float = 100.0) -> tuple[float, float]:
    entry = to_float(order.get("entry_price"))
    qty = to_float(order.get("quantity")) * max(0.0, min(100.0, pct)) / 100.0
    if entry <= 0 or qty <= 0:
        return 0.0, 0.0
    side = str(order.get("side") or "long").lower()
    gross = (price - entry) * qty if side == "long" else (entry - price) * qty
    notional = entry * qty
    pnl_pct = gross / max(notional, 1e-9) * 100
    return round(pnl_pct, 4), round(gross, 6)
