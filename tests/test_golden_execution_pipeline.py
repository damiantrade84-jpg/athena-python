"""Golden seam test for scan -> score -> risk -> executor dispatch."""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import risk_engine
from risk_engine import RiskApproval


def _synthetic_candles():
    return [
        {"time": "2026-05-05T13:00:00+00:00", "open": 99.0, "high": 100.2, "low": 98.8, "close": 100.0, "vol": 1000},
        {"time": "2026-05-05T14:00:00+00:00", "open": 100.0, "high": 101.2, "low": 99.8, "close": 101.0, "vol": 1100},
        {"time": "2026-05-05T15:00:00+00:00", "open": 101.0, "high": 102.2, "low": 100.8, "close": 102.0, "vol": 1200},
    ]


def _scan_score_decision(candles, *, force_invalid_levels=False):
    """Small deterministic scan/score seam: bullish close sequence becomes one LONG signal."""
    assert candles[-1]["close"] > candles[-2]["close"]
    price = float(candles[-1]["close"])
    sl = price * (1.01 if force_invalid_levels else 0.99)
    return {
        "pair": "ACME",
        "display": "ACME",
        "symbol": "ACME",
        "type": "stock",
        "direction": "LONG",
        "price": price,
        "sl": sl,
        "tp1": price * 1.03,
        "tp2": price * 1.05,
        "confluenceScore": 2.4,
        "maxScore": 3.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "candleConsistency": {"H1": {"status": "OK"}},
    }


class _StubExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, signal, approval):
        assert isinstance(approval, RiskApproval)
        assert approval.approved is True
        self.calls.append(
            {
                "pair": signal["pair"],
                "direction": signal["direction"],
                "volume": approval.volume,
                "risk_amount": approval.risk_amount,
            }
        )
        return {"success": True, "ticket": "stub-1", "volume": approval.volume}


def _dispatch_after_risk(signal, executor):
    approval = risk_engine.risk_check(
        signal=signal,
        account_balance=10000.0,
        account_equity=10000.0,
        open_positions=[],
        kill_switch=False,
    )
    if not approval.approved:
        return {"executed": False, "approval": approval, "result": None}
    return {
        "executed": True,
        "approval": approval,
        "result": executor.execute(signal, approval),
    }


def test_golden_pipeline_executes_only_after_risk_approval(monkeypatch):
    monkeypatch.setitem(risk_engine.CONFIG, "ADAPTIVE_KELLY_ENABLED", False)
    monkeypatch.setitem(risk_engine.CONFIG, "MAX_SL_PCT", {"stock": 0.05})
    monkeypatch.setitem(risk_engine.CONFIG, "MAX_RISK_PER_TRADE", 0.03)
    monkeypatch.setitem(risk_engine.CONFIG, "MAX_PORTFOLIO_HEAT", 0.06)
    monkeypatch.setitem(risk_engine.CONFIG, "MAX_OPEN_POSITIONS", 5)
    monkeypatch.setitem(risk_engine.CONFIG, "MAX_CORRELATED_POSITIONS", 2)
    monkeypatch.setattr(risk_engine, "_check_daily_loss", lambda *_args, **_kwargs: (False, 0.0))
    monkeypatch.setattr(risk_engine, "_current_drawdown", lambda *_args, **_kwargs: 0.0)

    executor = _StubExecutor()

    approved_signal = _scan_score_decision(_synthetic_candles())
    approved = _dispatch_after_risk(approved_signal, executor)

    assert approved["approval"].approved is True
    assert approved["approval"].reason == "OK"
    assert approved["executed"] is True
    assert approved["result"]["success"] is True
    assert executor.calls == [
        {
            "pair": "ACME",
            "direction": "LONG",
            "volume": approved["approval"].volume,
            "risk_amount": approved["approval"].risk_amount,
        }
    ]

    rejected_signal = _scan_score_decision(_synthetic_candles(), force_invalid_levels=True)
    rejected = _dispatch_after_risk(rejected_signal, executor)

    assert rejected["approval"].approved is False
    assert rejected["approval"].reason == "INVALID_LEVELS"
    assert rejected["executed"] is False
    assert len(executor.calls) == 1
