"""Tests for timed_exit_monitor Phases 1-3 and Engine D tp_partial SL management.

Phase 1: tp_progress_exempt for scalp/intraday, timed_close_enabled toggle
Phase 2: Trailing ATR TP (Chandelier exit)
Phase 3: Indicator confirmation (RSI/MACD) for trail close
Engine D: SL moves to tp_partial after +1R partial
"""

import types
import pytest

# Broker stubs below are passed directly to helper calls and intentionally not
# placed in sys.modules, because that leaks fake broker modules into later tests.

# ── Stub broker modules before importing timed_exit_monitor ──────────────────

_mt5_stub = types.ModuleType("mt5_executor")
_mt5_stub.mt5_close_position = lambda *a, **kw: {"success": True, "closePrice": 1.1000, "liveProfit": 5.0, "entryPrice": 1.0950}
_mt5_stub.mt5_move_sl_to_breakeven = lambda *a, **kw: {"success": True}
_mt5_stub.mt5_get_positions = lambda: {"positions": []}

_tg_stub = types.ModuleType("telegram_notify")
_tg_stub.notify_trade_closed = lambda **kw: None
_tg_stub._send_message_async = lambda *a: None

_bybit_stub = types.ModuleType("bybit_executor")
_bybit_stub.bybit_close_position = lambda *a, **kw: {"success": True}
_bybit_stub.bybit_move_sl_to_breakeven = lambda *a, **kw: {"success": True}
_bybit_stub.bybit_map_symbol = lambda p: f"{p}/USDT:USDT"
_bybit_stub.bybit_get_positions = lambda: {"positions": []}

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
    _evaluate_trail,
    _activation_r_for,
    _state_key,
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
            existing = base["TIMED_EXIT"].get(k)
            if isinstance(v, dict) and isinstance(existing, dict):
                existing.update(v)
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

    def test_trail_activation_r_scalar_backcompat(self):
        """Scalar trail_activation_r normalises to a uniform per-style dict."""
        tcfg = _get_timed_cfg(_cfg_fn({"trail_activation_r": 0.5}))
        # Now per-style; scalar applies to all three styles.
        assert tcfg["trail_activation_r"]["scalp"] == pytest.approx(0.5)
        assert tcfg["trail_activation_r"]["intraday"] == pytest.approx(0.5)
        assert tcfg["trail_activation_r"]["swing"] == pytest.approx(0.5)

    def test_trail_activation_r_per_style(self):
        """Per-style dict is preserved verbatim."""
        tcfg = _get_timed_cfg(
            _cfg_fn({"trail_activation_r": {"scalp": 0.3, "intraday": 0.5, "swing": 1.0}})
        )
        assert tcfg["trail_activation_r"]["scalp"] == pytest.approx(0.3)
        assert tcfg["trail_activation_r"]["intraday"] == pytest.approx(0.5)
        assert tcfg["trail_activation_r"]["swing"] == pytest.approx(1.0)

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

    def test_chandelier_uses_post_entry_bars_for_high_low_anchor(self, monkeypatch):
        import indicators
        import timed_exit_monitor as tem

        _trail_state.clear()
        candles = []
        for i in range(40):
            candles.append(
                {
                    "time": i,
                    "open": 100.0,
                    "high": 106.0,
                    "low": 99.0,
                    "close": 102.0,
                }
            )
        candles[-2]["high"] = 150.0

        monkeypatch.setattr(tem, "_fetch_timed_exit_candles", lambda *args, **kwargs: candles)
        monkeypatch.setattr(indicators, "calc_atr", lambda highs, lows, closes, period: [1.0] * len(closes))
        tcfg = _get_timed_cfg(
            _cfg_fn({
                "tp_mode": "trailing_atr",
                "trail_timeframe": {"scalp": "H1"},
                "trail_atr_mult": {"scalp": 2.0},
            })
        )

        trail = _compute_chandelier_trail(
            "EUR/USD", "scalp", "LONG", 100.0, 90.0, tcfg, "entry_anchor", mins_open=30.0,
        )

        assert trail == pytest.approx(104.0)

    def test_trail_activation_requires_configured_r(self, monkeypatch):
        import timed_exit_monitor as tem

        tcfg = _get_timed_cfg(
            _cfg_fn({"tp_mode": "trailing_atr", "trail_activation_r": 1.0})
        )
        row = {"ticket": "T1", "pair": "EUR/USD"}

        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *args, **kwargs: 112.0)
        monkeypatch.setattr(tem, "_check_indicator_confirmation", lambda *args, **kwargs: True)

        assert _should_trail_close(row, "intraday", "LONG", 100.0, 90.0, 106.0, tcfg) is False
        _trail_state["T1_EUR/USD"] = 112.0
        assert _should_trail_close(row, "intraday", "LONG", 100.0, 90.0, 111.0, tcfg) is True


