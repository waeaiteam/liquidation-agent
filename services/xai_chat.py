"""xAI Grok chat service with structured analysis output.

Capabilities:
- Multi-turn chat with loaded agent.md system prompt
- Auto-detect when to use X Live Search based on user query
- Parse structured JSON reports (daily_brief / coin_deep_dive / tweet_drafts)
- Token/cost tracking for budgeting
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx


XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.3"

# Triggers that indicate the user wants fresh X data
LIVE_SEARCH_TRIGGERS = [
    "现在", "今天", "最近", "实时", "当前", "刚才", "此刻", "这几天", "这几小时",
    "latest", "now", "today", "recent", "real-time", "current", "this week",
    "$", "#", "@", "热度", "讨论", "推文", "tweet", "twitter", "x上",
    "情绪", "sentiment", "涨", "跌", "拉", "砸", "crash", "pump", "dump",
]

# Triggers for structured JSON reports
JSON_REPORT_TRIGGERS = [
    "报告", "report", "简报", "brief", "结构化", "json", "格式化",
    "生成报告", "给我报告", "写一份", "做一份",
]

# Triggers for tweet drafting
TWEET_DRAFT_TRIGGERS = [
    "帮我写", "帮我发", "写条推文", "写一条", "发推", "发帖",
    "候选推文", "推文草稿", "draft tweet", "write a tweet",
]


@dataclass
class ChatTurn:
    role: str  # user | assistant
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class XAIChatService:
    def __init__(self) -> None:
        self._api_key: str = ""
        self._model: str = DEFAULT_MODEL
        self._base_url: str = XAI_BASE_URL
        self._system_prompt: str = ""
        self._conversations: dict[str, list[ChatTurn]] = {}
        self._api_calls: int = 0
        self._cost_usd: float = 0.0
        self._load_system_prompt()

    def _load_system_prompt(self) -> None:
        """Load agents/x_analyst.md as the system prompt."""
        # Try multiple paths to support both dev and PyInstaller frozen mode
        candidates = [
            Path(__file__).parent.parent / "agents" / "x_analyst.md",
        ]
        # PyInstaller _MEIPASS support
        if hasattr(os, "_MEIPASS") or getattr(__import__("sys"), "frozen", False):
            base = getattr(__import__("sys"), "_MEIPASS", "")
            if base:
                candidates.append(Path(base) / "agents" / "x_analyst.md")

        for p in candidates:
            if p.exists():
                self._system_prompt = p.read_text(encoding="utf-8")
                return
        self._system_prompt = "You are a crypto X sentiment analyst. Reply in Chinese."

    def configure(self, api_key: str, model: str | None = None, base_url: str | None = None) -> None:
        self._api_key = (api_key or "").strip()
        if model:
            self._model = model
        if base_url:
            self._base_url = base_url.rstrip("/")

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _should_use_live_search(self, user_message: str) -> bool:
        low = user_message.lower()
        return any(t.lower() in low for t in LIVE_SEARCH_TRIGGERS)

    def _wants_json_report(self, user_message: str) -> bool:
        low = user_message.lower()
        return any(t.lower() in low for t in JSON_REPORT_TRIGGERS)

    def _wants_tweet_draft(self, user_message: str) -> bool:
        low = user_message.lower()
        return any(t.lower() in low for t in TWEET_DRAFT_TRIGGERS)

    def _get_or_create_session(self, session_id: str) -> list[ChatTurn]:
        if session_id not in self._conversations:
            self._conversations[session_id] = []
        return self._conversations[session_id]

    def reset_session(self, session_id: str) -> None:
        self._conversations.pop(session_id, None)

    def _build_messages(self, session_id: str, user_message: str) -> list[dict[str, str]]:
        history = self._get_or_create_session(session_id)
        messages = [{"role": "system", "content": self._system_prompt}]
        # Include last 10 turns to keep context manageable
        for turn in history[-10:]:
            messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": user_message})
        return messages

    def _extract_json(self, text: str) -> dict[str, Any] | None:
        """Try to extract a JSON object from the response, or return None."""
        text = text.strip()
        # Try fenced code block first
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        # Fallback: first { to last }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        return None

    def chat(
        self,
        session_id: str,
        user_message: str,
        force_live_search: bool | None = None,
        force_json: bool | None = None,
    ) -> dict[str, Any]:
        """Send a message, get assistant reply. Auto-decides on live search + JSON mode."""
        if not self.is_configured():
            raise RuntimeError("xAI API not configured")

        use_live = force_live_search if force_live_search is not None else self._should_use_live_search(user_message)
        use_json = force_json if force_json is not None else (
            self._wants_json_report(user_message) or self._wants_tweet_draft(user_message)
        )

        messages = self._build_messages(session_id, user_message)

        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.5 if not use_json else 0.3,
        }

        is_official_xai = "api.x.ai" in self._base_url

        if use_live and is_official_xai:
            now = datetime.now(timezone.utc)
            body["search_parameters"] = {
                "mode": "on",
                "sources": [{"type": "x"}],
                "from_date": (now - timedelta(hours=6)).strftime("%Y-%m-%d"),
                "to_date": now.strftime("%Y-%m-%d"),
                "max_search_results": 20,
                "return_citations": True,
            }

        if use_json:
            body["response_format"] = {"type": "json_object"}

        with httpx.Client(timeout=90.0) as client:
            resp = client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if resp.status_code != 200:
                detail = resp.text[:300]
                raise RuntimeError(f"API {resp.status_code} ({self._base_url}, model={self._model}): {detail}")
            data = resp.json()

        self._api_calls += 1

        choice = (data.get("choices") or [{}])[0]
        reply_text = (choice.get("message") or {}).get("content", "")
        citations = data.get("citations", []) or []

        # Token/cost tracking
        usage = data.get("usage", {})
        in_tok = usage.get("prompt_tokens", 0)
        out_tok = usage.get("completion_tokens", 0)
        sources_used = len(citations) if use_live else 0
        call_cost = (in_tok / 1_000_000) * 0.20 + (out_tok / 1_000_000) * 0.50 + (sources_used / 1000) * 25.0
        self._cost_usd += call_cost

        # Persist to conversation history
        history = self._get_or_create_session(session_id)
        history.append(ChatTurn(role="user", content=user_message))
        history.append(ChatTurn(role="assistant", content=reply_text))

        # Try parsing structured JSON if requested
        structured: dict[str, Any] | None = None
        if use_json:
            structured = self._extract_json(reply_text)

        return {
            "reply": reply_text,
            "structured": structured,
            "used_live_search": use_live,
            "used_json_mode": use_json,
            "citations": citations,
            "usage": {
                "prompt_tokens": in_tok,
                "completion_tokens": out_tok,
                "sources_used": sources_used,
                "call_cost_usd": round(call_cost, 5),
            },
            "session_id": session_id,
            "total_calls": self._api_calls,
            "total_cost_usd": round(self._cost_usd, 4),
        }

    def get_history(self, session_id: str) -> list[dict[str, str]]:
        return [{"role": t.role, "content": t.content, "timestamp": t.timestamp}
                for t in self._get_or_create_session(session_id)]

    def status_info(self) -> dict[str, Any]:
        return {
            "configured": self.is_configured(),
            "model": self._model,
            "sessions": len(self._conversations),
            "total_calls": self._api_calls,
            "total_cost_usd": round(self._cost_usd, 4),
            "system_prompt_loaded": bool(self._system_prompt),
            "system_prompt_chars": len(self._system_prompt),
        }


_chat_service: XAIChatService | None = None


def get_chat_service() -> XAIChatService:
    global _chat_service
    if _chat_service is None:
        _chat_service = XAIChatService()
        key = os.getenv("XAI_API_KEY", "").strip()
        if key:
            _chat_service.configure(key)
    return _chat_service
