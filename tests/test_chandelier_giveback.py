"""Targeted tests for the peak-R give-back close in _evaluate_trail.

The chandelier ratchets SL on the way up; the give-back logic closes the
trade when current R drops below the peak R by trail_giveback_r. This is
the "as soon as it starts losing, close and take max profit" leg of the
exit policy.
"""

import types
import pytest


# ── Stub broker modules before importing timed_exit_monitor ──────────────────

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
    _giveback_r_for,
    _trail_atr_mult_for,
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
            "trail_atr_mult_by_venue": {
                "bybit": {"scalp": 2.5, "intraday": 3.0, "swing": 3.5},
                "mt5":   {"scalp": 1.8, "intraday": 2.2, "swing": 2.8},
            },
            "trail_giveback_r": {"scalp": 0.3, "intraday": 0.4, "swing": 0.6},
            "trail_giveback_r_by_venue": {
                "bybit": {"scalp": 0.4, "intraday": 0.5, "swing": 0.7},
                "mt5":   {"scalp": 0.25, "intraday": 0.35, "swing": 0.5},
            },
            "trail_clear_broker_tp_on_activation": True,
            "trail_lookback": 14,
            "trail_timeframe": {"scalp": "H1", "intraday": "H4", "swing": "D1"},
            "trail_indicator_confirm": False,
        }
    }
    if overrides:
        # Top-level keys are replaced wholesale so tests can fully clear
        # nested config (e.g. setting trail_giveback_r_by_venue: {} actually
        # empties the venue overrides instead of leaving the defaults in place).
        for k, v in overrides.items():
            base["TIMED_EXIT"][k] = v
    return _get_timed_cfg(lambda: base)


def _clear_state():
    _trail_state.clear()
    _peak_r_state.clear()
    _tp_cleared_state.clear()


class TestGiveBackClose:
    def setup_method(self):
        _clear_state()

    def test_giveback_closes_when_current_drops_below_peak(self, monkeypatch):
        # Fix trail level far below current price so it never breaches; the
        # close must come from the give-back path.
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 90.0)
        tcfg = _cfg()
        row = {"pair": "EURUSD", "ticket": 1, "audit_id": "a1"}
        # Entry=100, SL=90 → risk_dist=10. Activation=1.0R for intraday.

        # First tick: push to peak +2.0R (price=120)
        first = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 120.0, tcfg,
                                state_key="give_long", venue="mt5")
        assert first["action"] == "ratchet", first
        assert first["peak_r"] == pytest.approx(2.0)

        # Second tick: price retreats to +1.6R (=116). giveback_r for mt5/intraday = 0.35
        # 2.0 - 1.6 = 0.4 ≥ 0.35 → must close
        second = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 116.0, tcfg,
                                 state_key="give_long", venue="mt5")
        assert second["action"] == "close"
        assert second["reason"] == "peak_giveback"
        assert second["peak_r"] == pytest.approx(2.0)
        assert second["current_r"] == pytest.approx(1.6)

    def test_giveback_does_not_fire_when_drop_smaller_than_budget(self, monkeypatch):
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 90.0)
        tcfg = _cfg()
        row = {"pair": "EURUSD", "ticket": 2, "audit_id": "a2"}

        # Peak at +2.0R
        _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 120.0, tcfg,
                        state_key="give_long_2", venue="mt5")
        # Retreat to +1.8R (drop 0.2 < 0.35 budget)
        res = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 118.0, tcfg,
                              state_key="give_long_2", venue="mt5")
        assert res["action"] == "ratchet"

    def test_giveback_short(self, monkeypatch):
        # SHORT: entry=100, SL=110, risk_dist=10. Price at 80 = +2R. Retreat to 86 = +1.4R.
        # bybit/intraday giveback = 0.5; drop = 0.6 ≥ 0.5 → close.
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 200.0)
        tcfg = _cfg()
        row = {"pair": "BTCUSDT", "ticket": "p1", "audit_id": "s1"}

        peak = _evaluate_trail(row, "intraday", "SHORT", 100.0, 110.0, 80.0, tcfg,
                               state_key="give_short", venue="bybit")
        assert peak["action"] == "ratchet"
        assert peak["peak_r"] == pytest.approx(2.0)

        drop = _evaluate_trail(row, "intraday", "SHORT", 100.0, 110.0, 86.0, tcfg,
                               state_key="give_short", venue="bybit")
        assert drop["action"] == "close"
        assert drop["reason"] == "peak_giveback"

    def test_zero_giveback_disables_close(self, monkeypatch):
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 90.0)
        tcfg = _cfg({
            "trail_giveback_r": {"scalp": 0.0, "intraday": 0.0, "swing": 0.0},
            "trail_giveback_r_by_venue": {},
        })
        row = {"pair": "EURUSD", "ticket": 3, "audit_id": "a3"}

        # Peak +3.0R, then retreat to +1.5R. Drop = 1.5R, which would normally
        # trigger give-back — but giveback_r=0 disables it. Must stay above
        # activation_r (1.0) so we don't fall back to below_activation.
        _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 130.0, tcfg,
                        state_key="give_off", venue="mt5")
        res = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 115.0, tcfg,
                              state_key="give_off", venue="mt5")
        assert res["action"] == "ratchet"