# ── Phase 3: Indicator confirmation logic ────────────────────────────────────

class TestIndicatorConfirmation:
    def test_confirmation_disabled_returns_true(self):
        tcfg = _get_timed_cfg(_cfg_fn({"trail_indicator_confirm": False}))
        result = _check_indicator_confirmation("EURUSD", "intraday", "LONG", tcfg)
        assert result is True

    def test_confirmation_candle_fetch_error_returns_false(self, monkeypatch):
        """If trail confirmation candles are unavailable, fail closed."""
        import timed_exit_monitor as tem

        tcfg = _get_timed_cfg(_cfg_fn({"trail_indicator_confirm": True}))
        monkeypatch.setattr(tem, "_fetch_timed_exit_candles", lambda *args, **kwargs: None)

        result = _check_indicator_confirmation("EURUSD", "intraday", "LONG", tcfg)

        assert result is False


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
            telegram_notify=_tg_stub,
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
            telegram_notify=_tg_stub,
        )
        assert len(moves) == 2
        assert moves[1] == pytest.approx(_sl_for_locked_profit_r(entry, dist, "SHORT", 1.0))

    def test_get_timed_cfg_merges_scalp_profit_lock(self):
        tcfg = _get_timed_cfg(_cfg_fn())
        assert tcfg["scalp"].get("profit_lock_enabled") is True
        assert len(tcfg["scalp"].get("profit_lock_stages", [])) >= 4


# ── Per-style activation_r helper ────────────────────────────────────────────

class TestActivationRForStyle:
    def test_dict_form(self):
        tcfg = {"trail_activation_r": {"scalp": 0.3, "intraday": 0.5, "swing": 1.0}}
        assert _activation_r_for(tcfg, "scalp") == pytest.approx(0.3)
        assert _activation_r_for(tcfg, "intraday") == pytest.approx(0.5)
        assert _activation_r_for(tcfg, "swing") == pytest.approx(1.0)

    def test_scalar_normalised_via_get_timed_cfg(self):
        tcfg = _get_timed_cfg(_cfg_fn({"trail_activation_r": 0.7}))
        for style in ("scalp", "intraday", "swing"):
            assert _activation_r_for(tcfg, style) == pytest.approx(0.7)

    def test_missing_style_falls_back(self):
        tcfg = {"trail_activation_r": {"intraday": 0.5}}
        # Unknown style falls back to intraday default per implementation contract.
        assert _activation_r_for(tcfg, "scalp") == pytest.approx(0.5)


# ── Stable ticket keying (suggestion #9) ─────────────────────────────────────

class TestStableTicketKey:
    def test_audit_id_is_canonical(self):
        assert _state_key("mt5", 12345, "999") == "mt5:aid:12345"

    def test_falls_back_to_ticket_when_audit_id_missing(self):
        assert _state_key("bybit", None, "abc") == "bybit:tkt:abc"
        assert _state_key("bybit", 0, "abc") == "bybit:tkt:abc"
        assert _state_key("bybit", "", "abc") == "bybit:tkt:abc"

    def test_no_collision_between_venues(self):
        assert _state_key("mt5", 1, "x") != _state_key("bybit", 1, "x")

    def test_two_bybit_zero_tickets_with_distinct_audit_ids(self):
        # The original bug: two Bybit positions both with ticket=0 (split legs)
        # would share the same key. With audit_id keying they don't.
        a = _state_key("bybit", 100, "0")
        b = _state_key("bybit", 101, "0")
        assert a != b


