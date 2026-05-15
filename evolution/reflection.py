from __future__ import annotations

import json
from typing import Any

from services.potential_analyzer import _call_agent_llm, extract_json_object, read_agent_file
from services.potential_scanner import PotentialStore, normalize_agent_type


class ReflectionEngine:
    def __init__(self, store: PotentialStore):
        self.store = store

    def reflect(self, agent_type: str, *, trigger: str = "manual", limit: int = 20) -> dict[str, Any]:
        agent = normalize_agent_type(agent_type)
        cfg = self.store.get_agent_config(agent)
        trajectories = self.store.list_trajectory(agent, limit=limit)
        prompt_name = f"{agent}_reflection.md"
        system_prompt = read_agent_file(prompt_name) or (
            "你是交易 Agent 的复盘分析器。请基于历史轨迹总结正确判断、错误判断、参数调整和风险。"
            "只输出 JSON。"
        )
        payload = {
            "agent": agent,
            "task": "reflection",
            "trigger": trigger,
            "trajectories": trajectories,
            "memory": self.store.get_memory(agent),
            "required_schema": {
                "scoring_adjustments": {},
                "exit_rules_update": [],
                "lessons": [],
                "confidence": 0.7,
            },
        }
        raw = _call_agent_llm(cfg, payload, system_prompt, "请输出严格 JSON，不要 Markdown。")
        try:
            result = extract_json_object(raw)
        except Exception:
            result = {"lessons": ["LLM response was not valid JSON"], "raw": raw, "confidence": 0}
        return result
