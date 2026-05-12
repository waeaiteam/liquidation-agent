from __future__ import annotations

from typing import Any

from trading.paper_broker import PaperBroker


def run_simple_backtest(
    klines: list[dict[str, Any]],
    *,
    symbol: str,
    seed_usd: float,
    notional_usd: float,
    leverage: int = 1,
    stop_loss_pct: float = 0.01,
    take_profit_pct: float = 0.02,
) -> dict[str, Any]:
    broker = PaperBroker(seed_usd=seed_usd, slippage_bps=0)
    closes = [_close(k) for k in klines]
    closes = [c for c in closes if c > 0]
    if len(closes) < 2:
        return {"summary": {"trades": 0, "total_pnl_usd": 0, "win_rate": 0}, "orders": [], "equity_curve": []}

    position_open = False
    for idx, price in enumerate(closes):
        if not position_open and idx > 0:
            prev = closes[idx - 1]
            side = "LONG" if price >= prev else "SHORT"
            stop = price * (1 - stop_loss_pct) if side == "LONG" else price * (1 + stop_loss_pct)
            take = price * (1 + take_profit_pct) if side == "LONG" else price * (1 - take_profit_pct)
            broker.open_position(
                symbol=symbol,
                side=side,
                price=price,
                notional_usd=notional_usd,
                leverage=leverage,
                stop_loss=stop,
                take_profit=take,
                signal_id=f"backtest-{idx}",
            )
            position_open = True
        broker.mark_to_market(symbol, price)
        position_open = any(o.get("status") == "OPEN" for o in broker.orders)

    for order in list(broker.orders):
        if order.get("status") == "OPEN":
            broker.close_position(order["id"], closes[-1], "backtest final close")

    closed = [o for o in broker.orders if o.get("status") == "CLOSED"]
    wins = [o for o in closed if float(o.get("pnl_usd") or 0) > 0]
    total = round(sum(float(o.get("pnl_usd") or 0) for o in closed), 6)
    return {
        "summary": {
            "trades": len(closed),
            "total_pnl_usd": total,
            "win_rate": round(len(wins) / len(closed), 4) if closed else 0,
            "ending_equity_usd": broker.account["equity_usd"],
        },
        "orders": broker.orders,
        "fills": broker.fills,
        "equity_curve": broker.equity_curve,
    }


def _close(kline: dict[str, Any]) -> float:
    try:
        return float(kline.get("close") if isinstance(kline, dict) else 0)
    except (TypeError, ValueError):
        return 0.0