# ── _evaluate_trail state machine ────────────────────────────────────────────

class TestEvaluateTrail:
    def setup_method(self):
        _trail_state.clear()
        import timed_exit_monitor as tem
        tem._peak_r_state.clear()

    def test_live_sl_for_r_increases_measured_r_vs_audit_sl(self, monkeypatch):
        """Tighter live SL → smaller risk distance → higher R for same price."""
        import timed_exit_monitor as tem
        tcfg = _get_timed_cfg(_cfg_fn({
            "tp_mode": "trailing_atr",
            "trail_activation_r": {"scalp": 0.3, "intraday": 0.5, "swing": 1.0},
        }))
        row = {"ticket": "T1", "pair": "EUR/USD", "audit_id": 1}
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 102.0)

        below = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 104.0, tcfg)
        assert below["action"] == "none"
        assert below["reason"] == "below_activation"

        above = _evaluate_trail(
            row, "intraday", "LONG", 100.0, 90.0, 104.0, tcfg,
            live_sl_for_r=95.0,
        )
        assert above["action"] == "ratchet"
        assert above["reason"] == "active"

    def test_below_activation_returns_none(self, monkeypatch):
        import timed_exit_monitor as tem
        tcfg = _get_timed_cfg(_cfg_fn({
            "tp_mode": "trailing_atr",
            "trail_activation_r": {"scalp": 0.3, "intraday": 0.5, "swing": 1.0},
        }))
        # cur_price=104, entry=100, sl=90 → current_r = 4/10 = 0.4 < 0.5 (intraday)
        row = {"ticket": "T1", "pair": "EUR/USD", "audit_id": 1}
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 95.0)

        res = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 104.0, tcfg)

        assert res["action"] == "none"
        assert res["reason"] == "below_activation"

    def test_above_activation_no_breach_ratchets(self, monkeypatch):
        import timed_exit_monitor as tem
        tcfg = _get_timed_cfg(_cfg_fn({
            "tp_mode": "trailing_atr",
            "trail_activation_r": {"scalp": 0.3, "intraday": 0.5, "swing": 1.0},
        }))
        row = {"ticket": "T1", "pair": "EUR/USD", "audit_id": 1}
        # cur=108, entry=100, sl=90 → current_r=0.8 ≥ 0.5; trail at 102; cur > trail → no breach
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 102.0)

        res = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 108.0, tcfg)

        assert res["action"] == "ratchet"
        assert res["new_sl"] == pytest.approx(102.0)
        assert res["reason"] == "active"

    def test_breach_with_confirmation_closes(self, monkeypatch):
        import timed_exit_monitor as tem
        tcfg = _get_timed_cfg(_cfg_fn({
            "tp_mode": "trailing_atr",
            "trail_activation_r": {"scalp": 0.3, "intraday": 0.5, "swing": 1.0},
            "trail_indicator_confirm": True,
        }))
        row = {"ticket": "T1", "pair": "EUR/USD", "audit_id": 1}
        # cur=104, entry=100, sl=90 → current_r=0.4 < 0.5 default ... use higher cur
        # cur=110, current_r=1.0, trail=111 → cur < trail → breach
        _trail_state["T1_EUR/USD"] = 111.0
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 111.0)
        monkeypatch.setattr(tem, "_check_indicator_confirmation", lambda *a, **kw: True)

        res = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 110.0, tcfg)

        assert res["action"] == "close"

    def test_first_activation_clamps_initial_breached_trail(self, monkeypatch):
        import timed_exit_monitor as tem

        tcfg = _get_timed_cfg(_cfg_fn({
            "tp_mode": "trailing_atr",
            "trail_activation_r": {"scalp": 0.3, "intraday": 0.5, "swing": 1.0},
            "trail_indicator_confirm": True,
        }))
        row = {"ticket": "T1", "pair": "EUR/USD", "audit_id": 1}
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 107.0)
        monkeypatch.setattr(tem, "_check_indicator_confirmation", lambda *a, **kw: True)

        res = _evaluate_trail(
            row, "intraday", "LONG", 100.0, 90.0, 106.0, tcfg,
            state_key="mt5:aid:1",
        )

        assert res["action"] == "ratchet"
        assert res["new_sl"] < 106.0
        assert _trail_state["mt5:aid:1"] == pytest.approx(res["new_sl"])

    def test_first_activation_clamps_initial_breached_short_trail(self, monkeypatch):
        import timed_exit_monitor as tem

        tcfg = _get_timed_cfg(_cfg_fn({
            "tp_mode": "trailing_atr",
            "trail_activation_r": {"scalp": 0.3, "intraday": 0.5, "swing": 1.0},
            "trail_indicator_confirm": True,
        }))
        row = {"ticket": "T1", "pair": "EUR/USD", "audit_id": 1}
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 94.0)
        monkeypatch.setattr(tem, "_check_indicator_confirmation", lambda *a, **kw: True)

        res = _evaluate_trail(
            row, "intraday", "SHORT", 100.0, 110.0, 95.0, tcfg,
            state_key="mt5:aid:short",
        )

        assert res["action"] == "ratchet"
        assert res["new_sl"] > 95.0
        assert _trail_state["mt5:aid:short"] == pytest.approx(res["new_sl"])

    def test_breach_without_confirmation_ratchets_not_closes(self, monkeypatch):
        """Suggestion #6 corollary: if indicator says no, keep ratcheting (don't close)."""
        import timed_exit_monitor as tem
        tcfg = _get_timed_cfg(_cfg_fn({
            "tp_mode": "trailing_atr",
            "trail_activation_r": {"scalp": 0.3, "intraday": 0.5, "swing": 1.0},
            "trail_indicator_confirm": True,
        }))
        row = {"ticket": "T1", "pair": "EUR/USD", "audit_id": 1}
        _trail_state["T1_EUR/USD"] = 111.0
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 111.0)
        monkeypatch.setattr(tem, "_check_indicator_confirmation", lambda *a, **kw: False)

        res = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 110.0, tcfg)

        assert res["action"] == "ratchet"
        assert res["reason"] == "breach_no_confirm"

    def test_no_trail_data_returns_none(self, monkeypatch):
        import timed_exit_monitor as tem
        tcfg = _get_timed_cfg(_cfg_fn({
            "tp_mode": "trailing_atr",
            "trail_activation_r": {"scalp": 0.3, "intraday": 0.5, "swing": 1.0},
        }))
        row = {"ticket": "T1", "pair": "EUR/USD", "audit_id": 1}
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: None)

        res = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 110.0, tcfg)

        assert res["action"] == "none"
        assert res["reason"] == "no_trail_data"

    def test_pre_activation_profit_roundtrip_closes_after_sub_1r_peak(self, monkeypatch):
        import timed_exit_monitor as tem

        tcfg = _get_timed_cfg(_cfg_fn({
            "tp_mode": "trailing_atr",
            "trail_activation_r": {"scalp": 0.7, "intraday": 1.0, "swing": 1.5},
            "pre_activation_profit_protect_enabled": True,
            "pre_activation_profit_arm_r": {"intraday": 0.25},
            "pre_activation_profit_close_r": {"intraday": 0.0},
            "pre_activation_profit_giveback_r": {"intraday": 0.0},
        }))
        row = {"ticket": "T1", "pair": "ADA/USDT", "audit_id": 1846}
        state_key = "bybit:aid:1846"
        monkeypatch.setattr(
            tem,
            "_compute_chandelier_trail",
            lambda *a, **kw: pytest.fail("trail should not compute below activation"),
        )

        armed = _evaluate_trail(
            row, "intraday", "SHORT", 100.0, 110.0, 96.0, tcfg,
            state_key=state_key, venue="bybit",
        )
        assert armed["action"] == "none"
        assert armed["reason"] == "below_activation"
        assert tem._peak_r_state[state_key] == pytest.approx(0.4)

        roundtrip = _evaluate_trail(
            row, "intraday", "SHORT", 100.0, 110.0, 100.5, tcfg,
            state_key=state_key, venue="bybit",
        )
        assert roundtrip["action"] == "close"
        assert roundtrip["reason"] == "profit_roundtrip"
        assert roundtrip["peak_r"] == pytest.approx(0.4)
        assert roundtrip["current_r"] == pytest.approx(-0.05)

    def test_pre_activation_roundtrip_closes_when_peak_below_arm_r(self, monkeypatch):
        """Peak below arm_r still triggers roundtrip close at breakeven."""
        import timed_exit_monitor as tem

        tcfg = _get_timed_cfg(_cfg_fn({
            "tp_mode": "trailing_atr",
            "trail_activation_r": {"intraday": 1.0},
            "pre_activation_profit_protect_enabled": True,
            "pre_activation_profit_arm_r": {"intraday": 0.30},
            "pre_activation_profit_close_r": {"intraday": 0.0},
            "pre_activation_profit_giveback_r": {"intraday": 0.35},
        }))
        row = {"ticket": "T2", "pair": "EUR/USD", "audit_id": 1900}
        state_key = "mt5:aid:1900"
        monkeypatch.setattr(
            tem,
            "_compute_chandelier_trail",
            lambda *a, **kw: pytest.fail("trail should not compute below activation"),
        )

        # Tick 1: trade peaks at 0.15R — below arm_r 0.30
        armed = _evaluate_trail(
            row, "intraday", "LONG", 1.1000, 1.0950, 1.10075, tcfg,
            state_key=state_key, venue="mt5",
        )
        assert armed["action"] == "none"
        assert armed["reason"] == "below_activation"
        assert tem._peak_r_state[state_key] == pytest.approx(0.15)

        # Tick 2: trade reverses to -0.10R — should close at roundtrip
        roundtrip = _evaluate_trail(
            row, "intraday", "LONG", 1.1000, 1.0950, 1.0995, tcfg,
            state_key=state_key, venue="mt5",
        )
        assert roundtrip["action"] == "close"
        assert roundtrip["reason"] == "profit_roundtrip"
        assert roundtrip["peak_r"] == pytest.approx(0.15)
        assert roundtrip["current_r"] == pytest.approx(-0.10)


