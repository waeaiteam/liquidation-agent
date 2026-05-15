from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.environ.get("LIQAGENT_DATA_DIR", os.path.join(BASE_DIR, "data"))
POTENTIAL_DB_PATH = os.path.join(STATE_DIR, "potential_coin.db")


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_hour(value: str | None = None) -> str:
    dt = datetime.fromisoformat(str(value or utc_iso()).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d-%H")


def safe_json_loads(value: Any, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class ScannerConfig:
    poll_seconds: int = 60
    min_fr_positive_periods: int = 2
    min_oi_change_pct: float = 8.0
    min_volume_usdt: float = 2_000_000.0
    min_watch_fr_abs: float = 0.0001
    watch_confidence_threshold: float = 70.0
    watch_timeout_ticks: int = 480
    min_score_for_alert: float = 70.0
    min_score_for_analysis: float = 70.0
    auto_analyze: bool = False
    auto_trade: bool = False
    trading_notional_usdt: float = 100.0
    max_concurrent_positions: int = 3
    paper_slippage_pct: float = 0.003
    entry_confirmation_mode: str = "aggressive"
    max_publish_per_day: int = 80
    publish_cooldown_hours: float = 2.0
    publish_mode: str = "manual"
    square_api_key: str = ""
    tg_enabled: bool = False
    tg_bot_token: str = ""
    tg_chat_id: str = ""
    weekend_score_discount: float = 0.7

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ScannerConfig":
        base = cls()
        values = base.__dict__.copy()
        for key, value in (data or {}).items():
            if key in values:
                values[key] = value
        values["poll_seconds"] = max(10, int(values["poll_seconds"] or 60))
        values["min_fr_positive_periods"] = max(1, int(values["min_fr_positive_periods"] or 2))
        values["min_oi_change_pct"] = max(0.0, float(values["min_oi_change_pct"] or 0))
        values["min_volume_usdt"] = max(0.0, float(values["min_volume_usdt"] or 0))
        values["min_watch_fr_abs"] = max(0.0, float(values["min_watch_fr_abs"] or 0.0001))
        values["watch_confidence_threshold"] = max(1.0, min(100.0, float(values["watch_confidence_threshold"] or 70)))
        values["watch_timeout_ticks"] = max(1, int(values["watch_timeout_ticks"] or 480))
        values["min_score_for_alert"] = max(0.0, min(100.0, float(values["min_score_for_alert"] or 0)))
        values["min_score_for_analysis"] = max(0.0, min(100.0, float(values["min_score_for_analysis"] or 0)))
        values["trading_notional_usdt"] = max(1.0, float(values["trading_notional_usdt"] or 100))
        values["max_concurrent_positions"] = max(1, int(values["max_concurrent_positions"] or 3))
        values["paper_slippage_pct"] = max(0.0, min(0.05, float(values["paper_slippage_pct"] or 0)))
        values["entry_confirmation_mode"] = str(values["entry_confirmation_mode"] or "aggressive").lower()
        if values["entry_confirmation_mode"] not in {"aggressive", "breakout", "candle_close"}:
            values["entry_confirmation_mode"] = "aggressive"
        values["max_publish_per_day"] = max(0, int(values["max_publish_per_day"] or 0))
        values["publish_cooldown_hours"] = max(0.0, float(values["publish_cooldown_hours"] or 0))
        values["publish_mode"] = str(values["publish_mode"] or "manual").lower()
        if values["publish_mode"] not in {"manual", "ai_decides", "scheduled"}:
            values["publish_mode"] = "manual"
        values["square_api_key"] = str(values["square_api_key"] or "")
        values["weekend_score_discount"] = max(0.1, min(1.0, float(values["weekend_score_discount"] or 0.7)))
        values["tg_enabled"] = bool(values["tg_enabled"])
        values["auto_analyze"] = bool(values["auto_analyze"])
        values["auto_trade"] = bool(values["auto_trade"])
        return cls(**values)

    def raw_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def to_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        key = str(data.pop("square_api_key", "") or "")
        data["has_square_api_key"] = bool(key)
        data["square_api_key"] = "***" if key else ""
        return data


@dataclass
class AgentConfig:
    agent_type: str
    provider: str = "anthropic"
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    temperature: float = 0.2
    context_length: int = 0
    enabled: bool = True

    @classmethod
    def from_row(cls, row: sqlite3.Row | None, agent_type: str) -> "AgentConfig":
        if not row:
            return cls(agent_type=agent_type)
        return cls(
            agent_type=agent_type,
            provider=str(row["provider"] or "anthropic"),
            api_key=str(row["api_key"] or ""),
            model=str(row["model"] or ""),
            base_url=str(row["base_url"] or ""),
            temperature=to_float(row["temperature"], 0.2),
            context_length=int(row["context_length"] or 0),
            enabled=bool(row["enabled"]),
        )

    def to_dict(self, *, redact: bool = True) -> dict[str, Any]:
        return {
            "agent_type": self.agent_type,
            "provider": self.provider,
            "api_key": "***" if redact and self.api_key else self.api_key,
            "has_api_key": bool(self.api_key),
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "context_length": self.context_length,
            "enabled": self.enabled,
        }


class PotentialStore:
    def __init__(self, path: str = POTENTIAL_DB_PATH):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.RLock()
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_schema(self) -> None:
        with self._lock, self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    detected_hour TEXT NOT NULL,
                    potential_score REAL DEFAULT 0,
                    fr_current REAL,
                    fr_previous REAL,
                    fr_positive_periods INTEGER,
                    oi_change_pct REAL,
                    oi_segments TEXT,
                    oi_consecutive_rising INTEGER,
                    volume_24h REAL,
                    price REAL,
                    price_change_24h REAL,
                    has_spot INTEGER DEFAULT 0,
                    market_cap REAL DEFAULT 0,
                    square_heat INTEGER DEFAULT 0,
                    detected_at TEXT NOT NULL,
                    status TEXT DEFAULT 'new',
                    ai_analysis TEXT,
                    raw_payload TEXT,
                    UNIQUE(symbol, detected_hour)
                );
                CREATE TABLE IF NOT EXISTS watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT UNIQUE NOT NULL,
                    added_at TEXT NOT NULL,
                    last_updated TEXT,
                    ticks_watched INTEGER DEFAULT 0,
                    confidence REAL DEFAULT 0,
                    fr_current REAL,
                    fr_trend TEXT DEFAULT 'unknown',
                    oi_current REAL,
                    oi_trend TEXT DEFAULT 'unknown',
                    volume_current REAL,
                    volume_trend TEXT DEFAULT 'unknown',
                    price_current REAL,
                    price_at_add REAL,
                    status TEXT DEFAULT 'watching',
                    ai_analysis TEXT,
                    removed_reason TEXT,
                    signal_id INTEGER,
                    raw_payload TEXT
                );
                CREATE TABLE IF NOT EXISTS watchlist_ticks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    tick_at TEXT NOT NULL,
                    fr REAL,
                    oi REAL,
                    volume REAL,
                    price REAL,
                    confidence_delta REAL,
                    UNIQUE(symbol, tick_at)
                );
                CREATE TABLE IF NOT EXISTS publish_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER REFERENCES signals(id),
                    agent_type TEXT DEFAULT 'pub',
                    content TEXT NOT NULL,
                    mode TEXT DEFAULT 'draft',
                    post_id TEXT,
                    error TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    published_at TEXT
                );
                CREATE TABLE IF NOT EXISTS agent_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_type TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    category TEXT DEFAULT 'long',
                    updated_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(agent_type, key)
                );
                CREATE TABLE IF NOT EXISTS agent_config (
                    agent_type TEXT PRIMARY KEY,
                    provider TEXT DEFAULT 'anthropic',
                    api_key TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    base_url TEXT DEFAULT '',
                    temperature REAL DEFAULT 0.2,
                    context_length INTEGER DEFAULT 0,
                    enabled INTEGER DEFAULT 1,
                    updated_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    data TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS agent_trajectory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_type TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    input_data TEXT,
                    decision TEXT,
                    outcome TEXT,
                    timestamp TEXT NOT NULL,
                    session_id TEXT
                );
                CREATE TABLE IF NOT EXISTS paper_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL,
                    entry_time TEXT,
                    quantity REAL,
                    status TEXT DEFAULT 'open',
                    exit_price REAL,
                    exit_time TEXT,
                    pnl_pct REAL,
                    pnl_usdt REAL,
                    exit_reason TEXT,
                    max_drawdown_pct REAL,
                    max_profit_pct REAL,
                    stop_price REAL,
                    trailing_stop REAL,
                    notional_usdt REAL,
                    remaining_pct REAL DEFAULT 100,
                    signal_id INTEGER,
                    trajectory_id TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS paper_balance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_type TEXT UNIQUE NOT NULL,
                    initial_balance REAL DEFAULT 10000,
                    current_balance REAL DEFAULT 10000,
                    total_trades INTEGER DEFAULT 0,
                    win_trades INTEGER DEFAULT 0,
                    total_pnl REAL DEFAULT 0,
                    max_drawdown REAL DEFAULT 0,
                    sharpe_ratio REAL DEFAULT 0,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS evolution_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_type TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    changes TEXT NOT NULL,
                    trigger TEXT,
                    metrics_before TEXT,
                    metrics_after TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_type TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    payload TEXT,
                    status TEXT DEFAULT 'queued',
                    result TEXT,
                    error TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                );
                """
            )
            for agent_type in ("liq", "pot", "anl", "pub"):
                conn.execute(
                    "INSERT OR IGNORE INTO agent_config(agent_type) VALUES (?)",
                    (agent_type,),
                )
            conn.execute("INSERT OR IGNORE INTO config(id, data) VALUES (1, '{}')")

    def get_scanner_config(self) -> ScannerConfig:
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT data FROM config WHERE id = 1").fetchone()
        data = safe_json_loads(row["data"] if row else "{}", {})
        return ScannerConfig.from_dict(data)

    def update_scanner_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.get_scanner_config().raw_dict()
        patch = {k: v for k, v in (patch or {}).items() if k in current}
        if patch.get("square_api_key") == "***":
            patch.pop("square_api_key")
        current.update(patch)
        config = ScannerConfig.from_dict(current)
        with self._lock, self.connect() as conn:
            conn.execute("UPDATE config SET data = ? WHERE id = 1", (json.dumps(config.raw_dict(), ensure_ascii=False),))
        return config.to_dict()

    def get_agent_config(self, agent_type: str, *, redact: bool = False) -> AgentConfig | dict[str, Any]:
        agent_type = normalize_agent_type(agent_type)
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM agent_config WHERE agent_type = ?", (agent_type,)).fetchone()
        cfg = AgentConfig.from_row(row, agent_type)
        return cfg.to_dict(redact=redact) if redact else cfg

    def all_agent_configs(self, *, redact: bool = True) -> dict[str, dict[str, Any]]:
        return {agent: self.get_agent_config(agent, redact=redact) for agent in ("liq", "pot", "anl", "pub")}

    def update_agent_configs(self, configs: dict[str, Any]) -> dict[str, dict[str, Any]]:
        with self._lock, self.connect() as conn:
            for agent_type, data in (configs or {}).items():
                agent = normalize_agent_type(agent_type)
                data = data or {}
                current = self.get_agent_config(agent)
                provider = str(data.get("provider") or current.provider or "anthropic").lower()
                api_key = str(data.get("api_key") if "api_key" in data else current.api_key)
                model = str(data.get("model") if "model" in data else current.model)
                base_url = str(data.get("base_url") if "base_url" in data else current.base_url)
                temperature = to_float(data.get("temperature", current.temperature), current.temperature)
                context_length = int(data.get("context_length", current.context_length) or 0)
                enabled = 1 if bool(data.get("enabled", current.enabled)) else 0
                conn.execute(
                    """
                    INSERT INTO agent_config(agent_type, provider, api_key, model, base_url, temperature, context_length, enabled, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(agent_type) DO UPDATE SET
                        provider=excluded.provider,
                        api_key=excluded.api_key,
                        model=excluded.model,
                        base_url=excluded.base_url,
                        temperature=excluded.temperature,
                        context_length=excluded.context_length,
                        enabled=excluded.enabled,
                        updated_at=excluded.updated_at
                    """,
                    (agent, provider, api_key, model, base_url, temperature, context_length, enabled, utc_iso()),
                )
        return self.all_agent_configs(redact=True)

    def upsert_watchlist_item(self, symbol: str, *, fr: float, oi: float, volume: float, price: float) -> dict[str, Any]:
        symbol = str(symbol or "").upper()
        now = utc_iso()
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM watchlist WHERE symbol = ?", (symbol,)).fetchone()
            if not row:
                conn.execute(
                    """
                    INSERT INTO watchlist(symbol, added_at, last_updated, ticks_watched, confidence, fr_current, oi_current,
                        volume_current, price_current, price_at_add, status, raw_payload)
                    VALUES (?, ?, ?, 0, 0, ?, ?, ?, ?, ?, 'watching', ?)
                    """,
                    (symbol, now, now, fr, oi, volume, price, price, json.dumps({"source": "watchlist"}, ensure_ascii=False)),
                )
                row = conn.execute("SELECT * FROM watchlist WHERE symbol = ?", (symbol,)).fetchone()
            elif str(row["status"] or "") in {"removed", "exited"}:
                conn.execute(
                    """
                    UPDATE watchlist
                    SET added_at=?, last_updated=?, ticks_watched=0, confidence=0, fr_current=?, oi_current=?,
                        volume_current=?, price_current=?, price_at_add=?, status='watching', removed_reason=NULL, signal_id=NULL
                    WHERE symbol=?
                    """,
                    (now, now, fr, oi, volume, price, price, symbol),
                )
                row = conn.execute("SELECT * FROM watchlist WHERE symbol = ?", (symbol,)).fetchone()
        return self._watchlist_row(row)

    def get_watchlist_item(self, symbol: str) -> dict[str, Any] | None:
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM watchlist WHERE symbol = ?", (str(symbol or "").upper(),)).fetchone()
        return self._watchlist_row(row) if row else None

    def list_watchlist(self, status: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        sql = "SELECT * FROM watchlist"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY CASE status WHEN 'trading' THEN 0 WHEN 'analyzing' THEN 1 WHEN 'watching' THEN 2 ELSE 3 END, confidence DESC, last_updated DESC LIMIT ?"
        params.append(max(1, min(int(limit or 300), 1000)))
        with self._lock, self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._watchlist_row(row) for row in rows]

    def active_watchlist_symbols(self) -> list[str]:
        with self._lock, self.connect() as conn:
            rows = conn.execute("SELECT symbol FROM watchlist WHERE status IN ('watching', 'analyzing', 'trading')").fetchall()
        return [str(row["symbol"]) for row in rows]

    def update_watchlist_tick(self, symbol: str, *, fr: float, oi: float, volume: float, price: float, confidence_delta: float, trends: dict[str, str], status: str | None = None, signal_id: int | None = None, ai_analysis: Any = None, raw_payload: Any = None) -> dict[str, Any]:
        symbol = str(symbol or "").upper()
        now = utc_iso()
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM watchlist WHERE symbol = ?", (symbol,)).fetchone()
            if not row:
                raise ValueError("watchlist item not found")
            confidence = max(0.0, min(100.0, to_float(row["confidence"]) + float(confidence_delta or 0)))
            next_status = status or row["status"] or "watching"
            conn.execute(
                """
                UPDATE watchlist
                SET last_updated=?, ticks_watched=COALESCE(ticks_watched, 0)+1, confidence=?, fr_current=?, fr_trend=?,
                    oi_current=?, oi_trend=?, volume_current=?, volume_trend=?, price_current=?, status=?,
                    signal_id=COALESCE(?, signal_id),
                    ai_analysis=COALESCE(?, ai_analysis),
                    raw_payload=COALESCE(?, raw_payload)
                WHERE symbol=?
                """,
                (
                    now,
                    confidence,
                    fr,
                    trends.get("fr_trend") or "unknown",
                    oi,
                    trends.get("oi_trend") or "unknown",
                    volume,
                    trends.get("volume_trend") or "unknown",
                    price,
                    next_status,
                    signal_id,
                    json.dumps(ai_analysis, ensure_ascii=False) if ai_analysis is not None else None,
                    json.dumps(raw_payload, ensure_ascii=False) if raw_payload is not None else None,
                    symbol,
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO watchlist_ticks(symbol, tick_at, fr, oi, volume, price, confidence_delta)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (symbol, now, fr, oi, volume, price, confidence_delta),
            )
            row = conn.execute("SELECT * FROM watchlist WHERE symbol = ?", (symbol,)).fetchone()
        return self._watchlist_row(row)

    def remove_watchlist_item(self, symbol: str, reason: str) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                "UPDATE watchlist SET status='removed', removed_reason=?, last_updated=? WHERE symbol=? AND status IN ('watching', 'analyzing')",
                (reason, utc_iso(), str(symbol or "").upper()),
            )

    def mark_watchlist_status(self, symbol: str, status: str, **fields: Any) -> None:
        allowed = {"ai_analysis", "signal_id", "removed_reason"}
        updates = ["status = ?", "last_updated = ?"]
        values: list[Any] = [status, utc_iso()]
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "ai_analysis" and value is not None and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            updates.append(f"{key} = ?")
            values.append(value)
        values.append(str(symbol or "").upper())
        with self._lock, self.connect() as conn:
            conn.execute(f"UPDATE watchlist SET {', '.join(updates)} WHERE symbol = ?", values)

    def watchlist_ticks(self, symbol: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM watchlist_ticks WHERE symbol = ? ORDER BY tick_at DESC LIMIT ?",
                (str(symbol or "").upper(), max(1, min(int(limit or 50), 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def _watchlist_row(self, row: sqlite3.Row | None) -> dict[str, Any]:
        if not row:
            return {}
        data = dict(row)
        data["ai_analysis"] = safe_json_loads(data.get("ai_analysis"), None)
        data["raw_payload"] = safe_json_loads(data.get("raw_payload"), {})
        return data

    def signal_exists_for_hour(self, symbol: str, detected_hour: str) -> bool:
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM signals WHERE symbol = ? AND detected_hour = ?",
                (symbol.upper(), detected_hour),
            ).fetchone()
        return bool(row)

    def insert_signal(self, signal: dict[str, Any]) -> int | None:
        detected_at = signal.get("detected_at") or utc_iso()
        detected_hour = signal.get("detected_hour") or utc_hour(detected_at)
        payload = dict(signal)
        payload["detected_hour"] = detected_hour
        payload["detected_at"] = detected_at
        with self._lock, self.connect() as conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO signals(
                        symbol, detected_hour, potential_score, fr_current, fr_previous, fr_positive_periods,
                        oi_change_pct, oi_segments, oi_consecutive_rising, volume_24h, price, price_change_24h,
                        has_spot, market_cap, square_heat, detected_at, status, ai_analysis, raw_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(payload.get("symbol") or "").upper(),
                        detected_hour,
                        to_float(payload.get("potential_score")),
                        to_float(payload.get("fr_current")),
                        to_float(payload.get("fr_previous")),
                        int(payload.get("fr_positive_periods") or 0),
                        to_float(payload.get("oi_change_pct")),
                        json.dumps(payload.get("oi_segments") or [], ensure_ascii=False),
                        1 if payload.get("oi_consecutive_rising") else 0,
                        to_float(payload.get("volume_24h")),
                        to_float(payload.get("price")),
                        to_float(payload.get("price_change_24h")),
                        1 if payload.get("has_spot") else 0,
                        to_float(payload.get("market_cap")),
                        int(payload.get("square_heat") or 0),
                        detected_at,
                        str(payload.get("status") or "new"),
                        json.dumps(payload.get("ai_analysis"), ensure_ascii=False) if payload.get("ai_analysis") else None,
                        json.dumps(payload.get("raw_payload") or payload, ensure_ascii=False),
                    ),
                )
                signal_id = int(cur.lastrowid)
            except sqlite3.IntegrityError:
                return None
        return signal_id

    def update_signal(self, signal_id: int, **fields: Any) -> None:
        allowed = {"status", "ai_analysis", "raw_payload"}
        updates = []
        values = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in {"ai_analysis", "raw_payload"} and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            updates.append(f"{key} = ?")
            values.append(value)
        if not updates:
            return
        values.append(int(signal_id))
        with self._lock, self.connect() as conn:
            conn.execute(f"UPDATE signals SET {', '.join(updates)} WHERE id = ?", values)

    def list_signals(self, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 500))
        sql = "SELECT * FROM signals"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY detected_at DESC, potential_score DESC LIMIT ?"
        params.append(limit)
        with self._lock, self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._signal_row(row) for row in rows]

    def get_signal(self, signal_id: int) -> dict[str, Any] | None:
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM signals WHERE id = ?", (int(signal_id),)).fetchone()
        return self._signal_row(row) if row else None

    def _signal_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["oi_segments"] = safe_json_loads(data.get("oi_segments"), [])
        data["ai_analysis"] = safe_json_loads(data.get("ai_analysis"), None)
        data["raw_payload"] = safe_json_loads(data.get("raw_payload"), {})
        data["has_spot"] = bool(data.get("has_spot"))
        data["oi_consecutive_rising"] = bool(data.get("oi_consecutive_rising"))
        return data

    def create_publish_draft(self, signal_id: int, content: str, mode: str = "draft") -> int:
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO publish_queue(signal_id, content, mode, created_at) VALUES (?, ?, ?, ?)",
                (int(signal_id), content, mode, utc_iso()),
            )
            return int(cur.lastrowid)

    def list_drafts(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT q.*, s.symbol, s.potential_score
                FROM publish_queue q
                LEFT JOIN signals s ON s.id = q.signal_id
                ORDER BY q.created_at DESC
                LIMIT ?
                """,
                (max(1, min(int(limit or 100), 300)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_draft(self, draft_id: int) -> dict[str, Any] | None:
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM publish_queue WHERE id = ?", (int(draft_id),)).fetchone()
        return dict(row) if row else None

    def update_draft(self, draft_id: int, **fields: Any) -> None:
        allowed = {"content", "mode", "post_id", "error", "published_at"}
        updates = []
        values = []
        for key, value in fields.items():
            if key in allowed:
                updates.append(f"{key} = ?")
                values.append(value)
        if not updates:
            return
        values.append(int(draft_id))
        with self._lock, self.connect() as conn:
            conn.execute(f"UPDATE publish_queue SET {', '.join(updates)} WHERE id = ?", values)

    def publish_count_today(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM publish_queue WHERE mode = 'published' AND published_at LIKE ?",
                (today + "%",),
            ).fetchone()
        return int(row["c"] if row else 0)

    def last_publish_for_symbol(self, symbol: str) -> str | None:
        with self._lock, self.connect() as conn:
            row = conn.execute(
                """
                SELECT q.published_at
                FROM publish_queue q
                JOIN signals s ON s.id = q.signal_id
                WHERE q.mode = 'published' AND s.symbol = ?
                ORDER BY q.published_at DESC
                LIMIT 1
                """,
                (symbol.upper(),),
            ).fetchone()
        return row["published_at"] if row else None

    def log_trajectory(
        self,
        agent_type: str,
        action_type: str,
        *,
        input_data: Any = None,
        decision: Any = None,
        outcome: Any = None,
        session_id: str | None = None,
    ) -> int:
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO agent_trajectory(agent_type, action_type, input_data, decision, outcome, timestamp, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalize_agent_type(agent_type),
                    action_type,
                    json.dumps(input_data, ensure_ascii=False) if input_data is not None else None,
                    json.dumps(decision, ensure_ascii=False) if decision is not None else None,
                    json.dumps(outcome, ensure_ascii=False) if outcome is not None else None,
                    utc_iso(),
                    session_id or str(uuid4()),
                ),
            )
            return int(cur.lastrowid)

    def list_trajectory(self, agent_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM agent_trajectory"
        params: list[Any] = []
        if agent_type:
            sql += " WHERE agent_type = ?"
            params.append(normalize_agent_type(agent_type))
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(max(1, min(int(limit or 100), 500)))
        with self._lock, self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["input_data"] = safe_json_loads(item.get("input_data"), None)
            item["decision"] = safe_json_loads(item.get("decision"), None)
            item["outcome"] = safe_json_loads(item.get("outcome"), None)
            out.append(item)
        return out

    def set_memory(self, agent_type: str, key: str, value: Any, category: str = "long") -> None:
        value_s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_memory(agent_type, key, value, category, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(agent_type, key) DO UPDATE SET value=excluded.value, category=excluded.category, updated_at=excluded.updated_at
                """,
                (normalize_agent_type(agent_type), key, value_s, category, utc_iso()),
            )

    def get_memory(self, agent_type: str) -> dict[str, Any]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                "SELECT key, value, category, updated_at FROM agent_memory WHERE agent_type = ? ORDER BY updated_at DESC",
                (normalize_agent_type(agent_type),),
            ).fetchall()
        memory = {}
        for row in rows:
            memory[row["key"]] = {
                "value": safe_json_loads(row["value"], row["value"]),
                "category": row["category"],
                "updated_at": row["updated_at"],
            }
        return memory

    def add_evolution_log(self, agent_type: str, version: int, changes: Any, trigger: str, metrics_before: Any = None) -> int:
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO evolution_log(agent_type, version, changes, trigger, metrics_before, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    normalize_agent_type(agent_type),
                    int(version),
                    json.dumps(changes, ensure_ascii=False),
                    trigger,
                    json.dumps(metrics_before, ensure_ascii=False) if metrics_before is not None else None,
                    utc_iso(),
                ),
            )
            return int(cur.lastrowid)

    def evolution_logs(self, agent_type: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evolution_log WHERE agent_type = ? ORDER BY version DESC, created_at DESC LIMIT ?",
                (normalize_agent_type(agent_type), max(1, min(int(limit or 50), 200))),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["changes"] = safe_json_loads(item.get("changes"), {})
            item["metrics_before"] = safe_json_loads(item.get("metrics_before"), None)
            item["metrics_after"] = safe_json_loads(item.get("metrics_after"), None)
            items.append(item)
        return items


class BinancePublicClient:
    base_url = "https://fapi.binance.com"
    spot_url = "https://api.binance.com"
    web_url = "https://www.binance.com"

    def _get_json(self, url: str, params: dict[str, Any] | None = None, timeout: int = 12) -> Any:
        if params:
            url = url + "?" + urlencode(params)
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 LIQ-agent"})
        try:
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"Binance HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Binance request failed: {exc.reason}") from exc

    def premium_index(self) -> list[dict[str, Any]]:
        data = self._get_json(f"{self.base_url}/fapi/v1/premiumIndex")
        return data if isinstance(data, list) else []

    def exchange_info(self) -> list[str]:
        data = self._get_json(f"{self.base_url}/fapi/v1/exchangeInfo")
        symbols = []
        for item in data.get("symbols", []) if isinstance(data, dict) else []:
            if item.get("contractType") == "PERPETUAL" and item.get("quoteAsset") == "USDT" and item.get("status") == "TRADING":
                symbols.append(str(item.get("symbol") or "").upper())
        return symbols

    def tickers_24hr(self) -> dict[str, dict[str, Any]]:
        data = self._get_json(f"{self.base_url}/fapi/v1/ticker/24hr")
        if not isinstance(data, list):
            return {}
        return {str(item.get("symbol") or "").upper(): item for item in data}

    def funding_history(self, symbol: str, limit: int = 8) -> list[dict[str, Any]]:
        data = self._get_json(f"{self.base_url}/fapi/v1/fundingRate", {"symbol": symbol.upper(), "limit": int(limit)})
        if not isinstance(data, list):
            return []
        return sorted(data, key=lambda item: int(item.get("fundingTime") or 0))

    def open_interest_hist(self, symbol: str, period: str = "1h", limit: int = 24) -> list[dict[str, Any]]:
        data = self._get_json(
            f"{self.base_url}/futures/data/openInterestHist",
            {"symbol": symbol.upper(), "period": period, "limit": int(limit)},
        )
        return data if isinstance(data, list) else []

    def klines(self, symbol: str, interval: str = "1h", limit: int = 60) -> list[list[Any]]:
        data = self._get_json(f"{self.base_url}/fapi/v1/klines", {"symbol": symbol.upper(), "interval": interval, "limit": int(limit)})
        return data if isinstance(data, list) else []

    def spot_symbols(self) -> set[str]:
        try:
            data = self._get_json(f"{self.spot_url}/api/v3/exchangeInfo", timeout=10)
        except Exception:
            return set()
        return {
            str(item.get("baseAsset") or "").upper()
            for item in data.get("symbols", []) if isinstance(data, dict)
            if item.get("quoteAsset") == "USDT" and item.get("status") == "TRADING"
        }

    def market_caps(self) -> dict[str, float]:
        try:
            data = self._get_json(f"{self.web_url}/bapi/composite/v1/public/marketing/symbol/list", timeout=10)
        except Exception:
            return {}
        caps = {}
        for item in data.get("data", []) if isinstance(data, dict) else []:
            coin = str(item.get("name") or "").upper()
            cap = to_float(item.get("marketCap"))
            if coin and cap > 0:
                caps[coin] = cap
        return caps

    def square_discussion(self, coin: str) -> tuple[int, int]:
        try:
            data = self._get_json(
                f"{self.web_url}/bapi/composite/v4/friendly/pgc/content/queryByHashtag",
                {"hashtag": f"#{coin.lower()}", "pageIndex": 1, "pageSize": 1, "orderBy": "HOT"},
                timeout=8,
            )
            hashtag = data.get("data", {}).get("hashtag", {}) if isinstance(data, dict) else {}
            return int(hashtag.get("contentCount") or 0), int(hashtag.get("viewCount") or 0)
        except Exception:
            return 0, 0


def normalize_agent_type(agent_type: str) -> str:
    agent = str(agent_type or "").strip().lower().replace("agent", "")
    if agent not in {"liq", "pot", "anl", "pub"}:
        raise ValueError("agent_type must be one of liq, pot, anl, pub")
    return agent


def funding_confirmed_positive(history: list[dict[str, Any]], periods: int) -> tuple[bool, int, list[float]]:
    rates = [to_float(item.get("fundingRate")) for item in history if item.get("fundingRate") is not None]
    recent = rates[-max(1, int(periods)):]
    ok = len(recent) >= periods and all(rate > 0 for rate in recent)
    positives = 0
    for rate in reversed(rates):
        if rate > 0:
            positives += 1
        else:
            break
    return ok, positives, recent


def oi_segments(hist: list[dict[str, Any]]) -> tuple[list[float], float, bool]:
    values = []
    for item in hist:
        value = to_float(item.get("sumOpenInterestValue"), None) if item.get("sumOpenInterestValue") is not None else None
        if value is None:
            value = to_float(item.get("sumOpenInterest"))
        if value and value > 0:
            values.append(value)
    if len(values) < 24:
        return [], 0.0, False
    values = values[-24:]
    segments = [sum(values[i:i + 6]) / 6 for i in range(0, 24, 6)]
    rising = all(segments[i] < segments[i + 1] for i in range(3))
    change = ((segments[-1] - segments[0]) / segments[0] * 100) if segments[0] > 0 else 0.0
    return segments, change, rising


def _watch_oi_value(item: dict[str, Any]) -> float:
    raw = item.get("sumOpenInterestValue") if item.get("sumOpenInterestValue") is not None else item.get("sumOpenInterest")
    return to_float(raw)


def _trend(current: float, previous: float, *, tolerance: float = 0.002, up: str = "rising", down: str = "falling", flat: str = "flat") -> str:
    if previous <= 0 or current <= 0:
        return "unknown"
    change = (current - previous) / previous
    if change > tolerance:
        return up
    if change < -tolerance:
        return down
    return flat


def watchlist_trends(previous: dict[str, Any], fr: float, oi: float, volume: float, price: float) -> dict[str, str]:
    prev_fr = to_float(previous.get("fr_current"))
    if fr < 0 and abs(fr) > abs(prev_fr):
        fr_trend = "deepening"
    elif fr < 0 and abs(fr - prev_fr) <= 0.00002:
        fr_trend = "stable"
    elif fr < 0:
        fr_trend = "recovering"
    else:
        fr_trend = "positive"
    return {
        "fr_trend": fr_trend,
        "oi_trend": _trend(oi, to_float(previous.get("oi_current")), up="rising", down="falling", flat="flat"),
        "volume_trend": _trend(volume, to_float(previous.get("volume_current")), up="expanding", down="shrinking", flat="stable"),
        "price_trend": _trend(price, to_float(previous.get("price_current")), up="rising", down="falling", flat="stable"),
    }


def watchlist_confidence_delta(previous: dict[str, Any], fr: float, oi: float, volume: float, price: float, trends: dict[str, str]) -> float:
    delta = 0.0
    if fr < 0:
        delta += 5.0 if trends.get("fr_trend") == "deepening" else 2.0
    else:
        delta -= 20.0
    if trends.get("oi_trend") == "rising":
        delta += 8.0
    elif trends.get("oi_trend") == "falling":
        delta -= 15.0
    if trends.get("volume_trend") == "expanding":
        delta += 3.0
    elif trends.get("volume_trend") == "shrinking":
        delta -= 5.0
    price_at_add = to_float(previous.get("price_at_add"))
    if price_at_add > 0 and price > 0:
        gain = (price - price_at_add) / price_at_add * 100
        if gain < 5:
            delta += 2.0
        elif gain > 15:
            delta -= 10.0
    return round(delta, 4)


def score_signal(payload: dict[str, Any]) -> dict[str, Any]:
    current_fr = abs(min(0.0, to_float(payload.get("fr_current"))))
    fr_periods = int(payload.get("fr_positive_periods") or 0)
    oi_change = max(0.0, to_float(payload.get("oi_change_pct")))
    volume = max(0.0, to_float(payload.get("volume_24h")))
    price_chg = to_float(payload.get("price_change_24h"))
    market_cap = max(0.0, to_float(payload.get("market_cap")))
    square_heat = max(0, int(payload.get("square_heat") or 0))

    fr_score = min(12.0, current_fr / 0.001 * 12.0) + min(8.0, fr_periods / 5 * 8.0)
    oi_score = min(20.0, max(0.0, (oi_change - 8.0) / 12.0 * 20.0)) + (5.0 if payload.get("oi_consecutive_rising") else 0.0)
    volume_score = min(20.0, volume / 50_000_000 * 20.0)
    price_score = 15.0 if price_chg <= 0 else max(0.0, 15.0 - min(price_chg, 30.0) / 30.0 * 15.0)
    spot_mcap_score = (5.0 if payload.get("has_spot") else 0.0)
    if 20_000_000 <= market_cap <= 2_000_000_000:
        spot_mcap_score += 5.0
    elif market_cap > 0:
        spot_mcap_score += 2.5
    if square_heat <= 0:
        square_score = 0.0
    elif square_heat <= 200:
        square_score = min(10.0, square_heat / 200 * 10.0)
    else:
        square_score = max(3.0, 10.0 - min(square_heat - 200, 1000) / 1000 * 7.0)

    score = round(min(100.0, fr_score + oi_score + volume_score + price_score + spot_mcap_score + square_score), 2)
    return {
        "score": score,
        "components": {
            "funding_reversal": round(fr_score, 2),
            "oi_growth": round(oi_score, 2),
            "volume": round(volume_score, 2),
            "price_position": round(price_score, 2),
            "spot_market_cap": round(spot_mcap_score, 2),
            "square_heat": round(square_score, 2),
        },
    }


class PotentialScanner:
    def __init__(self, store: PotentialStore, client: BinancePublicClient | None = None):
        self.store = store
        self.client = client or BinancePublicClient()
        self._lock = threading.RLock()
        self.runtime: dict[str, Any] = {
            "running": False,
            "warmup_done": False,
            "tick_count": 0,
            "last_run_at": None,
            "next_run_at": None,
            "last_error": "",
            "consecutive_errors": 0,
            "last_summary": {},
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            cfg = self.store.get_scanner_config()
            today = datetime.now(timezone.utc).date().isoformat()
            signals_today = len([s for s in self.store.list_signals(500) if str(s.get("detected_at", "")).startswith(today)])
            return {
                **self.runtime,
                "config": cfg.to_dict(),
                "today_signal_count": signals_today,
                "watchlist_count": len(self.store.list_watchlist("watching", 1000)),
                "db_path": self.store.path,
                "in_memory_snapshot_symbols": 0,
            }

    def set_running(self, running: bool) -> None:
        with self._lock:
            self.runtime["running"] = bool(running)
            if not running:
                self.runtime["next_run_at"] = None

    def set_next_run(self, seconds: int | float) -> None:
        with self._lock:
            self.runtime["next_run_at"] = datetime.fromtimestamp(time.time() + max(0.0, float(seconds)), timezone.utc).isoformat()

    def scan_once(self, *, manual: bool = False) -> dict[str, Any]:
        cfg = self.store.get_scanner_config()
        started_at = utc_iso()
        with self._lock:
            self.runtime["tick_count"] += 1
            self.runtime["last_run_at"] = started_at
        try:
            result = self._scan_once(cfg, manual=manual)
            with self._lock:
                self.runtime["warmup_done"] = True
                self.runtime["last_error"] = ""
                self.runtime["consecutive_errors"] = 0
                self.runtime["last_summary"] = result
            return result
        except Exception as exc:
            with self._lock:
                self.runtime["last_error"] = str(exc)
                self.runtime["consecutive_errors"] = int(self.runtime.get("consecutive_errors") or 0) + 1
            raise

    def _scan_once(self, cfg: ScannerConfig, *, manual: bool = False) -> dict[str, Any]:
        premium = self.client.premium_index()
        current_rates = {
            str(item.get("symbol") or "").upper(): to_float(item.get("lastFundingRate"))
            for item in premium
            if str(item.get("symbol") or "").upper().endswith("USDT")
        }
        if not current_rates:
            raise RuntimeError("premiumIndex returned no USDT symbols")

        tickers = self.client.tickers_24hr()
        exchange_symbols = set(self.client.exchange_info())
        active: list[str] = []
        watch_candidates: list[str] = []
        for symbol, rate in current_rates.items():
            if exchange_symbols and symbol not in exchange_symbols:
                continue
            volume = to_float((tickers.get(symbol) or {}).get("quoteVolume"))
            if volume >= cfg.min_volume_usdt:
                active.append(symbol)
                if rate <= -cfg.min_watch_fr_abs:
                    watch_candidates.append(symbol)

        detected_hour = utc_hour()
        spot_set = self.client.spot_symbols()
        market_caps = self.client.market_caps()
        signals = []
        skipped = []
        watch_updates = []
        watch_symbols = sorted(set(watch_candidates) | set(self.store.active_watchlist_symbols()))

        for idx, symbol in enumerate(watch_symbols):
            if idx > 0 and idx % 10 == 0:
                time.sleep(1)
            ticker = tickers.get(symbol) or {}
            fr = current_rates.get(symbol)
            volume = to_float(ticker.get("quoteVolume"))
            price = to_float(ticker.get("lastPrice"))
            if fr is None:
                skipped.append({"symbol": symbol, "reason": "missing_premium"})
                continue
            try:
                update = self._update_watchlist_symbol(symbol, cfg, fr, ticker)
            except Exception as exc:
                skipped.append({"symbol": symbol, "reason": str(exc)})
                continue
            watch_updates.append(update)
            if update.get("status") in {"removed", "trading"}:
                continue
            if fr >= 0:
                self.store.remove_watchlist_item(symbol, "fr_positive")
                update["status"] = "removed"
                update["removed_reason"] = "fr_positive"
                continue
            if int(update.get("ticks_watched") or 0) > cfg.watch_timeout_ticks:
                self.store.remove_watchlist_item(symbol, "timeout")
                update["status"] = "removed"
                update["removed_reason"] = "timeout"
                continue
            if float(update.get("confidence") or 0) < cfg.watch_confidence_threshold or str(update.get("status")) != "watching":
                continue
            if self.store.signal_exists_for_hour(symbol, detected_hour):
                self.store.mark_watchlist_status(symbol, "analyzing")
                skipped.append({"symbol": symbol, "reason": "duplicate_hour"})
                continue
            try:
                candidate = self._evaluate_symbol(symbol, cfg, update, tickers, spot_set, market_caps, detected_hour)
            except Exception as exc:
                skipped.append({"symbol": symbol, "reason": str(exc)})
                continue
            if not candidate:
                continue
            signal_id = self.store.insert_signal(candidate)
            if not signal_id:
                skipped.append({"symbol": symbol, "reason": "duplicate_hour"})
                continue
            candidate["id"] = signal_id
            self.store.mark_watchlist_status(symbol, "analyzing", signal_id=signal_id)
            signals.append(candidate)
            session_id = str(uuid4())
            self.store.log_trajectory("pot", "signal_detected", input_data=candidate.get("raw_payload"), decision=candidate, session_id=session_id)
            if cfg.tg_enabled and candidate.get("potential_score", 0) >= cfg.min_score_for_alert:
                send_tg(format_tg_signal(candidate), cfg)

        return {
            "status": "ok",
            "checked": len(active),
            "watch_candidates": len(watch_candidates),
            "watchlist_updates": len(watch_updates),
            "inserted": len(signals),
            "signals": signals,
            "watchlist": self.store.list_watchlist(limit=300),
            "skipped": skipped,
            "timestamp": utc_iso(),
            "manual": bool(manual),
        }

    def _update_watchlist_symbol(self, symbol: str, cfg: ScannerConfig, current_fr: float, ticker: dict[str, Any]) -> dict[str, Any]:
        price = to_float(ticker.get("lastPrice"))
        volume = to_float(ticker.get("quoteVolume"))
        oi_hist = self.client.open_interest_hist(symbol, period="1h", limit=2)
        oi_values = [_watch_oi_value(item) for item in oi_hist]
        oi_values = [value for value in oi_values if value > 0]
        oi = oi_values[-1] if oi_values else 0.0
        previous = self.store.get_watchlist_item(symbol)
        if not previous:
            previous = self.store.upsert_watchlist_item(symbol, fr=current_fr, oi=oi, volume=volume, price=price)
        trends = watchlist_trends(previous, current_fr, oi, volume, price)
        delta = watchlist_confidence_delta(previous, current_fr, oi, volume, price, trends)
        raw_payload = {
            "ticker": ticker,
            "oi_points": len(oi_hist),
            "confidence_delta": delta,
            "trends": trends,
        }
        return self.store.update_watchlist_tick(
            symbol,
            fr=current_fr,
            oi=oi,
            volume=volume,
            price=price,
            confidence_delta=delta,
            trends=trends,
            raw_payload=raw_payload,
        )

    def _evaluate_symbol(
        self,
        symbol: str,
        cfg: ScannerConfig,
        watch_item: dict[str, Any],
        tickers: dict[str, dict[str, Any]],
        spot_set: set[str],
        market_caps: dict[str, float],
        detected_hour: str,
    ) -> dict[str, Any] | None:
        funding = self.client.funding_history(symbol, limit=8)
        funding_ok, positive_periods, recent_rates = funding_confirmed_positive(funding, cfg.min_fr_positive_periods)
        if not funding_ok:
            return None

        oi_hist = self.client.open_interest_hist(symbol, period="1h", limit=24)
        segments, oi_change, rising = oi_segments(oi_hist)
        if not rising or oi_change < cfg.min_oi_change_pct:
            return None

        ticker = tickers.get(symbol) or {}
        coin = symbol[:-4] if symbol.endswith("USDT") else symbol
        square_posts, square_views = self.client.square_discussion(coin)
        current_fr = to_float(watch_item.get("fr_current"))
        payload = {
            "symbol": symbol,
            "detected_hour": detected_hour,
            "fr_current": current_fr,
            "fr_previous": current_fr,
            "fr_positive_periods": positive_periods,
            "recent_settled_funding_rates": recent_rates,
            "oi_change_pct": oi_change,
            "oi_segments": segments,
            "oi_consecutive_rising": rising,
            "volume_24h": to_float(ticker.get("quoteVolume")),
            "price": to_float(ticker.get("lastPrice")),
            "price_change_24h": to_float(ticker.get("priceChangePercent")),
            "has_spot": coin.upper() in spot_set,
            "market_cap": market_caps.get(coin.upper(), 0),
            "square_heat": square_posts,
            "square_views": square_views,
            "detected_at": utc_iso(),
            "status": "new",
            "watchlist_id": watch_item.get("id"),
            "watch_confidence": to_float(watch_item.get("confidence")),
            "watch_trends": {
                "fr_trend": watch_item.get("fr_trend"),
                "oi_trend": watch_item.get("oi_trend"),
                "volume_trend": watch_item.get("volume_trend"),
            },
            "raw_payload": {
                "watchlist": watch_item,
                "funding_history": funding,
                "oi_history_count": len(oi_hist),
                "ticker": ticker,
            },
        }
        scoring = score_signal(payload)
        score = scoring["score"]
        components = dict(scoring["components"])
        if datetime.now(timezone.utc).weekday() >= 5 and cfg.weekend_score_discount < 1:
            discounted = round(score * cfg.weekend_score_discount, 2)
            components["weekend_discount"] = round(discounted - score, 2)
            payload["weekend_discount_applied"] = True
            score = discounted
        payload["potential_score"] = score
        payload["score_components"] = components
        payload["raw_payload"]["score_components"] = components
        payload["raw_payload"]["weekend_discount_applied"] = bool(payload.get("weekend_discount_applied"))
        return payload


def send_tg(text: str, cfg: ScannerConfig) -> None:
    if not cfg.tg_bot_token or not cfg.tg_chat_id:
        return
    url = f"https://api.telegram.org/bot{cfg.tg_bot_token}/sendMessage"
    for chunk in [text[i:i + 4000] for i in range(0, len(text), 4000)]:
        body = json.dumps({"chat_id": cfg.tg_chat_id, "text": chunk, "parse_mode": "Markdown"}).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(req, timeout=10):
                pass
        except Exception:
            fallback = json.dumps({"chat_id": cfg.tg_chat_id, "text": chunk}).encode("utf-8")
            try:
                with urlopen(Request(url, data=fallback, headers={"Content-Type": "application/json"}, method="POST"), timeout=10):
                    pass
            except Exception:
                pass


def format_tg_signal(signal: dict[str, Any]) -> str:
    symbol = str(signal.get("symbol") or "")
    coin = symbol.replace("USDT", "")
    segs = " > ".join(f"{to_float(v) / 1_000_000:.1f}M" for v in (signal.get("oi_segments") or []))
    return (
        f"*POTagent 潜力币信号* {datetime.now().strftime('%m-%d %H:%M')}\n\n"
        f"```{coin}\n"
        f"评分: {to_float(signal.get('potential_score')):.1f}/100\n"
        f"价格: {to_float(signal.get('price')):.6g}  24h: {to_float(signal.get('price_change_24h')):+.2f}%\n"
        f"费率: {to_float(signal.get('fr_previous')):+.4%} -> {to_float(signal.get('fr_current')):+.4%}\n"
        f"OI: +{to_float(signal.get('oi_change_pct')):.2f}% ({segs})\n"
        f"成交额: ${to_float(signal.get('volume_24h')) / 1_000_000:.1f}M\n"
        f"广场: {int(signal.get('square_heat') or 0)}帖\n"
        f"```"
    )
