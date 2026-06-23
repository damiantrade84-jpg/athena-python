"""TSMOM live demo bridge — gate-sequence safety tests (no broker; injected deps).

Proves the standalone bridge enforces, in order: demo gate -> rr floor -> freshness ->
guardian -> risk_check -> executor, that the kill switch halts submission, that non-demo
mode and stale candles fail closed, and that closes are demo-gated. Mirrors the ASE
demo-execution test contract so the new path holds the same guarantees.
"""
from __future__ import annotations

from tsmom_live.bridge import TsmomExecDeps, close_trade, open_trade
from tsmom_live.signal import INSTRUMENTS, LiveDecision, PositionState

GOLD = INSTRUMENTS["gold"]


class _Approval:
    def __init__(self, approved: bool, volume: float = 0.1, reason: str = "OK"):
        self.approved = approved
        self.volume = volume
        self.reason = reason


def _long_decision(entry: float = 2000.0, risk: float = 60.0, rr: float = 3.0) -> LiveDecision:
    sl = entry - risk
    return LiveDecision(
        action="OPEN_LONG", reason="regime_flip_long", direction="LONG",
        entry_ref=entry, sl=sl, tp1=entry + rr * risk, tp2=entry + (rr + 1) * risk,
        trail_stop=sl, extreme=entry, atr=risk / 3.0, ema_fast=entry + 1, ema_slow=entry - 1,
        bar_time_ms=0, instrument="gold",
    )


def _demo_deps(**overrides) -> TsmomExecDeps:
    base = dict(
        get_executor_mode=lambda: "paper",
        get_mt5_trade_mode=lambda: 0,
        get_bybit_base_url=lambda: "https://api-testnet.bybit.com",
        get_kill_switch=lambda: False,
        get_account=lambda _v: {"balance": 10000.0, "equity": 10000.0},
        get_positions=lambda _v: ([], None),
        get_symbol_info=lambda _s, _p: {"volume_min": 0.01},
        guardian_check=lambda _s, _p, _a, _r: (True, "OK"),
        candle_freshness_ok=lambda _c, _d: (True, "ok"),
    )
    base.update(overrides)
    return TsmomExecDeps(**base)


def test_open_long_happy_path_runs_gates_in_order():
    calls: list[str] = []

    def risk_check(**kw):
        calls.append("risk")
        assert kw["signal"]["tsmomExecution"] is True
        assert kw["signal"]["direction"] == "LONG"
        assert kw["signal"]["engineATradeEnabled"] is False  # never masquerades as Engine A
        assert kw["kill_switch"] is False
        assert kw["execution_context"] == "tsmom_bridge"
        return _Approval(True, volume=0.2)

    def mt5_execute(signal, approval):
        calls.append("mt5")
        assert signal["sl"] < signal["price"] < signal["tp1"]
        assert approval.volume == 0.2
        return {"success": True, "orderId": "demo-1"}

    deps = _demo_deps(
        guardian_check=lambda *_a, **_k: (calls.append("guard") or (True, "OK")),
        risk_check=risk_check,
        mt5_execute=mt5_execute,
    )
    res = open_trade(_long_decision(), GOLD, deps=deps, write_journal=False)
    assert res["executed"] is True
    assert res["approval_volume"] == 0.2
    assert calls == ["guard", "risk", "mt5"]


def test_kill_switch_is_passed_and_halts_submission():
    calls: list[str] = []

    def risk_check(**kw):
        calls.append("risk")
        assert kw["kill_switch"] is True
        return _Approval(False, reason="KILL_SWITCH")

    deps = _demo_deps(
        get_kill_switch=lambda: True,
        risk_check=risk_check,
        mt5_execute=lambda _s, _a: calls.append("mt5") or {"success": True},
    )
    res = open_trade(_long_decision(), GOLD, deps=deps, write_journal=False)
    assert res["executed"] is False
    assert "mt5" not in calls


def test_non_demo_mode_rejected_before_anything():
    calls: list[str] = []
    deps = _demo_deps(
        get_executor_mode=lambda: "live",
        risk_check=lambda **_k: calls.append("risk") or _Approval(True),
        mt5_execute=lambda _s, _a: calls.append("mt5") or {"success": True},
    )
    res = open_trade(_long_decision(), GOLD, deps=deps, write_journal=False)
    assert res["executed"] is False
    assert "demo_gate" in res["reason"]
    assert calls == []


def test_stale_candles_fail_closed():
    calls: list[str] = []
    deps = _demo_deps(
        candle_freshness_ok=lambda _c, _d: (False, "stale_3_buckets"),
        risk_check=lambda **_k: calls.append("risk") or _Approval(True),
    )
    res = open_trade(_long_decision(), GOLD, deps=deps, write_journal=False)
    assert res["executed"] is False
    assert "freshness" in res["reason"]
    assert calls == []


def test_rr_floor_rejects_thin_target():
    bad = LiveDecision(
        action="OPEN_LONG", reason="x", direction="LONG", entry_ref=2000.0, sl=1940.0,
        tp1=2010.0, tp2=2020.0, trail_stop=1940.0, extreme=2000.0, atr=20.0,
        ema_fast=2001.0, ema_slow=1999.0, bar_time_ms=0, instrument="gold",
    )
    res = open_trade(bad, GOLD, deps=_demo_deps(), write_journal=False)
    assert res["executed"] is False
    assert "rr_floor" in res["reason"]


def test_non_open_decision_is_noop():
    none_dec = LiveDecision("NONE", "no_flip", "NONE", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, "gold")
    res = open_trade(none_dec, GOLD, deps=_demo_deps(), write_journal=False)
    assert res["executed"] is False
    assert res["reason"] == "not_open"


def test_close_is_demo_gated():
    state = PositionState(direction=1, entry=2000.0, stop=1980.0, extreme=2050.0,
                          entry_time_ms=0, ticket=12345, volume=0.2)
    dec = LiveDecision("CLOSE", "regime_flip_exit", "LONG", 2000.0, 1980.0, 2180.0, 2240.0,
                       1980.0, 2050.0, 20.0, 2001.0, 1999.0, 0, "gold")

    # live mode → blocked
    res_live = close_trade(state, GOLD, dec, deps=TsmomExecDeps(get_executor_mode=lambda: "live"),
                           write_journal=False)
    assert res_live["closed"] is False
    assert "demo_gate" in res_live["reason"]

    # demo mode → close routed to broker with ticket+volume
    closed: list[tuple] = []
    deps = _demo_deps(
        get_executor_mode=lambda: "demo",
        mt5_close=lambda t, v: closed.append((t, v)) or {"success": True},
    )
    res = close_trade(state, GOLD, dec, deps=deps, write_journal=False)
    assert res["closed"] is True
    assert closed == [(12345, 0.2)]
