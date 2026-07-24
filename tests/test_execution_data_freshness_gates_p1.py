"""Phase 1: paper fail-closed live-quote / ATR / broker-tick execution gates."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import risk_engine
from risk_engine import join_peak_audit_queue, risk_check


@pytest.fixture(autouse=True)
def _reset_peak_equity():
    with risk_engine._peak_lock:
        old = dict(risk_engine._peak_equity)
        risk_engine._peak_equity = {}
    with risk_engine._daily_lock:
        old_daily_pnl = dict(risk_engine._daily_pnl)
        old_daily_pnl_date = risk_engine._daily_pnl_date
        old_daily_start_balance = dict(risk_engine._daily_start_balance)
        risk_engine._daily_pnl = {}
        risk_engine._daily_pnl_date = ""
        risk_engine._daily_start_balance = {}
    yield
    with risk_engine._peak_lock:
        risk_engine._peak_equity = old
    with risk_engine._daily_lock:
        risk_engine._daily_pnl = old_daily_pnl
        risk_engine._daily_pnl_date = old_daily_pnl_date
        risk_engine._daily_start_balance = old_daily_start_balance
    join_peak_audit_queue(timeout=5.0)


def _fresh_signal(**overrides):
    base = {
        "pair": "BTCUSDT",
        "direction": "LONG",
        "price": 60000,
        "sl": 59000,
        "tp1": 62000,
        "tp2": 64000,
        "type": "crypto",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confluenceScore": 5.0,
        "quoteAgeSec": 1.0,
        "candleFetchMeta": {
            "D1": {"stalenessSeverity": "fresh"},
            "H4": {"stalenessSeverity": "fresh"},
            "H1": {"stalenessSeverity": "fresh"},
        },
    }
    base.update(overrides)
    return base


def test_risk_rejects_stale_live_quote(monkeypatch):
    monkeypatch.setitem(
        risk_engine.CONFIG,
        "LIVE_PRICE_MAX_AGE_SEC",
        {"crypto": 15, "forex": 60},
    )
    monkeypatch.setitem(risk_engine.CONFIG, "LIVE_PRICE_BLOCK_EXECUTION", True)
    monkeypatch.setitem(
        risk_engine.CONFIG, "LIVE_PRICE_BLOCK_EXECUTION_ON_UNKNOWN_AGE", False
    )

    result = risk_check(
        _fresh_signal(quoteAgeSec=120.0),
        10000,
        10000,
        [],
    )
    assert result.approved is False
    assert result.reason.startswith("STALE_LIVE_QUOTE:")


def test_risk_recomputes_live_quote_age_at_execution(monkeypatch):
    monkeypatch.setitem(risk_engine.CONFIG, "LIVE_PRICE_MAX_AGE_SEC", {"crypto": 15})
    monkeypatch.setitem(risk_engine.CONFIG, "LIVE_PRICE_BLOCK_EXECUTION", True)
    monkeypatch.setattr(risk_engine._time, "time", lambda: 1_020.0)

    result = risk_check(
        _fresh_signal(
            quoteAgeSec=1.0,
            quoteTimestamp=1_000.0,
            quoteAgeEval={"would_block": False, "reason": "FRESH"},
        ),
        10000,
        10000,
        [],
    )

    assert result.approved is False
    assert result.reason == "STALE_LIVE_QUOTE:STALE"


def test_risk_rejects_unknown_live_quote_age_when_configured(monkeypatch):
    monkeypatch.setitem(
        risk_engine.CONFIG,
        "LIVE_PRICE_MAX_AGE_SEC",
        {"crypto": 15},
    )
    monkeypatch.setitem(risk_engine.CONFIG, "LIVE_PRICE_BLOCK_EXECUTION", True)
    monkeypatch.setitem(
        risk_engine.CONFIG, "LIVE_PRICE_BLOCK_EXECUTION_ON_UNKNOWN_AGE", True
    )

    sig = _fresh_signal()
    sig.pop("quoteAgeSec")
    result = risk_check(sig, 10000, 10000, [])
    assert result.approved is False
    assert "AGE_UNKNOWN" in result.reason


def test_risk_rejects_stale_atr_would_block(monkeypatch):
    monkeypatch.setitem(risk_engine.CONFIG, "LIVE_PRICE_BLOCK_EXECUTION", False)

    result = risk_check(
        _fresh_signal(
            atrFreshness={
                "enabled": True,
                "stale": True,
                "would_block": True,
                "reason": "atr_age_20000s_exceeds_H4_limit_18000s",
            }
        ),
        10000,
        10000,
        [],
    )
    assert result.approved is False
    assert result.reason.startswith("STALE_ATR:")


def test_risk_allows_fresh_quote_and_non_blocking_atr(monkeypatch):
    monkeypatch.setitem(
        risk_engine.CONFIG,
        "LIVE_PRICE_MAX_AGE_SEC",
        {"crypto": 15},
    )
    monkeypatch.setitem(risk_engine.CONFIG, "LIVE_PRICE_BLOCK_EXECUTION", True)
    monkeypatch.setitem(
        risk_engine.CONFIG, "LIVE_PRICE_BLOCK_EXECUTION_ON_UNKNOWN_AGE", True
    )

    result = risk_check(
        _fresh_signal(
            quoteAgeSec=2.0,
            atrFreshness={
                "enabled": True,
                "stale": False,
                "would_block": False,
                "reason": "fresh",
            },
        ),
        10000,
        10000,
        [],
    )
    assert result.approved is True


def test_mt5_broker_tick_age_limit_reads_paper_default(monkeypatch):
    import mt5_executor

    monkeypatch.setitem(
        mt5_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"mt5": 5, "bybit": 3}
    )
    assert mt5_executor._mt5_max_tick_age_sec() == 5.0


def test_bybit_broker_tick_age_limit_reads_paper_default(monkeypatch):
    import bybit_executor

    monkeypatch.setitem(
        bybit_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"mt5": 5, "bybit": 3}
    )
    assert bybit_executor._bybit_max_tick_age_sec() == 3.0


def test_mt5_stale_tick_age_exceeds_limit(monkeypatch):
    import mt5_executor

    monkeypatch.setitem(mt5_executor.CONFIG, "MT5_BROKER_UTC_OFFSET", 3)
    monkeypatch.setattr(mt5_executor.time, "time", lambda: 1_716_200_020.0)
    tick = SimpleNamespace(time=1_716_200_020.0 + 3 * 3600 - 20)
    age = mt5_executor._mt5_tick_age_seconds(tick)
    assert age == 20.0
    monkeypatch.setitem(mt5_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"mt5": 5})
    assert age > mt5_executor._mt5_max_tick_age_sec()
