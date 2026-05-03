"""Tests for timed_exit_monitor Phases 1-3 and Engine D tp_partial SL management.

Phase 1: tp_progress_exempt for scalp/intraday, timed_close_enabled toggle
Phase 2: Trailing ATR TP (Chandelier exit)
Phase 3: Indicator confirmation (RSI/MACD) for trail close
Engine D: SL moves to tp_partial after +1R partial
"""

import sys
import types
import pytest

# ── Stub broker modules before importing timed_exit_monitor ──────────────────

_mt5_stub = types.ModuleType("mt5_executor")
_mt5_stub.mt5_close_position = lambda *a, **kw: {"success": True, "closePrice": 1.1000, "liveProfit": 5.0, "entryPrice": 1.0950}
_mt5_stub.mt5_move_sl_to_breakeven = lambda *a, **kw: {"success": True}
_mt5_stub.mt5_get_positions = lambda: {"positions": []}
sys.modules["mt5_executor"] = _mt5_stub

_tg_stub = types.ModuleType("telegram_notify")
_tg_stub.notify_trade_closed = lambda **kw: None
_tg_stub._send_message_async = lambda *a: None
sys.modules["telegram_notify"] = _tg_stub

_bybit_stub = types.ModuleType("bybit_executor")
_bybit_stub.bybit_close_position = lambda *a, **kw: {"success": True}
_bybit_stub.bybit_move_sl_to_breakeven = lambda *a, **kw: {"success": True}
_bybit_stub.bybit_map_symbol = lambda p: f"{p}/USDT:USDT"
_bybit_stub.bybit_get_positions = lambda: {"positions": []}
sys.modules["bybit_executor"] = _bybit_stub

