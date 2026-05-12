from __future__ import annotations

import math
from statistics import median
from typing import Any


META_KEYS = {"prices", "lastprice", "last_price", "lastindex", "last_index", "symbol", "basecoin", "base_coin", "interval", "time", "timestamp"}
TIER_WEIGHTS = {"high": 1.0, "medium": 0.75, "low": 0.45}


class HeatmapClusterAnalyzer:
    def analyze(
        self,
        liq_map: dict[str, Any] | None,
        current_price: float,
        symbol: str,
        bucket_pct: float,
        min_cluster_score: float,
        max_distance_pct: float,
        allowed_tiers: list[str],
    ) -> dict[str, Any]:
        current_price = float(current_price or 0)
        if current_price <= 0:
            return self._empty("missing current price")
        data = self._map_data(liq_map)
        prices = self._prices(data)
        if not prices:
            return self._empty("missing heatmap prices")

        bucket_size = self._bucket_size(symbol, current_price, bucket_pct)
        buckets = self._buckets(data, prices, bucket_size)
        if not buckets:
            return self._empty("missing heatmap liquidation volumes")

        records = self._bucket_records(buckets, bucket_size, current_price)
        baseline = median([r["volume"] for r in records if r["volume"] > 0]) or 1
        for record in records:
            record["score"] = round(record["volume"] / max(baseline, 1), 4)
            self._decorate_cluster(record, current_price)

        clusters = self._clusters(records, bucket_size, baseline, current_price, min_cluster_score)
        clusters.sort(key=lambda c: c["volume"] * c["score"], reverse=True)
        allowed = {tier.lower() for tier in (allowed_tiers or [])} or {"high", "medium"}
        result = {
            "has_data": True,
            "bucket_size": bucket_size,
            "baseline_volume": round(baseline, 4),
            "clusters": clusters[:20],
            "above": [c for c in clusters if c["side"] == "above"][:8],
            "below": [c for c in clusters if c["side"] == "below"][:8],
            "at_price": [c for c in clusters if c["side"] == "at_price"][:4],
            "leverage_tiers": self._tier_summary(records, clusters),
        }
        result["long_match"] = self._match(clusters, {"below", "at_price"}, allowed, max_distance_pct, "LONG")
        result["short_match"] = self._match(clusters, {"above", "at_price"}, allowed, max_distance_pct, "SHORT")
        result["strongest_cluster"] = clusters[0] if clusters else None
        return result

    def _map_data(self, liq_map: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(liq_map, dict):
            return {}
        data = liq_map.get("data", liq_map)
        if isinstance(data, dict) and isinstance(data.get("agg_map"), dict):
            return data["agg_map"]
        if isinstance(data, dict) and isinstance(data.get("liq_map"), dict):
            return data["liq_map"]
        return data if isinstance(data, dict) else {}

    def _prices(self, data: dict[str, Any]) -> list[float]:
        raw = data.get("prices") or data.get("price") or data.get("priceList") or []
        return [value for value in (self._number(item) for item in raw) if value is not None]

    def _buckets(self, data: dict[str, Any], prices: list[float], bucket_size: float) -> dict[float, float]:
        buckets: dict[float, float] = {}
        for key, values in data.items():
            if key.lower() in META_KEYS or not isinstance(values, list) or len(values) != len(prices):
                continue
            for price, raw_volume in zip(prices, values):
                volume = self._number(raw_volume)
                if volume is None or volume <= 0:
                    continue
                low = math.floor(price / bucket_size) * bucket_size
                buckets[low] = buckets.get(low, 0.0) + volume
        return buckets

    def _bucket_records(self, buckets: dict[float, float], bucket_size: float, current_price: float) -> list[dict[str, Any]]:
        records = []
        for low, volume in sorted(buckets.items()):
            high = low + bucket_size
            center = low + bucket_size / 2
            record = {"low": round(low, 8), "high": round(high, 8), "center": round(center, 8), "volume": round(volume, 4), "bucket_count": 1}
            self._decorate_cluster(record, current_price)
            records.append(record)
        return records

    def _clusters(self, records: list[dict[str, Any]], bucket_size: float, baseline: float, current_price: float, min_score: float) -> list[dict[str, Any]]:
        clusters = []
        current = None
        for record in records:
            if record["score"] < min_score:
                continue
            if current and record["low"] <= current["high"] + bucket_size * 0.01:
                current["high"] = record["high"]
                current["volume"] += record["volume"]
                current["bucket_count"] += 1
                current["weighted_center"] += record["center"] * record["volume"]
            else:
                if current:
                    clusters.append(self._finalize_cluster(current, baseline, current_price))
                current = {
                    "low": record["low"],
                    "high": record["high"],
                    "volume": record["volume"],
                    "bucket_count": 1,
                    "weighted_center": record["center"] * record["volume"],
                }
        if current:
            clusters.append(self._finalize_cluster(current, baseline, current_price))
        return clusters

    def _finalize_cluster(self, cluster: dict[str, Any], baseline: float, current_price: float) -> dict[str, Any]:
        cluster["center"] = round(cluster.pop("weighted_center") / max(cluster["volume"], 1), 8)
        cluster["volume"] = round(cluster["volume"], 4)
        cluster["score"] = round(cluster["volume"] / max(baseline * cluster["bucket_count"], 1), 4)
        self._decorate_cluster(cluster, current_price)
        return cluster

    def _decorate_cluster(self, cluster: dict[str, Any], current_price: float):
        low = float(cluster["low"])
        high = float(cluster["high"])
        center = float(cluster["center"])
        if high <= current_price:
            side = "below"
            distance = current_price - high
        elif low >= current_price:
            side = "above"
            distance = low - current_price
        else:
            side = "at_price"
            distance = 0.0
        distance_pct = distance / max(current_price, 1)
        center_distance_pct = abs(center - current_price) / max(current_price, 1)
        estimated_leverage = min(200.0, 1 / max(center_distance_pct, 0.005))
        cluster["side"] = side
        cluster["distance_pct"] = round(distance_pct, 6)
        cluster["center_distance_pct"] = round(center_distance_pct, 6)
        cluster["estimated_leverage"] = round(estimated_leverage, 2)
        cluster["leverage_tier"] = self._leverage_tier(estimated_leverage)

    def _match(self, clusters: list[dict[str, Any]], sides: set[str], allowed_tiers: set[str], max_distance_pct: float, signal_side: str) -> dict[str, Any]:
        candidates = [c for c in clusters if c["side"] in sides]
        if not candidates:
            return {"valid": False, "reason": f"no heatmap cluster on {signal_side} reversal side", "cluster": None, "heatmap_score": 0}
        ranked = []
        for cluster in candidates:
            tier = cluster["leverage_tier"]
            tier_allowed = tier in allowed_tiers
            distance_ok = cluster["distance_pct"] <= max_distance_pct
            distance_weight = max(0.0, 1 - cluster["distance_pct"] / max(max_distance_pct, 0.0001))
            heatmap_score = cluster["score"] * distance_weight * TIER_WEIGHTS.get(tier, 0.3)
            ranked.append((tier_allowed and distance_ok, heatmap_score, cluster))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]["score"]), reverse=True)
        valid, heatmap_score, cluster = ranked[0]
        reason = "heatmap cluster confirms reversal" if valid else f"nearest heatmap cluster tier/distance not allowed for {signal_side}"
        return {"valid": valid, "reason": reason, "cluster": cluster, "heatmap_score": round(heatmap_score, 4)}

    def _tier_summary(self, records: list[dict[str, Any]], clusters: list[dict[str, Any]]) -> dict[str, Any]:
        summary: dict[str, Any] = {side: {tier: {"volume": 0.0, "clusters": 0} for tier in ("high", "medium", "low")} for side in ("above", "below", "at_price")}
        for record in records:
            side = record["side"]
            tier = record["leverage_tier"]
            if side in summary and tier in summary[side]:
                summary[side][tier]["volume"] = round(summary[side][tier]["volume"] + record["volume"], 4)
        for cluster in clusters:
            side = cluster["side"]
            tier = cluster["leverage_tier"]
            if side in summary and tier in summary[side]:
                summary[side][tier]["clusters"] += 1
        return summary

    def _bucket_size(self, symbol: str, current_price: float, bucket_pct: float) -> float:
        raw = max(current_price * max(bucket_pct, 0.0001), self._min_bucket(symbol))
        power = 10 ** math.floor(math.log10(raw))
        scaled = raw / power
        if scaled <= 1:
            nice = 1
        elif scaled <= 2:
            nice = 2
        elif scaled <= 5:
            nice = 5
        else:
            nice = 10
        return round(nice * power, 8)

    def _min_bucket(self, symbol: str) -> float:
        symbol = symbol.upper()
        if symbol.startswith("BTC"):
            return 50
        if symbol.startswith("ETH"):
            return 5
        if symbol.startswith("SOL"):
            return 0.2
        return 0.01

    def _leverage_tier(self, estimated_leverage: float) -> str:
        if estimated_leverage >= 50:
            return "high"
        if estimated_leverage >= 20:
            return "medium"
        return "low"

    def _number(self, value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        return number

    def _empty(self, reason: str) -> dict[str, Any]:
        match = {"valid": False, "reason": reason, "cluster": None, "heatmap_score": 0}
        return {
            "has_data": False,
            "reason": reason,
            "bucket_size": 0,
            "baseline_volume": 0,
            "clusters": [],
            "above": [],
            "below": [],
            "at_price": [],
            "leverage_tiers": {},
            "long_match": match,
            "short_match": match,
            "strongest_cluster": None,
        }
