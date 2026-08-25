"""TSMOM runtime routing + freshness-severity mapping (no broker, no feed; injected deps).

Proves the daily runner maps decisions to the right gated bridge action and captures/
ratchets/clears trail state correctly, and that the freshness gate accepts only a
closed-bar-healthy D1 state (fresh / one-bucket lag / calendar-gap policy)."""
from __future__ import annotations

from types import SimpleNamespace

import tsmom_live.runtime as runtime_mod
from tsmom_live.bridge import TsmomExecDeps
from tsmom_live.feed import _severity_ok
from tsmom_live.runtime import route_decision
from tsmom_live.signal import INSTRUMENTS, LiveDecision, PositionState

GOLD = INSTRUMENTS["gold"]


class _Approval:
    def __init__(self, approved=True, volume=0.2, reason="OK"):
        self.approved, self.volume, self.reason = approved, volume, reason


def _open_decision() -> LiveDecision:
    return LiveDecision("OPEN_LONG", "regime_flip_long", "LONG", 2000.0, 1940.0, 2180.0, 2240.0,
                        1940.0, 2000.0, 20.0, 2001.0, 1999.0, 111, "gold")


def _hold_decision(trail=1990.0, extreme=2050.0) -> LiveDecision:
    return LiveDecision("HOLD", "trail", "LONG", 2000.0, trail, 2180.0, 2240.0,
                        trail, extreme, 20.0, 2010.0, 1990.0, 222, "gold")


def _close_decision() -> LiveDecision:
    return LiveDecision("CLOSE", "regime_flip_exit", "LONG", 2000.0, 1990.0, 2180.0, 2240.0,
                        1990.0, 2050.0, 20.0, 1990.0, 2000.0, 333, "gold")


def _position(ticket=777, volume=0.2, stop=1940.0) -> PositionState:
    return PositionState(direction=1, entry=2000.0, stop=stop, extreme=2000.0,
                         entry_time_ms=100, ticket=ticket, volume=volume)


def _exec_deps(calls: list, **overrides) -> TsmomExecDeps:
    base = dict(
        get_executor_mode=lambda: "paper",
        get_mt5_trade_mode=lambda: 0,
        get_bybit_base_url=lambda: "https://api-testnet.bybit.com",
        get_kill_switch=lambda: False,
        get_account=lambda _v: {"balance": 10000.0, "equity": 10000.0},
        get_positions=lambda _v: ([], None),
        get_symbol_info=lambda _s, _p: {"volume_min": 0.01},
        guardian_check=lambda *_a, **_k: (True, "OK"),
        candle_freshness_ok=lambda _c, _d: (True, "ok"),
        risk_check=lambda **_k: calls.append("risk") or _Approval(),
        mt5_execute=lambda _s, _a: calls.append("mt5") or {"success": True, "orderId": "777"},
        mt5_close=lambda t, v: calls.append(("close", t, v)) or {"success": True},
        modify_sl=lambda t, sl: calls.append(("modify", t, sl)) or {"success": True},
    )
    base.update(overrides)
    return TsmomExecDeps(**base)


def test_route_open_opens_and_captures_trail_state():
    calls: list = []
    out = route_decision(_open_decision(), None, GOLD, _exec_deps(calls))
    assert out["status"] == "opened"
    assert "mt5" in calls
    assert out["state"]["ticket"] == "777"
    assert out["state"]["volume"] == 0.2
    assert out["state"]["stop"] == 1940.0
    assert out["state"]["extreme"] == 2000.0


def test_route_hold_ratchets_broker_stop():
    calls: list = []
    out = route_decision(_hold_decision(trail=1990.0, extreme=2050.0), _position(), GOLD, _exec_deps(calls))
    assert out["status"] == "hold"
    assert ("modify", 777, 1990.0) in calls       # broker SL pushed to the new trail
    assert "mt5" not in calls                       # no new order on a hold
    assert out["state"]["stop"] == 1990.0
    assert out["state"]["extreme"] == 2050.0


