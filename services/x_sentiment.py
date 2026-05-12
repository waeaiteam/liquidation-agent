"""Crypto X (Twitter) sentiment via xAI Grok Live Search.

Architecture (2026):
- Uses xAI Grok API's native Live Search with X source
- Single API call returns sentiment + trending coins + top tweets (all structured)
- No separate X API subscription required (saves $200/mo Basic tier)
- Pay-per-call: ~$0.20/1M input + $0.50/1M output (Grok 4 Fast) + ~$25/1K X search sources

Official docs: https://docs.x.ai/docs/guides/live-search
API endpoint: https://api.x.ai/v1/chat/completions (OpenAI-compatible)
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None


XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.3"
MAX_SEARCH_RESULTS = 25  # hard cap to control cost

SYSTEM_PROMPT = """You are a crypto X (Twitter) sentiment analyst. Given the user's request, search X for recent crypto discussions and return STRUCTURED JSON ONLY (no prose, no markdown fences) matching this schema:

{
  "sentiment": {
    "score": <int 0-100, 50=neutral>,
    "label": "bullish|bearish|neutral",
    "summary": "<1-2 sentence Chinese summary>",
    "total_analyzed": <int>
  },
  "trending": [
    {"coin": "BTC", "mentions": <int>, "sentiment_pct": <0-100>, "label": "bullish|bearish|neutral", "headline": "<1 sentence Chinese summary of discussion>"}
  ],
  "top_tweets": [
    {"author": "<display name>", "handle": "<username without @>", "text": "<tweet text>", "sentiment_label": "positive|negative|neutral", "url": "<full https://x.com/... URL if known else empty>", "engagement_estimate": "<low|medium|high|viral>"}
  ],
  "narrative": "<3-4 sentence Chinese analysis of current crypto X narrative, risks, opportunities>"
}

