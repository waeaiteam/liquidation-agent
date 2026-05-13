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
from services.llm import _as_dict, _openai_response_text


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


IMAGE_SYSTEM_PROMPT = """你是加密货币市场视觉卡片策划师。根据市场数据和候选推文，为每条推文生成一张适合 X 发布的 16:9 市场图片卡片方案。
要求：
- 只输出 JSON，不要 Markdown。
- 每张卡片包含 title、subtitle、metric_label、metric_value、trend、bullets、risk_note、alt_text。
- 不要编造没有给出的价格、收益率、清算金额；没有数据就写“等待实时数据”。
- 语气专业、克制，适合交易员快速扫描。

返回格式：
{
  "image_cards": [
    {
      "title": "BTC 关键区间观察",
      "subtitle": "OKX · 15m · 实时市场",
      "metric_label": "现价",
      "metric_value": "$82,000",
      "trend": "neutral",
      "bullets": ["资金费率等待实时数据", "清算热区等待实时数据"],
      "risk_note": "非投资建议",
      "alt_text": "市场图卡文字描述"
    }
  ]
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


def _to_float(value: Any, default: float | None = 0.0) -> float | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if isinstance(value, dict):
        for key in ("value", "rate", "funding_rate", "fundingRate", "openInterest", "oi", "price", "last", "markPrice"):
            if key in value:
                parsed = _to_float(value.get(key), None)
                if parsed is not None:
                    return parsed
        return default
    if isinstance(value, list):
        for item in value:
            parsed = _to_float(item, None)
            if parsed is not None:
                return parsed
        return default
    return default


def _compact_json(value: Any, limit: int = 8000) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)[:limit]


def _fmt_price(value: Any) -> str:
    parsed = _to_float(value, None)
    return "-" if parsed is None else f"${parsed:,.2f}"


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
        image_provider_id: str | None = None,
        image_api_key: str | None = None,
        image_model: str | None = None,
        image_custom_base_url: str | None = None,
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
        user_prompt = self._build_prompt_safe(context)

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

        image_error = ""
        image_cards = []
        if candidates:
            try:
                image_cards = self._generate_image_cards(
                    context,
                    candidates,
                    provider_id=image_provider_id or provider_id,
                    api_key=(image_api_key or "").strip() or api_key,
                    model=(image_model or "").strip() or model,
                    custom_base_url=(image_custom_base_url or "").strip() or custom_base_url,
                )
            except Exception as exc:
                image_error = str(exc)
                image_cards = self._fallback_image_cards(context, candidates)
            if len(image_cards) < len(candidates):
                image_cards.extend(self._fallback_image_cards(context, candidates[len(image_cards):]))
            for idx, candidate in enumerate(candidates):
                candidate["image_card"] = image_cards[idx]

        result = {
            "candidates": candidates,
            "context_summary": parsed.get("context_summary", ""),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider_id,
            "model": model,
            "image_provider": image_provider_id or provider_id,
            "image_model": image_model or model,
            "image_error": image_error,
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
        return _openai_response_text(data)

    def _call_provider(self, provider_id: str, api_key: str, model: str, prompt: str, system_prompt: str) -> str:
        if provider_id == "anthropic":
            if not anthropic:
                raise RuntimeError("anthropic package not installed")
            client = anthropic.Anthropic(api_key=api_key, timeout=60.0)
            response = client.messages.create(
                model=model,
                max_tokens=1600,
                system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": prompt}],
            )
            return "\n".join(block.text for block in response.content if block.type == "text").strip()
        base_url = _resolve_base_url(provider_id, None)
        if not base_url:
            raise RuntimeError(f"No base URL for provider '{provider_id}'. Use custom_base_url.")
        return self._call_openai_compatible_with_system(base_url, api_key, model, prompt, system_prompt)

    def _call_openai_compatible_with_system(self, base_url: str, api_key: str, model: str, user_prompt: str, system_prompt: str) -> str:
        url = base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.35,
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
            raise RuntimeError(f"Image LLM API HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Image LLM API request failed: {exc.reason}") from exc
        return _openai_response_text(data)

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

    def _generate_image_cards(
        self,
        context: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        provider_id: str | None,
        api_key: str,
        model: str,
        custom_base_url: str | None,
    ) -> list[dict[str, Any]]:
        provider_id = provider_id or self._default_provider
        if not api_key:
            return self._fallback_image_cards(context, candidates)
        if not model:
            provider = _get_provider(provider_id)
            model = (provider.get("mdl") or [""])[0]
        if not model:
            return self._fallback_image_cards(context, candidates)
        prompt = self._build_image_prompt(context, candidates)
        if provider_id == "anthropic":
            reply_text = self._call_anthropic_with_system(api_key, model, prompt, IMAGE_SYSTEM_PROMPT)
        else:
            base_url = _resolve_base_url(provider_id, custom_base_url)
            if not base_url:
                return self._fallback_image_cards(context, candidates)
            reply_text = self._call_openai_compatible_with_system(base_url, api_key, model, prompt, IMAGE_SYSTEM_PROMPT)
        parsed = self._extract_json(reply_text)
        cards = parsed.get("image_cards") or []
        if not isinstance(cards, list):
            return self._fallback_image_cards(context, candidates)
        return [self._normalize_image_card(card, context, idx) for idx, card in enumerate(cards)]

    def _call_anthropic_with_system(self, api_key: str, model: str, user_prompt: str, system_prompt: str) -> str:
        if not anthropic:
            raise RuntimeError("anthropic package not installed")
        client = anthropic.Anthropic(api_key=api_key, timeout=60.0)
        response = client.messages.create(
            model=model,
            max_tokens=1600,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_prompt}],
        )
        return "\n".join(block.text for block in response.content if block.type == "text").strip()

    def _build_image_prompt(self, context: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
        payload = {
            "context": context,
            "candidates": [
                {
                    "text": c.get("text", ""),
                    "style": c.get("style", ""),
                    "data_sources": c.get("data_sources", []),
                    "confidence": c.get("confidence", 0),
                }
                for c in candidates
            ],
        }
        return "请为以下候选推文生成同数量的市场图片卡片方案：\n" + _compact_json(payload, 12000)

    def _fallback_image_cards(self, context: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        market = context.get("market", {}) if isinstance(context.get("market"), dict) else {}
        signal = context.get("signal", {}) if isinstance(context.get("signal"), dict) else {}
        sentiment = context.get("sentiment", {}) if isinstance(context.get("sentiment"), dict) else {}
        symbol = str(market.get("symbol") or "BTCUSDT")
        price = _fmt_price(market.get("price"))
        side = signal.get("side") or signal.get("action") or "wait"
        trend = "neutral"
        if str(side).upper() in {"LONG", "BUY"}:
            trend = "bullish"
        elif str(side).upper() in {"SHORT", "SELL"}:
            trend = "bearish"
        return [
            {
                "title": f"{symbol} 市场观察",
                "subtitle": "实时行情 · 策略上下文",
                "metric_label": "现价",
                "metric_value": price,
                "trend": trend,
                "bullets": [
                    f"策略动作：{signal.get('action', 'wait')}",
                    f"社区情绪：{sentiment.get('label', 'neutral')} / {sentiment.get('score', 50)}",
                    "清算与资金流字段按真实返回展示",
                ],
                "risk_note": "非投资建议",
                "alt_text": f"{symbol} 市场观察卡片，现价 {price}",
            }
            for _ in candidates
        ]

    def _normalize_image_card(self, card: Any, context: dict[str, Any], idx: int) -> dict[str, Any]:
        fallback = self._fallback_image_cards(context, [{}])[0]
        if not isinstance(card, dict):
            return fallback
        bullets = card.get("bullets")
        if not isinstance(bullets, list):
            bullets = fallback["bullets"]
        return {
            "title": str(card.get("title") or fallback["title"])[:80],
            "subtitle": str(card.get("subtitle") or fallback["subtitle"])[:120],
            "metric_label": str(card.get("metric_label") or fallback["metric_label"])[:40],
            "metric_value": str(card.get("metric_value") or fallback["metric_value"])[:40],
            "trend": str(card.get("trend") or fallback["trend"]).lower()[:20],
            "bullets": [str(item)[:90] for item in bullets[:4]],
            "risk_note": str(card.get("risk_note") or fallback["risk_note"])[:80],
            "alt_text": str(card.get("alt_text") or fallback["alt_text"])[:420],
        }

    def _collect_context(self, agent_state: Any, x_sentiment_service: Any) -> dict[str, Any]:
        context: dict[str, Any] = {"market": {}, "signal": {}, "sentiment": {}}

        snapshot = _as_dict(getattr(agent_state, "last_snapshot", None))
        if snapshot:
            market_blob = _as_dict(snapshot.get("market"))
            funding_rate = _to_float(
                snapshot.get("funding_rate")
                if snapshot.get("funding_rate") is not None
                else snapshot.get("funding") or market_blob.get("funding_rate") or market_blob.get("funding")
            )
            open_interest = _to_float(
                snapshot.get("open_interest")
                if snapshot.get("open_interest") is not None
                else snapshot.get("oi") or market_blob.get("open_interest") or market_blob.get("oi")
            )
            context["market"] = {
                "symbol": snapshot.get("symbol", ""),
                "exchange": snapshot.get("exchange") or market_blob.get("exchange") or market_blob.get("source", ""),
                "price": _to_float(snapshot.get("price"), 0),
                "change_24h_pct": _to_float(market_blob.get("change_24h_pct"), None),
                "volume_24h_quote": _to_float(market_blob.get("volume_24h_quote"), None),
                "funding_rate": funding_rate,
                "open_interest": open_interest,
            }

        last_signal = _as_dict(getattr(agent_state, "last_signal", None))
        last_risk = _as_dict(getattr(agent_state, "last_risk", None))
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
                x_data = _as_dict(x_sentiment_service.fetch_all())
                sentiment = _as_dict(x_data.get("sentiment"))
                trending = x_data.get("trending", []) if isinstance(x_data.get("trending"), list) else []
                context["sentiment"] = {
                    "score": sentiment.get("score", 50),
                    "label": sentiment.get("label", "neutral"),
                    "summary": sentiment.get("summary", ""),
                    "trending": [
                        {"coin": _as_dict(t).get("coin", ""), "sentiment_pct": _as_dict(t).get("sentiment_pct", 50)}
                        for t in trending[:5]
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

    def _build_prompt_safe(self, context: dict[str, Any]) -> str:
        market = context.get("market", {}) if isinstance(context.get("market"), dict) else {}
        signal = context.get("signal", {}) if isinstance(context.get("signal"), dict) else {}
        sentiment = context.get("sentiment", {}) if isinstance(context.get("sentiment"), dict) else {}

        parts = ["请根据以下实时数据生成 3 条候选推文：\n"]
        price = _to_float(market.get("price"), None)
        funding_rate = _to_float(market.get("funding_rate"), None)
        open_interest = _to_float(market.get("open_interest"), None)
        change_24h = _to_float(market.get("change_24h_pct"), None)
        volume_24h = _to_float(market.get("volume_24h_quote"), None)

        if price:
            parts.append(f"【市场数据】{market.get('symbol', 'BTC')} 当前价格 ${price:,.2f}")
            if market.get("exchange"):
                parts.append(f"  交易所/数据源: {market.get('exchange')}")
            if change_24h is not None:
                parts.append(f"  24h 涨跌: {change_24h:+.2f}%")
            if volume_24h is not None:
                parts.append(f"  24h 成交额: ${volume_24h:,.0f}")
            if funding_rate:
                parts.append(f"  资金费率: {funding_rate:.4%}")
            if open_interest:
                parts.append(f"  持仓量: ${open_interest:,.0f}")

        if signal.get("action") != "wait":
            confidence = _to_float(signal.get("confidence"), 0) or 0
            parts.append(f"\n【策略信号】方向: {signal.get('side', '-')} | 动作: {signal.get('action', '-')} | 置信度: {confidence:.0%}")
            parts.append(f"  当前阶段: {signal.get('phase', '-')}")

        if sentiment.get("score") and not sentiment.get("error"):
            parts.append(f"\n【X 社区情绪】得分: {sentiment['score']}/100 ({sentiment.get('label', '-')})")
            if sentiment.get("summary"):
                parts.append(f"  摘要: {sentiment['summary']}")
            if sentiment.get("trending"):
                coins = ", ".join(f"${t.get('coin')}" for t in sentiment["trending"][:3] if isinstance(t, dict) and t.get("coin"))
                if coins:
                    parts.append(f"  热门: {coins}")
            if sentiment.get("narrative"):
                parts.append(f"  叙事: {sentiment['narrative'][:200]}")

        if len(parts) == 1:
            parts.append("当前没有可用市场数据，请生成保守、明确说明数据等待中的候选推文。")
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