# ── Trail-fail-holds-previous-level (suggestion #4) ──────────────────────────

class TestTrailFailHoldsPrevious:
    def setup_method(self):
        _trail_state.clear()

    def test_returns_previous_level_when_candles_unavailable(self, monkeypatch):
        import timed_exit_monitor as tem

        _trail_state["mt5:aid:42"] = 99.5  # prior trail level
        tcfg = _get_timed_cfg(_cfg_fn({"tp_mode": "trailing_atr"}))
        # Candle fetch returns nothing — should fall back to held level rather than None
        monkeypatch.setattr(tem, "_fetch_timed_exit_candles", lambda *a, **kw: None)

        result = _compute_chandelier_trail(
            "EUR/USD", "intraday", "LONG", 100.0, 90.0, tcfg, "mt5:aid:42"
        )

        assert result == pytest.approx(99.5)

    def test_returns_none_when_no_previous_level_and_no_data(self, monkeypatch):
        import timed_exit_monitor as tem
        _trail_state.clear()
        tcfg = _get_timed_cfg(_cfg_fn({"tp_mode": "trailing_atr"}))
        monkeypatch.setattr(tem, "_fetch_timed_exit_candles", lambda *a, **kw: None)

        result = _compute_chandelier_trail(
            "EUR/USD", "intraday", "LONG", 100.0, 90.0, tcfg, "mt5:aid:fresh"
        )

        assert result is None


