"""Fakeout-hardening tests for the chandelier trail (2026-07-10).

Covers the three hardening changes in timed_exit_monitor:
1. trail_close_confirm_ticks — software closes (trail breach, peak give-back,
   profit roundtrip) must hold for N consecutive monitor ticks before the
   position closes; a reclaimed condition resets the counter.
2. trail_broker_sl_buffer_frac_of_rope — the broker SL ratchets a wick-buffer
   beyond the software trail line instead of sitting exactly on it, so the
   hard stop is disconnect protection rather than the first thing a stop-run
   wick hits.
3. _tp_clear_succeeded — broker-TP clears count as done only when the broker
   call confirmed them (MT5 "no change" skip = already cleared; Bybit
   sl_not_tighter skip = request never sent, must retry).
"""

import pytest

import timed_exit_monitor as tem
from timed_exit_monitor import (
    _evaluate_trail,
    _get_timed_cfg,
    _tp_clear_succeeded,
    _trail_state,
)


def _cfg(overrides=None):
    base = {
        "TIMED_EXIT": {
            "enabled": True,
            "scalp": {"breakeven_min": 5, "close_min": 10, "timed_close_enabled": False, "tp_progress_exempt": 0.40},
            "intraday": {"breakeven_min": 15, "close_min": 30, "timed_close_enabled": False, "tp_progress_exempt": 0.50},
            "swing": {"breakeven_days": 2.5, "close_days": 5.0, "timed_close_enabled": False, "tp_progress_exempt": 0.50},
            "tp_mode": "trailing_atr",
            "trail_activation_r": {"scalp": 0.3, "intraday": 0.5, "swing": 1.0},
            "trail_atr_mult": {"scalp": 2.0, "intraday": 2.5, "swing": 3.0},
            "trail_giveback_r": {"scalp": 0.0, "intraday": 0.0, "swing": 0.0},
            "trail_clear_broker_tp_on_activation": True,
            "trail_lookback": 14,
            "trail_timeframe": {"scalp": "H1", "intraday": "H4", "swing": "D1"},
            "trail_indicator_confirm": False,
            "stagnation_exit": {"enabled": False},
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
    tem._peak_r_state.clear()
    tem._tp_cleared_state.clear()
    tem._trail_atr_state.clear()
    tem._close_pending_state.clear()


ROW = {"pair": "EURUSD", "ticket": 1, "audit_id": "fh1"}


class TestCloseConfirmTicks:
    def setup_method(self):
        _clear_state()

    def test_breach_close_requires_consecutive_ticks(self, monkeypatch):
        _trail_state["fh_breach"] = 111.0
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 111.0)
        tcfg = _cfg({"trail_close_confirm_ticks": 2})

        # cur=110 < trail=111 → breached; first tick must NOT close.
        first = _evaluate_trail(ROW, "intraday", "LONG", 100.0, 90.0, 110.0, tcfg,
                                state_key="fh_breach", venue="mt5")
        assert first["action"] == "ratchet"
        assert first["reason"] == "breach_pending_confirm"

        second = _evaluate_trail(ROW, "intraday", "LONG", 100.0, 90.0, 110.0, tcfg,
                                 state_key="fh_breach", venue="mt5")
        assert second["action"] == "close"
        assert second["reason"] == "breach_confirmed"

    def test_reclaimed_breach_resets_pending_counter(self, monkeypatch):
        _trail_state["fh_wick"] = 111.0
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 111.0)
        tcfg = _cfg({"trail_close_confirm_ticks": 2})

        pending = _evaluate_trail(ROW, "intraday", "LONG", 100.0, 90.0, 110.0, tcfg,
                                  state_key="fh_wick", venue="mt5")
        assert pending["reason"] == "breach_pending_confirm"

        # Wick reclaimed: price back above the trail — counter must reset.
        reclaimed = _evaluate_trail(ROW, "intraday", "LONG", 100.0, 90.0, 115.0, tcfg,
                                    state_key="fh_wick", venue="mt5")
        assert reclaimed["action"] == "ratchet"
        assert reclaimed["reason"] == "active"

        again = _evaluate_trail(ROW, "intraday", "LONG", 100.0, 90.0, 110.0, tcfg,
                                state_key="fh_wick", venue="mt5")
        assert again["action"] == "ratchet"
        assert again["reason"] == "breach_pending_confirm"

    def test_giveback_below_activation_requires_consecutive_ticks(self, monkeypatch):
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 102.0)
        tcfg = _cfg({
            "trail_close_confirm_ticks": 2,
            "trail_giveback_r": {"scalp": 0.0, "intraday": 0.5, "swing": 0.0},
        })

        # Activate + set peak (r=2.0), no breach (trail 102 < 110).
        opened = _evaluate_trail(ROW, "intraday", "LONG", 100.0, 90.0, 110.0, tcfg,
                                 state_key="fh_gb", venue="mt5")
        assert opened["action"] == "ratchet"

        # Drop to r=0.4 (below activation, give-back 1.6R owed).
        first = _evaluate_trail(ROW, "intraday", "LONG", 100.0, 90.0, 104.0, tcfg,
                                state_key="fh_gb", venue="mt5")
        assert first["action"] == "none"
        assert first["reason"] == "peak_giveback_pending_confirm"

        second = _evaluate_trail(ROW, "intraday", "LONG", 100.0, 90.0, 104.0, tcfg,
                                 state_key="fh_gb", venue="mt5")
        assert second["action"] == "close"
        assert second["reason"] == "peak_giveback"

    def test_profit_roundtrip_requires_consecutive_ticks(self, monkeypatch):
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 95.0)
        tcfg = _cfg({
            "trail_close_confirm_ticks": 2,
            "pre_activation_profit_protect_enabled": True,
            "pre_activation_profit_arm_r": {"scalp": 0.2, "intraday": 0.2, "swing": 0.2},
            "pre_activation_profit_close_r": {"scalp": -0.15, "intraday": -0.15, "swing": -0.15},
            "pre_activation_profit_giveback_r": {"scalp": 0.4, "intraday": 0.4, "swing": 0.4},
        })

        # Green first (r=0.4, arms the peak), then red past close_r (r=-0.2).
        _evaluate_trail(ROW, "intraday", "LONG", 100.0, 90.0, 104.0, tcfg,
                        state_key="fh_rt", venue="mt5")
        first = _evaluate_trail(ROW, "intraday", "LONG", 100.0, 90.0, 98.0, tcfg,
                                state_key="fh_rt", venue="mt5")
        assert first["action"] == "none"
        assert first["reason"] == "profit_roundtrip_pending_confirm"

        second = _evaluate_trail(ROW, "intraday", "LONG", 100.0, 90.0, 98.0, tcfg,
                                 state_key="fh_rt", venue="mt5")
        assert second["action"] == "close"
        assert second["reason"] == "profit_roundtrip"

    def test_default_single_tick_keeps_legacy_close(self, monkeypatch):
        _trail_state["fh_legacy"] = 111.0
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 111.0)
        tcfg = _cfg()  # trail_close_confirm_ticks defaults to 1

        res = _evaluate_trail(ROW, "intraday", "LONG", 100.0, 90.0, 110.0, tcfg,
                              state_key="fh_legacy", venue="mt5")
        assert res["action"] == "close"
        assert res["reason"] == "breach_confirmed"