Rules:
- Return ONLY the JSON object, no additional text
- trending: top 10 coins by mention volume, sorted descending
- top_tweets: 8-10 most impactful tweets
- Use Chinese for all summary/narrative/headline fields
- If no data found, return the schema with empty arrays and score=50
"""


@dataclass
class XDataResult:
    sentiment: dict[str, Any]
    trending: list[dict[str, Any]]
    top_tweets: list[dict[str, Any]]
    narrative: str
    fetched_at: str
    citations: list[str]


class XSentimentService:
    """Crypto X sentiment via Grok Live Search — single API call pattern."""

    def __init__(self) -> None:
        self._api_key: str = ""
        self._model: str = DEFAULT_MODEL
        self._base_url: str = XAI_BASE_URL
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_ttl = 900  # 15 min default
        self._api_calls_total: int = 0
        self._last_fetch_ts: float = 0.0
        self._last_cost_estimate: float = 0.0

    def configure(self, api_key: str, model: str | None = None, cache_ttl: int | None = None, base_url: str | None = None) -> None:
        self._api_key = (api_key or "").strip()
        if model:
            self._model = model
        if base_url:
            self._base_url = base_url.rstrip("/")
        if cache_ttl is not None:
            self._cache_ttl = int(cache_ttl)

    def is_configured(self) -> bool:
        return bool(self._api_key) and httpx is not None

    def _cache_get(self, key: str) -> Any | None:
        if key in self._cache:
            ts, val = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return val
        return None

    def _cache_set(self, key: str, val: Any) -> None:
        self._cache[key] = (time.time(), val)

    def _cache_age(self, key: str) -> float | None:
        if key in self._cache:
            return time.time() - self._cache[key][0]
        return None

    def _extract_json(self, text: str) -> dict[str, Any]:
        """Grok sometimes wraps JSON in markdown fences despite instructions."""
        text = text.strip()
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start:end + 1]
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return {"sentiment": {"score": 50, "label": "neutral", "summary": "JSON parse error"}, "trending": [], "top_tweets": [], "narrative": ""}

    def _call_grok(self, user_prompt: str, max_results: int = MAX_SEARCH_RESULTS) -> tuple[dict[str, Any], list[str]]:
        """Make one Grok API call with Live Search on X source. Returns (parsed_json, citations)."""
        if not self.is_configured():
            raise RuntimeError("xAI API not configured")

        now = datetime.now(timezone.utc)
        from_date = (now - timedelta(hours=2)).strftime("%Y-%m-%d")
        to_date = now.strftime("%Y-%m-%d")

        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }

        is_official_xai = "api.x.ai" in self._base_url
        if is_official_xai:
            body["search_parameters"] = {
                "mode": "on",
                "sources": [{"type": "x"}],
                "from_date": from_date,
                "to_date": to_date,
                "max_search_results": max_results,
                "return_citations": True,
            }

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        self._api_calls_total += 1
        # rough cost estimate: $25/1K sources + tokens (Grok 4 Fast)
        usage = data.get("usage", {})
        in_tok = usage.get("prompt_tokens", 0)
        out_tok = usage.get("completion_tokens", 0)
        sources_used = usage.get("num_sources_used", max_results)
        self._last_cost_estimate += (
            (in_tok / 1_000_000) * 0.20
            + (out_tok / 1_000_000) * 0.50
            + (sources_used / 1000) * 25.0
        )

        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content", "")
        citations = data.get("citations", []) or choice.get("message", {}).get("citations", []) or []
        parsed = self._extract_json(content)
        return parsed, citations

    def fetch_all(self, force: bool = False) -> dict[str, Any]:
        """Single Grok API call → sentiment + trending + top_tweets + narrative.

        15-min cache by default. Pass force=True to bypass.
        """
        cache_key = "all"
        if not force:
            cached = self._cache_get(cache_key)
            if cached:
                return cached

        prompt = (
            "搜索最近 1-2 小时 X 上关于加密货币的讨论（关键词: crypto, bitcoin, ethereum, "
            "$BTC, $ETH, $SOL, $BNB, $XRP, $DOGE, $ADA, $AVAX 以及其他热门币种）。"
            "分析整体情绪、识别讨论热度最高的币种、挑选影响力最大的推文。"
            "按规定的 JSON 格式返回结果。"
        )
        try:
            parsed, citations = self._call_grok(prompt, max_results=MAX_SEARCH_RESULTS)
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"xAI API error: {e.response.status_code} {e.response.text[:200]}") from e
        except Exception as e:
            raise RuntimeError(f"xAI API call failed: {e}") from e

        self._last_fetch_ts = time.time()

        # Normalize with defaults
        sentiment = parsed.get("sentiment") or {}
        sentiment.setdefault("score", 50)
        sentiment.setdefault("label", "neutral")
        sentiment.setdefault("summary", "")
        sentiment.setdefault("total_analyzed", 0)

        trending = parsed.get("trending") or []
        top_tweets = parsed.get("top_tweets") or []
        narrative = parsed.get("narrative") or ""

        result = {
            "sentiment": sentiment,
            "trending": trending,
            "top_tweets": top_tweets,
            "narrative": narrative,
            "citations": citations,
            "meta": {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "cache_ttl_sec": self._cache_ttl,
                "api_calls_total": self._api_calls_total,
                "cost_estimate_usd": round(self._last_cost_estimate, 4),
                "model": self._model,
                "sources_used": len(citations),
            },
        }
        self._cache_set(cache_key, result)
        return result

    def ai_narrative(self, user_question: str = "") -> str:
        """Optional: deeper analysis reusing cached X data (no extra X source fee)."""
        data = self.fetch_all()
        # For now the narrative is already included in fetch_all()
        return data.get("narrative", "")

    def status_info(self) -> dict[str, Any]:
        age = self._cache_age("all")
        return {
            "configured": self.is_configured(),
            "model": self._model,
            "cache_age_sec": round(age, 1) if age is not None else None,
            "cache_ttl_sec": self._cache_ttl,
            "next_refresh_sec": round(self._cache_ttl - age, 1) if age is not None and age < self._cache_ttl else 0,
            "api_calls_total": self._api_calls_total,
            "cost_estimate_usd": round(self._last_cost_estimate, 4),
        }


_service: XSentimentService | None = None


def get_service() -> XSentimentService:
    global _service
    if _service is None:
        _service = XSentimentService()
        key = os.getenv("XAI_API_KEY", "").strip()
        if key:
            _service.configure(key)
    return _service