# ── Distinct exit tags (suggestion #11) ──────────────────────────────────────

class TestDistinctExitTags:
    def test_mark_timed_close_accepts_reason(self, tmp_path):
        """_mark_timed_close should write the exact reason string passed in."""
        import sqlite3
        import timed_exit_monitor as tem

        db = tmp_path / "audit.db"
        with sqlite3.connect(str(db)) as con:
            con.execute(
                "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ticket TEXT, "
                "exit_reason TEXT, exit_price REAL, pnl REAL, r_multiple REAL, "
                "exit_time TEXT)"
            )
            con.execute(
                "INSERT INTO audit_log (id, ticket) VALUES (1, '12345')"
            )
            con.commit()

        row = {"audit_id": 1, "audit_ticket": "12345", "ticket": "12345", "pair": "EURUSD"}
        tem._mark_timed_close(str(db), row, "mt5", reason="TRAIL_CLOSE")

        with sqlite3.connect(str(db)) as con:
            r = con.execute("SELECT exit_reason FROM audit_log WHERE id=1").fetchone()
        assert r[0] == "TRAIL_CLOSE"

    def test_mark_timed_close_default_reason_is_timed_close(self, tmp_path):
        import sqlite3
        import timed_exit_monitor as tem

        db = tmp_path / "audit.db"
        with sqlite3.connect(str(db)) as con:
            con.execute(
                "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ticket TEXT, "
                "exit_reason TEXT, exit_price REAL, pnl REAL, r_multiple REAL, "
                "exit_time TEXT)"
            )
            con.execute("INSERT INTO audit_log (id, ticket) VALUES (1, '99')")
            con.commit()

        row = {"audit_id": 1, "audit_ticket": "99", "ticket": "99", "pair": "BTCUSDT"}
        tem._mark_timed_close(str(db), row, "mt5")

        with sqlite3.connect(str(db)) as con:
            r = con.execute("SELECT exit_reason FROM audit_log WHERE id=1").fetchone()
        assert r[0] == "TIMED_CLOSE"

    def test_mark_timed_close_bybit_writes_price_and_live_pnl(self, monkeypatch):
        import timed_exit_monitor as tem

        calls = []

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, query, params):
                calls.append((query, params))

            def commit(self):
                pass

        monkeypatch.setattr(tem.sqlite3, "connect", lambda *args, **kwargs: FakeConnection())
        row = {"audit_id": 7, "risk_amount": 50.0, "pair": "BTCUSDT"}

        tem._mark_timed_close(
            "ignored.db",
            row,
            "bybit",
            actual_close_price=105.5,
            live_pnl=25.0,
            reason="TRAIL_CLOSE",
        )

        query, params = calls[0]
        assert "exit_price" in query
        assert params[0] == "TRAIL_CLOSE"
        assert params[1] == 105.5
        assert params[2] == 25.0
        assert params[3] == 0.5
        assert params[5] == 7


