from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from strategy.models import Order, RiskDecision, Signal, StrategyConfig, utc_now


STATE_DIR = os.environ.get(
    "LIQAGENT_DATA_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"),
)
HEATMAP_SNAPSHOTS_PATH = os.path.join(STATE_DIR, "heatmap_snapshots.jsonl")
API_COSTS_PATH = os.path.join(STATE_DIR, "api_costs.jsonl")
ORDERS_PATH = os.path.join(STATE_DIR, "orders.jsonl")
EVENTS_PATH = os.path.join(STATE_DIR, "events.jsonl")
DECISION_REPORTS_PATH = os.path.join(STATE_DIR, "decision_reports.jsonl")
CLAW402_RAW_SAMPLES_PATH = os.path.join(STATE_DIR, "claw402_raw_samples.jsonl")
CORE_STATE_PATH = os.path.join(STATE_DIR, "agent_state.json")


class AgentState:
    def __init__(self):
        self.config = StrategyConfig()
        self.running = False
        self.last_signal: dict[str, Any] | None = None
        self.last_risk: dict[str, Any] | None = None
        self.last_snapshot: dict[str, Any] | None = None
        self.last_llm_review: dict[str, Any] | None = None
        self.orders: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.heatmap_snapshots: list[dict[str, Any]] = []
        self.api_costs: list[dict[str, Any]] = []
        self.decision_reports: list[dict[str, Any]] = []
        self.claw402_raw_samples: list[dict[str, Any]] = []
        self.agent_phase = "STOPPED"
        self.last_decision_report: dict[str, Any] | None = None
        self.diagnostics_summary: dict[str, Any] = {}
        self.tick_count = 0
        self.last_tick_started_at: str | None = None
        self.last_tick_finished_at: str | None = None
        self.last_tick_status: int | None = None
        self.next_tick_due_at: str | None = None
        self.worker_error_count = 0
        self._last_trade_ts: dict[str, float] = {}
        self._loss_pause_until = 0.0
        self._lock = threading.RLock()
        self._load_persistent_state()

    def _load_persistent_state(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        self.heatmap_snapshots = self._read_jsonl(HEATMAP_SNAPSHOTS_PATH, 288)
        self.api_costs = self._read_jsonl(API_COSTS_PATH, 1000)
        self.decision_reports = self._read_jsonl(DECISION_REPORTS_PATH, self.config.decision_report_retention)
        self.claw402_raw_samples = self._read_jsonl(CLAW402_RAW_SAMPLES_PATH, 50)
        self.orders = self._read_jsonl(ORDERS_PATH, 100)
        self.events = self._read_jsonl(EVENTS_PATH, 200)
        core = self._read_json(CORE_STATE_PATH)
        if core.get("config"):
            self.config = StrategyConfig.from_dict(core.get("config") or {})
        self.last_signal = core.get("last_signal")
        self.last_risk = core.get("last_risk")
        self.last_snapshot = core.get("last_snapshot")
        self.last_llm_review = core.get("last_llm_review")
        self.last_decision_report = core.get("last_decision_report") or (self.decision_reports[0] if self.decision_reports else None)
        self.diagnostics_summary = core.get("diagnostics_summary") if isinstance(core.get("diagnostics_summary"), dict) else {}
        self.tick_count = int(core.get("tick_count") or 0)
        self.last_tick_started_at = core.get("last_tick_started_at")
        self.last_tick_finished_at = core.get("last_tick_finished_at")
        self.last_tick_status = core.get("last_tick_status")
        self.next_tick_due_at = core.get("next_tick_due_at")
        self.worker_error_count = int(core.get("worker_error_count") or 0)
        self._loss_pause_until = float(core.get("loss_pause_until") or 0)
        self._rebuild_last_trade_ts()

    def _read_jsonl(self, path: str, limit: int) -> list[dict[str, Any]]:
        if not os.path.exists(path):
            return []
        items = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return list(reversed(items[-limit:]))

    def _read_json(self, path: str) -> dict[str, Any]:
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_json(self, path: str, item: dict[str, Any]):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(item, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, path)

    def _save_core_state(self):
        self._write_json(
            CORE_STATE_PATH,
            {
                "config": self.config.to_dict(),
                "last_signal": self.last_signal,
                "last_risk": self.last_risk,
                "last_snapshot": self.last_snapshot,
                "last_llm_review": self.last_llm_review,
                "last_decision_report": self.last_decision_report,
                "diagnostics_summary": self.diagnostics_summary,
                "tick_count": self.tick_count,
                "last_tick_started_at": self.last_tick_started_at,
                "last_tick_finished_at": self.last_tick_finished_at,
                "last_tick_status": self.last_tick_status,
                "next_tick_due_at": self.next_tick_due_at,
                "worker_error_count": self.worker_error_count,
                "loss_pause_until": self._loss_pause_until,
                "last_trade_ts": self._last_trade_ts,
                "saved_at": utc_now(),
            },
        )

    def _rebuild_last_trade_ts(self):
        self._last_trade_ts = {}
        for order in reversed(self.orders):
            if order.get("status") in {"FILLED", "OPEN", "SUBMITTED", "CLOSED"}:
                symbol = str(order.get("symbol") or "").upper()
                if symbol:
                    self._last_trade_ts[symbol] = self._parse_epoch(order.get("timestamp")) or time.time()

    def _persist_orders(self):
        self._rewrite_jsonl(ORDERS_PATH, self.orders)

    def _append_jsonl(self, path: str, item: dict[str, Any]):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _rewrite_jsonl(self, path: str, items: list[dict[str, Any]]):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for item in reversed(items):
                fh.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")

    def update_config(self, data: dict[str, Any]) -> StrategyConfig:
        with self._lock:
            merged = self.config.to_dict()
            merged.update(data or {})
            self.config = StrategyConfig.from_dict(merged)
            self.add_event("config", "updated", self.config.to_dict())
            self._save_core_state()
            return self.config

    def set_running(self, running: bool):
        with self._lock:
            self.running = running
            self.agent_phase = "SCANNING" if running else "STOPPED"
            self.add_event("agent", "running" if running else "stopped", {"running": running})
            self._save_core_state()

    def set_phase(self, phase: str, data: dict[str, Any] | None = None):
        with self._lock:
            self.agent_phase = phase
            self.add_event("phase", phase, data or {})
            self._save_core_state()

    def record_tick_start(self):
        with self._lock:
            self.tick_count += 1
            self.last_tick_started_at = utc_now()
            self.last_tick_status = None
            self.add_event("tick", "started", {"tick_count": self.tick_count}, module="agent", action="tick.start")
            self._save_core_state()

    def record_tick_finish(self, status: int, result: dict[str, Any] | None = None):
        with self._lock:
            self.last_tick_finished_at = utc_now()
            self.last_tick_status = int(status)
            if status >= 400:
                self.worker_error_count += 1
            else:
                self.worker_error_count = 0
            data = {"tick_count": self.tick_count, "status": status}
            if isinstance(result, dict) and result.get("error"):
                data["error"] = result.get("error")
                data["code"] = result.get("code")
            self.add_event("tick", "finished", data, level="error" if status >= 400 else "info", module="agent", action="tick.finish")
            self._save_core_state()

    def set_next_tick_due(self, wait_seconds: int | float):
        with self._lock:
            due_epoch = time.time() + max(0, float(wait_seconds or 0))
            self.next_tick_due_at = datetime.fromtimestamp(due_epoch, timezone.utc).isoformat()
            self._save_core_state()

    def record_snapshot(self, snapshot):
        with self._lock:
            self.last_snapshot = snapshot.to_dict() if hasattr(snapshot, "to_dict") else snapshot
            price = float(self.last_snapshot.get("price") or 0)
            symbol = str(self.last_snapshot.get("symbol") or "").upper()
            if price > 0 and symbol:
                self._mark_to_market(symbol, price)
            self._save_core_state()

    def record_signal(self, signal: Signal):
        with self._lock:
            self.last_signal = signal.to_dict()
            self.add_event("signal", signal.action, self.last_signal)
            self._save_core_state()

    def record_risk(self, decision: RiskDecision):
        with self._lock:
            self.last_risk = decision.to_dict()
            self.add_event("risk", "approved" if decision.approved else "rejected", self.last_risk)
            self._save_core_state()

    def record_llm_review(self, review: dict[str, Any]):
        with self._lock:
            self.last_llm_review = review
            self.add_event("llm_review", str(review.get("decision") or "skipped"), review)
            self._save_core_state()

    def record_decision_report(self, report: dict[str, Any]):
        with self._lock:
            self.last_decision_report = report
            self.decision_reports.insert(0, report)
            self.decision_reports = self.decision_reports[: self.config.decision_report_retention]
            self._append_jsonl(DECISION_REPORTS_PATH, report)
            if len(self.decision_reports) >= self.config.decision_report_retention:
                self._rewrite_jsonl(DECISION_REPORTS_PATH, self.decision_reports)
            self.add_event("decision", str(report.get("final_action") or "unknown"), {
                "score": (report.get("opportunity_score") or {}).get("score"),
                "blockers": report.get("blockers") or [],
                "next_watch": report.get("next_watch"),
            }, module="agent", action="decision.report")
            self._save_core_state()

    def record_claw402_raw_sample(self, kind: str, symbol: str, exchange: str, interval: str, raw: Any):
        if not self.config.save_claw402_raw_samples:
            return
        with self._lock:
            sample = {
                "timestamp": utc_now(),
                "kind": kind,
                "symbol": symbol,
                "exchange": exchange,
                "interval": interval,
                "raw": self._redact(raw),
            }
            self.claw402_raw_samples.insert(0, sample)
            self.claw402_raw_samples = self.claw402_raw_samples[:50]
            self._append_jsonl(CLAW402_RAW_SAMPLES_PATH, sample)

    def record_diagnostics_summary(self, summary: dict[str, Any]):
        with self._lock:
            self.diagnostics_summary = summary
            self._save_core_state()

    def _redact(self, value: Any):
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                key_s = str(key).lower()
                if any(secret in key_s for secret in ("key", "secret", "token", "auth", "wallet", "private")):
                    redacted[key] = "***"
                else:
                    redacted[key] = self._redact(item)
            return redacted
        if isinstance(value, list):
            return [self._redact(item) for item in value[:500]]
        return value

    def record_order(self, order: Order):
        with self._lock:
            data = order.to_dict()
            self.orders.insert(0, data)
            self.orders = self.orders[:100]
            if order.status in {"FILLED", "OPEN", "SUBMITTED"}:
                self._last_trade_ts[order.symbol] = time.time()
            self._persist_orders()
            self.add_event("order", order.status, data)
            self._save_core_state()

    def replace_orders(self, orders: list[dict[str, Any]]):
        with self._lock:
            self.orders = [order for order in orders if isinstance(order, dict)][:100]
            self._persist_orders()
            self._save_core_state()

    def _mark_to_market(self, symbol: str, price: float):
        for order in self.orders:
            if order.get("symbol") != symbol or order.get("mode") != "paper" or order.get("status") != "OPEN":
                continue
            side = order.get("side")
            entry = float(order.get("entry_price") or 0)
            qty = float(order.get("qty") or 0)
            stop_loss = float(order.get("stop_loss") or 0)
            take_profit = float(order.get("take_profit") or 0)
            if entry <= 0 or qty <= 0:
                continue
            if side == "LONG":
                pnl = (price - entry) * qty
                closed = (stop_loss and price <= stop_loss) or (take_profit and price >= take_profit)
            elif side == "SHORT":
                pnl = (entry - price) * qty
                closed = (stop_loss and price >= stop_loss) or (take_profit and price <= take_profit)
            else:
                continue
            order["pnl_usd"] = round(pnl, 4)
            max_hold_seconds = self.config.max_holding_minutes * 60
            opened_epoch = self._parse_epoch(order.get("timestamp"))
            time_expired = bool(opened_epoch and time.time() - opened_epoch >= max_hold_seconds)
            if closed or time_expired:
                order["status"] = "CLOSED"
                order["exit_price"] = price
                if time_expired and not closed:
                    order["reason"] = "paper time stop"
                else:
                    order["reason"] = "paper stop loss hit" if pnl < 0 else "paper take profit hit"
                self._after_order_closed(order)
                self._persist_orders()
                self.add_event("order", order["status"], order.copy())
                self._save_core_state()

    def _parse_epoch(self, timestamp: Any) -> float:
        try:
            return datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return 0.0

    def _after_order_closed(self, order: dict[str, Any]):
        pnl = float(order.get("pnl_usd") or 0)
        if pnl < 0 and self.consecutive_losses() >= self.config.max_consecutive_losses:
            self._loss_pause_until = max(self._loss_pause_until, time.time() + self.config.loss_pause_minutes * 60)
            self.add_event("risk", "consecutive loss pause", {"pause_until": self._loss_pause_until})
            self._save_core_state()

    def add_event(self, kind: str, message: str, data: dict[str, Any] | None = None, *, level: str = "info", module: str = "agent", action: str | None = None, request_id: str | None = None):
        event = {
            "timestamp": utc_now(),
            "level": level,
            "module": module,
            "action": action or message,
            "request_id": request_id or str(uuid4()),
            "kind": kind,
            "message": message,
            "data": data or {},
        }
        self.events.insert(0, event)
        self.events = self.events[:200]
        self._append_jsonl(EVENTS_PATH, event)
        if len(self.events) == 200:
            self._rewrite_jsonl(EVENTS_PATH, self.events)

    def record_heatmap_snapshot(self, snapshot: dict[str, Any], limit: int):
        with self._lock:
            self.heatmap_snapshots.insert(0, snapshot)
            self.heatmap_snapshots = self.heatmap_snapshots[:limit]
            self._append_jsonl(HEATMAP_SNAPSHOTS_PATH, snapshot)
            if len(self.heatmap_snapshots) == limit:
                self._rewrite_jsonl(HEATMAP_SNAPSHOTS_PATH, self.heatmap_snapshots)
            self.add_event("heatmap", "snapshot", {"symbol": snapshot.get("symbol"), "cost_usdc": snapshot.get("cost_usdc")})

    def latest_heatmap_snapshot(self, symbol: str, interval: str) -> dict[str, Any] | None:
        symbol = symbol.upper()
        for snapshot in self.heatmap_snapshots:
            if snapshot.get("symbol") == symbol and snapshot.get("interval") == interval:
                return snapshot
        return None

    def heatmap_snapshot_age_seconds(self, snapshot: dict[str, Any] | None) -> float:
        if not snapshot:
            return 10**9
        return max(0.0, time.time() - float(snapshot.get("epoch") or 0))

    def record_api_cost(self, kind: str, cost_usdc: float, symbol: str):
        with self._lock:
            item = {"timestamp": utc_now(), "kind": kind, "symbol": symbol, "cost_usdc": float(cost_usdc)}
            self.api_costs.insert(0, item)
            self.api_costs = self.api_costs[:1000]
            self._append_jsonl(API_COSTS_PATH, item)

    def api_cost_for_day(self, day: str, kind: str | None = None) -> float:
        total = 0.0
        for item in self.api_costs:
            if not str(item.get("timestamp", "")).startswith(day):
                continue
            if kind and item.get("kind") != kind:
                continue
            total += float(item.get("cost_usdc") or 0)
        return round(total, 6)

    def api_calls_for_day(self, day: str, kind: str | None = None) -> int:
        return sum(1 for item in self.api_costs if str(item.get("timestamp", "")).startswith(day) and (not kind or item.get("kind") == kind))

    def can_spend_api_budget(self, cost_usdc: float, daily_budget_usdc: float, kind: str | None = None, kind_budget_usdc: float | None = None) -> bool:
        today = datetime.now(timezone.utc).date().isoformat()
        if self.api_cost_for_day(today) + cost_usdc > daily_budget_usdc:
            return False
        if kind and kind_budget_usdc is not None and self.api_cost_for_day(today, kind) + cost_usdc > kind_budget_usdc:
            return False
        return True

    def heatmap_status(self) -> dict[str, Any]:
        today = datetime.now(timezone.utc).date().isoformat()
        latest = self.heatmap_snapshots[0] if self.heatmap_snapshots else None
        latest_summary = None
        if latest:
            latest_summary = {
                "symbol": latest.get("symbol"),
                "interval": latest.get("interval"),
                "timestamp": latest.get("timestamp"),
                "price": latest.get("price"),
                "cost_usdc": latest.get("cost_usdc"),
            }
        return {
            "snapshots_count": len(self.heatmap_snapshots),
            "latest_snapshot": latest_summary,
            "latest_age_seconds": None if not latest else round(self.heatmap_snapshot_age_seconds(latest), 1),
            "liq_map_calls_today": self.api_calls_for_day(today, "liq_map"),
            "liq_map_cost_today": self.api_cost_for_day(today, "liq_map"),
            "snapshots": self.heatmap_snapshots[:20],
        }

    def seconds_since_last_trade(self, symbol: str) -> float:
        ts = self._last_trade_ts.get(symbol.upper(), 0)
        if not ts:
            return 10**9
        return time.time() - ts

    def trade_count_for_day(self, day: str) -> int:
        return sum(1 for order in self.orders if str(order.get("timestamp", "")).startswith(day) and order.get("status") in {"FILLED", "OPEN", "SUBMITTED", "CLOSED"})

    def realized_loss_for_day(self, day: str) -> float:
        loss = 0.0
        for order in self.orders:
            if not str(order.get("timestamp", "")).startswith(day):
                continue
            if order.get("status") != "CLOSED":
                continue
            pnl = float(order.get("pnl_usd") or 0)
            if pnl < 0:
                loss += abs(pnl)
        return round(loss, 4)

    def consecutive_losses(self) -> int:
        losses = 0
        for order in self.orders:
            if order.get("status") != "CLOSED":
                continue
            pnl = float(order.get("pnl_usd") or 0)
            if pnl < 0:
                losses += 1
                continue
            break
        return losses

    def loss_pause_remaining_seconds(self) -> float:
        return max(0.0, self._loss_pause_until - time.time())

    def status(self) -> dict[str, Any]:
        with self._lock:
            heartbeat = {
                "tick_count": self.tick_count,
                "last_tick_started_at": self.last_tick_started_at,
                "last_tick_finished_at": self.last_tick_finished_at,
                "last_tick_status": self.last_tick_status,
                "next_tick_due_at": self.next_tick_due_at,
                "worker_error_count": self.worker_error_count,
                "stale": self._heartbeat_stale(),
            }
            return {
                "running": self.running,
                "phase": self.agent_phase,
                "config": self.config.to_dict(),
                "safety_mode": self.config.safety_mode,
                "last_signal": self.last_signal,
                "last_risk": self.last_risk,
                "last_snapshot": self.last_snapshot,
                "last_llm_review": self.last_llm_review,
                "last_decision_report": self.last_decision_report,
                "diagnostics_summary": self.diagnostics_summary,
                "heartbeat": heartbeat,
                "tick_count": self.tick_count,
                "last_tick_started_at": self.last_tick_started_at,
                "last_tick_finished_at": self.last_tick_finished_at,
                "last_tick_status": self.last_tick_status,
                "next_tick_due_at": self.next_tick_due_at,
                "worker_error_count": self.worker_error_count,
                "last_order": self.orders[0] if self.orders else None,
                "heatmap": self.heatmap_status(),
                "consecutive_losses": self.consecutive_losses(),
                "loss_pause_remaining_seconds": round(self.loss_pause_remaining_seconds(), 1),
                "orders_count": len(self.orders),
                "events_count": len(self.events),
            }

    def _heartbeat_stale(self) -> bool:
        if not self.running:
            return False
        timestamp = self.last_tick_finished_at or self.last_tick_started_at
        if not timestamp:
            return True
        elapsed = time.time() - self._parse_epoch(timestamp)
        return elapsed > max(self.config.poll_seconds * 2.5, 180)
