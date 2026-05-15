from __future__ import annotations

import json
import os
from typing import Any

from services.potential_analyzer import _call_agent_llm, extract_json_object, read_agent_file
from services.potential_scanner import PotentialStore, safe_json_loads, utc_iso


class PubAgent:
    def __init__(self, store: PotentialStore):
        self.store = store

    def generate_for_signal(self, signal_id: int, *, mode: str = "draft") -> dict[str, Any]:
        signal = self.store.get_signal(signal_id)
        if not signal:
            raise ValueError("signal not found")
        analysis = safe_json_loads(signal.get("ai_analysis"), signal.get("ai_analysis") or {})
        if not isinstance(analysis, dict) or not analysis:
            raise ValueError("POTagent analysis is required before PUBagent can generate content")
        if str(analysis.get("decision") or "").lower() == "skip":
            raise ValueError("POTagent decision is skip; PUBagent will not generate a publishable post")

        config = self.store.get_agent_config("pub")
        system_prompt = "\n\n".join(part for part in (read_agent_file("pub_agent.md"), read_agent_file("pub_skill.md")) if part)
        payload = {
            "agent": "PUBagent",
            "task": "generate_binance_square_post",
            "signal": signal,
            "pot_analysis": analysis,
            "memory": self.store.get_memory("pub"),
            "required_schema": {
                "publish_worthy": True,
                "bodyTextOnly": "200-500 Chinese chars with hashtags",
                "hashtags": ["#BTC", "#crypto"],
                "risk_note": "AI建议仅供参考，不构成投资建议",
            },
        }
        raw = _call_agent_llm(config, payload, system_prompt, "请输出严格 JSON，不要 Markdown。")
        try:
            data = extract_json_object(raw)
        except Exception:
            data = {"publish_worthy": False, "bodyTextOnly": "", "error": "LLM response was not valid JSON", "raw": raw}
        content = normalize_square_content(data, signal)
        draft_id = self.store.create_publish_draft(signal_id, content["bodyTextOnly"], mode=mode)
        self.store.log_trajectory("pub", "draft", input_data={"signal": signal, "pot_analysis": analysis}, decision=content, session_id=str(signal_id))
        return {"draft_id": draft_id, "draft": content, "created_at": utc_iso()}


def normalize_square_content(data: dict[str, Any], signal: dict[str, Any]) -> dict[str, Any]:
    symbol = str(signal.get("symbol") or "").replace("USDT", "")
    body = str(data.get("bodyTextOnly") or data.get("article_draft") or "").strip()
    if not body:
        body = (
            f"{symbol} 出现 POTagent 潜力币观察信号：预测费率持续为负并进入观察清单，且已结算费率此前保持为正，"
            f"OI 四段连续放大，当前评分 {float(signal.get('potential_score') or 0):.1f}/100。"
            f"这类结构通常代表合约资金正在重新定价，空头付费压力上升，但仍需要观察成交量是否持续、价格是否突破信号高点，以及 BTC 是否配合。\n\n"
            "我的处理方式会更偏向观察清单：若后续 OI 继续抬升、费率维持负值且价格没有明显追高，才具备进一步复评价值。"
            "风险在于低流动性、假突破和大盘共振下跌。AI建议仅供参考，不构成投资建议。"
        )
    if "不构成投资建议" not in body:
        body += "\n\nAI建议仅供参考，不构成投资建议。"
    tags = data.get("hashtags") if isinstance(data.get("hashtags"), list) else []
    tag_text = " ".join(str(tag if str(tag).startswith("#") else "#" + str(tag)).strip() for tag in tags[:5])
    if f"#{symbol}" not in body:
        body += f"\n#{symbol}"
    if tag_text:
        body += " " + tag_text
    body = body.replace("http://", "").replace("https://", "")
    return {
        "publish_worthy": bool(data.get("publish_worthy", True)),
        "bodyTextOnly": body[:1800],
        "hashtags": tags,
        "risk_note": "AI建议仅供参考，不构成投资建议",
        "generated_at": utc_iso(),
    }
