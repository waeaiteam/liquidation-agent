from __future__ import annotations

from typing import Any

from strategy.models import MarketSnapshot, RiskDecision, Signal, StrategyConfig, utc_now


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def opportunity_score(
    signal: Signal,
    heatmap_result: dict[str, Any] | None,
    config: StrategyConfig,
    risk: RiskDecision | None = None,
) -> dict[str, Any]:
    metrics = signal.metrics or {}
    heatmap_result = heatmap_result or {}
    components: dict[str, float] = {}
    notes: list[str] = []

    dominant = _num(metrics.get("long_liquidation_usd")) if metrics.get("long_liquidation_usd") else 0
    dominant = max(dominant, _num(metrics.get("short_liquidation_usd")))
    components["liquidation_size"] = _clamp((dominant / max(config.min_liquidation_usd, 1)) * 18, 0, 18)
    if dominant < config.min_liquidation_usd:
        notes.append("dominant liquidation below threshold")

    dominance = _num(metrics.get("dominance_ratio") or signal.dominance_ratio)
    components["dominance"] = _clamp((dominance / max(config.dominance_ratio, 0.01)) * 14, 0, 14)

    share = _num(metrics.get("liq_24h_share"))
    components["liquidation_share"] = _clamp((share / max(config.min_liq_24h_share, 0.001)) * 10, 0, 10)

    spike = _num(metrics.get("history_spike_ratio"))
    components["history_spike"] = _clamp((spike / max(config.min_history_spike_ratio, 0.01)) * 10, 0, 10)

    oi_ratio = _num(metrics.get("oi_liq_ratio"))
    components["oi_confirmation"] = _clamp((oi_ratio / max(config.min_oi_liq_ratio, 0.0001)) * 8, 0, 8)

    funding = _num(metrics.get("funding_rate"))
    funding_supports = (
        signal.side == "LONG" and funding <= -config.funding_extreme_threshold
    ) or (
        signal.side == "SHORT" and funding >= config.funding_extreme_threshold
    )
    components["funding_extreme"] = 6 if funding_supports else 0

    heatmap = metrics.get("heatmap") if isinstance(metrics.get("heatmap"), dict) else {}
    match_key = "long_match" if signal.side == "LONG" else "short_match"
    match = heatmap.get(match_key) if isinstance(heatmap.get(match_key), dict) else {}
    components["heatmap_cluster"] = _clamp(_num(match.get("heatmap_score")) * 8, 0, 20) if match.get("valid") else 0
    if config.use_heatmap_confirmation and not match.get("valid"):
        notes.append(str(match.get("reason") or "heatmap confirmation missing"))

    age = heatmap_result.get("age_seconds")
    if age is None:
        age = metrics.get("heatmap_age_seconds")
    age_num = _num(age, 10**9)
    freshness = 14 * (1 - min(age_num, config.max_heatmap_trade_age_seconds) / max(config.max_heatmap_trade_age_seconds, 1))
    components["heatmap_freshness"] = _clamp(freshness, 0, 14)

    score = sum(components.values())
    penalties: dict[str, float] = {}
    if risk and not risk.approved:
        penalties["risk_gate"] = min(25, max(5, len(risk.reasons) * 4))
        score -= penalties["risk_gate"]
    if not signal.valid:
        penalties["no_signal"] = 25
        score -= penalties["no_signal"]

    final_score = round(_clamp(score), 2)
    return {
        "score": final_score,
        "threshold": float(config.min_opportunity_score),
        "passed": final_score >= float(config.min_opportunity_score),
        "components": {k: round(v, 2) for k, v in components.items()},
        "penalties": {k: round(v, 2) for k, v in penalties.items()},
        "notes": notes,
    }


def build_decision_report(
    *,
    config: StrategyConfig,
    snapshot: MarketSnapshot | None,
    candidate: Signal | None,
    signal: Signal | None,
    heatmap_result: dict[str, Any] | None,
    risk: RiskDecision | None,
    llm_review: dict[str, Any] | None,
    order: Any | None,
    score: dict[str, Any] | None,
    final_action: str,
    blockers: list[str],
    next_watch: str = "",
    error: str = "",
    tick_count: int = 0,
) -> dict[str, Any]:
    heatmap_result = heatmap_result or {}
    signal_dict = signal.to_dict() if hasattr(signal, "to_dict") else (signal or {})
    candidate_dict = candidate.to_dict() if hasattr(candidate, "to_dict") else (candidate or {})
    risk_dict = risk.to_dict() if hasattr(risk, "to_dict") else (risk or {})
    order_dict = order.to_dict() if hasattr(order, "to_dict") else (order or None)
    snapshot_dict = snapshot.to_dict() if hasattr(snapshot, "to_dict") else (snapshot or {})
    market = snapshot_dict.get("market") if isinstance(snapshot_dict.get("market"), dict) else {}
    return {
        "timestamp": utc_now(),
        "tick_count": tick_count,
        "symbol": snapshot_dict.get("symbol") or config.symbol,
        "exchange": snapshot_dict.get("exchange") or config.exchange,
        "safety_mode": config.safety_mode,
        "market_check": {
            "ok": bool(snapshot_dict.get("price")),
            "price": snapshot_dict.get("price"),
            "source": market.get("source") or market.get("exchange"),
            "warnings": snapshot_dict.get("data_warnings") or [],
        },
        "liquidation_event": {
            "detected": bool(candidate_dict.get("valid") and candidate_dict.get("action") != "WAIT"),
            "side": candidate_dict.get("side"),
            "action": candidate_dict.get("action"),
            "reasons": candidate_dict.get("reasons") or [],
            "metrics": candidate_dict.get("metrics") or {},
        },
        "heatmap_confirmation": {
            "usable": bool(heatmap_result.get("usable")),
            "confirmed": bool(heatmap_result.get("usable")),
            "reason": heatmap_result.get("reason"),
            "age_seconds": heatmap_result.get("age_seconds"),
            "from_cache": bool(heatmap_result.get("from_cache")),
            "refreshed": bool(heatmap_result.get("refreshed")),
        },
        "opportunity_score": score or {"score": 0, "passed": False, "components": {}, "penalties": {}},
        "risk_gate": {
            "approved": bool(risk_dict.get("approved")),
            "reasons": risk_dict.get("reasons") or [],
            "notional_usd": risk_dict.get("notional_usd"),
            "leverage": risk_dict.get("leverage"),
            "stop_loss": risk_dict.get("stop_loss"),
            "take_profit": risk_dict.get("take_profit"),
        },
        "llm_review": llm_review,
        "final_action": final_action,
        "order": order_dict,
        "blockers": blockers,
        "next_watch": next_watch or _next_watch(blockers, heatmap_result, score),
        "error": error,
    }


def _next_watch(blockers: list[str], heatmap_result: dict[str, Any], score: dict[str, Any] | None) -> str:
    if blockers:
        first = blockers[0]
        if "heatmap" in first.lower():
            return "wait for a fresh liquidation-map snapshot or manually refresh the liquidation map"
        if "score" in first.lower() or "opportunity" in first.lower():
            return "watch for stronger liquidation, heatmap cluster, OI, or funding confirmation"
        return f"clear blocker first: {first}"
    if score and not score.get("passed"):
        return "keep observing until opportunity score reaches threshold"
    if heatmap_result.get("reason"):
        return str(heatmap_result.get("reason"))
    return "review again on the next scheduled tick"
