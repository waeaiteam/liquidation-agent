from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from services.coinank import unwrap_data
from strategy.heatmap import HeatmapClusterAnalyzer


PRICE_KEYS = ("prices", "price", "priceList", "price_list", "priceAxis", "price_axis")
TIME_KEYS = ("chartTimeArray", "timeArray", "times", "time", "timestamps", "timestampArray")
MATRIX_META_KEYS = {
    "prices", "price", "priceList", "price_list", "priceAxis", "price_axis",
    "symbol", "exchange", "interval", "baseCoin", "base_coin", "time", "timestamp",
    "lastPrice", "last_price", "lastIndex", "last_index", "chartTimeArray", "timeArray",
    "times", "timestamps", "tickSize", "chartInterval", "start", "end", "maxLiqValue",
    "liqHeatMap",
}


def normalize_liquidation_map(raw: Any, *, symbol: str, exchange: str, interval: str) -> dict[str, Any]:
    payload = unwrap_data(raw, {}) or {}
    data = _map_payload(payload)
    price_axis = _price_axis(data)
    if not price_axis:
        return _empty(symbol, exchange, interval, "No liquidation price axis found in Claw402 response", raw)

    level_map = _liquidation_level_map(data, price_axis)
    series = _series(data, len(price_axis))
    points = []
    for name, values in series:
        for idx, value in enumerate(values):
            numeric = _number(value)
            if numeric is None or numeric <= 0:
                continue
            leverage = _leverage_from_name(name)
            points.append({
                "price": price_axis[idx],
                "series": name,
                "series_label": _series_label(name),
                "leverage": leverage,
                "leverage_bucket": _leverage_bucket(leverage),
                "value": numeric,
                "side": _side_from_name(name),
            })
    points.extend(level_map["points"])

    if not points:
        return _empty(symbol, exchange, interval, "No positive liquidation values found in Claw402 response", raw)

    max_value = max([p["value"] for p in points] + [level_map["max_value"]])
    clusters = HeatmapClusterAnalyzer().analyze(
        {"price_axis": price_axis, "points": points},
        _mid_price(price_axis),
        symbol,
        bucket_pct=0.002,
        min_cluster_score=1.0,
        max_distance_pct=1.0,
        allowed_tiers=["high", "medium", "low"],
    ).get("clusters", [])
    return {
        "has_data": True,
        "source": "claw402_coinank",
        "scope": _scope_from_series(series),
        "symbol": symbol.upper().replace("/", ""),
        "exchange": exchange.lower(),
        "interval": interval,
        "price_axis": price_axis,
        "time_axis": level_map["time_axis"],
        "points": points,
        "level_map": level_map["level_map"],
        "clusters": clusters,
        "max_value": max_value,
        "max_liq_value": level_map["max_value"] or max_value,
        "meta": level_map["meta"],
        "raw": raw,
        "shape": {
            "price_count": len(price_axis),
            "price_levels": len(price_axis),
            "time_count": len(level_map["time_axis"]),
            "matrix_cells": len(level_map["level_map"]),
            "series_count": len(series),
            "point_count": len(points),
        },
    }


