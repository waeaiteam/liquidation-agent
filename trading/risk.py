from __future__ import annotations

from datetime import date

from strategy.models import RiskDecision, Signal, StrategyConfig


class RiskManager:
    def validate(self, signal: Signal, config: StrategyConfig, state, live_confirmed: bool = False) -> RiskDecision:
        reasons = []
        mode = config.mode

        if not signal.valid or signal.action == "WAIT":
            return RiskDecision(False, signal.reasons or ["no actionable signal"], mode, config.notional_usd, config.leverage)
        if mode not in {"paper", "live"}:
            reasons.append("mode must be paper or live")
        if mode == "live" and not config.live_enabled:
            reasons.append("live mode is disabled in config")
        if mode == "live" and not live_confirmed:
            reasons.append("live mode requires explicit confirmation")
        if signal.symbol.upper() not in config.allowed_symbols:
            reasons.append(f"symbol {signal.symbol} not allowed")
        if config.notional_usd <= 0:
            reasons.append("notional must be positive")
        if config.notional_usd > config.max_notional_usd:
            reasons.append(f"notional ${config.notional_usd:,.0f} exceeds max ${config.max_notional_usd:,.0f}")
        if config.leverage <= 0 or config.leverage > config.max_leverage:
            reasons.append(f"leverage {config.leverage} exceeds max {config.max_leverage}")
        if config.stop_loss_pct <= 0 or config.take_profit_pct <= 0:
            reasons.append("stop loss and take profit are required")
        if state.seconds_since_last_trade(signal.symbol) < config.cooldown_seconds:
            reasons.append(f"cooldown active for {signal.symbol}")
        if state.trade_count_for_day(date.today().isoformat()) >= config.max_daily_trades:
            reasons.append("daily trade limit reached")
        if state.realized_loss_for_day(date.today().isoformat()) >= config.max_daily_loss_usd:
            reasons.append("daily loss limit reached")
        heatmap_age = signal.metrics.get("heatmap_age_seconds")
        if config.use_heatmap_confirmation and (heatmap_age is None or heatmap_age > config.max_heatmap_trade_age_seconds):
            reasons.append("heatmap snapshot too old for trade")
        pause_remaining = state.loss_pause_remaining_seconds()
        if pause_remaining > 0:
            reasons.append(f"consecutive loss pause active for {int(pause_remaining)}s")

        stop_loss, take_profit = self._stops(signal, config)
        if not reasons:
            reasons.append("risk checks passed")
        return RiskDecision(not reasons or reasons == ["risk checks passed"], reasons, mode, config.notional_usd, config.leverage, stop_loss, take_profit)

    def _stops(self, signal: Signal, config: StrategyConfig) -> tuple[float, float]:
        if config.stop_mode == "fixed":
            return self._fixed_stops(signal, config)
        heatmap_stops = self._heatmap_stops(signal, config)
        if config.stop_mode == "heatmap":
            return heatmap_stops if heatmap_stops != (0, 0) else self._fixed_stops(signal, config)
        if heatmap_stops == (0, 0):
            return self._fixed_stops(signal, config)
        return heatmap_stops

    def _fixed_stops(self, signal: Signal, config: StrategyConfig) -> tuple[float, float]:
        price = signal.price
        sl = config.stop_loss_pct / 100
        tp = config.take_profit_pct / 100
        if signal.side == "LONG":
            return round(price * (1 - sl), 4), round(price * (1 + tp), 4)
        if signal.side == "SHORT":
            return round(price * (1 + sl), 4), round(price * (1 - tp), 4)
        return 0, 0

    def _heatmap_stops(self, signal: Signal, config: StrategyConfig) -> tuple[float, float]:
        price = signal.price
        heatmap = signal.metrics.get("heatmap") or {}
        if not heatmap:
            return 0, 0
        if signal.side == "LONG":
            match = heatmap.get("long_match") or {}
            stop_cluster = match.get("cluster") or self._nearest_cluster(heatmap.get("below") or [], price, "below")
            target_cluster = self._nearest_cluster(heatmap.get("above") or [], price, "above")
            if not stop_cluster:
                return 0, 0
            buffer_pct = config.stop_buffer_pct / 100
            heatmap_stop = float(stop_cluster.get("low") or stop_cluster.get("center") or 0) * (1 - buffer_pct)
            max_stop = price * (1 - config.max_stop_loss_pct / 100)
            stop_loss = max(heatmap_stop, max_stop)
            risk = price - stop_loss
            if risk <= 0:
                return self._fixed_stops(signal, config)
            rr_target = price + risk * config.min_reward_risk
            cluster_target = float((target_cluster or {}).get("center") or rr_target)
            capped_target = price * (1 + config.max_take_profit_pct / 100)
            take_profit = min(max(rr_target, cluster_target), capped_target)
            return round(stop_loss, 4), round(take_profit, 4)
        if signal.side == "SHORT":
            match = heatmap.get("short_match") or {}
            stop_cluster = match.get("cluster") or self._nearest_cluster(heatmap.get("above") or [], price, "above")
            target_cluster = self._nearest_cluster(heatmap.get("below") or [], price, "below")
            if not stop_cluster:
                return 0, 0
            buffer_pct = config.stop_buffer_pct / 100
            heatmap_stop = float(stop_cluster.get("high") or stop_cluster.get("center") or 0) * (1 + buffer_pct)
            max_stop = price * (1 + config.max_stop_loss_pct / 100)
            stop_loss = min(heatmap_stop, max_stop)
            risk = stop_loss - price
            if risk <= 0:
                return self._fixed_stops(signal, config)
            rr_target = price - risk * config.min_reward_risk
            cluster_target = float((target_cluster or {}).get("center") or rr_target)
            capped_target = price * (1 - config.max_take_profit_pct / 100)
            take_profit = max(min(rr_target, cluster_target), capped_target)
            return round(stop_loss, 4), round(take_profit, 4)
        return 0, 0

    def _nearest_cluster(self, clusters, price: float, side: str) -> dict | None:
        if not clusters:
            return None
        if side == "above":
            candidates = [cluster for cluster in clusters if float(cluster.get("center") or 0) >= price]
        else:
            candidates = [cluster for cluster in clusters if float(cluster.get("center") or 0) <= price]
        candidates = candidates or clusters
        return min(candidates, key=lambda cluster: abs(float(cluster.get("center") or price) - price))
