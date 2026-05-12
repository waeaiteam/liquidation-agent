from __future__ import annotations

import json
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
        "system": [{"type": "text", "text": ANALYSIS_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
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
            {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
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
    choices = data.get("choices") or []
    if not choices:
        return json.dumps(data, ensure_ascii=False)
    return (choices[0].get("message") or {}).get("content", "").strip()


def _request_json(req: Request) -> dict:
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"LLM provider returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"LLM provider request failed: {exc.reason}") from exc
