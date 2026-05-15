from __future__ import annotations

import json
import os
from typing import Any

from services.llm import get_provider, _call_anthropic, _call_openai_compatible


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_PATH = os.path.join(BASE_DIR, "AGENTS.md")


def chat_with_agent(provider_id: str, api_key: str, model: str, question: str, context: dict[str, Any]) -> str:
    if not api_key:
        raise ValueError("LLM API key is required")
    question = (question or "").strip()
    if not question:
        raise ValueError("Question is required")
    provider = get_provider(provider_id)
    model = model or (provider.get("mdl") or [""])[0]
    if not model:
        raise ValueError("Model is required")
    system = _agents_prompt()
    context = _normalize_context_units(context)
    user_payload = {
        "question": question,
        "context": context,
    }
    user_message = "请根据 AGENTS.md 身份和以下实时上下文回答用户问题。先给结论，再解释依据。\n\n" + json.dumps(user_payload, ensure_ascii=False, sort_keys=True)[:24_000]
    if provider_id == "anthropic":
        return _call_anthropic(api_key, model, {"agent_chat": user_payload, "system_override": system}, user_message)
    base_url = context.get("custom_base_url") if provider_id == "custom" else provider.get("base")
    if not base_url:
        raise ValueError("Provider base URL is required")
    return _call_openai_compatible(base_url, api_key, model, {"agent_chat": user_payload, "system_override": system}, user_message)


def _agents_prompt() -> str:
    try:
        with open(AGENTS_PATH, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return "你是清算反向短线交易策略 agent。只能解释、审查和建议，不能越权交易。"


def _normalize_context_units(context: dict[str, Any]) -> dict[str, Any]:
    def walk(value: Any) -> Any:
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        item = {k: walk(v) for k, v in value.items()}
        if isinstance(item.get("clusters"), list):
            item["clusters"] = [_normalize_cluster_units(cluster) for cluster in item["clusters"]]
        for key in ("above", "below"):
            if isinstance(item.get(key), list):
                item[key] = [_normalize_cluster_units(cluster) for cluster in item[key]]
        return item
    return walk(context)


def _normalize_cluster_units(cluster: Any) -> Any:
    if not isinstance(cluster, dict):
        return cluster
    out = dict(cluster)
    try:
        raw = float(out.get("volume", out.get("value", 0)) or 0)
    except (TypeError, ValueError):
        raw = 0.0
    if raw > 0 and "volume_usd" not in out:
        volume_usd = raw / 1000.0
        out["volume_usd"] = round(volume_usd, 2)
        out["volume_display"] = f"${volume_usd / 1_000_000:.2f}M"
        out["volume_unit_note"] = "raw CoinAnk cluster.volume divided by 1000"
    return out
