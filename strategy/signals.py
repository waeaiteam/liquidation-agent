from __future__ import annotations

from statistics import median
from uuid import uuid4

from strategy.heatmap import HeatmapClusterAnalyzer
from strategy.models import MarketSnapshot, Signal, StrategyConfig, utc_now


class LiquidationContrarianSignalEngine:
    def __init__(self):
        self.heatmap_analyzer = HeatmapClusterAnalyzer()

    def generate(self, snapshot: MarketSnapshot, config: StrategyConfig, *, require_heatmap: bool = True) -> Signal:
        metrics = self._metrics(snapshot, config)
        if not config.enabled:
            return Signal.none(config.symbol, ["strategy disabled"], metrics)
        if snapshot.price <= 0:
            return Signal.none(config.symbol, ["missing price"], metrics)

        long_liq = metrics["long_liquidation_usd"]
        short_liq = metrics["short_liquidation_usd"]
        dominant = max(long_liq, short_liq)
        opposite = min(long_liq, short_liq)
        if dominant < config.min_liquidation_usd:
            return Signal.none(
                config.symbol,
                [f"dominant liquidation ${dominant:,.0f} below threshold ${config.min_liquidation_usd:,.0f}"],
                metrics,
            )

        ratio = dominant / max(opposite, 1)
        if ratio < config.dominance_ratio:
            return Signal.none(
                config.symbol,
                [f"dominance ratio {ratio:.2f} below threshold {config.dominance_ratio:.2f}"],
                metrics,
            )

        if long_liq > short_liq:
            side = "LONG"
            action = "BUY"
            reasons = ["long liquidation dominates, contrarian long rebound"]
        else:
            side = "SHORT"
            action = "SELL"
            reasons = ["short liquidation dominates, contrarian short mean reversion"]
        reasons.extend([f"1H liquidation ${dominant:,.0f}", f"dominance ratio {ratio:.2f}"])

        confirmations = 0
        blockers: list[str] = []
        share = metrics["liq_24h_share"]
        if share >= config.min_liq_24h_share:
            confirmations += 1
            reasons.append(f"1H/24H liquidation share {share:.1%}")
        else:
            blockers.append(f"1H/24H liquidation share {share:.1%} below {config.min_liq_24h_share:.1%}")

        spike_ratio = metrics["history_spike_ratio"]
        if spike_ratio >= config.min_history_spike_ratio:
            confirmations += 1
            reasons.append(f"liquidation spike ratio {spike_ratio:.2f}")
        elif spike_ratio > 0:
            blockers.append(f"liquidation spike ratio {spike_ratio:.2f} below {config.min_history_spike_ratio:.2f}")

        oi_value = metrics["open_interest_usd"]
        oi_ratio = dominant / max(oi_value, 1)
        metrics["oi_liq_ratio"] = oi_ratio
        if oi_value > 0 and oi_ratio >= config.min_oi_liq_ratio:
            confirmations += 1
            reasons.append(f"liquidation/OI ratio {oi_ratio:.4f}")
        elif config.use_oi_confirmation:
            return Signal.none(
                config.symbol,
                [f"liquidation/OI ratio {oi_ratio:.4f} below threshold {config.min_oi_liq_ratio:.4f}"],
                metrics,
            )

        ls_ratio = metrics["long_short_ratio"]
        if ls_ratio > 0:
            if (side == "LONG" and ls_ratio < 1) or (side == "SHORT" and ls_ratio > 1):
                confirmations += 1
                reasons.append(f"long/short ratio {ls_ratio:.2f} supports squeeze reversal")
            elif config.use_long_short_confirmation:
                return Signal.none(config.symbol, [f"long/short ratio {ls_ratio:.2f} does not confirm {side}"], metrics)
        elif config.use_long_short_confirmation:
            return Signal.none(config.symbol, ["missing long/short confirmation"], metrics)

        funding_rate = metrics["funding_rate"]
        if funding_rate:
            if (side == "LONG" and funding_rate <= -config.funding_extreme_threshold) or (side == "SHORT" and funding_rate >= config.funding_extreme_threshold):
                confirmations += 1
                reasons.append(f"funding {funding_rate:.4%} supports contrarian entry")

        heatmap_match = metrics["heatmap"]["long_match" if side == "LONG" else "short_match"]
        if require_heatmap:
            if heatmap_match["valid"]:
                confirmations += 1
                cluster = heatmap_match["cluster"] or {}
                reasons.append(
                    f"heatmap {cluster.get('side')} {cluster.get('leverage_tier')} tier cluster score {cluster.get('score')} distance {float(cluster.get('distance_pct') or 0):.2%}"
                )
                metrics["heatmap_score"] = heatmap_match["heatmap_score"]
            elif config.use_heatmap_confirmation:
                return Signal.none(config.symbol, [heatmap_match["reason"]], metrics)
        else:
            metrics["heatmap_required"] = False

        required_confirmations = 1 if config.entry_mode == "fast" else 2
        metrics["confirmations"] = confirmations
        metrics["required_confirmations"] = required_confirmations
        if confirmations < required_confirmations:
            return Signal.none(
                config.symbol,
                [f"{config.entry_mode} mode needs {required_confirmations} confirmations, got {confirmations}", *blockers],
                metrics,
            )

        confidence = min(0.95, 0.4 + min(ratio / max(config.dominance_ratio, 1), 3) * 0.1 + confirmations * 0.07 + min(metrics.get("heatmap_score", 0), 10) * 0.015)
        reasons.append(f"{config.entry_mode} mode confirmations {confirmations}/{required_confirmations}")

        return Signal(
            id=str(uuid4()),
            timestamp=utc_now(),
            symbol=config.symbol,
            side=side,
            action=action,
            confidence=round(confidence, 3),
            price=snapshot.price,
            liquidation_usd=dominant,
            opposite_liquidation_usd=opposite,
            dominance_ratio=round(ratio, 3),
            reasons=reasons,
            metrics=metrics,
        )

    def _metrics(self, snapshot: MarketSnapshot, config: StrategyConfig) -> dict:
        intervals = snapshot.intervals or {}
        long_short_ratio = 0
        if snapshot.long_short:
            first = snapshot.long_short[0]
            long_short_ratio = first.get("longShortRatio") or first.get("long_short_ratio") or 0
        long_liq = float(intervals.get("h1Long") or 0)
        short_liq = float(intervals.get("h1Short") or 0)
        total_24h = float(intervals.get("h24Total") or 0)
        dominant = max(long_liq, short_liq)
        heatmap = self.heatmap_analyzer.analyze(
            snapshot.liq_map,
            snapshot.price,
            snapshot.symbol,
            config.heatmap_bucket_pct,
            config.min_heatmap_cluster_score,
            config.max_heatmap_distance_pct,
            config.allowed_heatmap_leverage_tiers,
        )
        if hasattr(snapshot, "heatmap") and isinstance(getattr(snapshot, "heatmap", None), dict):
            heatmap.update(snapshot.heatmap)
        return {
            "long_liquidation_usd": long_liq,
            "short_liquidation_usd": short_liq,
            "total_24h_liquidation_usd": total_24h,
            "liq_24h_share": dominant / max(total_24h, 1),
            "history_spike_ratio": self._history_spike_ratio(snapshot, dominant),
            "open_interest_usd": self._open_interest(snapshot),
            "long_short_ratio": float(long_short_ratio or 0),
            "funding_rate": self._funding_rate(snapshot),
            "funding_count": len(snapshot.funding or []),
            "heatmap": heatmap,
            "heatmap_age_seconds": heatmap.get("age_seconds"),
            "data_warnings": snapshot.data_warnings,
            "price": snapshot.price,
        }

    def _history_spike_ratio(self, snapshot: MarketSnapshot, dominant: float) -> float:
        values = []
        for item in snapshot.history or []:
            long_value = float(item.get("longVolUsd") or item.get("longTurnover") or 0)
            short_value = float(item.get("shortVolUsd") or item.get("shortTurnover") or 0)
            total = max(long_value, short_value)
            if total > 0:
                values.append(total)
        if not values:
            return 0.0
        baseline = median(values)
        return dominant / max(baseline, 1)

    def _funding_rate(self, snapshot: MarketSnapshot) -> float:
        rates = []
        exchange = snapshot.exchange.lower()
        for item in snapshot.funding or []:
            symbol = str(item.get("symbol") or "").upper()
            item_exchange = str(item.get("exchange") or "").lower()
            if symbol and symbol != snapshot.symbol.upper():
                continue
            if item_exchange and item_exchange != exchange:
                continue
            try:
                rates.append(float(item.get("rate") or 0))
            except (TypeError, ValueError):
                continue
        return rates[0] if rates else 0.0

    def _open_interest(self, snapshot: MarketSnapshot) -> float:
        total = 0.0
        for item in snapshot.oi or []:
            total += float(item.get("openInterest") or item.get("openInterestUsd") or item.get("coinValue") or 0)
        return total