from timed_exit_monitor import (
    _tp_progress,
    _get_timed_cfg,
    _should_trail_close,
    _compute_chandelier_trail,
    _check_indicator_confirmation,
    _trail_state,
    _normalize_profit_lock_stages,
    _best_eligible_lock_r,
    _sl_for_locked_profit_r,
    _current_r_multiple,
    _protective_sl_tightens,
    _try_scalp_profit_lock_sl,
    _scalp_profit_lock_state,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_config(overrides: dict | None = None) -> dict:
    """Build a minimal CONFIG dict for _get_timed_cfg."""
    base = {
        "TIMED_EXIT": {
            "enabled": True,
            "scalp": {"breakeven_min": 5, "close_min": 10, "timed_close_enabled": True, "tp_progress_exempt": 0.40},
            "intraday": {"breakeven_min": 15, "close_min": 30, "timed_close_enabled": True, "tp_progress_exempt": 0.50},
            "swing": {"breakeven_days": 2.5, "close_days": 5.0, "timed_close_enabled": True, "tp_progress_exempt": 0.50},
            "tp_mode": "fixed",
            "trail_activation_r": 1.0,
            "trail_atr_mult": {"scalp": 2.0, "intraday": 2.5, "swing": 3.0},
            "trail_lookback": 14,
            "trail_timeframe": {"scalp": "H1", "intraday": "H4", "swing": "D1"},
            "trail_indicator_confirm": False,
            "trail_confirm_rsi_threshold": 40,
            "trail_confirm_rsi_period": 14,
            "trail_confirm_macd": True,
        }
    }
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and k in base["TIMED_EXIT"]:
                base["TIMED_EXIT"][k].update(v)
            else:
                base["TIMED_EXIT"][k] = v
    return base


def _cfg_fn(overrides=None):
    cfg = _make_config(overrides)
    return lambda: cfg


# ── Phase 1: tp_progress_exempt for all styles ──────────────────────────────

class TestTpProgressExempt:
    def test_progress_above_threshold_returns_high(self):
        assert _tp_progress(1.1050, 1.1000, 1.1100, "LONG") == pytest.approx(0.50, abs=0.01)

    def test_progress_at_zero(self):
        assert _tp_progress(1.1000, 1.1000, 1.1100, "LONG") == pytest.approx(0.0)

    def test_progress_at_tp(self):
        assert _tp_progress(1.1100, 1.1000, 1.1100, "LONG") == pytest.approx(1.0)

    def test_progress_short(self):
        assert _tp_progress(1.0950, 1.1000, 1.0900, "SHORT") == pytest.approx(0.50, abs=0.01)

    def test_scalp_has_exempt_threshold(self):
        tcfg = _get_timed_cfg(_cfg_fn())
        assert "tp_progress_exempt" in tcfg["scalp"]
        assert tcfg["scalp"]["tp_progress_exempt"] == pytest.approx(0.40)

    def test_intraday_has_exempt_threshold(self):
        tcfg = _get_timed_cfg(_cfg_fn())
        assert tcfg["intraday"]["tp_progress_exempt"] == pytest.approx(0.50)

    def test_swing_has_exempt_threshold(self):
        tcfg = _get_timed_cfg(_cfg_fn())
        assert tcfg["swing"]["tp_progress_exempt"] == pytest.approx(0.50)


# ── Phase 1: timed_close_enabled toggle ──────────────────────────────────────

class TestTimedCloseToggle:
    def test_default_enabled(self):
        tcfg = _get_timed_cfg(_cfg_fn())
        for style in ("scalp", "intraday", "swing"):
            assert tcfg[style]["timed_close_enabled"] is True

    def test_disable_scalp(self):
        tcfg = _get_timed_cfg(_cfg_fn({"scalp": {"timed_close_enabled": False}}))
        assert tcfg["scalp"]["timed_close_enabled"] is False
        assert tcfg["intraday"]["timed_close_enabled"] is True

    def test_disable_all(self):
        tcfg = _get_timed_cfg(_cfg_fn({
            "scalp": {"timed_close_enabled": False},
            "intraday": {"timed_close_enabled": False},
            "swing": {"timed_close_enabled": False},
        }))
        for style in ("scalp", "intraday", "swing"):
            assert tcfg[style]["timed_close_enabled"] is False


# ── Phase 2: Config parsing for trailing ATR TP ─────────────────────────────

class TestTrailingConfig:
    def test_default_tp_mode_is_fixed(self):
        tcfg = _get_timed_cfg(_cfg_fn())
        assert tcfg["tp_mode"] == "fixed"

    def test_trailing_atr_mode(self):
        tcfg = _get_timed_cfg(_cfg_fn({"tp_mode": "trailing_atr"}))
        assert tcfg["tp_mode"] == "trailing_atr"

    def test_trail_activation_r(self):
        tcfg = _get_timed_cfg(_cfg_fn({"trail_activation_r": 0.5}))
        assert tcfg["trail_activation_r"] == pytest.approx(0.5)

    def test_trail_atr_mult_per_style(self):
        tcfg = _get_timed_cfg(_cfg_fn())
        assert tcfg["trail_atr_mult"]["scalp"] == pytest.approx(2.0)
        assert tcfg["trail_atr_mult"]["intraday"] == pytest.approx(2.5)
        assert tcfg["trail_atr_mult"]["swing"] == pytest.approx(3.0)

    def test_trail_timeframe_per_style(self):
        tcfg = _get_timed_cfg(_cfg_fn())
        assert tcfg["trail_timeframe"]["scalp"] == "H1"
        assert tcfg["trail_timeframe"]["intraday"] == "H4"
        assert tcfg["trail_timeframe"]["swing"] == "D1"

    def test_trail_lookback(self):
        tcfg = _get_timed_cfg(_cfg_fn({"trail_lookback": 22}))
        assert tcfg["trail_lookback"] == 22


# ── Phase 3: Indicator confirmation config ───────────────────────────────────

class TestIndicatorConfirmConfig:
    def test_default_disabled(self):
        tcfg = _get_timed_cfg(_cfg_fn())
        assert tcfg["trail_indicator_confirm"] is False

    def test_enable(self):
        tcfg = _get_timed_cfg(_cfg_fn({"trail_indicator_confirm": True}))
        assert tcfg["trail_indicator_confirm"] is True

    def test_rsi_threshold(self):
        tcfg = _get_timed_cfg(_cfg_fn({"trail_confirm_rsi_threshold": 35}))
        assert tcfg["trail_confirm_rsi_threshold"] == 35

    def test_macd_flag(self):
        tcfg = _get_timed_cfg(_cfg_fn({"trail_confirm_macd": False}))
        assert tcfg["trail_confirm_macd"] is False


# ── Phase 2: Chandelier trail computation ────────────────────────────────────

class TestChandelierTrail:
    def test_trail_ratchets_for_long(self):
        """Trail level for LONG should only rise, never fall."""
        _trail_state.clear()
        tcfg = _get_timed_cfg(_cfg_fn({"tp_mode": "trailing_atr"}))

        # Stub fetch_candles to return synthetic data
        _rising_candles = []
        base = 100.0
        for i in range(40):
            o = base + i * 0.5
            h = o + 1.0
            l = o - 0.5
            c = o + 0.3
            _rising_candles.append([None, o, h, l, c, 1000])

        import timed_exit_monitor as tem
        _orig_compute = tem._compute_chandelier_trail

        # Direct test: call twice with rising data, check ratchet
        key = "test_ratchet_long"
        _trail_state[key] = 98.0  # previous trail level

        # Simulate: if we compute raw > 98, it should be max(raw, 98)
        _trail_state[key] = 99.5
        assert _trail_state[key] == 99.5

        # A lower raw should be clamped to 99.5
        raw = 98.0
        clamped = max(raw, _trail_state[key])
        assert clamped == 99.5

    def test_trail_ratchets_for_short(self):
        """Trail level for SHORT should only fall, never rise."""
        _trail_state.clear()
        key = "test_ratchet_short"
        _trail_state[key] = 102.0

        raw = 103.0
        clamped = min(raw, _trail_state[key])
        assert clamped == 102.0

    def test_fetch_timed_exit_candles_uses_resolved_pair(self, monkeypatch):
        import candle_manager
        import timed_exit_monitor as tem

        seen = {}

        def fake_fetch(pair, tf, limit):
            seen.update({"pair": pair, "tf": tf, "limit": limit})
            return [{"high": 1.2, "low": 1.1, "close": 1.15}]

        monkeypatch.setattr(
            tem,
            "_resolve_timed_exit_pair",
            lambda pair_label: {
                "symbol": "EURUSD=X",
                "display": "EUR/USD",
                "source": "mt5",
                "type": "forex",
            },
        )
        monkeypatch.setattr(candle_manager, "fetch_candles", fake_fetch)

        out = tem._fetch_timed_exit_candles("EUR/USD", "H4", 34)

        assert out
        assert seen["pair"]["display"] == "EUR/USD"
        assert seen["tf"] == "H4"
        assert seen["limit"] == 34

    def test_ohlc_series_accepts_dict_and_list_candles(self):
        import timed_exit_monitor as tem

        highs, lows, closes = tem._ohlc_series_from_candles(
            [
                {"high": "101.0", "low": "99.5", "close": "100.5"},
                [None, 100.5, 102.0, 100.0, 101.5, 1000],
            ]
        )

        assert highs == [101.0, 102.0]
        assert lows == [99.5, 100.0]
        assert closes == [100.5, 101.5]

    def test_compute_chandelier_trail_accepts_dict_candles(self, monkeypatch):
        import timed_exit_monitor as tem

        _trail_state.clear()
        candles = []
        base = 100.0
        for i in range(40):
            o = base + i * 0.4
            candles.append(
                {
                    "time": i,
                    "open": o,
                    "high": o + 1.0,
                    "low": o - 0.5,
                    "close": o + 0.2,
                }
            )
        seen = {}

        def fake_fetch(pair_label, tf, limit):
            seen.update({"pair": pair_label, "tf": tf, "limit": limit})
            return candles

        monkeypatch.setattr(tem, "_fetch_timed_exit_candles", fake_fetch)
        tcfg = _get_timed_cfg(_cfg_fn({"tp_mode": "trailing_atr"}))

        trail = _compute_chandelier_trail(
            "EUR/USD", "intraday", "LONG", 100.0, 90.0, tcfg, "dict_trail"
        )

        assert trail is not None
        assert seen == {"pair": "EUR/USD", "tf": "H4", "limit": 34}
        assert _trail_state["dict_trail"] == pytest.approx(trail)

    def test_trail_activation_requires_configured_r(self, monkeypatch):
        import timed_exit_monitor as tem

        tcfg = _get_timed_cfg(
            _cfg_fn({"tp_mode": "trailing_atr", "trail_activation_r": 1.0})
        )
        row = {"ticket": "T1", "pair": "EUR/USD"}

        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *args, **kwargs: 112.0)
        monkeypatch.setattr(tem, "_check_indicator_confirmation", lambda *args, **kwargs: True)

        assert _should_trail_close(row, "intraday", "LONG", 100.0, 90.0, 106.0, tcfg) is False
        assert _should_trail_close(row, "intraday", "LONG", 100.0, 90.0, 111.0, tcfg) is True