def test_route_close_closes_and_clears_state():
    calls: list = []
    out = route_decision(_close_decision(), _position(), GOLD, _exec_deps(calls))
    assert out["status"] == "closed"
    assert out["clear"] is True
    assert ("close", 777, 0.2) in calls


def test_route_none_is_flat_noop():
    calls: list = []
    none_dec = LiveDecision("NONE", "no_flip", "NONE", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "gold")
    out = route_decision(none_dec, None, GOLD, _exec_deps(calls))
    assert out["status"] == "flat"
    assert calls == []


def test_freshness_severity_mapping():
    assert _severity_ok("fresh")
    assert _severity_ok("stale_1_bucket")
    assert _severity_ok("d1_calendar_gap_policy_ok")
    assert not _severity_ok("stale_multi_bucket")
    assert not _severity_ok("missing_current_bucket")
    assert not _severity_ok(None)


def test_manual_execute_rejects_when_decision_bar_changed(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        runtime_mod.feed,
        "load_daily_bars",
        lambda _cfg, **_kwargs: SimpleNamespace(
            frame=[1], freshness_ok=True, freshness_reason="fresh", error=None
        ),
    )
    monkeypatch.setattr(runtime_mod.state_store, "load_state", lambda _cfg: None)
    monkeypatch.setattr(runtime_mod, "compute_decision", lambda *_a, **_k: _open_decision())
    monkeypatch.setattr(
        runtime_mod,
        "route_decision",
        lambda *_a, **_k: calls.append("route") or {"status": "opened"},
    )

    result = runtime_mod.execute_manual(
        GOLD,
        expected_action="OPEN_LONG",
        expected_bar_time_ms=_open_decision().bar_time_ms + 1,
    )

    assert result == {
        "status": "rejected",
        "executed": False,
        "reason": "decision_bar_changed",
    }
    assert calls == []


def test_manual_execute_rejects_when_scanned_action_changed(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        runtime_mod.feed,
        "load_daily_bars",
        lambda _cfg, **_kwargs: SimpleNamespace(
            frame=[1], freshness_ok=True, freshness_reason="fresh", error=None
        ),
    )
    monkeypatch.setattr(runtime_mod.state_store, "load_state", lambda _cfg: None)
    monkeypatch.setattr(runtime_mod, "compute_decision", lambda *_a, **_k: _close_decision())
    monkeypatch.setattr(
        runtime_mod,
        "route_decision",
        lambda *_a, **_k: calls.append("route") or {"status": "closed"},
    )

    result = runtime_mod.execute_manual(
        GOLD,
        expected_action="OPEN_LONG",
        expected_bar_time_ms=_close_decision().bar_time_ms,
    )

    assert result == {
        "status": "rejected",
        "executed": False,
        "reason": "decision_action_changed:OPEN_LONG->CLOSE",
    }
    assert calls == []


def test_manual_execute_revalidates_then_routes_exact_scanned_action(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        runtime_mod.feed,
        "load_daily_bars",
        lambda _cfg, **_kwargs: SimpleNamespace(
            frame=[1], freshness_ok=True, freshness_reason="fresh", error=None
        ),
    )
    monkeypatch.setattr(runtime_mod.state_store, "load_state", lambda _cfg: None)
    monkeypatch.setattr(runtime_mod, "compute_decision", lambda *_a, **_k: _open_decision())
    monkeypatch.setattr(
        runtime_mod.state_store,
        "save_state",
        lambda instrument, state: calls.append(("save", instrument, state)),
    )
    monkeypatch.setattr(
        runtime_mod,
        "route_decision",
        lambda decision, state, cfg, deps=None: calls.append(("route", decision.action, state, cfg.instrument))
        or {"status": "opened", "executed": True, "state": {"ticket": "demo-1"}},
    )

    result = runtime_mod.execute_manual(
        GOLD,
        expected_action="OPEN_LONG",
        expected_bar_time_ms=_open_decision().bar_time_ms,
        deps=_exec_deps([]),
    )

    assert result["status"] == "opened"
    assert result["executed"] is True
    assert calls[0] == ("route", "OPEN_LONG", None, "gold")
    assert calls[1][0:2] == ("save", "gold")
