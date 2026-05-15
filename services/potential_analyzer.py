from __future__ import annotations

import json
import os
from typing import Any

from services.llm import _call_anthropic, _call_openai_compatible, get_provider
from services.potential_scanner import AgentConfig, PotentialStore, safe_json_loads, utc_iso


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_DIR = os.path.join(BASE_DIR, "agents")


def read_agent_file(name: str) -> str:
    path = os.path.join(AGENT_DIR, name)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def extract_json_object(text: str) -> dict[str, Any]:
    if not text:
        raise ValueError("empty LLM response")
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"raw": data}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                return data
        raise


def _call_agent_llm(config: AgentConfig, payload: dict[str, Any], system_prompt: str, user_prompt: str) -> str:
    if not config.enabled:
        raise ValueError(f"{config.agent_type.upper()}agent is disabled")
    if not config.api_key:
        raise ValueError(f"{config.agent_type.upper()}agent LLM API key is required")
    provider = get_provider(config.provider)
    model = config.model or (provider.get("mdl") or [""])[0]
    if not model:
        raise ValueError(f"{config.agent_type.upper()}agent model is required")
    body = {
        **payload,
        "system_override": system_prompt,
        "custom_base_url": config.base_url,
        "temperature": config.temperature,
    }
    if config.provider == "anthropic":
        return _call_anthropic(config.api_key, model, body, user_prompt)
    base_url = config.base_url if config.provider == "custom" and config.base_url else provider.get("base")
    if config.base_url and config.provider != "anthropic":
        base_url = config.base_url
    if not base_url:
        raise ValueError(f"{config.provider} base URL is required")
    return _call_openai_compatible(base_url, config.api_key, model, body, user_prompt)


class PotentialAnalyzer:
    def __init__(self, store: PotentialStore):
        self.store = store

    def analyze_signal(self, signal_id: int, *, user_prompt: str = "") -> dict[str, Any]:
        signal = self.store.get_signal(signal_id)
        if not signal:
            raise ValueError("signal not found")
        config = self.store.get_agent_config("pot")
        memory = self.store.get_memory("pot")
        system_prompt = "\n\n".join(
            part for part in (
                read_agent_file("pot_agent.md"),
                read_agent_file("pot_skill.md"),
            ) if part
        )
        payload = {
            "agent": "POTagent",
            "task": "analyze_potential_coin_signal",
            "signal": signal,
            "memory": memory,
            "required_schema": {
                "decision": "strong_buy | buy | watch | skip",
                "confidence": 75,
                "reasoning": "...",
                "entry_suggestion": {
                    "price_range": "0.4500 - 0.4650",
                    "position_pct": "5-10%",
                    "initial_stop": "based on 2xATR",
                    "exit_conditions": [],
                    "timeframe": "1-7 days",
                },
                "key_risks": [],
            },
        }
        prompt = user_prompt or "请按 POTagent schema 输出严格 JSON，不要输出 Markdown。"
        raw = _call_agent_llm(config, payload, system_prompt, prompt)
        try:
            analysis = extract_json_object(raw)
        except Exception:
            analysis = {"decision": "watch", "confidence": 0, "reasoning": "LLM response was not valid JSON", "raw": raw}
        analysis = normalize_pot_analysis(analysis)
        self.store.update_signal(signal_id, status="analyzed", ai_analysis=analysis)
        self.store.log_trajectory("pot", "analysis", input_data=signal, decision=analysis, session_id=str(signal.get("id")))
        return analysis

    def review_position(self, position: dict[str, Any], market_context: dict[str, Any] | None = None) -> dict[str, Any]:
        config = self.store.get_agent_config("pot")
        system_prompt = "\n\n".join(
            part for part in (
                read_agent_file("pot_agent.md"),
                read_agent_file("pot_skill.md"),
            ) if part
        )
        payload = {
            "agent": "POTagent",
            "task": "review_open_position",
            "position": position,
            "market_context": market_context or {},
            "memory": self.store.get_memory("pot"),
            "required_schema": {
                "action": "hold | reduce | close",
                "reasoning": "...",
                "new_stop": "0.4800",
                "reduce_pct": 0,
            },
        }
        raw = _call_agent_llm(config, payload, system_prompt, "请输出严格 JSON，不要输出 Markdown。")
        try:
            review = extract_json_object(raw)
        except Exception:
            review = {"action": "hold", "reasoning": "LLM response was not valid JSON", "new_stop": position.get("trailing_stop"), "reduce_pct": 0}
        review["action"] = str(review.get("action") or "hold").lower()
        if review["action"] not in {"hold", "reduce", "close"}:
            review["action"] = "hold"
        review["reviewed_at"] = utc_iso()
        self.store.log_trajectory("pot", "position_review", input_data={"position": position, "market_context": market_context or {}}, decision=review, session_id=str(position.get("trajectory_id") or position.get("id")))
        return review


def normalize_pot_analysis(value: dict[str, Any]) -> dict[str, Any]:
    decision = str(value.get("decision") or "watch").lower()
    if decision not in {"strong_buy", "buy", "watch", "skip"}:
        decision = "watch"
    confidence = value.get("confidence")
    try:
        confidence = max(0, min(100, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0
    suggestion = value.get("entry_suggestion") if isinstance(value.get("entry_suggestion"), dict) else {}
    risks = value.get("key_risks") if isinstance(value.get("key_risks"), list) else []
    return {
        "decision": decision,
        "confidence": confidence,
        "reasoning": str(value.get("reasoning") or ""),
        "entry_suggestion": {
            "price_range": str(suggestion.get("price_range") or ""),
            "position_pct": str(suggestion.get("position_pct") or ""),
            "initial_stop": str(suggestion.get("initial_stop") or ""),
            "exit_conditions": suggestion.get("exit_conditions") if isinstance(suggestion.get("exit_conditions"), list) else [],
            "timeframe": str(suggestion.get("timeframe") or "1-7天"),
        },
        "key_risks": [str(item) for item in risks],
        "raw": value,
        "analyzed_at": utc_iso(),
    }


def analysis_allows_entry(analysis: dict[str, Any] | str | None) -> bool:
    data = safe_json_loads(analysis, analysis if isinstance(analysis, dict) else {})
    return str((data or {}).get("decision") or "").lower() in {"strong_buy", "buy"}
