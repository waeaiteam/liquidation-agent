from __future__ import annotations

import json
import re
from typing import Any

from services.llm import get_provider, _call_anthropic, _call_openai_compatible
from strategy.models import RiskDecision, Signal, StrategyConfig


STRATEGY_REVIEW_SYSTEM = """你是清算反向短线交易 agent 的审查员，不是执行器。
只能根据给定候选信号输出 JSON，不能输出 Markdown。
必须遵守硬约束：不能扩大最大止损，不能移除止损，不能无视热力图过期、连续亏损暂停、预算限制或 live 确认。
允许的 decision 只有 approve、reject、reduce_size、tighten_stop、wait。
如果证据不足，优先 wait 或 reject。
"""


def review_trade_with_llm(provider_id: str, api_key: str, model: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not api_key:
        return _fallback("skipped", "LLM review disabled or missing API key")
    provider = get_provider(provider_id)
    model = model or (provider.get("mdl") or [""])[0]
    if not model:
        return _fallback("skipped", "LLM review model missing")
    user_message = _review_user_message(payload)
    try:
        if provider_id == "anthropic":
            text = _call_anthropic(api_key, model, {"review_payload": payload}, user_message)
        else:
            base_url = payload.get("custom_base_url") if provider_id == "custom" else provider.get("base")
            if not base_url:
                return _fallback("skipped", "LLM provider base URL missing")
            text = _call_openai_compatible(base_url, api_key, model, {"review_payload": payload}, user_message)
        return _sanitize_review(_parse_json(text))
    except Exception as exc:
        return _fallback("error", str(exc))


def apply_llm_review(decision: RiskDecision, review: dict[str, Any], config: StrategyConfig, signal: Signal) -> RiskDecision:
    if review.get("decision") in {"reject", "wait"}:
        return RiskDecision(False, [f"LLM review {review.get('decision')}: {review.get('reason', '-')}", *decision.reasons], decision.mode, decision.notional_usd, decision.leverage, decision.stop_loss, decision.take_profit)
    notional = decision.notional_usd
    multiplier = float(review.get("notional_multiplier") or 1)
    if review.get("decision") == "reduce_size":
        multiplier = min(multiplier, 0.75)
    multiplier = min(max(multiplier, 0.1), 1.0)
    notional = min(config.max_notional_usd, max(0, notional * multiplier))
    stop_loss = decision.stop_loss
    take_profit = decision.take_profit
    adjustments = review.get("adjustments") or {}
    proposed_sl = _float_or_none(adjustments.get("stop_loss"))
    proposed_tp = _float_or_none(adjustments.get("take_profit"))
    if proposed_sl:
        stop_loss = _clamp_stop(signal, decision.stop_loss, proposed_sl)
    if proposed_tp:
        take_profit = _clamp_take_profit(signal, proposed_tp, config)
    reasons = [*decision.reasons, f"LLM review {review.get('decision')}: {review.get('reason', '-')}"]
    return RiskDecision(decision.approved, reasons, decision.mode, notional, decision.leverage, stop_loss, take_profit)


def _review_user_message(payload: dict[str, Any]) -> str:
    compact = json.dumps(payload, ensure_ascii=False, sort_keys=True)[:18_000]
    return "请审查以下清算反向短线候选交易，只返回 JSON：\n" + compact + """

JSON schema:
{
  "decision": "approve|reject|reduce_size|tighten_stop|wait",
  "confidence": 0.0,
  "reason": "中文简短原因",
  "notional_multiplier": 1.0,
  "adjustments": {
    "stop_loss": null,
    "take_profit": null,
    "max_holding_minutes": null
  }
}
"""


def _parse_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("{"):
        return json.loads(text)
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError("LLM review did not return JSON")
    return json.loads(match.group(0))


def _sanitize_review(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("LLM review JSON must be an object")
    decision = str(data.get("decision") or "wait").lower()
    if decision not in {"approve", "reject", "reduce_size", "tighten_stop", "wait"}:
        decision = "wait"
    confidence = min(max(float(data.get("confidence") or 0), 0), 1)
    multiplier = min(max(float(data.get("notional_multiplier") or 1), 0.1), 1)
    return {
        "enabled": True,
        "decision": decision,
        "confidence": confidence,
        "reason": str(data.get("reason") or ""),
        "notional_multiplier": multiplier,
        "adjustments": data.get("adjustments") if isinstance(data.get("adjustments"), dict) else {},
    }


def _fallback(decision: str, reason: str) -> dict[str, Any]:
    return {"enabled": False, "decision": decision, "confidence": 0, "reason": reason, "notional_multiplier": 1, "adjustments": {}}


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_stop(signal: Signal, current_stop: float, proposed_stop: float) -> float:
    if signal.side == "LONG":
        return round(max(current_stop, min(proposed_stop, signal.price)), 4)
    if signal.side == "SHORT":
        return round(min(current_stop, max(proposed_stop, signal.price)), 4)
    return current_stop


def _clamp_take_profit(signal: Signal, proposed_tp: float, config: StrategyConfig) -> float:
    price = signal.price
    if signal.side == "LONG":
        return round(min(max(proposed_tp, price), price * (1 + config.max_take_profit_pct / 100)), 4)
    if signal.side == "SHORT":
        return round(max(min(proposed_tp, price), price * (1 - config.max_take_profit_pct / 100)), 4)
    return proposed_tp