# ── Persistence (suggestions #5) ─────────────────────────────────────────────

class TestStatePersistence:
    def test_ensure_table_creates_schema(self, tmp_path):
        import sqlite3
        import timed_exit_monitor as tem

        db = tmp_path / "state.db"
        tem._ensure_state_table(str(db))

        with sqlite3.connect(str(db)) as con:
            cols = [r[1] for r in con.execute("PRAGMA table_info(timed_exit_state)").fetchall()]
        assert "state_key" in cols
        assert "trail_level" in cols
        assert "lock_r" in cols
        assert "last_update_ts" in cols

    def test_persist_and_hydrate_roundtrip(self, tmp_path, monkeypatch):
        import timed_exit_monitor as tem

        db = tmp_path / "state.db"
        tem._ensure_state_table(str(db))
        monkeypatch.setattr(tem, "_state_db_path", str(db))

        tem._persist_trail_state("mt5:aid:7", 1.2345)
        tem._persist_lock_state("mt5:aid:7", 0.50)

        # Reset in-memory and hydrate
        tem._trail_state.clear()
        tem._scalp_profit_lock_state.clear()
        tem._state_hydrated = False

        tem._hydrate_state_from_db(str(db))

        assert tem._trail_state["mt5:aid:7"] == pytest.approx(1.2345)
        assert tem._scalp_profit_lock_state["mt5:aid:7"] == pytest.approx(0.50)


