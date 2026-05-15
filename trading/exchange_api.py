from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class BinanceSignedClient:
    def __init__(self, api_key: str, api_secret: str, *, base_url: str = "https://fapi.binance.com"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")

    def signed_request(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.api_key or not self.api_secret:
            raise ValueError("Binance API key and secret are required")
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        sig = hmac.digest(self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hex()
        query = query + "&signature=" + sig
        url = f"{self.base_url}{path}?{query}"
        req = Request(url, headers={"X-MBX-APIKEY": self.api_key, "User-Agent": "LIQ-agent"}, method=method.upper())
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def account(self) -> dict[str, Any]:
        return self.signed_request("GET", "/fapi/v2/account")

    def create_order(self, **params: Any) -> dict[str, Any]:
        return self.signed_request("POST", "/fapi/v1/order", params)

    def health_check(self) -> dict[str, Any]:
        try:
            account = self.account()
        except Exception as exc:
            return {"ok": False, "status": "fail", "reason": str(exc)}
        assets = account.get("assets") if isinstance(account, dict) else []
        usdt = next((item for item in assets if item.get("asset") == "USDT"), {}) if isinstance(assets, list) else {}
        available = float(usdt.get("availableBalance") or account.get("availableBalance") or 0)
        can_trade = bool(account.get("canTrade", True))
        blockers = []
        if not can_trade:
            blockers.append("account canTrade=false")
        if available <= 0:
            blockers.append("USDT available balance is zero")
        return {
            "ok": not blockers,
            "status": "pass" if not blockers else "warn",
            "can_trade": can_trade,
            "available_usdt": available,
            "blockers": blockers,
        }
