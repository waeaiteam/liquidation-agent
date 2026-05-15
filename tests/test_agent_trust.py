import unittest

from services.decision import build_decision_report, opportunity_score
from services.evolution import EvolutionEngine
from services.strategy_agent import apply_llm_review
from strategy.models import MarketSnapshot, RiskDecision, Signal, StrategyConfig, utc_now


def signal(valid=True):
    return Signal(
        id="sig-1",
        timestamp=utc_now(),
        symbol="BTCUSDT",
        side="LONG",
        action="BUY" if valid else "WAIT",
        confidence=0.8 if valid else 0,
        price=100.0,
        liquidation_usd=30_000_000,
        opposite_liquidation_usd=5_000_000,
        dominance_ratio=6.0,
        reasons=["fixture signal"] if valid else ["no signal"],
        valid=valid,
        metrics={
            "long_liquidation_usd": 30_000_000,
            "short_liquidation_usd": 5_000_000,
            "dominance_ratio": 6.0,
            "liq_24h_share": 0.3,
            "history_spike_ratio": 3.0,
            "oi_liq_ratio": 0.03,
            "funding_rate": -0.001,
            "heatmap": {
                "long_match": {"valid": True, "heatmap_score": 3.2, "cluster": {"score": 3.2}},
                "short_match": {"valid": False, "reason": "no short cluster"},
            },
            "heatmap_age_seconds": 5,
        },
    )


class AgentTrustTests(unittest.TestCase):
    def test_opportunity_score_passes_and_blocks(self):
        cfg = StrategyConfig.from_dict({"min_opportunity_score": 70})
        approved = RiskDecision(True, [], "paper", 100, 2, 98, 105)

        good = opportunity_score(signal(), {"usable": True, "age_seconds": 5}, cfg, approved)
        blocked = opportunity_score(signal(False), {"usable": False, "age_seconds": 900}, cfg, RiskDecision(False, ["risk blocked"], "paper", 100, 2))

        self.assertTrue(good["passed"])
        self.assertGreaterEqual(good["score"], 70)
        self.assertFalse(blocked["passed"])
        self.assertIn("no_signal", blocked["penalties"])

    def test_decision_report_contains_required_sections(self):
        cfg = StrategyConfig.from_dict({"safety_mode": "observe"})
        snap = MarketSnapshot(coin="BTC", symbol="BTCUSDT", exchange="binance", interval="1h", price=100, market={"source": "unit"})
        risk = RiskDecision(False, ["score below threshold"], "paper", 100, 2, 98, 105)
        score = {"score": 55, "threshold": 70, "passed": False, "components": {}, "penalties": {}}

        report = build_decision_report(
            config=cfg,
            snapshot=snap,
            candidate=signal(),
            signal=signal(),
            heatmap_result={"usable": True, "age_seconds": 1},
            risk=risk,
            llm_review={"decision": "wait"},
            order=None,
            score=score,
            final_action="REJECTED",
            blockers=["opportunity score 55 below threshold 70"],
            tick_count=3,
        )

        for key in ("market_check", "liquidation_event", "heatmap_confirmation", "opportunity_score", "risk_gate", "llm_review", "final_action", "blockers", "next_watch"):
            self.assertIn(key, report)
        self.assertEqual(report["safety_mode"], "observe")
        self.assertEqual(report["tick_count"], 3)

    def test_llm_review_cannot_increase_risk(self):
        cfg = StrategyConfig.from_dict({"max_notional_usd": 500, "max_stop_loss_pct": 1.0, "max_take_profit_pct": 3.0})
        sig = signal()
        decision = RiskDecision(True, [], "paper", 100, 2, 99, 102)
        reviewed = apply_llm_review(
            decision,
            {"enabled": True, "decision": "approve", "notional_usd": 1000, "stop_loss": 50, "take_profit": 200},
            cfg,
            sig,
        )

        self.assertLessEqual(reviewed.notional_usd, decision.notional_usd)
        self.assertGreaterEqual(reviewed.stop_loss, decision.stop_loss)
        self.assertLessEqual(reviewed.take_profit, decision.take_profit)

    def test_evolution_clusters_and_sample_guard(self):
        class FakeState:
            orders = [
                {"id": "o1", "mode": "paper", "status": "CLOSED", "pnl_usd": -10, "reason": "stop loss cluster"},
                {"id": "o2", "mode": "paper", "status": "CLOSED", "pnl_usd": -2, "reason": "time stop"},
            ]
            events = [
                {"request_id": "e1", "message": "heatmap distance too far", "data": {}},
                {"request_id": "e2", "message": "weak cluster score", "data": {}},
                {"request_id": "e3", "message": "stale data too old", "data": {}},
            ]

        result = EvolutionEngine().analyze(FakeState(), StrategyConfig(), lookback=20)

        self.assertEqual(result["failure_clusters"]["heatmap_too_far"]["count"], 1)
        self.assertEqual(result["failure_clusters"]["weak_cluster"]["count"], 1)
        self.assertEqual(result["failure_clusters"]["stale_data"]["count"], 1)
        self.assertEqual(result["recommendations"][0]["param"], "none")
        self.assertIn("evidence", result["recommendations"][0])


if __name__ == "__main__":
    unittest.main()