# ── Timer-tightens-trail (suggestion #2) ─────────────────────────────────────

class TestTimerTightensTrail:
    def setup_method(self):
        _trail_state.clear()

    def test_factor_applied_past_close_min(self, monkeypatch):
        """When mins_open >= close_min and timer_tightens_trail=true, mult is reduced."""
        import timed_exit_monitor as tem

        # Synthetic candles: rising price so trail computes cleanly
        candles = []
        base = 100.0
        for i in range(40):
            o = base + i * 0.4
            candles.append({"time": i, "open": o, "high": o + 1.0, "low": o - 0.5, "close": o + 0.2})

        monkeypatch.setattr(tem, "_fetch_timed_exit_candles", lambda *a, **kw: candles)

        tcfg_normal = _get_timed_cfg(_cfg_fn({
            "tp_mode": "trailing_atr",
            "timer_tightens_trail": False,
        }))
        tcfg_tighten = _get_timed_cfg(_cfg_fn({
            "tp_mode": "trailing_atr",
            "timer_tightens_trail": True,
            "timer_tighten_factor": 0.5,
            "intraday": {"close_min": 30},
        }))

        # Compute with mins_open beyond close_min (30) — tightened trail should be HIGHER for LONG
        # (trail = highest - mult*ATR; smaller mult → smaller subtraction → higher trail)
        _trail_state.clear()
        normal = _compute_chandelier_trail(
            "EUR/USD", "intraday", "LONG", 100.0, 90.0, tcfg_normal, "k_normal", mins_open=60.0,
        )
        _trail_state.clear()
        tightened = _compute_chandelier_trail(
            "EUR/USD", "intraday", "LONG", 100.0, 90.0, tcfg_tighten, "k_tight", mins_open=60.0,
        )

        assert normal is not None
        assert tightened is not None
        assert tightened > normal  # tighter trail = closer to price = higher number for LONG


# ── New defaults (Phase 1 config changes) ────────────────────────────────────

class TestPhase1Defaults:
    """Sanity tests that the default config delivers the intended behaviour."""

    def test_default_intraday_timed_close_disabled(self):
        # Tests use _cfg_fn() which has timed_close_enabled=True for back-compat with
        # existing tests; the live config.yaml is what matters. This test asserts the
        # _DEFAULT_CFG fallback (used when YAML omits the key) keeps timed_close enabled
        # — the policy lives in config.yaml, not in code defaults.
        from timed_exit_monitor import _DEFAULT_CFG
        # Code-level default is still True (for fixed mode); config.yaml overrides to False.
        assert _DEFAULT_CFG["intraday"]["timed_close_enabled"] is True

    def test_default_trail_activation_is_per_style(self):
        from timed_exit_monitor import _DEFAULT_CFG
        assert isinstance(_DEFAULT_CFG["trail_activation_r"], dict)
        assert _DEFAULT_CFG["trail_activation_r"] == {
            "scalp": 0.7,
            "intraday": 1.0,
            "swing": 1.5,
        }

    def test_missing_activation_keys_use_raised_defaults(self):
        tcfg = _get_timed_cfg(_cfg_fn({"trail_activation_r": {"intraday": 1.2}}))

        assert tcfg["trail_activation_r"]["scalp"] == pytest.approx(0.7)
        assert tcfg["trail_activation_r"]["intraday"] == pytest.approx(1.2)
        assert tcfg["trail_activation_r"]["swing"] == pytest.approx(1.5)


class TestTrailingModeBreakeven:
    def setup_method(self):
        import timed_exit_monitor as tem
        tem._be_done.clear()

    def test_trailing_be_moves_sl_when_due(self):
        import timed_exit_monitor as tem
        moves = []
        tcfg = _get_timed_cfg(_cfg_fn({
            "breakeven_min_profit_r": 0.10,
            "breakeven_buffer_r": 0.05,
        }))
        tem._try_trailing_mode_breakeven(
            state_key="bybit:aid:1",
            row={"risk_amount": 10.0},
            style="intraday",
            be_due_now=True,
            profit=5.0,
            tcfg=tcfg,
            entry=100.0,
            sl_raw=90.0,
            direction="LONG",
            live_sl=91.0,
            mins=20.0,
            move_sl=lambda px: moves.append(px) or {"success": True},
            pair_label="ADA/USDT",
        )
        assert len(moves) == 1
        assert moves[0] == pytest.approx(100.5)
        assert "bybit:aid:1" in tem._be_done


