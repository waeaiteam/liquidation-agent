from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class MarketDataError(RuntimeError):
    pass


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class BinanceMarketDataService:
    BASE_URL = "https://fapi.binance.com"

    def __init__(self, urlopen_fn: Callable[..., Any] = urlopen):
        self._urlopen = urlopen_fn

    def fetch_snapshot(self, symbol: str, *, exchange: str = "binance", interval: str = "1m", limit: int = 120) -> dict[str, Any]:
        if exchange.lower() != "binance":
            raise MarketDataError(f"Exchange '{exchange}' is not implemented for real-time market data")
        symbol = symbol.upper().replace("/", "")
        ticker = self._get("/fapi/v1/ticker/24hr", {"symbol": symbol})
        premium = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        open_interest = self._get("/fapi/v1/openInterest", {"symbol": symbol})
        klines = self._get("/fapi/v1/klines", {"symbol": symbol, "interval": self._binance_interval(interval), "limit": max(2, min(int(limit), 500))})

        price = _to_float(ticker.get("lastPrice") or premium.get("markPrice"))
        return {
            "source": "binance_futures",
            "exchange": "binance",
            "symbol": symbol,
            "timestamp": time.time(),
            "price": price,
            "change_24h_pct": _to_float(ticker.get("priceChangePercent")),
            "volume_24h_base": _to_float(ticker.get("volume")),
            "volume_24h_quote": _to_float(ticker.get("quoteVolume")),
            "high_24h": _to_float(ticker.get("highPrice")),
            "low_24h": _to_float(ticker.get("lowPrice")),
            "funding_rate": _to_float(premium.get("lastFundingRate")),
            "mark_price": _to_float(premium.get("markPrice"), price),
            "open_interest": _to_float(open_interest.get("openInterest")),
            "klines": [self._kline(row) for row in klines if isinstance(row, list) and len(row) >= 7],
        }

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        query = urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.BASE_URL}{path}?{query}" if query else f"{self.BASE_URL}{path}"
        req = Request(url, headers={"User-Agent": "liquidation-agent/0.1"})
        try:
            with self._urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise MarketDataError(f"Binance HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise MarketDataError(f"Binance request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise MarketDataError("Binance returned invalid JSON") from exc

    def _binance_interval(self, interval: str) -> str:
        value = str(interval or "1m")
        return value[:-1] + value[-1].lower() if value[-1:] in {"H", "D", "W", "M"} else value

    def _kline(self, row: list[Any]) -> dict[str, Any]:
        return {
            "open_time": int(row[0]),
            "open": _to_float(row[1]),
            "high": _to_float(row[2]),
            "low": _to_float(row[3]),
            "close": _to_float(row[4]),
            "volume": _to_float(row[5]),
            "close_time": int(row[6]),
            "quote_volume": _to_float(row[7] if len(row) > 7 else 0),
        }


market_data_service = BinanceMarketDataService()