class TestBrokerSlWickBuffer:
    def setup_method(self):
        _clear_state()

    def test_ratchet_offsets_broker_sl_by_buffer(self, monkeypatch):
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 102.0)
        tem._trail_atr_state["fh_buf"] = 2.0
        tcfg = _cfg({"trail_broker_sl_buffer_frac_of_rope": 0.25})

        res = _evaluate_trail(ROW, "intraday", "LONG", 100.0, 90.0, 108.0, tcfg,
                              state_key="fh_buf", venue="mt5")
        assert res["action"] == "ratchet"
        # buffer = 0.25 * (mult 2.5 * atr 2.0) = 1.25 below the 102.0 line.
        assert res["new_sl"] == pytest.approx(100.75)
        assert res["trail_level"] == pytest.approx(102.0)

    def test_short_direction_buffers_above_line(self, monkeypatch):
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 98.0)
        tem._trail_atr_state["fh_buf_s"] = 2.0
        tcfg = _cfg({"trail_broker_sl_buffer_frac_of_rope": 0.25})

        res = _evaluate_trail(ROW, "intraday", "SHORT", 100.0, 110.0, 92.0, tcfg,
                              state_key="fh_buf_s", venue="mt5")
        assert res["action"] == "ratchet"
        assert res["new_sl"] == pytest.approx(99.25)

    def test_no_atr_known_falls_back_to_trail_line(self, monkeypatch):
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 102.0)
        tcfg = _cfg({"trail_broker_sl_buffer_frac_of_rope": 0.25})

        res = _evaluate_trail(ROW, "intraday", "LONG", 100.0, 90.0, 108.0, tcfg,
                              state_key="fh_noatr", venue="mt5")
        assert res["action"] == "ratchet"
        assert res["new_sl"] == pytest.approx(102.0)

    def test_buffer_disabled_by_default(self, monkeypatch):
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 102.0)
        tem._trail_atr_state["fh_off"] = 2.0
        tcfg = _cfg()

        res = _evaluate_trail(ROW, "intraday", "LONG", 100.0, 90.0, 108.0, tcfg,
                              state_key="fh_off", venue="mt5")
        assert res["new_sl"] == pytest.approx(102.0)


class TestTpClearSucceeded:
    def test_plain_success_counts(self):
        assert _tp_clear_succeeded({"success": True}) is True

    def test_mt5_no_change_skip_counts_as_cleared(self):
        assert _tp_clear_succeeded(
            {"success": True, "skipped": True, "reason": "no change"}
        ) is True

    def test_bybit_not_tighter_skip_must_retry(self):
        assert _tp_clear_succeeded(
            {"success": True, "skipped": True, "reason": "sl_not_tighter"}
        ) is False

    def test_failure_must_retry(self):
        assert _tp_clear_succeeded({"success": False, "error": "boom"}) is False
