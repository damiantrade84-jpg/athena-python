"""Targeted tests for the broker-TP-clear signal emitted by _evaluate_trail.

When the chandelier first activates on a ticket, _evaluate_trail emits
clear_broker_tp=True so the handler can drop the fixed broker TP. The flag
keeps re-emitting until the handler confirms the broker call succeeded and
marks the state via _mark_tp_cleared (2026-07-10 retry contract — previously
one-shot at evaluate time, which let a single failed broker call leave the
fixed TP silently capping the runner).
"""

import types
import pytest


_mt5_stub = types.ModuleType("mt5_executor")
_mt5_stub.mt5_close_position = lambda *a, **kw: {"success": True}
_mt5_stub.mt5_move_sl_to_breakeven = lambda *a, **kw: {"success": True}
_mt5_stub.mt5_modify_protective_stops = lambda *a, **kw: {"success": True}
_mt5_stub.mt5_get_positions = lambda: {"positions": []}

_bybit_stub = types.ModuleType("bybit_executor")
_bybit_stub.bybit_close_position = lambda *a, **kw: {"success": True}
_bybit_stub.bybit_move_sl_to_breakeven = lambda *a, **kw: {"success": True}
_bybit_stub.bybit_map_symbol = lambda p: f"{p}/USDT:USDT"
_bybit_stub.bybit_get_positions = lambda: {"positions": []}

_tg_stub = types.ModuleType("telegram_notify")
_tg_stub.notify_trade_closed = lambda **kw: None
_tg_stub._send_message_async = lambda *a: None


from timed_exit_monitor import (
    _evaluate_trail,
    _get_timed_cfg,
    _trail_state,
    _peak_r_state,
    _tp_cleared_state,
)
import timed_exit_monitor as tem


def _cfg(overrides=None):
    base = {
        "TIMED_EXIT": {
            "enabled": True,
            "scalp": {"breakeven_min": 5, "close_min": 10, "timed_close_enabled": True, "tp_progress_exempt": 0.40},
            "intraday": {"breakeven_min": 15, "close_min": 30, "timed_close_enabled": True, "tp_progress_exempt": 0.50},
            "swing": {"breakeven_days": 2.5, "close_days": 5.0, "timed_close_enabled": True, "tp_progress_exempt": 0.50},
            "tp_mode": "trailing_atr",
            "trail_activation_r": {"scalp": 0.7, "intraday": 1.0, "swing": 1.5},
            "trail_atr_mult": {"scalp": 2.0, "intraday": 2.5, "swing": 3.0},
            "trail_giveback_r": {"scalp": 0.0, "intraday": 0.0, "swing": 0.0},  # disable for these tests
            "trail_clear_broker_tp_on_activation": True,
            "trail_lookback": 14,
            "trail_timeframe": {"scalp": "H1", "intraday": "H4", "swing": "D1"},
            "trail_indicator_confirm": False,
        }
    }
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and isinstance(base["TIMED_EXIT"].get(k), dict):
                base["TIMED_EXIT"][k].update(v)
            else:
                base["TIMED_EXIT"][k] = v
    return _get_timed_cfg(lambda: base)


def _clear_state():
    _trail_state.clear()
    _peak_r_state.clear()
    _tp_cleared_state.clear()


class TestClearBrokerTpSignal:
    def setup_method(self):
        _clear_state()

    def test_first_activation_emits_clear_broker_tp(self, monkeypatch):
        # Trail level well below price → action=ratchet (not close/breach).
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 95.0)
        tcfg = _cfg()
        row = {"pair": "EURUSD", "ticket": 1, "audit_id": "tp1"}

        res = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 112.0, tcfg,
                              state_key="tp_first", venue="mt5")
        assert res["action"] == "ratchet"
        assert res["clear_broker_tp"] is True

    def test_clear_reemits_until_handler_marks_success(self, monkeypatch):
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 95.0)
        tcfg = _cfg()
        row = {"pair": "EURUSD", "ticket": 2, "audit_id": "tp2"}

        first = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 112.0, tcfg,
                                state_key="tp_repeat", venue="mt5")
        assert first["clear_broker_tp"] is True
        # Handler has not confirmed the broker call — the clear must retry.
        second = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 114.0, tcfg,
                                 state_key="tp_repeat", venue="mt5")
        assert second["action"] == "ratchet"
        assert second["clear_broker_tp"] is True
        # Broker success confirmed — no further clears.
        tem._mark_tp_cleared("tp_repeat")
        third = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 116.0, tcfg,
                                state_key="tp_repeat", venue="mt5")
        assert third["action"] == "ratchet"
        assert third["clear_broker_tp"] is False

    def test_below_activation_does_not_emit_clear(self, monkeypatch):
        # Don't even reach activation_r — must not emit clear.
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 95.0)
        tcfg = _cfg()
        row = {"pair": "EURUSD", "ticket": 3, "audit_id": "tp3"}

        res = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 104.0, tcfg,
                              state_key="tp_below", venue="mt5")
        assert res["action"] == "none"
        # Below-activation result has no clear_broker_tp key — the signal should
        # only ever appear on ratchet/close actions.
        assert "clear_broker_tp" not in res

    def test_disabled_via_config_never_emits(self, monkeypatch):
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 95.0)
        tcfg = _cfg({"trail_clear_broker_tp_on_activation": False})
        row = {"pair": "EURUSD", "ticket": 4, "audit_id": "tp4"}

        res = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 112.0, tcfg,
                              state_key="tp_disabled", venue="mt5")
        assert res["action"] == "ratchet"
        assert res["clear_broker_tp"] is False

    def test_breach_no_confirm_path_also_carries_clear_flag(self, monkeypatch):
        # Trail level above price → breach; force indicator confirm to fail so
        # the result is ratchet/breach_no_confirm (still ratchets SL).
        # Pre-seed _trail_state so first-activation clamp doesn't fire (which
        # would otherwise clip the trail down below price and avoid breach).
        _trail_state["tp_breach_noc"] = 115.0
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 115.0)
        monkeypatch.setattr(tem, "_check_indicator_confirmation", lambda *a, **kw: False)
        tcfg = _cfg({"trail_indicator_confirm": True})
        row = {"pair": "EURUSD", "ticket": 5, "audit_id": "tp5"}

        res = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 112.0, tcfg,
                              state_key="tp_breach_noc", venue="mt5")
        assert res["action"] == "ratchet"
        assert res["reason"] == "breach_no_confirm"
        assert res["clear_broker_tp"] is True
