from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from services.potential_scanner import PotentialStore, utc_iso


SQUARE_POST_URL = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
SENSITIVE_WORDS = (
    "暴涨",
    "暴跌",
    "必涨",
    "稳赚",
    "翻倍",
    "百倍币",
    "无风险",
    "梭哈",
    "稳赚不赔",
)


class SquarePublisher:
    def __init__(self, store: PotentialStore):
        self.store = store

    def publish(self, draft_id: int, api_key: str, *, force: bool = False) -> dict[str, Any]:
        draft = self.store.get_draft(draft_id)
        if not draft:
            raise ValueError("draft not found")
        signal = self.store.get_signal(int(draft.get("signal_id") or 0))
        cfg = self.store.get_scanner_config()
        content = str(draft.get("content") or "").strip()
        blockers = self._safety_blockers(content, signal, cfg, force=force)
        if blockers:
            error = "; ".join(blockers)
            self.store.update_draft(draft_id, mode="failed", error=error)
            return {"published": False, "error": error, "blockers": blockers}
        if not api_key:
            raise ValueError("Square OpenAPI Key is required")

        body = json.dumps({"bodyTextOnly": content}, ensure_ascii=False).encode("utf-8")
        req = Request(
            SQUARE_POST_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Square-OpenAPI-Key": api_key,
                "clienttype": "binanceSkill",
                "User-Agent": "Mozilla/5.0 LIQ-agent",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            error = f"Square HTTP {exc.code}: {detail}"
            self.store.update_draft(draft_id, mode="failed", error=error)
            return {"published": False, "error": error}
        except URLError as exc:
            error = f"Square request failed: {exc.reason}"
            self.store.update_draft(draft_id, mode="failed", error=error)
            return {"published": False, "error": error}

        business_error = self._business_error(data)
        if business_error:
            self.store.update_draft(draft_id, mode="failed", error=business_error["error"])
            return {"published": False, **business_error, "response": data}

        post_id = str((data.get("data") or {}).get("postId") or (data.get("data") or {}).get("id") or "")
        published_at = utc_iso()
        self.store.update_draft(draft_id, mode="published", post_id=post_id, error="", published_at=published_at)
        self.store.log_trajectory("pub", "publish", input_data=draft, decision={"post_id": post_id}, outcome=data, session_id=str(draft.get("signal_id") or draft_id))
        return {"published": True, "post_id": post_id, "response": data, "published_at": published_at}

    def _business_error(self, data: dict[str, Any]) -> dict[str, str] | None:
        if not isinstance(data, dict):
            return {"error": "Square returned a non-JSON object", "code": "invalid_response"}
        code = data.get("code")
        success = data.get("success")
        if code in (None, "", "000000", "0", 0) and success is not False:
            return None
        error_code = str(code or "unknown")
        message = str(data.get("message") or data.get("msg") or "")
        if error_code == "220009":
            error = "今日发布数已达上限"
        elif error_code in {"20002", "20022"}:
            error = f"内容包含敏感词: {message}"
        elif error_code == "220003":
            error = "API Key无效或未找到"
        elif error_code == "220004":
            error = "API Key已过期"
        else:
            error = f"Square错误 {error_code}: {message}".strip()
        return {"error": error, "code": error_code}

    def _safety_blockers(self, content: str, signal: dict[str, Any] | None, cfg, *, force: bool = False) -> list[str]:
        if force:
            return []
        blockers = []
        if not signal:
            blockers.append("linked signal not found")
            return blockers
        if float(signal.get("potential_score") or 0) < 70:
            blockers.append("potential score below 70")
        if cfg.max_publish_per_day and self.store.publish_count_today() >= cfg.max_publish_per_day:
            blockers.append("daily Square publish limit reached")
        if "http://" in content or "https://" in content:
            blockers.append("content must not contain URLs")
        if len(content) < 200:
            blockers.append("content too short")
        if len(content) > 1800:
            blockers.append("content too long")
        if "不构成投资建议" not in content:
            blockers.append("risk disclaimer missing")
        found_sensitive = [word for word in SENSITIVE_WORDS if word in content]
        if found_sensitive:
            blockers.append("sensitive words: " + ", ".join(found_sensitive))
        last = self.store.last_publish_for_symbol(str(signal.get("symbol") or ""))
        if last and cfg.publish_cooldown_hours > 0:
            try:
                elapsed = datetime.now(timezone.utc).timestamp() - datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
                if elapsed < cfg.publish_cooldown_hours * 3600:
                    blockers.append("symbol publish cooldown active")
            except ValueError:
                pass
        return blockers