class TestPerVenueResolution:
    def test_trail_atr_mult_uses_venue_override(self):
        tcfg = _cfg()
        assert _trail_atr_mult_for(tcfg, "intraday", "bybit") == pytest.approx(3.0)
        assert _trail_atr_mult_for(tcfg, "intraday", "mt5") == pytest.approx(2.2)
        # Unknown venue falls back to default.
        assert _trail_atr_mult_for(tcfg, "intraday", None) == pytest.approx(2.5)
        assert _trail_atr_mult_for(tcfg, "intraday", "unknown") == pytest.approx(2.5)

    def test_giveback_uses_venue_override(self):
        tcfg = _cfg()
        assert _giveback_r_for(tcfg, "scalp", "bybit") == pytest.approx(0.4)
        assert _giveback_r_for(tcfg, "scalp", "mt5") == pytest.approx(0.25)
        # Fallback to default when no venue override.
        assert _giveback_r_for(tcfg, "scalp", None) == pytest.approx(0.3)

    def test_giveback_missing_config_returns_zero(self):
        tcfg = _cfg({"trail_giveback_r": {}, "trail_giveback_r_by_venue": {}})
        assert _giveback_r_for(tcfg, "intraday", "mt5") == pytest.approx(0.0)


class TestPercentOfPeakBudgetResolver:
    def test_frac_budget_scales_with_peak(self):
        tcfg = _cfg({
            "trail_giveback_frac_of_peak": 0.40,
            "trail_giveback_min_r": 0.30,
        })
        # 0.40 * 2.0R = 0.8R budget (floor 0.30 not binding)
        assert tem._giveback_budget_r_for(tcfg, "intraday", "mt5", 2.0) == pytest.approx(0.8)

    def test_min_floor_binds_near_activation(self):
        tcfg = _cfg({
            "trail_giveback_frac_of_peak": 0.40,
            "trail_giveback_min_r": 0.30,
        })
        # 0.40 * 0.7R = 0.28 < 0.30 floor -> 0.30
        assert tem._giveback_budget_r_for(tcfg, "scalp", "mt5", 0.7) == pytest.approx(0.30)

    def test_frac_zero_falls_back_to_fixed(self):
        tcfg = _cfg({
            "trail_giveback_frac_of_peak": 0.0,
            "trail_giveback_min_r": 0.30,
        })
        # Falls back to fixed trail_giveback_r_by_venue: mt5/intraday = 0.35
        assert tem._giveback_budget_r_for(tcfg, "intraday", "mt5", 2.0) == pytest.approx(0.35)

    def test_per_style_dict_accepted(self):
        tcfg = _cfg({
            "trail_giveback_frac_of_peak": {"scalp": 0.5, "intraday": 0.4, "swing": 0.35},
            "trail_giveback_min_r": 0.30,
        })
        assert tem._giveback_budget_r_for(tcfg, "swing", None, 4.0) == pytest.approx(1.4)

    def test_frac_clamped_below_one(self):
        # A misconfigured frac >= 1 would let the close fire at/below 0R.
        # Parser clamps to 0.9 -> budget 0.9 * 2.0 = 1.8R.
        tcfg = _cfg({
            "trail_giveback_frac_of_peak": 1.5,
            "trail_giveback_min_r": 0.30,
        })
        assert tem._giveback_budget_r_for(tcfg, "intraday", "mt5", 2.0) == pytest.approx(1.8)
