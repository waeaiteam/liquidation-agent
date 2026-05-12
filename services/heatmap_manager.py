from __future__ import annotations

import time
from typing import Any

from services.coinank import unwrap_data
from strategy.heatmap import HeatmapClusterAnalyzer
from strategy.models import StrategyConfig, utc_now


class HeatmapSnapshotManager:
    def __init__(self):
        self.analyzer = HeatmapClusterAnalyzer()

    def get_for_decision(self, client, state, config: StrategyConfig, price: float, *, force: bool = False) -> dict[str, Any]:
        latest = state.latest_heatmap_snapshot(config.symbol, config.interval)
        latest_age = state.heatmap_snapshot_age_seconds(latest)
        if latest and latest_age <= config.max_heatmap_snapshot_age_seconds and not force:
            return self._result(latest, "fresh snapshot reused", from_cache=True)

        should_refresh = force or not latest or latest_age >= config.liq_map_snapshot_interval_seconds
        if not should_refresh:
            if latest and latest_age <= config.max_heatmap_snapshot_age_seconds:
                return self._result(latest, "snapshot reused before refresh interval", from_cache=True)
            return self._stale(latest, "heatmap snapshot stale")

        cost = config.liq_map_cost_usdc
        budget_kind = "liq_map_event" if force else "liq_map_scheduled"
        kind_budget = config.event_liq_map_budget_usdc if force else config.scheduled_liq_map_budget_usdc
        if not state.can_spend_api_budget(cost, config.daily_api_budget_usdc, budget_kind, kind_budget):
            reason = f"{budget_kind} budget exhausted"
            if latest and latest_age <= config.max_heatmap_snapshot_age_seconds:
                return self._result(latest, reason, from_cache=True, budget_blocked=True)
            return self._stale(latest, reason, budget_blocked=True)

        try:
            raw = client.coinank.liquidation.agg_liq_map(base_coin=config.coin, interval=config.interval)
            liq_map = unwrap_data(raw, {}) or {}
        except Exception as exc:
            if latest and latest_age <= config.max_heatmap_snapshot_age_seconds:
                return self._result(latest, f"liq map refresh failed, using cached snapshot: {exc}", from_cache=True, refresh_error=str(exc))
            return self._stale(latest, f"liq map refresh failed: {exc}", refresh_error=str(exc))

        analysis = self.analyzer.analyze(
            liq_map,
            price,
            config.symbol,
            config.heatmap_bucket_pct,
            config.min_heatmap_cluster_score,
            config.max_heatmap_distance_pct,
            config.allowed_heatmap_leverage_tiers,
        )
        snapshot = {
            "symbol": config.symbol,
            "coin": config.coin,
            "interval": config.interval,
            "timestamp": utc_now(),
            "epoch": time.time(),
            "price": price,
            "liq_map": liq_map,
            "heatmap": analysis,
            "cost_usdc": cost,
        }
        state.record_api_cost("liq_map", cost, config.symbol)
        state.record_api_cost(budget_kind, cost, config.symbol)
        change_ratio = self._change_ratio(latest, snapshot)
        snapshot["change_ratio"] = change_ratio
        if not latest or change_ratio >= config.min_heatmap_change_ratio:
            state.record_heatmap_snapshot(snapshot, config.max_heatmap_snapshots)
            return self._result(snapshot, "liq map refreshed", refreshed=True, change_ratio=change_ratio)
        return self._result(latest, "liq map refreshed but unchanged", from_cache=True, refreshed=True, deduped=True, change_ratio=change_ratio)

    def _change_ratio(self, previous: dict[str, Any] | None, current: dict[str, Any]) -> float:
        if not previous:
            return 1.0
        prev_map = previous.get("liq_map") or {}
        curr_map = current.get("liq_map") or {}
        prev = self._volume_by_price(prev_map)
        curr = self._volume_by_price(curr_map)
        keys = set(prev) | set(curr)
        if not keys:
            return 1.0
        diff = sum(abs(curr.get(key, 0.0) - prev.get(key, 0.0)) for key in keys)
        base = sum(max(curr.get(key, 0.0), prev.get(key, 0.0)) for key in keys)
        return round(diff / max(base, 1), 6)

    def _volume_by_price(self, liq_map: dict[str, Any]) -> dict[float, float]:
        prices = [self._to_float(value) for value in (liq_map.get("prices") or [])]
        volumes: dict[float, float] = {}
        for key, values in liq_map.items():
            if key in {"prices", "price", "lastIndex", "last_index", "lastPrice", "last_price"} or not isinstance(values, list) or len(values) != len(prices):
                continue
            for price, raw in zip(prices, values):
                volume = self._to_float(raw)
                if price is None or volume is None or volume <= 0:
                    continue
                volumes[price] = volumes.get(price, 0.0) + volume
        return volumes

    def _to_float(self, value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _result(self, snapshot: dict[str, Any], reason: str, **flags) -> dict[str, Any]:
        heatmap = snapshot.get("heatmap") or {}
        return {
            "usable": bool(heatmap.get("has_data")),
            "reason": reason,
            "snapshot": snapshot,
            "liq_map": snapshot.get("liq_map") or {},
            "heatmap": heatmap,
            "age_seconds": max(0, time.time() - float(snapshot.get("epoch") or time.time())),
            **flags,
        }

    def _stale(self, snapshot: dict[str, Any] | None, reason: str, **flags) -> dict[str, Any]:
        return {
            "usable": False,
            "reason": reason,
            "snapshot": snapshot,
            "liq_map": (snapshot or {}).get("liq_map") or {},
            "heatmap": (snapshot or {}).get("heatmap") or {},
            "age_seconds": None if not snapshot else max(0, time.time() - float(snapshot.get("epoch") or time.time())),
            **flags,
        }