def fuse_liquidation_maps(agent_map: dict[str, Any], visual_map: dict[str, Any] | None = None, leverage_map: dict[str, Any] | None = None) -> dict[str, Any]:
    agent_map = agent_map if isinstance(agent_map, dict) else {}
    visual_map = visual_map if isinstance(visual_map, dict) else {}
    leverage_map = leverage_map if isinstance(leverage_map, dict) else {}
    if not agent_map.get("has_data"):
        fused = deepcopy(leverage_map if leverage_map.get("has_data") else visual_map)
        if leverage_map.get("has_data"):
            _attach_leverage_map(fused, leverage_map)
        return fused
    if not visual_map.get("has_data"):
        fused = deepcopy(agent_map)
        if leverage_map.get("has_data"):
            fused["source"] = "claw402_coinank_fused"
            fused["scope"] = "aggregate" if agent_map.get("scope") == "aggregate" else agent_map.get("scope")
            fused["visual_source"] = None
            fused["meta"] = {
                **(fused.get("meta") if isinstance(fused.get("meta"), dict) else {}),
                "agent_source": agent_map.get("source"),
            }
            fused["raw"] = {"agent": agent_map.get("raw"), "leverage": leverage_map.get("raw")}
            _attach_leverage_map(fused, leverage_map)
        return fused

    fused = deepcopy(agent_map)
    fused["source"] = "claw402_coinank_fused"
    fused["scope"] = "aggregate" if agent_map.get("scope") == "aggregate" else agent_map.get("scope")
    fused["visual_source"] = visual_map.get("source")
    fused["level_map"] = deepcopy(visual_map.get("level_map") or [])
    fused["time_axis"] = deepcopy(visual_map.get("time_axis") or [])
    fused["max_liq_value"] = max(float(fused.get("max_liq_value") or 0), float(visual_map.get("max_liq_value") or 0))
    fused["max_value"] = max(float(fused.get("max_value") or 0), float(visual_map.get("max_value") or 0))
    fused["meta"] = {
        **(fused.get("meta") if isinstance(fused.get("meta"), dict) else {}),
        "agent_source": agent_map.get("source"),
        "visual_source": visual_map.get("source"),
        "visual": visual_map.get("meta") if isinstance(visual_map.get("meta"), dict) else {},
    }
    shape = fused.get("shape") if isinstance(fused.get("shape"), dict) else {}
    visual_shape = visual_map.get("shape") if isinstance(visual_map.get("shape"), dict) else {}
    fused["shape"] = {
        **shape,
        "time_count": visual_shape.get("time_count", 0),
        "matrix_cells": visual_shape.get("matrix_cells", 0),
        "visual_matrix_cells": visual_shape.get("matrix_cells", 0),
        "agent_point_count": shape.get("point_count", 0),
    }
    fused["raw"] = {"agent": agent_map.get("raw"), "visual": visual_map.get("raw")}
    if leverage_map.get("has_data"):
        _attach_leverage_map(fused, leverage_map)
        fused["raw"]["leverage"] = leverage_map.get("raw")
    return fused


def _attach_leverage_map(fused: dict[str, Any], leverage_map: dict[str, Any]) -> None:
    fused["leverage_points"] = deepcopy(leverage_map.get("points") or [])
    fused["leverage_price_axis"] = deepcopy(leverage_map.get("price_axis") or [])
    fused["leverage_last_price"] = _number(_map_payload(unwrap_data(leverage_map.get("raw"), {}) or {}).get("lastPrice"))
    meta = fused.get("meta") if isinstance(fused.get("meta"), dict) else {}
    fused["meta"] = {
        **meta,
        "leverage_source": leverage_map.get("source"),
        "leverage_exchange": leverage_map.get("exchange"),
    }
    shape = fused.get("shape") if isinstance(fused.get("shape"), dict) else {}
    leverage_shape = leverage_map.get("shape") if isinstance(leverage_map.get("shape"), dict) else {}
    fused["shape"] = {
        **shape,
        "leverage_price_levels": leverage_shape.get("price_levels", len(fused["leverage_price_axis"])),
        "leverage_point_count": leverage_shape.get("point_count", len(fused["leverage_points"])),
    }


def _map_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for key in ("agg_map", "liq_map", "heat_map", "map", "liqHeatMap"):
        nested = payload.get(key)
        if isinstance(nested, dict) and nested is not payload:
            merged = _map_payload(nested)
            if key == "liqHeatMap":
                for meta_key in ("chartTimeArray", "priceArray", "tickSize", "chartInterval", "start", "end", "maxLiqValue"):
                    if meta_key in payload and meta_key not in merged:
                        merged[meta_key] = payload[meta_key]
            return merged
    return payload


def _price_axis(data: dict[str, Any]) -> list[float]:
    for key in PRICE_KEYS:
        values = data.get(key)
        if isinstance(values, list):
            numbers = [_number(v) for v in values]
            result = [v for v in numbers if v is not None and math.isfinite(v)]
            if result:
                return result
    values = data.get("priceArray")
    if isinstance(values, list):
        numbers = [_number(v) for v in values]
        result = [v for v in numbers if v is not None and math.isfinite(v)]
        if result:
            return result
    rows = data.get("rows") or data.get("list") or data.get("values")
    if isinstance(rows, list):
        result = []
        for row in rows:
            if isinstance(row, dict):
                value = _number(row.get("price") or row.get("p") or row.get("priceLevel"))
            elif isinstance(row, list) and row:
                value = _number(row[0])
            else:
                value = None
            if value is not None and math.isfinite(value):
                result.append(value)
        if result:
            return result
    return []


def _time_axis(data: dict[str, Any]) -> list[Any]:
    for key in TIME_KEYS:
        values = data.get(key)
        if isinstance(values, list) and values:
            return values
    return []


