from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import anthropic

from providers import LLM_PROVIDERS


ANALYSIS_SYSTEM_PROMPT = """你是一个加密货币衍生品策略分析助手。你的任务是把 CoinAnk/Claw402 API 返回的结构化数据整理成中文自然语言。
要求：
1. 先给结论，再解释关键数据。
2. 说明大额清算方向、反向开单倾向、风控注意事项。
3. 不要承诺收益，不要把分析写成投资建议。
4. 如果数据不足，明确说哪些字段缺失。
5. 输出 4 个小节：市场状态、清算信号、策略动作、风险提醒。
"""


def get_provider(provider_id: str) -> dict:
    for provider in LLM_PROVIDERS:
        if provider["id"] == provider_id:
            return provider
    raise ValueError(f"Unknown LLM provider: {provider_id}")


def analyze_with_llm(provider_id: str, api_key: str, model: str, payload: dict, user_prompt: str = "") -> str:
    if not api_key:
        raise ValueError("LLM API key is required")
    provider = get_provider(provider_id)
    model = model or (provider.get("mdl") or [""])[0]
    if not model:
        raise ValueError("Model is required")
    if provider_id == "anthropic":
        return _call_anthropic(api_key, model, payload, user_prompt)
    if provider_id == "custom":
        base_url = payload.get("custom_base_url") or provider.get("base")
    else:
        base_url = provider.get("base")
    if not base_url:
        raise ValueError("Provider base URL is required")
    return _call_openai_compatible(base_url, api_key, model, payload, user_prompt)


def _analysis_user_message(payload: dict, user_prompt: str) -> str:
    compact = json.dumps(payload, ensure_ascii=False, sort_keys=True)[:20_000]
    extra = f"\n用户额外要求：{user_prompt}" if user_prompt else ""
    return f"请解读以下 API 调用结果，整理成交易策略自然语言摘要。{extra}\n\n数据 JSON：\n{compact}"


def _call_anthropic(api_key: str, model: str, payload: dict, user_prompt: str) -> str:
    client = anthropic.Anthropic(api_key=api_key, timeout=60.0)
    kwargs = {
        "model": model,
        "max_tokens": 1600,
        "system": [{"type": "text", "text": _system_prompt(payload), "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": _analysis_user_message(payload, user_prompt)}],
    }
    if model.startswith(("claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6")):
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": "medium"}
    try:
        response = client.messages.create(**kwargs)
    except anthropic.APIError as exc:
        raise RuntimeError(f"Anthropic API error: {exc}") from exc
    return "\n".join(block.text for block in response.content if block.type == "text").strip()


def _call_openai_compatible(base_url: str, api_key: str, model: str, payload: dict, user_prompt: str) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt(payload)},
            {"role": "user", "content": _analysis_user_message(payload, user_prompt)},
        ],
    }
    req = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    data = _request_json(req)
    return _openai_response_text(data)


def _openai_response_text(data: Any) -> str:
    if isinstance(data, str):
        return data.strip()
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected LLM provider JSON: {type(data).__name__}")
    choices = data.get("choices") or []
    if not choices:
        return json.dumps(data, ensure_ascii=False)
    return _choice_content(choices[0])


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _system_prompt(payload: Any) -> str:
    if isinstance(payload, dict):
        override = payload.get("system_override")
        if isinstance(override, str) and override.strip():
            return override
    return ANALYSIS_SYSTEM_PROMPT


def _choice_content(choice: Any) -> str:
    if isinstance(choice, str):
        return choice.strip()
    if not isinstance(choice, dict):
        raise RuntimeError(f"Unexpected LLM provider choice JSON: {type(choice).__name__}")
    message = choice.get("message") or choice.get("delta") or {}
    if isinstance(message, str):
        return message.strip()
    if not isinstance(message, dict):
        raise RuntimeError(f"Unexpected LLM provider message JSON: {type(message).__name__}")
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts).strip()
    text = choice.get("text")
    if isinstance(text, str):
        return text.strip()
    return json.dumps(choice, ensure_ascii=False)


def _request_json(req: Request) -> dict | str:
    try:
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, (dict, str)):
                return data
            raise RuntimeError(f"Unexpected LLM provider JSON: {type(data).__name__}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"LLM provider returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"LLM provider request failed: {exc.reason}") from exc