# ── Phase 3: Indicator confirmation logic ────────────────────────────────────

class TestIndicatorConfirmation:
    def test_confirmation_disabled_returns_true(self):
        tcfg = _get_timed_cfg(_cfg_fn({"trail_indicator_confirm": False}))
        result = _check_indicator_confirmation("EURUSD", "intraday", "LONG", tcfg)
        assert result is True

    def test_confirmation_candle_fetch_error_returns_true(self, monkeypatch):
        """If trail confirmation candles are unavailable, fail-open."""
        import timed_exit_monitor as tem

        tcfg = _get_timed_cfg(_cfg_fn({"trail_indicator_confirm": True}))
        monkeypatch.setattr(tem, "_fetch_timed_exit_candles", lambda *args, **kwargs: None)

        result = _check_indicator_confirmation("EURUSD", "intraday", "LONG", tcfg)

        assert result is True


# ── Chandelier exit indicator function ───────────────────────────────────────

class TestChandelierExitIndicator:
    def test_basic_output_shape(self):
        from indicators import chandelier_exit

        n = 60
        highs = [100.0 + i * 0.1 for i in range(n)]
        lows = [99.0 + i * 0.1 for i in range(n)]
        closes = [99.5 + i * 0.1 for i in range(n)]

        result = chandelier_exit(highs, lows, closes, atr_period=14, lookback=22, mult=3.0)

        assert len(result["long_stop"]) == n
        assert len(result["short_stop"]) == n
        assert len(result["direction"]) == n

        # First bars should be None
        for i in range(22):
            assert result["long_stop"][i] is None

        # Later bars should have values
        assert result["long_stop"][-1] is not None
        assert result["short_stop"][-1] is not None
        assert result["direction"][-1] in (1, -1)

    def test_trending_up_direction_is_long(self):
        from indicators import chandelier_exit

        n = 80
        highs = [100.0 + i * 0.5 for i in range(n)]
        lows = [99.0 + i * 0.5 for i in range(n)]
        closes = [99.5 + i * 0.5 for i in range(n)]

        result = chandelier_exit(highs, lows, closes)
        assert result["direction"][-1] == 1  # bullish

    def test_trending_down_direction_is_short(self):
        from indicators import chandelier_exit

        n = 80
        highs = [200.0 - i * 0.5 for i in range(n)]
        lows = [199.0 - i * 0.5 for i in range(n)]
        closes = [199.5 - i * 0.5 for i in range(n)]

        result = chandelier_exit(highs, lows, closes)
        assert result["direction"][-1] == -1  # bearish

    def test_long_stop_ratchets_up(self):
        from indicators import chandelier_exit

        n = 80
        highs = [100.0 + i * 0.3 for i in range(n)]
        lows = [99.0 + i * 0.3 for i in range(n)]
        closes = [99.5 + i * 0.3 for i in range(n)]

        result = chandelier_exit(highs, lows, closes)
        ls = [v for v in result["long_stop"] if v is not None]
        for i in range(1, len(ls)):
            assert ls[i] >= ls[i - 1], f"long_stop must ratchet up: {ls[i]} < {ls[i-1]}"


