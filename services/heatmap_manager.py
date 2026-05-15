from __future__ import annotations

import time
from typing import Any

from services.llm import _as_dict

from services.coinank import coinank_exchange
from services.liquidation_maps import fuse_liquidation_maps, normalize_liquidation_map
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

        base_cost = config.liq_map_cost_usdc
        planned_cost = base_cost * 2
        budget_kind = "liq_map_event" if force else "liq_map_scheduled"
        kind_budget = config.event_liq_map_budget_usdc if force else config.scheduled_liq_map_budget_usdc
        include_leverage_reference = True
        if not state.can_spend_api_budget(planned_cost, config.daily_api_budget_usdc, budget_kind, kind_budget):
            include_leverage_reference = False
            if not state.can_spend_api_budget(base_cost, config.daily_api_budget_usdc, budget_kind, kind_budget):
                reason = f"{budget_kind} budget exhausted"
                if latest and latest_age <= config.max_heatmap_snapshot_age_seconds:
                    return self._result(latest, reason, from_cache=True, budget_blocked=True)
                return self._stale(latest, reason, budget_blocked=True)

        try:
            raw_agg = client.coinank.liquidation.agg_liq_map(base_coin=config.coin, interval=config.interval)
            state.record_claw402_raw_sample("agg_liq_map", config.symbol, config.exchange, config.interval, raw_agg)
            agg_map = normalize_liquidation_map(raw_agg, symbol=config.symbol, exchange=config.exchange, interval=config.interval)
            raw_liq = None
            exchange_map = normalize_liquidation_map(raw_liq, symbol=config.symbol, exchange=config.exchange, interval=config.interval)
            leverage_error = None
            if include_leverage_reference:
                claw_exchange = coinank_exchange(config.exchange)
                try:
                    raw_liq = client.coinank.liquidation.liq_map(symbol=config.symbol, exchange=claw_exchange, interval=config.interval)
                    state.record_claw402_raw_sample("liq_map", config.symbol, config.exchange, config.interval, raw_liq)
                    exchange_map = normalize_liquidation_map(raw_liq, symbol=config.symbol, exchange=config.exchange, interval=config.interval)
                except Exception as exc:
                    leverage_error = str(exc)
                    state.add_event(
                        "heatmap",
                        "leverage reference map unavailable; using aggregate liquidation map only",
                        {"symbol": config.symbol, "exchange": config.exchange, "interval": config.interval, "error": leverage_error},
                        level="warn",
                        module="claw402",
                        action="liqmap_leverage",
                    )
            liq_map = fuse_liquidation_maps(agg_map if agg_map.get("has_data") else exchange_map, leverage_map=exchange_map)
            liq_map["scope"] = "aggregate"
            actual_cost = base_cost * (2 if raw_liq is not None else 1)
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
            "map_scope": "aggregate",
            "leverage_exchange": config.exchange if raw_liq is not None else None,
            "leverage_error": leverage_error,
            "interval": config.interval,
            "timestamp": utc_now(),
            "epoch": time.time(),
            "price": price,
            "liq_map": liq_map,
            "heatmap": analysis,
            "cost_usdc": actual_cost,
        }
        state.record_api_cost("liq_map", actual_cost, config.symbol)
        state.record_api_cost(budget_kind, actual_cost, config.symbol)
        change_ratio = self._change_ratio(latest, snapshot)
        snapshot["change_ratio"] = change_ratio
        schema_changed = not self._has_current_snapshot_schema(latest)
        if not latest or schema_changed or change_ratio >= config.min_heatmap_change_ratio:
            state.record_heatmap_snapshot(snapshot, config.max_heatmap_snapshots)
            return self._result(snapshot, "liq map refreshed", refreshed=True, change_ratio=change_ratio, schema_changed=schema_changed)
        return self._result(latest, "liq map refreshed but unchanged", from_cache=True, refreshed=True, deduped=True, change_ratio=change_ratio)

    def _change_ratio(self, previous: dict[str, Any] | None, current: dict[str, Any]) -> float:
        previous = _as_dict(previous)
        current = _as_dict(current)
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
        liq_map = _as_dict(liq_map)
        if isinstance(liq_map.get("points"), list):
            volumes: dict[float, float] = {}
            for point in liq_map["points"]:
                point = _as_dict(point)
                price = self._to_float(point.get("price"))
                volume = self._to_float(point.get("value"))
                if price is None or volume is None or volume <= 0:
                    continue
                volumes[price] = volumes.get(price, 0.0) + volume
            return volumes
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

    def _visual_interval(self, liq_map: dict[str, Any], fallback: str) -> str:
        meta = _as_dict(liq_map.get("meta"))
        visual = _as_dict(meta.get("visual"))
        return str(visual.get("chart_interval") or meta.get("chart_interval") or fallback or "")

    def _has_current_snapshot_schema(self, snapshot: dict[str, Any] | None) -> bool:
        snapshot = _as_dict(snapshot)
        if not snapshot:
            return False
        liq_map = _as_dict(snapshot.get("liq_map"))
        shape = _as_dict(liq_map.get("shape"))
        return bool(shape.get("leverage_point_count"))

    def _result(self, snapshot: dict[str, Any], reason: str, **flags) -> dict[str, Any]:
        snapshot = _as_dict(snapshot)
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
        snapshot_dict = _as_dict(snapshot)
        return {
            "usable": False,
            "reason": reason,
            "snapshot": snapshot,
            "liq_map": snapshot_dict.get("liq_map") or {},
            "heatmap": snapshot_dict.get("heatmap") or {},
            "age_seconds": None if not snapshot_dict else max(0, time.time() - float(snapshot_dict.get("epoch") or time.time())),
            **flags,
        }
