from __future__ import annotations

from statistics import mean
from typing import Any

from strategy.models import StrategyConfig, utc_now


EVOLVABLE_PARAMS = {
    "min_liquidation_usd",
    "dominance_ratio",
    "min_heatmap_cluster_score",
    "max_heatmap_distance_pct",
    "stop_buffer_pct",
    "min_reward_risk",
    "max_stop_loss_pct",
    "max_take_profit_pct",
    "max_holding_minutes",
    "cooldown_seconds",
    "loss_pause_minutes",
}

BLOCKED_PARAMS = {
    "live_enabled",
    "max_leverage",
    "max_notional_usd",
    "daily_api_budget_usdc",
    "scheduled_liq_map_budget_usdc",
    "event_liq_map_budget_usdc",
}


class EvolutionEngine:
    def analyze(self, state, config: StrategyConfig, lookback: int = 50) -> dict[str, Any]:
        orders = [order for order in state.orders if order.get("mode") == "paper" and order.get("status") == "CLOSED"][:lookback]
        summary = self._summary(orders)
        recommendations = self._recommendations(summary, config, orders)
        return {
            "timestamp": utc_now(),
            "mode": "suggest_only",
            "lookback": lookback,
            "summary": summary,
            "recommendations": recommendations,
            "blocked_params": sorted(BLOCKED_PARAMS),
            "evolvable_params": sorted(EVOLVABLE_PARAMS),
        }

    def apply(self, config: StrategyConfig, recommendations: list[dict[str, Any]], selected: list[str] | None = None) -> dict[str, Any]:
        selected_set = set(selected or [])
        updates: dict[str, Any] = {}
        applied = []
        skipped = []
        for rec in recommendations or []:
            param = rec.get("param")
            if not param or param not in EVOLVABLE_PARAMS or param in BLOCKED_PARAMS:
                skipped.append({"param": param, "reason": "not evolvable"})
                continue
            if selected_set and param not in selected_set:
                skipped.append({"param": param, "reason": "not selected"})
                continue
            current = getattr(config, param, None)
            suggested = self._clamp(param, current, rec.get("suggested"))
            if suggested is None or suggested == current:
                skipped.append({"param": param, "reason": "no effective change"})
                continue
            updates[param] = suggested
            applied.append({"param": param, "current": current, "suggested": suggested, "reason": rec.get("reason", "")})
        return {"updates": updates, "applied": applied, "skipped": skipped}

    def _summary(self, orders: list[dict[str, Any]]) -> dict[str, Any]:
        pnls = [float(order.get("pnl_usd") or 0) for order in orders]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl < 0]
        time_stops = [order for order in orders if "time stop" in str(order.get("reason", ""))]
        stop_losses = [order for order in orders if "stop loss" in str(order.get("reason", ""))]
        take_profits = [order for order in orders if "take profit" in str(order.get("reason", ""))]
        return {
            "trades": len(orders),
            "win_rate": round(len(wins) / len(orders), 4) if orders else 0,
            "avg_pnl_usd": round(mean(pnls), 4) if pnls else 0,
            "total_pnl_usd": round(sum(pnls), 4),
            "avg_win_usd": round(mean(wins), 4) if wins else 0,
            "avg_loss_usd": round(mean(losses), 4) if losses else 0,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else (999 if wins else 0),
            "max_consecutive_losses": self._max_consecutive_losses(pnls),
            "time_stop_rate": round(len(time_stops) / len(orders), 4) if orders else 0,
            "stop_loss_rate": round(len(stop_losses) / len(orders), 4) if orders else 0,
            "take_profit_rate": round(len(take_profits) / len(orders), 4) if orders else 0,
        }

    def _recommendations(self, summary: dict[str, Any], config: StrategyConfig, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        recs = []
        trades = summary["trades"]
        if trades < 10:
            return [{"param": "none", "current": None, "suggested": None, "reason": "样本少于 10 笔，只建议继续 paper 收集数据", "confidence": 0.2}]
        if summary["time_stop_rate"] > 0.35:
            recs.append(self._rec("max_holding_minutes", config.max_holding_minutes, max(8, int(config.max_holding_minutes * 0.75)), "时间止损比例偏高，缩短持仓时间以减少回吐", 0.65))
        if summary["stop_loss_rate"] > 0.35:
            recs.append(self._rec("max_heatmap_distance_pct", config.max_heatmap_distance_pct, max(0.003, round(config.max_heatmap_distance_pct * 0.85, 4)), "止损比例偏高，收紧热力图距离要求，减少离关键清算区太远的反打", 0.68))
            recs.append(self._rec("min_heatmap_cluster_score", config.min_heatmap_cluster_score, round(config.min_heatmap_cluster_score * 1.15, 3), "止损比例偏高，提高 cluster score 门槛过滤弱热力图信号", 0.62))
        if summary["max_consecutive_losses"] >= config.max_consecutive_losses:
            recs.append(self._rec("loss_pause_minutes", config.loss_pause_minutes, min(360, int(config.loss_pause_minutes * 1.25)), "最大连续亏损触发，延长暂停时间降低单边行情连续接飞刀风险", 0.7))
        if summary["profit_factor"] > 1.4 and summary["win_rate"] > 0.5 and summary["take_profit_rate"] > 0.35:
            recs.append(self._rec("min_reward_risk", config.min_reward_risk, min(2.2, round(config.min_reward_risk * 1.1, 2)), "近期收益因子和胜率较好，可小幅提高 R:R 以扩大优质信号收益", 0.55))
        if summary["profit_factor"] < 0.9 and summary["trades"] >= 20:
            recs.append(self._rec("cooldown_seconds", config.cooldown_seconds, min(28_800, int(config.cooldown_seconds * 1.2)), "收益因子低于 0.9，增加冷却时间减少过度交易", 0.66))
        return recs or [{"param": "none", "current": None, "suggested": None, "reason": "当前样本未发现明确需要调整的参数", "confidence": 0.5}]

    def _rec(self, param: str, current: Any, suggested: Any, reason: str, confidence: float) -> dict[str, Any]:
        return {"param": param, "current": current, "suggested": suggested, "reason": reason, "confidence": confidence}

    def _max_consecutive_losses(self, pnls: list[float]) -> int:
        best = 0
        current = 0
        for pnl in pnls:
            if pnl < 0:
                current += 1
                best = max(best, current)
            else:
                current = 0
        return best

    def _clamp(self, param: str, current: Any, suggested: Any) -> Any:
        try:
            if isinstance(current, int):
                value = int(float(suggested))
            else:
                value = float(suggested)
        except (TypeError, ValueError):
            return None
        bounds = {
            "dominance_ratio": (1.05, 5.0),
            "min_heatmap_cluster_score": (0.5, 20.0),
            "max_heatmap_distance_pct": (0.001, 0.05),
            "stop_buffer_pct": (0.0, 1.0),
            "min_reward_risk": (0.8, 3.0),
            "max_stop_loss_pct": (0.2, 3.0),
            "max_take_profit_pct": (0.5, 8.0),
            "max_holding_minutes": (3, 180),
            "cooldown_seconds": (60, 86_400),
            "loss_pause_minutes": (5, 720),
            "min_liquidation_usd": (100_000, 1_000_000_000),
        }
        low, high = bounds.get(param, (None, None))
        if low is not None:
            value = max(low, value)
        if high is not None:
            value = min(high, value)
        return int(value) if isinstance(current, int) else round(value, 6)