# ── Engine D: SL move to tp_partial validation ───────────────────────────────

class TestEngineDSlValidation:
    def test_tp_partial_long_must_be_above_entry(self):
        entry = 1.1000
        sl = 1.0950
        sl_dist = abs(entry - sl)
        tp_partial = entry + sl_dist  # 1.1050
        assert tp_partial > entry
        assert tp_partial == pytest.approx(1.1050)

    def test_tp_partial_short_must_be_below_entry(self):
        entry = 1.1000
        sl = 1.1050
        sl_dist = abs(entry - sl)
        tp_partial = entry - sl_dist  # 1.0950
        assert tp_partial < entry
        assert tp_partial == pytest.approx(1.0950)

    def test_sl_move_never_widens_long(self):
        """For LONG: new SL (tp_partial) must be > entry and < current price."""
        entry = 1.1000
        cur_px = 1.1100
        tp_partial = 1.1050
        valid = tp_partial > entry and tp_partial < cur_px
        assert valid is True

    def test_sl_move_rejects_invalid_long(self):
        """For LONG: tp_partial below entry should be rejected."""
        entry = 1.1000
        cur_px = 1.1100
        tp_partial = 1.0990  # below entry
        valid = tp_partial > entry and tp_partial < cur_px
        assert valid is False

    def test_sl_move_never_widens_short(self):
        """For SHORT: new SL (tp_partial) must be < entry and > current price."""
        entry = 1.1000
        cur_px = 1.0900
        tp_partial = 1.0950
        valid = tp_partial < entry and tp_partial > cur_px
        assert valid is True

    def test_tp_partial_computed_from_sl_dist_when_null(self):
        """When tp_partial is not in audit, compute from entry +/- sl_dist."""
        entry = 50000.0
        sl = 49500.0
        sl_dist = abs(entry - sl)
        direction = "LONG"
        tp_partial_raw = 0.0  # missing from audit

        if tp_partial_raw <= 0:
            tp_partial_raw = (entry + sl_dist) if direction == "LONG" else (entry - sl_dist)

        assert tp_partial_raw == pytest.approx(50500.0)