def _liquidation_level_map(data: dict[str, Any], price_axis: list[float]) -> dict[str, Any]:
    matrix = _matrix_data(data)
    time_axis = _time_axis(data)
    rows = []
    points = []
    max_value = _number(data.get("maxLiqValue")) or 0.0
    if not matrix:
        return {"time_axis": time_axis, "level_map": [], "points": [], "max_value": max_value, "meta": _heatmap_meta(data)}

    if _is_sparse_triplets(matrix, len(time_axis), len(price_axis)):
        for row in matrix:
            time_idx = _index(row[0])
            price_idx = _index(row[1])
            value = _number(row[2])
            if time_idx is None or price_idx is None or value is None or value <= 0:
                continue
            if price_idx < 0 or price_idx >= len(price_axis):
                continue
            if time_axis and (time_idx < 0 or time_idx >= len(time_axis)):
                continue
            max_value = max(max_value, value)
            price = price_axis[price_idx]
            rows.append({
                "time_index": time_idx,
                "price_index": price_idx,
                "time": _axis_item(time_axis, time_idx),
                "price": price,
                "value": value,
                "side": _level_side(price, price_axis),
            })
        by_price: dict[int, float] = {}
        for item in rows:
            by_price[item["price_index"]] = by_price.get(item["price_index"], 0.0) + item["value"]
        for price_idx, value in by_price.items():
            points.append({"price": price_axis[price_idx], "series": "liq_heatmap", "value": value, "side": _level_side(price_axis[price_idx], price_axis)})
        return {"time_axis": time_axis, "level_map": rows, "points": points, "max_value": max_value, "meta": {**_heatmap_meta(data), "orientation": "sparse_time_price"}}

    row_count = len(matrix)
    price_len = len(price_axis)
    orientation = "time_price" if row_count != price_len else "price_time"
    if row_count == len(time_axis) and row_count != price_len:
        orientation = "time_price"
    elif row_count == price_len and row_count != len(time_axis):
        orientation = "price_time"

    if orientation == "time_price":
        for time_idx, row in enumerate(matrix):
            if not isinstance(row, list):
                continue
            for price_idx, raw_value in enumerate(row[:price_len]):
                value = _number(raw_value)
                if value is None or value <= 0:
                    continue
                max_value = max(max_value, value)
                price = price_axis[price_idx]
                item = {"time_index": time_idx, "price_index": price_idx, "time": _axis_item(time_axis, time_idx), "price": price, "value": value, "side": _level_side(price, price_axis)}
                rows.append(item)
    else:
        for price_idx, row in enumerate(matrix[:price_len]):
            if not isinstance(row, list):
                continue
            for time_idx, raw_value in enumerate(row):
                value = _number(raw_value)
                if value is None or value <= 0:
                    continue
                max_value = max(max_value, value)
                price = price_axis[price_idx]
                item = {"time_index": time_idx, "price_index": price_idx, "time": _axis_item(time_axis, time_idx), "price": price, "value": value, "side": _level_side(price, price_axis)}
                rows.append(item)

    by_price: dict[int, float] = {}
    for item in rows:
        by_price[item["price_index"]] = by_price.get(item["price_index"], 0.0) + item["value"]
    for price_idx, value in by_price.items():
        points.append({"price": price_axis[price_idx], "series": "liq_heatmap", "value": value, "side": _level_side(price_axis[price_idx], price_axis)})
    return {"time_axis": time_axis, "level_map": rows, "points": points, "max_value": max_value, "meta": {**_heatmap_meta(data), "orientation": orientation}}


def _matrix_data(data: dict[str, Any]) -> list[Any]:
    heat = data.get("liqHeatMap")
    if isinstance(heat, dict):
        candidate = heat.get("data") or heat.get("values") or heat.get("list")
        if isinstance(candidate, list):
            return candidate
    for key in ("data", "matrix", "values"):
        candidate = data.get(key)
        if isinstance(candidate, list) and candidate and isinstance(candidate[0], list):
            return candidate
    return []


