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
        return {
            "timestamp": utc_now(),
            "mode": "suggest_only",
            "lookback": lookback,
            "summary": summary,
            "failure_clusters": self._failure_clusters(orders, state.events),
            "recommendations": self._recommendations(summary, config, orders),
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
            applied.append({"param": param, "current": current, "suggested": suggested, "rollback": current, "reason": rec.get("reason", "")})
        return {"updates": updates, "applied": applied, "skipped": skipped}

    def _summary(self, orders: list[dict[str, Any]]) -> dict[str, Any]:
        pnls = [float(order.get("pnl_usd") or 0) for order in orders]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl < 0]
        time_stops = [order for order in orders if "time stop" in str(order.get("reason", "")).lower()]
        stop_losses = [order for order in orders if "stop loss" in str(order.get("reason", "")).lower()]
        take_profits = [order for order in orders if "take profit" in str(order.get("reason", "")).lower()]
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
            return [{
                "param": "none",
                "current": None,
                "suggested": None,
                "reason": "sample size below 10 CLOSED paper trades; keep collecting paper data",
                "evidence": {"trades": trades},
                "impact": "none",
                "rollback": None,
                "confidence": 0.2,
            }]
        if summary["time_stop_rate"] > 0.35:
            recs.append(self._rec("max_holding_minutes", config.max_holding_minutes, max(8, int(config.max_holding_minutes * 0.75)), "time-stop rate is high; shorten holding time to reduce late reversals", 0.65))
        if summary["stop_loss_rate"] > 0.35:
            recs.append(self._rec("max_heatmap_distance_pct", config.max_heatmap_distance_pct, max(0.003, round(config.max_heatmap_distance_pct * 0.85, 4)), "stop-loss rate is high; require entries closer to heatmap clusters", 0.68))
            recs.append(self._rec("min_heatmap_cluster_score", config.min_heatmap_cluster_score, round(config.min_heatmap_cluster_score * 1.15, 3), "stop-loss rate is high; raise heatmap cluster score threshold", 0.62))
        if summary["max_consecutive_losses"] >= config.max_consecutive_losses:
            recs.append(self._rec("loss_pause_minutes", config.loss_pause_minutes, min(360, int(config.loss_pause_minutes * 1.25)), "max consecutive losses reached; extend pause after loss streaks", 0.7))
        if summary["profit_factor"] > 1.4 and summary["win_rate"] > 0.5 and summary["take_profit_rate"] > 0.35:
            recs.append(self._rec("min_reward_risk", config.min_reward_risk, min(2.2, round(config.min_reward_risk * 1.1, 2)), "profit factor and win rate are strong; slightly raise reward/risk target", 0.55))
        if summary["profit_factor"] < 0.9 and summary["trades"] >= 20:
            recs.append(self._rec("cooldown_seconds", config.cooldown_seconds, min(28_800, int(config.cooldown_seconds * 1.2)), "profit factor below 0.9; increase cooldown to reduce overtrading", 0.66))
        return recs or [{
            "param": "none",
            "current": None,
            "suggested": None,
            "reason": "no clear parameter adjustment found in the current sample",
            "evidence": {"trades": trades},
            "impact": "none",
            "rollback": None,
            "confidence": 0.5,
        }]

    def _rec(self, param: str, current: Any, suggested: Any, reason: str, confidence: float) -> dict[str, Any]:
        return {
            "param": param,
            "current": current,
            "suggested": suggested,
            "rollback": current,
            "reason": reason,
            "evidence": {},
            "impact": "future paper ticks only",
            "confidence": confidence,
        }

    def _failure_clusters(self, orders: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
        clusters = {
            "heatmap_too_far": 0,
            "weak_cluster": 0,
            "time_stop": 0,
            "stop_loss_cluster": 0,
            "low_rr": 0,
            "overtrading": 0,
            "stale_data": 0,
        }
        evidence = {key: [] for key in clusters}
        for order in orders:
            reason = str(order.get("reason") or "").lower()
            pnl = float(order.get("pnl_usd") or 0)
            if "time stop" in reason:
                clusters["time_stop"] += 1
                evidence["time_stop"].append(order.get("id"))
            if "stop loss" in reason or pnl < 0:
                clusters["stop_loss_cluster"] += 1
                evidence["stop_loss_cluster"].append(order.get("id"))
        for event in events[:200]:
            text = (str(event.get("message") or "") + " " + str(event.get("data") or "")).lower()
            if "distance" in text and "heatmap" in text:
                clusters["heatmap_too_far"] += 1
                evidence["heatmap_too_far"].append(event.get("request_id"))
            if "cluster" in text and ("weak" in text or "score" in text):
                clusters["weak_cluster"] += 1
                evidence["weak_cluster"].append(event.get("request_id"))
            if "reward" in text or "r:r" in text:
                clusters["low_rr"] += 1
                evidence["low_rr"].append(event.get("request_id"))
            if "cooldown" in text or "daily trade limit" in text:
                clusters["overtrading"] += 1
                evidence["overtrading"].append(event.get("request_id"))
            if "stale" in text or "too old" in text:
                clusters["stale_data"] += 1
                evidence["stale_data"].append(event.get("request_id"))
        return {key: {"count": count, "evidence": evidence[key][:5]} for key, count in clusters.items()}

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
