"""Tweet Pipeline: one-click generate candidate tweets from combined data sources.

Flow: collect market + strategy + X sentiment data → LLM generates 3 candidate tweets → user reviews → publish.

Supports all providers in providers.py (OpenAI, Anthropic, xAI, DeepSeek, Qwen, etc.)
plus relay/proxy services (OpenRouter, 302.ai, etc.) via custom base URL.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import anthropic
except ImportError:
    anthropic = None

from providers import LLM_PROVIDERS


PIPELINE_SYSTEM_PROMPT = """你是加密货币交易策略推文生成器。你的任务是根据实时市场数据、策略信号和 X 社区情绪，生成高质量的候选推文。

规则：
- 每条推文 ≤ 280 字符（含 emoji 和 cashtag）
- 生成 3 条不同风格的候选推文
- 风格分别为：理性分析、激进观点、幽默吐槽
- 必须包含相关 cashtag（如 $BTC $ETH）
- 不承诺收益，不构成投资建议
- 中文为主，cashtag/hashtag 用英文
- 数据有支撑的观点才写，没有数据就不要硬编

返回格式（纯 JSON，无其他文字）：
{
  "candidates": [
    {
      "text": "推文正文",
      "style": "理性分析 | 激进观点 | 幽默吐槽",
      "char_count": 142,
      "data_sources": ["market", "signal", "sentiment"],
      "confidence": 0.75
    }
  ],
  "context_summary": "一句话概括当前市场状态"
}
"""


def _get_provider(provider_id: str) -> dict:
    for p in LLM_PROVIDERS:
        if p["id"] == provider_id:
            return p
    return {}


def _resolve_base_url(provider_id: str, custom_base_url: str | None) -> str:
    if custom_base_url:
        return custom_base_url.rstrip("/")
    provider = _get_provider(provider_id)
    return (provider.get("base") or "").rstrip("/")


class TweetPipelineService:
    def __init__(self) -> None:
        self._default_provider: str = "xai"
        self._default_api_key: str = ""
        self._default_model: str = ""
        self._default_base_url: str = ""
        self._generation_history: list[dict[str, Any]] = []
        self._api_calls: int = 0
        self._cost_usd: float = 0.0

    def configure(self, api_key: str, model: str | None = None, provider_id: str | None = None, base_url: str | None = None) -> None:
        self._default_api_key = (api_key or "").strip()
        if model:
            self._default_model = model
        if provider_id:
            self._default_provider = provider_id
        if base_url:
            self._default_base_url = base_url.rstrip("/")

    def is_configured(self) -> bool:
        return bool(self._default_api_key)

    def generate(
        self,
        agent_state: Any,
        x_sentiment_service: Any,
        *,
        provider_id: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        custom_base_url: str | None = None,
    ) -> dict[str, Any]:
        provider_id = provider_id or self._default_provider
        api_key = (api_key or "").strip() or self._default_api_key
        model = (model or "").strip() or self._default_model
        custom_base_url = (custom_base_url or "").strip() or self._default_base_url

        if not api_key:
            raise RuntimeError("Pipeline LLM API key not configured")

        if not model:
            provider = _get_provider(provider_id)
            model = (provider.get("mdl") or [""])[0]
        if not model:
            raise RuntimeError("Pipeline LLM model not specified")

        context = self._collect_context(agent_state, x_sentiment_service)
        user_prompt = self._build_prompt(context)

        if provider_id == "anthropic":
            reply_text = self._call_anthropic(api_key, model, user_prompt)
        else:
            base_url = _resolve_base_url(provider_id, custom_base_url)
            if not base_url:
                raise RuntimeError(f"No base URL for provider '{provider_id}'. Use custom_base_url.")
            reply_text = self._call_openai_compatible(base_url, api_key, model, user_prompt)

        self._api_calls += 1
        parsed = self._extract_json(reply_text)

        candidates = parsed.get("candidates") or []
        for c in candidates:
            c["char_count"] = len(c.get("text") or "")

        result = {
            "candidates": candidates,
            "context_summary": parsed.get("context_summary", ""),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider_id,
            "model": model,
            "context_used": context,
        }
        self._generation_history.append(result)
        if len(self._generation_history) > 50:
            self._generation_history = self._generation_history[-50:]
        return result

    def _call_anthropic(self, api_key: str, model: str, user_prompt: str) -> str:
        if not anthropic:
            raise RuntimeError("anthropic package not installed")
        client = anthropic.Anthropic(api_key=api_key, timeout=60.0)
        response = client.messages.create(
            model=model,
            max_tokens=1600,
            system=[{"type": "text", "text": PIPELINE_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_prompt}],
        )
        return "\n".join(block.text for block in response.content if block.type == "text").strip()

    def _call_openai_compatible(self, base_url: str, api_key: str, model: str, user_prompt: str) -> str:
        url = base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": PIPELINE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.6,
        }
        req = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"LLM API HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"LLM API request failed: {exc.reason}") from exc
        choices = data.get("choices") or []
        if not choices:
            return json.dumps(data, ensure_ascii=False)
        return (choices[0].get("message") or {}).get("content", "").strip()

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        return [
            {
                "candidates": h["candidates"],
                "context_summary": h["context_summary"],
                "generated_at": h["generated_at"],
                "provider": h.get("provider", ""),
                "model": h.get("model", ""),
            }
            for h in self._generation_history[-limit:][::-1]
        ]

    def status_info(self) -> dict[str, Any]:
        return {
            "configured": self.is_configured(),
            "default_provider": self._default_provider,
            "default_model": self._default_model,
            "default_base_url": self._default_base_url or None,
            "total_calls": self._api_calls,
            "generations_count": len(self._generation_history),
        }

    def _collect_context(self, agent_state: Any, x_sentiment_service: Any) -> dict[str, Any]:
        context: dict[str, Any] = {"market": {}, "signal": {}, "sentiment": {}}

        snapshot = getattr(agent_state, "last_snapshot", None) or {}
        if snapshot:
            context["market"] = {
                "symbol": snapshot.get("symbol", ""),
                "price": snapshot.get("price", 0),
                "funding_rate": snapshot.get("funding_rate", 0),
                "open_interest": snapshot.get("open_interest") or snapshot.get("oi", 0),
            }

        last_signal = getattr(agent_state, "last_signal", None) or {}
        last_risk = getattr(agent_state, "last_risk", None) or {}
        phase = getattr(agent_state, "agent_phase", "STOPPED")
        context["signal"] = {
            "action": last_signal.get("action", "wait"),
            "side": last_signal.get("side", ""),
            "confidence": last_signal.get("confidence", 0),
            "phase": phase,
            "risk_approved": last_risk.get("approved", False),
        }

        if x_sentiment_service and x_sentiment_service.is_configured():
            try:
                x_data = x_sentiment_service.fetch_all()
                sentiment = x_data.get("sentiment", {})
                trending = x_data.get("trending", [])[:5]
                context["sentiment"] = {
                    "score": sentiment.get("score", 50),
                    "label": sentiment.get("label", "neutral"),
                    "summary": sentiment.get("summary", ""),
                    "trending": [
                        {"coin": t.get("coin", ""), "sentiment_pct": t.get("sentiment_pct", 50)}
                        for t in trending
                    ],
                    "narrative": x_data.get("narrative", ""),
                }
            except Exception:
                context["sentiment"] = {"score": 50, "label": "neutral", "error": "fetch failed"}
        else:
            context["sentiment"] = {"score": 50, "label": "neutral", "error": "not configured"}

        return context

    def _build_prompt(self, context: dict[str, Any]) -> str:
        market = context.get("market", {})
        signal = context.get("signal", {})
        sentiment = context.get("sentiment", {})

        parts = ["请根据以下实时数据生成 3 条候选推文：\n"]

        if market.get("price"):
            parts.append(f"【市场数据】{market.get('symbol', 'BTC')} 当前价格 ${market['price']:,.2f}")
            if market.get("funding_rate"):
                parts.append(f"  资金费率: {float(market['funding_rate']):.4%}")
            if market.get("open_interest"):
                parts.append(f"  持仓量: ${float(market['open_interest']):,.0f}")

        if signal.get("action") != "wait":
            parts.append(f"\n【策略信号】方向: {signal.get('side', '-')} | 动作: {signal.get('action', '-')} | 置信度: {signal.get('confidence', 0):.0%}")
            parts.append(f"  当前阶段: {signal.get('phase', '-')}")

        if sentiment.get("score") and not sentiment.get("error"):
            parts.append(f"\n【X 社区情绪】得分: {sentiment['score']}/100 ({sentiment.get('label', '-')})")
            if sentiment.get("summary"):
                parts.append(f"  摘要: {sentiment['summary']}")
            if sentiment.get("trending"):
                coins = ", ".join(f"${t['coin']}" for t in sentiment["trending"][:3])
                parts.append(f"  热门: {coins}")
            if sentiment.get("narrative"):
                parts.append(f"  叙事: {sentiment['narrative'][:200]}")

        return "\n".join(parts)

    def _extract_json(self, text: str) -> dict[str, Any]:
        text = text.strip()
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        return {"candidates": [], "context_summary": "JSON parse failed"}


_pipeline: TweetPipelineService | None = None


def get_pipeline_service() -> TweetPipelineService:
    global _pipeline
    if _pipeline is None:
        _pipeline = TweetPipelineService()
        key = os.getenv("XAI_API_KEY", "").strip()
        if key:
            _pipeline.configure(key, provider_id="xai")
    return _pipeline
