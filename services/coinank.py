from __future__ import annotations

import copy
import time
from typing import Any, Callable

from claw402 import Claw402

from strategy.models import MarketSnapshot


_CACHE_TTL_SECONDS = 45
_CACHE: dict[tuple[Any, ...], tuple[float, Any]] = {}


def _cache_key(name: str, *parts: Any) -> tuple[Any, ...]:
    return (name, *[str(part).lower() for part in parts])


def _cached_call(key: tuple[Any, ...], fetcher: Callable[[], Any]) -> Any:
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return copy.deepcopy(cached[1])
    data = fetcher()
    _CACHE[key] = (now, copy.deepcopy(data))
    return data


def _optional_call(label: str, key: tuple[Any, ...], fetcher: Callable[[], Any], fallback: Any, warnings: list[str]) -> Any:
    try:
        return _cached_call(key, fetcher)
    except Exception as exc:
        warnings.append(f"{label} unavailable: {exc}")
        return fallback


def create_client(private_key: str):
    try:
        client = Claw402(private_key=private_key)
        return client, client._account.address, ""
    except Exception as exc:
        return None, "", str(exc)


def coinank_exchange(exchange: str) -> str:
    value = str(exchange or "").strip()
    return {
        "binance": "Binance",
        "okx": "OKX",
        "bybit": "Bybit",
    }.get(value.lower(), value)


def unwrap_data(raw: Any, fallback: Any):
    if isinstance(raw, dict):
        return raw.get("data", raw)
    return fallback if raw is None else raw


def parse_intervals(raw_intervals: Any) -> dict[str, float]:
    data = unwrap_data(raw_intervals or {}, {}) or {}
    h1 = data.get("1h", {}) if isinstance(data, dict) else {}
    h24 = data.get("24h", {}) if isinstance(data, dict) else {}
    return {
        "h1Long": h1.get("longTurnover", 0) or 0,
        "h1Short": h1.get("shortTurnover", 0) or 0,
        "h1Total": h1.get("totalTurnover", 0) or 0,
        "h24Long": h24.get("longTurnover", 0) or 0,
        "h24Short": h24.get("shortTurnover", 0) or 0,
        "h24Total": h24.get("totalTurnover", 0) or 0,
    }


def parse_funding(raw_funding: Any) -> list[dict[str, Any]]:
    funding = []
    funding_list = unwrap_data(raw_funding or [], [])
    if not isinstance(funding_list, list):
        return funding
    for item in funding_list[:3]:
        if not isinstance(item, dict):
            continue
        sym = item.get("symbol", "")
        umap = item.get("umap", {}) or {}
        if not isinstance(umap, dict):
            continue
        for exchange, value in umap.items():
            if not isinstance(value, dict):
                continue
            rate = value.get("fr") or value.get("fundingRate") or 0
            funding.append({"symbol": sym, "exchange": exchange, "rate": rate})
    return funding


def fetch_market_snapshot(client, coin: str, symbol: str, exchange: str, interval: str, size: int, include_map: bool = False) -> MarketSnapshot:
    warnings: list[str] = []
    raw_intervals = _cached_call(_cache_key("liq_intervals", coin), lambda: client.coinank.liquidation.intervals(base_coin=coin))
    raw_price = _cached_call(_cache_key("price_last", symbol, exchange), lambda: client.coinank.price.last(symbol=symbol, exchange=exchange) or {})
    history = _optional_call(
        "liquidation history",
        _cache_key("liq_history", coin, interval, size),
        lambda: client.coinank.liquidation.agg_history(base_coin=coin, interval=interval, size=size) or [],
        [],
        warnings,
    )
    raw_oi = _optional_call("open interest", _cache_key("oi", coin), lambda: client.coinank.oi.all(base_coin=coin) or [], [], warnings)
    raw_ls = _optional_call("long/short ratio", _cache_key("longshort", coin), lambda: client.coinank.longshort.realtime(base_coin=coin) or [], [], warnings)
    raw_funding = _optional_call("funding rate", _cache_key("funding_current"), lambda: client.coinank.funding_rate.current(type_="current") or [], [], warnings)

    price_data = unwrap_data(raw_price, {})
    price = price_data.get("price", 0) if isinstance(price_data, dict) else 0
    oi = unwrap_data(raw_oi, [])
    long_short = unwrap_data(raw_ls, [])
    liq_map = {}
    if include_map:
        raw_map = _optional_call(
            "liquidation map",
            _cache_key("liq_map", coin, interval),
            lambda: client.coinank.liquidation.agg_liq_map(base_coin=coin, interval=interval),
            {},
            warnings,
        )
        liq_map = unwrap_data(raw_map, {}) or {}

    return MarketSnapshot(
        coin=coin,
        symbol=symbol,
        exchange=exchange,
        interval=interval,
        price=float(price or 0),
        intervals=parse_intervals(raw_intervals),
        history=history if isinstance(history, list) else unwrap_data(history, []),
        oi=oi if isinstance(oi, list) else [],
        long_short=long_short if isinstance(long_short, list) else [],
        funding=parse_funding(raw_funding),
        liq_map=liq_map if isinstance(liq_map, dict) else {},
        data_warnings=warnings,
    )
