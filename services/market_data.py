from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class MarketDataError(RuntimeError):
    pass


class MarketDataRestrictedError(MarketDataError):
    pass


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_symbol(symbol: str) -> str:
    return str(symbol or "BTCUSDT").upper().replace("/", "").replace("-", "").replace("_", "")


class BinanceMarketDataService:
    BINANCE_BASE_URL = "https://fapi.binance.com"
    OKX_BASE_URL = "https://www.okx.com"
    BYBIT_BASE_URL = "https://api.bybit.com"

    def __init__(self, urlopen_fn: Callable[..., Any] = urlopen):
        self._urlopen = urlopen_fn

    def fetch_snapshot(
        self,
        symbol: str,
        *,
        exchange: str = "binance",
        interval: str = "1m",
        limit: int = 120,
    ) -> dict[str, Any]:
        exchange = str(exchange or "binance").strip().lower()
        if exchange == "binance":
            return self._fetch_binance(symbol, interval, limit)
        if exchange == "okx":
            return self._fetch_okx(symbol, interval, limit)
        if exchange == "bybit":
            return self._fetch_bybit(symbol, interval, limit)
        raise MarketDataError(f"Exchange '{exchange}' is not implemented for real-time market data")

    def _fetch_binance(self, symbol: str, interval: str, limit: int) -> dict[str, Any]:
        symbol = _clean_symbol(symbol)
        ticker = self._get(self.BINANCE_BASE_URL, "/fapi/v1/ticker/24hr", {"symbol": symbol}, "Binance")
        premium = self._get(self.BINANCE_BASE_URL, "/fapi/v1/premiumIndex", {"symbol": symbol}, "Binance")
        open_interest = self._get(self.BINANCE_BASE_URL, "/fapi/v1/openInterest", {"symbol": symbol}, "Binance")
        klines = self._get(
            self.BINANCE_BASE_URL,
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": self._binance_interval(interval), "limit": self._limit(limit)},
            "Binance",
        )

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
            "klines": [self._binance_kline(row) for row in klines if isinstance(row, list) and len(row) >= 7],
        }

    def _fetch_okx(self, symbol: str, interval: str, limit: int) -> dict[str, Any]:
        inst_id = self._okx_inst_id(symbol)
        ticker = self._okx_data(self._get(self.OKX_BASE_URL, "/api/v5/market/ticker", {"instId": inst_id}, "OKX"), "ticker")
        funding = self._okx_data(self._get(self.OKX_BASE_URL, "/api/v5/public/funding-rate", {"instId": inst_id}, "OKX"), "funding-rate")
        oi = self._okx_data(self._get(self.OKX_BASE_URL, "/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst_id}, "OKX"), "open-interest")
        candles = self._okx_rows(self._get(
            self.OKX_BASE_URL,
            "/api/v5/market/candles",
            {"instId": inst_id, "bar": self._okx_interval(interval), "limit": self._limit(limit)},
            "OKX",
        ), "candles")

        price = _to_float(ticker.get("last"))
        open_24h = _to_float(ticker.get("open24h"))
        change = ((price - open_24h) / open_24h * 100.0) if open_24h else 0.0
        return {
            "source": "okx_swap",
            "exchange": "okx",
            "symbol": _clean_symbol(symbol),
            "inst_id": inst_id,
            "timestamp": time.time(),
            "price": price,
            "change_24h_pct": change,
            "volume_24h_base": _to_float(ticker.get("volCcy24h")),
            "volume_24h_quote": _to_float(ticker.get("volCcy24h")) * price if price else 0.0,
            "high_24h": _to_float(ticker.get("high24h")),
            "low_24h": _to_float(ticker.get("low24h")),
            "funding_rate": _to_float(funding.get("fundingRate")),
            "mark_price": price,
            "open_interest": _to_float(oi.get("oiCcy") or oi.get("oi")),
            "klines": [self._okx_kline(row) for row in reversed(candles) if isinstance(row, list) and len(row) >= 6],
        }

    def _fetch_bybit(self, symbol: str, interval: str, limit: int) -> dict[str, Any]:
        symbol = _clean_symbol(symbol)
        ticker = self._bybit_first(self._get(self.BYBIT_BASE_URL, "/v5/market/tickers", {"category": "linear", "symbol": symbol}, "Bybit"), "tickers")
        candles = self._bybit_rows(self._get(
            self.BYBIT_BASE_URL,
            "/v5/market/kline",
            {"category": "linear", "symbol": symbol, "interval": self._bybit_interval(interval), "limit": self._limit(limit)},
            "Bybit",
        ), "kline")

        price = _to_float(ticker.get("lastPrice"))
        return {
            "source": "bybit_linear",
            "exchange": "bybit",
            "symbol": symbol,
            "timestamp": time.time(),
            "price": price,
            "change_24h_pct": _to_float(ticker.get("price24hPcnt")) * 100.0,
            "volume_24h_base": _to_float(ticker.get("volume24h")),
            "volume_24h_quote": _to_float(ticker.get("turnover24h")),
            "high_24h": _to_float(ticker.get("highPrice24h")),
            "low_24h": _to_float(ticker.get("lowPrice24h")),
            "funding_rate": _to_float(ticker.get("fundingRate")),
            "mark_price": _to_float(ticker.get("markPrice"), price),
            "open_interest": _to_float(ticker.get("openInterest")),
            "klines": [self._bybit_kline(row) for row in reversed(candles) if isinstance(row, list) and len(row) >= 7],
        }

    def _get(self, base_url: str, path: str, params: dict[str, Any], label: str) -> Any:
        query = urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{base_url}{path}?{query}" if query else f"{base_url}{path}"
        req = Request(url, headers={"User-Agent": "liquidation-agent/0.1", "Accept": "application/json"})
        try:
            with self._urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            message = f"{label} HTTP {exc.code}: {detail}"
            if exc.code in {451, 403} and ("restricted" in detail.lower() or label in {"Binance", "Bybit"}):
                raise MarketDataRestrictedError(message) from exc
            raise MarketDataError(message) from exc
        except URLError as exc:
            raise MarketDataError(f"{label} request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise MarketDataError(f"{label} returned invalid JSON") from exc

    def _okx_data(self, payload: Any, name: str) -> dict[str, Any]:
        rows = self._okx_rows(payload, name)
        if not rows:
            raise MarketDataError(f"OKX {name} returned no data")
        return rows[0]

    def _okx_rows(self, payload: Any, name: str) -> list[Any]:
        if not isinstance(payload, dict):
            raise MarketDataError(f"OKX {name} returned unexpected payload")
        if str(payload.get("code")) != "0":
            raise MarketDataError(f"OKX {name} error {payload.get('code')}: {payload.get('msg')}")
        data = payload.get("data")
        return data if isinstance(data, list) else []

    def _bybit_first(self, payload: Any, name: str) -> dict[str, Any]:
        rows = self._bybit_rows(payload, name)
        if not rows:
            raise MarketDataError(f"Bybit {name} returned no data")
        return rows[0]

    def _bybit_rows(self, payload: Any, name: str) -> list[Any]:
        if not isinstance(payload, dict):
            raise MarketDataError(f"Bybit {name} returned unexpected payload")
        if int(payload.get("retCode", -1)) != 0:
            raise MarketDataError(f"Bybit {name} error {payload.get('retCode')}: {payload.get('retMsg')}")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        data = result.get("list")
        return data if isinstance(data, list) else []

    def _limit(self, limit: int) -> int:
        return max(2, min(int(limit or 120), 500))

    def _binance_interval(self, interval: str) -> str:
        value = str(interval or "1m")
        return value[:-1] + value[-1].lower() if value[-1:] in {"H", "D", "W", "M"} else value

    def _okx_interval(self, interval: str) -> str:
        value = str(interval or "1m")
        if value.endswith("h"):
            return value[:-1] + "H"
        if value.endswith("d"):
            return value[:-1] + "D"
        if value.endswith("w"):
            return value[:-1] + "W"
        return value

    def _bybit_interval(self, interval: str) -> str:
        value = self._binance_interval(interval)
        if value.endswith("m"):
            return value[:-1]
        if value.endswith("h"):
            return str(int(value[:-1]) * 60)
        if value.endswith("d"):
            return "D"
        if value.endswith("w"):
            return "W"
        if value.endswith("M"):
            return "M"
        return value

    def _okx_inst_id(self, symbol: str) -> str:
        value = str(symbol or "BTCUSDT").upper().replace("/", "-")
        if value.endswith("-SWAP"):
            return value
        if "-" in value:
            return f"{value}-SWAP"
        clean = _clean_symbol(value)
        if not clean.endswith("USDT"):
            raise MarketDataError(f"OKX swap symbol must be USDT-margined: {symbol}")
        return f"{clean[:-4]}-USDT-SWAP"

    def _binance_kline(self, row: list[Any]) -> dict[str, Any]:
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

    def _okx_kline(self, row: list[Any]) -> dict[str, Any]:
        open_time = int(row[0])
        return {
            "open_time": open_time,
            "open": _to_float(row[1]),
            "high": _to_float(row[2]),
            "low": _to_float(row[3]),
            "close": _to_float(row[4]),
            "volume": _to_float(row[5]),
            "close_time": open_time,
            "quote_volume": _to_float(row[6] if len(row) > 6 else 0),
        }

    def _bybit_kline(self, row: list[Any]) -> dict[str, Any]:
        open_time = int(row[0])
        return {
            "open_time": open_time,
            "open": _to_float(row[1]),
            "high": _to_float(row[2]),
            "low": _to_float(row[3]),
            "close": _to_float(row[4]),
            "volume": _to_float(row[5]),
            "close_time": open_time,
            "quote_volume": _to_float(row[6]),
        }


market_data_service = BinanceMarketDataService()
