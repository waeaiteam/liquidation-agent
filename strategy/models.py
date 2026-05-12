from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StrategyConfig:
    enabled: bool = True
    mode: str = "paper"
    coin: str = "BTC"
    symbol: str = "BTCUSDT"
    exchange: str = "binance"
    interval: str = "1h"
    size: int = 24
    poll_seconds: int = 60
    min_liquidation_usd: float = 10_000_000
    dominance_ratio: float = 1.3
    entry_mode: str = "conservative"
    use_oi_confirmation: bool = False
    min_oi_liq_ratio: float = 0.005
    use_long_short_confirmation: bool = False
    min_liq_24h_share: float = 0.08
    min_history_spike_ratio: float = 1.5
    funding_extreme_threshold: float = 0.0003
    use_heatmap_confirmation: bool = True
    heatmap_bucket_pct: float = 0.002
    min_heatmap_cluster_score: float = 3.0
    max_heatmap_distance_pct: float = 0.012
    allowed_heatmap_leverage_tiers: list[str] = field(default_factory=lambda: ["high", "medium"])
    liq_map_cost_usdc: float = 0.001
    daily_api_budget_usdc: float = 0.2
    scheduled_liq_map_budget_usdc: float = 0.15
    event_liq_map_budget_usdc: float = 0.05
    liq_map_snapshot_interval_seconds: int = 600
    max_heatmap_snapshot_age_seconds: int = 900
    max_heatmap_trade_age_seconds: int = 300
    max_heatmap_snapshots: int = 288
    min_heatmap_change_ratio: float = 0.05
    event_triggered_liq_map: bool = True
    notional_usd: float = 100
    max_notional_usd: float = 1_000
    leverage: int = 2
    max_leverage: int = 5
    stop_loss_pct: float = 0.6
    take_profit_pct: float = 2.0
    stop_mode: str = "hybrid"
    stop_buffer_pct: float = 0.2
    min_reward_risk: float = 1.5
    max_stop_loss_pct: float = 1.2
    max_take_profit_pct: float = 3.0
    cooldown_seconds: int = 14_400
    max_daily_trades: int = 12
    max_daily_loss_usd: float = 100
    max_holding_minutes: int = 30
    max_consecutive_losses: int = 2
    loss_pause_minutes: int = 180
    llm_review_enabled: bool = False
    llm_review_provider: str = "anthropic"
    llm_review_model: str = ""
    llm_review_min_confidence: float = 0.65
    allowed_symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    live_enabled: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StrategyConfig":
        base = cls()
        allowed = set(asdict(base))
        values = asdict(base)
        for key, value in (data or {}).items():
            if key in allowed:
                values[key] = value
        values["size"] = int(values["size"])
        values["poll_seconds"] = max(10, int(values["poll_seconds"]))
        values["leverage"] = int(values["leverage"])
        values["max_leverage"] = int(values["max_leverage"])
        values["liq_map_snapshot_interval_seconds"] = max(60, int(values["liq_map_snapshot_interval_seconds"]))
        values["max_heatmap_snapshot_age_seconds"] = max(60, int(values["max_heatmap_snapshot_age_seconds"]))
        values["max_heatmap_trade_age_seconds"] = max(30, int(values["max_heatmap_trade_age_seconds"]))
        values["max_heatmap_snapshots"] = max(1, int(values["max_heatmap_snapshots"]))
        values["max_holding_minutes"] = max(1, int(values["max_holding_minutes"]))
        values["max_consecutive_losses"] = max(1, int(values["max_consecutive_losses"]))
        values["loss_pause_minutes"] = max(1, int(values["loss_pause_minutes"]))
        values["allowed_symbols"] = [str(s).upper() for s in values["allowed_symbols"]]
        values["allowed_heatmap_leverage_tiers"] = [str(s).lower() for s in values["allowed_heatmap_leverage_tiers"]]
        values["coin"] = str(values["coin"]).upper()
        values["symbol"] = str(values["symbol"]).upper()
        values["exchange"] = str(values["exchange"]).lower()
        values["mode"] = str(values["mode"]).lower()
        values["entry_mode"] = str(values["entry_mode"]).lower()
        values["stop_mode"] = str(values["stop_mode"]).lower()
        values["llm_review_provider"] = str(values["llm_review_provider"]).lower()
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketSnapshot:
    coin: str
    symbol: str
    exchange: str
    interval: str
    price: float = 0
    intervals: dict[str, float] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    oi: list[dict[str, Any]] = field(default_factory=list)
    long_short: list[dict[str, Any]] = field(default_factory=list)
    funding: list[dict[str, Any]] = field(default_factory=list)
    liq_map: dict[str, Any] = field(default_factory=dict)
    data_warnings: list[str] = field(default_factory=list)
    wallet: str = ""
    timestamp: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Signal:
    id: str
    timestamp: str
    symbol: str
    side: str
    action: str
    confidence: float
    price: float
    liquidation_usd: float
    opposite_liquidation_usd: float
    dominance_ratio: float
    reasons: list[str]
    metrics: dict[str, Any]
    valid: bool = True

    @classmethod
    def none(cls, symbol: str, reasons: list[str], metrics: dict[str, Any] | None = None) -> "Signal":
        return cls(
            id=str(uuid4()),
            timestamp=utc_now(),
            symbol=symbol,
            side="NONE",
            action="WAIT",
            confidence=0,
            price=0,
            liquidation_usd=0,
            opposite_liquidation_usd=0,
            dominance_ratio=0,
            reasons=reasons,
            metrics=metrics or {},
            valid=False,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RiskDecision:
    approved: bool
    reasons: list[str]
    mode: str
    notional_usd: float
    leverage: int
    stop_loss: float = 0
    take_profit: float = 0
    timestamp: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Order:
    id: str
    signal_id: str
    timestamp: str
    mode: str
    exchange: str
    symbol: str
    side: str
    action: str
    qty: float
    notional_usd: float
    entry_price: float
    stop_loss: float
    take_profit: float
    leverage: int
    status: str
    exit_price: float = 0
    pnl_usd: float = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
