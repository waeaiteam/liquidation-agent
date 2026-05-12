from __future__ import annotations

import math
from typing import Any

from services.coinank import unwrap_data


PRICE_KEYS = ("prices", "price", "priceList", "price_list", "priceAxis", "price_axis")
MATRIX_META_KEYS = {
    "prices", "price", "priceList", "price_list", "priceAxis", "price_axis",
    "symbol", "exchange", "interval", "baseCoin", "base_coin", "time", "timestamp",
    "lastPrice", "last_price", "lastIndex", "last_index",
}


def normalize_liquidation_map(raw: Any, *, symbol: str, exchange: str, interval: str) -> dict[str, Any]:
    payload = unwrap_data(raw, {}) or {}
    data = _map_payload(payload)
    price_axis = _price_axis(data)
    if not price_axis:
        return _empty(symbol, exchange, interval, "No liquidation price axis found in Claw402 response", raw)

    series = _series(data, len(price_axis))
    points = []
    for name, values in series:
        for idx, value in enumerate(values):
            numeric = _number(value)
            if numeric is None or numeric <= 0:
                continue
            points.append({
                "price": price_axis[idx],
                "series": name,
                "value": numeric,
                "side": _side_from_name(name),
            })

    if not points:
        return _empty(symbol, exchange, interval, "No positive liquidation values found in Claw402 response", raw)

    max_value = max(p["value"] for p in points)
    return {
        "has_data": True,
        "source": "claw402_coinank",
        "symbol": symbol.upper().replace("/", ""),
        "exchange": exchange.lower(),
        "interval": interval,
        "price_axis": price_axis,
        "points": points,
        "max_value": max_value,
        "raw": raw,
        "shape": {
            "price_count": len(price_axis),
            "series_count": len(series),
            "point_count": len(points),
        },
    }


def _map_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for key in ("agg_map", "liq_map", "heat_map", "map", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict) and nested is not payload:
            return _map_payload(nested)
    return payload


def _price_axis(data: dict[str, Any]) -> list[float]:
    for key in PRICE_KEYS:
        values = data.get(key)
        if isinstance(values, list):
            numbers = [_number(v) for v in values]
            result = [v for v in numbers if v is not None and math.isfinite(v)]
            if result:
                return result
    return []


def _series(data: dict[str, Any], expected_len: int) -> list[tuple[str, list[Any]]]:
    result = []
    for key, values in data.items():
        if key in MATRIX_META_KEYS:
            continue
        if isinstance(values, list) and len(values) == expected_len:
            result.append((str(key), values))
    return result


def _side_from_name(name: str) -> str:
    lowered = name.lower()
    if "long" in lowered or "多" in lowered:
        return "long"
    if "short" in lowered or "空" in lowered:
        return "short"
    return "unknown"


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
        "points": [],
        "max_value": 0,
        "error": error,
        "raw": raw,
        "shape": {"price_count": 0, "series_count": 0, "point_count": 0},
    }