def _is_sparse_triplets(matrix: list[Any], time_len: int, price_len: int) -> bool:
    sample = [row for row in matrix[: min(len(matrix), 200)] if isinstance(row, list) and len(row) >= 3]
    if not sample:
        return False
    if len(matrix) <= max(time_len, price_len, 1) * 2 and not all(len(row) == 3 for row in sample):
        return False
    matches = 0
    for row in sample:
        time_idx = _index(row[0])
        price_idx = _index(row[1])
        value = _number(row[2])
        if time_idx is None or price_idx is None or value is None:
            continue
        if 0 <= price_idx < price_len and (not time_len or 0 <= time_idx < time_len):
            matches += 1
    return matches / max(len(sample), 1) >= 0.9


def _axis_item(axis: list[Any], idx: int) -> Any:
    return axis[idx] if 0 <= idx < len(axis) else idx


def _index(value: Any) -> int | None:
    numeric = _number(value)
    if numeric is None:
        return None
    rounded = int(numeric)
    if abs(numeric - rounded) > 1e-9:
        return None
    return rounded


def _level_side(price: float, price_axis: list[float]) -> str:
    return "short" if price >= _mid_price(price_axis) else "long"


def _heatmap_meta(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "tick_size": data.get("tickSize"),
        "chart_interval": data.get("chartInterval"),
        "start": data.get("start"),
        "end": data.get("end"),
    }


def _series(data: dict[str, Any], expected_len: int) -> list[tuple[str, list[Any]]]:
    result = []
    for key, values in data.items():
        if key in MATRIX_META_KEYS:
            continue
        if key in {"rows", "list", "values"} and isinstance(values, list) and values and isinstance(values[0], (dict, list)):
            continue
        if isinstance(values, list) and len(values) == expected_len:
            result.append((str(key), values))
    if result:
        return result
    rows = data.get("rows") or data.get("list") or data.get("values")
    if isinstance(rows, list):
        long_values = []
        short_values = []
        total_values = []
        for row in rows:
            if isinstance(row, dict):
                long_values.append(row.get("long") or row.get("longValue") or row.get("longVol") or 0)
                short_values.append(row.get("short") or row.get("shortValue") or row.get("shortVol") or 0)
                total_values.append(row.get("value") or row.get("vol") or row.get("amount") or 0)
            elif isinstance(row, list):
                long_values.append(row[1] if len(row) > 1 else 0)
                short_values.append(row[2] if len(row) > 2 else 0)
                total_values.append(row[1] if len(row) > 1 else 0)
        if any(_number(v) for v in long_values):
            result.append(("long", long_values))
        if any(_number(v) for v in short_values):
            result.append(("short", short_values))
        if not result and any(_number(v) for v in total_values):
            result.append(("value", total_values))
    return result


def _side_from_name(name: str) -> str:
    lowered = name.lower()
    if "long" in lowered or "多" in lowered:
        return "long"
    if "short" in lowered or "空" in lowered:
        return "short"
    return "unknown"


def _leverage_from_name(name: str) -> int | None:
    lowered = str(name or "").lower()
    digits = "".join(ch for ch in lowered if ch.isdigit())
    if not digits:
        return None
    try:
        value = int(digits)
    except ValueError:
        return None
    return value if 0 < value <= 500 else None


def _leverage_bucket(leverage: int | None) -> str | None:
    if leverage is None:
        return None
    if leverage <= 5:
        return "x5"
    if leverage <= 10:
        return "x10"
    if leverage <= 25:
        return "x25"
    if leverage <= 50:
        return "x50"
    return "x100"


def _series_label(name: str) -> str:
    leverage = _leverage_from_name(name)
    return f"{leverage}x" if leverage else str(name)


def _scope_from_series(series: list[tuple[str, list[Any]]]) -> str:
    if series and all(_leverage_from_name(name) is not None for name, _values in series):
        return "exchange_leverage"
    return "aggregate"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _empty(symbol: str, exchange: str, interval: str, error: str, raw: Any) -> dict[str, Any]:
    return {
        "has_data": False,
        "source": "claw402_coinank",
        "symbol": symbol.upper().replace("/", ""),
        "exchange": exchange.lower(),
        "interval": interval,
        "price_axis": [],
        "time_axis": [],
        "points": [],
        "level_map": [],
        "clusters": [],
        "max_value": 0,
        "max_liq_value": 0,
        "meta": {},
        "error": error,
        "raw": raw,
        "shape": {"price_count": 0, "price_levels": 0, "time_count": 0, "matrix_cells": 0, "series_count": 0, "point_count": 0},
    }


def _mid_price(price_axis: list[float]) -> float:
    if not price_axis:
        return 0.0
    ordered = sorted(price_axis)
    return ordered[len(ordered) // 2]