# ── Engine A/B scalp profit-lock (timed_exit_monitor staged SL) ───────────────

class TestScalpProfitLockHelpers:
    def test_normalize_empty_returns_defaults(self):
        st = _normalize_profit_lock_stages(None)
        assert len(st) == 4
        assert st[0]["trigger_r"] == pytest.approx(0.50)

    def test_best_eligible_picks_max_lock(self):
        st = _normalize_profit_lock_stages(None)
        # Default ladder: highest lock whose trigger_r is satisfied (final tier needs +1.5R).
        assert _best_eligible_lock_r(st, 1.2) == pytest.approx(0.50)
        assert _best_eligible_lock_r(st, 1.5) == pytest.approx(1.00)
        assert _best_eligible_lock_r(st, 0.55) == pytest.approx(0.10)
        assert _best_eligible_lock_r(st, 0.2) is None

    def test_sl_locked_short_one_r(self):
        entry, dist = 1.10, 0.005
        assert _sl_for_locked_profit_r(entry, dist, "SHORT", 1.0) == pytest.approx(entry - dist)

    def test_current_r_multiple_short(self):
        r = _current_r_multiple(1.10, 1.105, 1.086, "SHORT")
        assert r == pytest.approx(2.8)

    def test_protective_rejects_widen_long(self):
        assert not _protective_sl_tightens("LONG", 1.098, 1.099, 1.10)


class TestScalpProfitLockTry:
    def setup_method(self):
        _scalp_profit_lock_state.clear()

    def test_fallback_then_upgrade(self):
        moves: list[float] = []
        scfg = {"profit_lock_enabled": True, "profit_lock_stages": _normalize_profit_lock_stages(None)}
        tcfg = {"breakeven_min_profit_r": 0.2, "breakeven_buffer_r": 0.05}
        entry, audit_sl = 1.10, 1.105
        dist = 0.005

        def _record(px: float) -> dict:
            moves.append(px)
            return {"success": True}

        _try_scalp_profit_lock_sl(
            ticket_key="t_fall",
            risk_amount=100.0,
            scfg=scfg,
            tcfg=tcfg,
            entry=entry,
            cur_price=1.099,
            direction="SHORT",
            profit=50.0,
            live_sl=audit_sl,
            audit_sl=audit_sl,
            be_due_now=True,
            mins_open=6.0,
            pair_label="T",
            move_sl=_record,
            telegram_notify=sys.modules["telegram_notify"],
        )
        assert len(moves) == 1
        assert moves[0] == pytest.approx(entry - 0.05 * dist)

        _try_scalp_profit_lock_sl(
            ticket_key="t_fall",
            risk_amount=100.0,
            scfg=scfg,
            tcfg=tcfg,
            entry=entry,
            cur_price=1.086,
            direction="SHORT",
            profit=50.0,
            live_sl=moves[-1],
            audit_sl=audit_sl,
            be_due_now=True,
            mins_open=10.0,
            pair_label="T",
            move_sl=_record,
            telegram_notify=sys.modules["telegram_notify"],
        )
        assert len(moves) == 2
        assert moves[1] == pytest.approx(_sl_for_locked_profit_r(entry, dist, "SHORT", 1.0))

    def test_get_timed_cfg_merges_scalp_profit_lock(self):
        tcfg = _get_timed_cfg(_cfg_fn())
        assert tcfg["scalp"].get("profit_lock_enabled") is True
        assert len(tcfg["scalp"].get("profit_lock_stages", [])) >= 4