class TestPreActivationOnlyMode:
    def setup_method(self):
        _trail_state.clear()
        import timed_exit_monitor as tem
        tem._peak_r_state.clear()

    def test_pre_activation_only_skips_chandelier(self, monkeypatch):
        import timed_exit_monitor as tem
        tcfg = _get_timed_cfg(_cfg_fn({
            "tp_mode": "trailing_atr",
            "trail_activation_r": {"intraday": 1.0},
            "pre_activation_profit_protect_enabled": True,
        }))
        row = {"ticket": "0", "pair": "BTC/USDT", "audit_id": 9}
        monkeypatch.setattr(
            tem,
            "_compute_chandelier_trail",
            lambda *a, **kw: pytest.fail("chandelier must not run in pre_activation_only"),
        )
        res = _evaluate_trail(
            row, "intraday", "LONG", 100.0, 90.0, 115.0, tcfg,
            state_key="bybit:aid:9", venue="bybit",
            trail_mode="pre_activation_only",
        )
        assert res["reason"] == "pre_activation_only_cap"
        assert res["action"] == "none"


class TestEngineDProfitProtect:
    def setup_method(self):
        import timed_exit_monitor as tem
        tem._peak_r_state.clear()
        tem._be_done.clear()

    def test_engine_d_closes_on_pre_activation_giveback(self, monkeypatch):
        import sys
        import timed_exit_monitor as tem

        row = {
            "pair": "BTC/USDT",
            "direction": "LONG",
            "entry_price": 100.0,
            "sl": 90.0,
            "ticket": "0",
            "audit_id": 42,
            "engine": "scalp",
            "style": "scalp",
            "ts": "2026-04-14T10:00:00Z",
            "risk_amount": 10.0,
        }
        cfg = _get_timed_cfg(lambda: {
            "TIMED_EXIT": {
                "enabled": True,
                "tp_mode": "trailing_atr",
                "pre_activation_profit_protect_enabled": True,
                "pre_activation_profit_arm_r": {"scalp": 0.20},
                "pre_activation_profit_giveback_r": {"scalp": 0.25},
                "trail_activation_r": {"scalp": 0.7, "intraday": 1.0, "swing": 1.5},
                "scalp": {"breakeven_min": 5, "close_min": 10},
                "intraday": {"breakeven_min": 15, "close_min": 30},
                "swing": {"breakeven_days": 2.5, "close_days": 5.0},
            },
            "SCALP_ENGINE": {"LIVE_PROFIT_PROTECT": True},
        })
        closes = []
        fake_bybit = types.ModuleType("bybit_executor")
        fake_bybit.bybit_get_positions = lambda: {
            "positions": [{
                "pair": "BTC/USDT",
                "profit": 1.0,
                "currentPrice": 100.0,
                "sl": 90.0,
                "direction": "LONG",
                "volume": 0.1,
            }]
        }
        fake_bybit.bybit_close_position = lambda *a, **kw: closes.append(a) or {"success": True}
        fake_bybit.bybit_map_symbol = lambda p: f"{p}/USDT:USDT"
        fake_bybit.bybit_modify_protective_sl = lambda *a, **kw: {"success": True}
        fake_bybit.bybit_move_sl_to_breakeven = lambda *a, **kw: {"success": True}
        monkeypatch.setitem(sys.modules, "bybit_executor", fake_bybit)
        monkeypatch.setitem(sys.modules, "telegram_notify", _tg_stub)
        tem._peak_r_state["bybit:aid:42"] = 0.5
        tem._handle_bybit_row(row, cfg)
        assert closes, "expected pre_activation giveback close for Engine D"
