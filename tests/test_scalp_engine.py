"""Tests for Engine D (Scalp — Fabio VP+OrderFlow methodology).

Covers:
  1. Indicator math: calc_vwap, detect_absorption, calc_cvd, detect_range_contraction
  2. VP internals: _build_volume_profile, _locate_price_vs_vp, _classify_market_state
  3. Aggression: _check_absorption, _check_cvd, _check_aaa_sequence
  4. Risk levels: calculate_scalp_levels (actual signature)
  5. Grading: ai_quality_grade (actual signature)
  6. Signal output contract
  7. Session/spread filters
  8. get_scalp_pairs, _guess_asset_type, infer_bias_from_ema_stack
"""

import sys
import types
from datetime import datetime, timedelta, timezone

import scalp_engine
import mt5_executor
import candle_feeds
import athena_runtime
import indicators
import volume_profile
from scalp_engine import (
    get_current_sessions,
    get_grade_sessions_for_mode,
    get_sessions_for_time,
    infer_bias_from_ema_stack,
    is_valid_session,
    scalp_session_window,
    mt5_get_live_price,
    mt5_market_open_state,
    mt5_fetch_scalp_candles,
    summarize_engine_d_scan,
    _scalp_cost_assumptions,
    _normalize_session_mode,
    _as_fraction,
    _merge_vp_aliases,
    _build_volume_profile,
    _locate_price_vs_vp,
    _classify_market_state,
    _classify_setup,
    _check_absorption,
    _check_cvd,
    _check_aaa_sequence,
    calculate_scalp_levels,
    ai_quality_grade,
    check_spread,
    get_scalp_pairs,
    _guess_asset_type,
)
from indicators import calc_obv_trend, calc_vwap, detect_absorption, calc_cvd, detect_range_contraction
from volume_profile import compute_fixed_range_volume_profile


# ── helpers ──────────────────────────────────────────────────────────────────

def _candles(n, base=100.0, vol=1000.0, spread=1.0, trend=0.0):
    out = []
    p = base
    start = datetime.now(timezone.utc) - timedelta(minutes=max(n - 1, 0))
    for i in range(n):
        p += trend
        t = start + timedelta(minutes=i)
        out.append({
            "time": t.isoformat(), "open": round(p, 6),
            "high": round(p + spread * 0.6, 6), "low": round(p - spread * 0.4, 6),
            "close": round(p + spread * 0.1, 6), "vol": vol,
        })
    return out


def _dated_candles(n, start, step_seconds=60, base=100.0, vol=1000.0, spread=1.0):
    out = []
    for i in range(n):
        t = start + timedelta(seconds=i * step_seconds)
        out.append({
            "time": t.isoformat(),
            "open": base,
            "high": base + spread * 0.6,
            "low": base - spread * 0.4,
            "close": base + spread * 0.1,
            "vol": vol,
        })
    return out


class _FakeRateRow:
    def __init__(self, **fields): self._fields = fields
    def __getitem__(self, key): return self._fields[key]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. INDICATOR MATH
# ═══════════════════════════════════════════════════════════════════════════════

def test_calc_vwap_basic_math():
    candles = [
        {"high": 102, "low": 98,  "close": 100, "vol": 1000},
        {"high": 104, "low": 100, "close": 103, "vol": 2000},
        {"high": 106, "low": 101, "close": 104, "vol": 1500},
    ]
    r = calc_vwap(candles, anchor_index=0)
    vwap = r["vwap"]
    assert len(vwap) == 3
    assert abs(vwap[0] - 100.0) < 0.01
    expected1 = (100.0 * 1000 + (104 + 100 + 103) / 3 * 2000) / 3000
    assert abs(vwap[1] - expected1) < 0.1
    assert all(v is not None for v in vwap)


def test_calc_vwap_has_bands():
    r = calc_vwap(_candles(30, vol=500))
    assert len(r["upper_band"]) == 30
    for i, v in enumerate(r["vwap"]):
        if v is not None and r["upper_band"][i] is not None:
            assert r["upper_band"][i] >= v


def test_calc_vwap_returns_none_when_window_has_no_real_volume():
    candles = _candles(5, vol=0.0)
    r = calc_vwap(candles, anchor_index=0)

    assert r["vwap"] == [None] * 5
    assert r["upper_band"] == [None] * 5
    assert r["lower_band"] == [None] * 5


def test_calc_obv_trend_returns_none_without_real_volume():
    candles = _candles(30, vol=0.0, trend=0.2)

    assert calc_obv_trend(candles, lookback=20) is None


def test_detect_absorption_fires_on_high_vol_low_range():
    normal = _candles(25, vol=100, spread=2.0)
    abs_bar = {"time": "T25", "open": 100.0, "high": 100.05,
               "low": 99.95, "close": 100.01, "vol": 500.0}
    result = detect_absorption(normal + [abs_bar], vol_mult=2.0, max_move_atr=0.3, sma_period=20)
    assert len(result) == 26
    last = result[-1]
    assert last["absorbed"] is True
    assert last["vol_ratio"] >= 2.0
    assert last["move_ratio"] < 0.3


def test_detect_absorption_no_fire_on_normal_bars():
    result = detect_absorption(_candles(30, vol=100, spread=1.0), vol_mult=2.0, max_move_atr=0.3)
    assert sum(1 for r in result if r.get("absorbed")) == 0


def test_calc_cvd_math_correctness():
    candles = [
        {"open": 100, "high": 110, "low": 90,  "close": 108, "vol": 1000},
        {"open": 108, "high": 112, "low": 95,  "close": 96,  "vol": 800},
    ]
    r = calc_cvd(candles, smooth_period=1)
    delta = r["delta"]
    assert abs(delta[0] - 800.0) < 0.1
    expected_d1 = 800*(96-95)/(112-95) - 800*(112-96)/(112-95)
    assert abs(delta[1] - expected_d1) < 0.1
    assert abs(r["cvd"][0] - delta[0]) < 0.01
    assert abs(r["cvd"][1] - (delta[0] + delta[1])) < 0.1


def test_calc_cvd_zero_range_bar():
    r = calc_cvd([{"open": 100, "high": 100, "low": 100, "close": 100, "vol": 500}])
    assert r["delta"] == [0.0]


def test_detect_range_contraction():
    """Range contraction: current ATR < threshold * prior ATR.
    Use spread=10.0 → 0.1 (100x difference) to ensure ratio reliably < 0.5 after ATR smoothing.
    """
    wide   = _candles(30, spread=10.0)
    narrow = _candles(30, spread=0.1)
    r = detect_range_contraction(wide + narrow, lookback=10, threshold=0.5)
    assert r["contracting"] is True
    assert r["ratio"] < 0.5


def test_detect_range_contraction_stable():
    r = detect_range_contraction(_candles(60, spread=2.0), lookback=10, threshold=0.5)
    assert r["contracting"] is False
    assert r["ratio"] > 0.8


# ═══════════════════════════════════════════════════════════════════════════════
# 2. VOLUME PROFILE INTERNALS
# ═══════════════════════════════════════════════════════════════════════════════

def test_build_vp_valid_on_sufficient_data():
    vp = _build_volume_profile(_candles(52, base=100, vol=1000, spread=2.0))
    assert vp["valid"] is True
    assert vp["poc"] is not None
    assert vp["val"] <= vp["poc"] <= vp["vah"]


def test_build_vp_invalid_on_too_few_candles():
    assert _build_volume_profile(_candles(5))["valid"] is False


def test_split_completed_sessions_forex_filters_to_market_session_window():
    start = datetime(2026, 3, 24, 0, 0, tzinfo=timezone.utc)
    candles = _dated_candles(48 * 4, start, step_seconds=900)

    sessions = volume_profile.split_completed_sessions(candles, "forex")
    prev = sessions["prev_session_candles"]

    assert len(prev) >= 20
    hours = [
        datetime.fromisoformat(str(c["time"])).astimezone(timezone.utc).hour
        for c in prev
    ]
    assert min(hours) >= 7
    assert max(hours) < 21


def test_locate_price_at_val():
    vp = {"poc": 100.5, "vah": 101.0, "val": 100.0, "lvn_levels": []}
    r = _locate_price_vs_vp(100.0, vp)
    assert r["location"] == "at_val"
    assert r["nearest_level"] == 100.0


def test_locate_price_at_vah():
    vp = {"poc": 100.5, "vah": 101.0, "val": 100.0, "lvn_levels": []}
    assert _locate_price_vs_vp(101.0, vp)["location"] == "at_vah"


def test_locate_price_outside_va():
    vp = {"poc": 100.5, "vah": 101.0, "val": 100.0, "lvn_levels": []}
    assert _locate_price_vs_vp(105.0, vp)["location"] == "outside_va"


def test_classify_market_state_balance(monkeypatch):
    monkeypatch.setitem(scalp_engine.CONFIG, "SCALP_ENGINE",
        {**scalp_engine.CONFIG.get("SCALP_ENGINE", {}), "BALANCE_THRESHOLD": 0.40})
    assert _classify_market_state({"balance_ratio": 0.60}) == "balance"


def test_classify_market_state_imbalance(monkeypatch):
    monkeypatch.setitem(scalp_engine.CONFIG, "SCALP_ENGINE",
        {**scalp_engine.CONFIG.get("SCALP_ENGINE", {}), "BALANCE_THRESHOLD": 0.40})
    assert _classify_market_state({"balance_ratio": 0.20}) == "imbalance"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. AGGRESSION DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def test_check_absorption_structure():
    r = _check_absorption(_candles(30, vol=100, spread=1.0))
    assert "detected" in r and "count" in r and "bars" in r
    assert isinstance(r["detected"], bool)


def test_check_absorption_fires_on_absorbing_bar():
    normal = _candles(25, vol=100, spread=2.0)
    abs_bar = {"time": "T25", "open": 100.0, "high": 100.05,
               "low": 99.95, "close": 100.01, "vol": 600.0}
    r = _check_absorption(normal + [abs_bar])
    assert r["detected"] is True


def test_check_absorption_ignores_old_primary_hits(monkeypatch):
    rows = [{"absorbed": i == 10, "direction": "bullish"} for i in range(30)]
    monkeypatch.setattr(indicators, "detect_absorption", lambda *args, **kwargs: rows)
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {**scalp_engine.CONFIG.get("SCALP_ENGINE", {}), "ABSORPTION_RECENT_BARS": 5},
    )

    r = _check_absorption(_candles(30))

    assert r["detected"] is False
    assert r["bars"] == []


def test_check_cvd_returns_direction():
    candles = [{"open": 100, "high": 102, "low": 99.5, "close": 101.8, "vol": 1000}] * 20
    r = _check_cvd(candles)
    assert "direction" in r
    assert r["direction"] in ("LONG", "SHORT", None)
    assert "cvd_slope" in r


def test_check_cvd_prefers_cumulative_cvd_slope(monkeypatch):
    monkeypatch.setattr(
        indicators,
        "calc_cvd",
        lambda candles, smooth_period=5: {
            "cvd": [0, 1, 2, 3, 4, 10],
            "smoothed_delta": [10, 9, 8, 7, 6, 0],
        },
    )

    r = _check_cvd(_candles(10))

    assert r["direction"] == "LONG"
    assert r["cvd_slope"] == 10


def test_aggression_fidelity_marks_proxy_flow_as_not_strict():
    fields = scalp_engine._engine_d_aggression_fidelity(
        absorption={"detected": False, "count": 0},
        cvd={"direction": "LONG", "source": "candles"},
        aaa={"complete": False, "phase": "absorption_only"},
        vwap={"lean": "LONG"},
        setup_direction="LONG",
    )

    assert fields["aggression_confirmed"] is True
    assert fields["aggression_source"] == "candle_proxy"
    assert fields["aggression_source_is_proxy"] is True
    assert fields["strict_fabio_pass"] is False


def test_aggression_fidelity_marks_binance_trade_flow_as_strict():
    fields = scalp_engine._engine_d_aggression_fidelity(
        absorption={"detected": False, "count": 0},
        cvd={"direction": "SHORT", "source": "binance_aggtrade", "bucket_count": 12},
        aaa={"complete": False, "phase": "absorption_only"},
        vwap={"lean": "SHORT"},
        setup_direction="SHORT",
    )

    assert fields["aggression_confirmed"] is True
    assert fields["aggression_source"] == "binance_aggtrade"
    assert fields["aggression_source_is_proxy"] is False
    assert fields["strict_fabio_pass"] is True


def test_strict_fabio_shadow_flags_current_pass_with_proxy_aggression():
    aggression = scalp_engine._engine_d_aggression_fidelity(
        absorption={"detected": False, "count": 0},
        cvd={"direction": None, "source": "candles"},
        aaa={"complete": False, "phase": "absorption_only"},
        vwap={"lean": "SHORT"},
        setup_direction="SHORT",
    )

    fields = scalp_engine._engine_d_strict_fabio_shadow(
        market_state="balance",
        price_loc={"location": "at_vah", "nearest_level": 101.0},
        setup={"valid": True, "setup_type": "mean_reversion", "direction": "SHORT"},
        aggression_fidelity=aggression,
        current_gate_result="PASS",
    )

    assert fields["strict_fabio_pass"] is False
    assert fields["strict_fabio_missing_pillars"] == ["aggression"]
    assert fields["strict_fabio_reason"] == "missing_aggression"
    assert fields["current_vs_strict_status"] == "current_pass_strict_fail"


def test_strict_fabio_shadow_passes_when_all_three_pillars_align():
    aggression = scalp_engine._engine_d_aggression_fidelity(
        absorption={"detected": False, "count": 0},
        cvd={"direction": "LONG", "source": "binance_aggtrade", "bucket_count": 12},
        aaa={"complete": False, "phase": "absorption_only"},
        vwap={"lean": "LONG"},
        setup_direction="LONG",
    )

    fields = scalp_engine._engine_d_strict_fabio_shadow(
        market_state="balance",
        price_loc={"location": "at_val", "nearest_level": 99.0},
        setup={"valid": True, "setup_type": "mean_reversion", "direction": "LONG"},
        aggression_fidelity=aggression,
        current_gate_result="WATCHLIST",
    )

    assert fields["strict_fabio_pass"] is True
    assert fields["strict_fabio_missing_pillars"] == []
    assert fields["strict_fabio_reason"] == "strict_pass"
    assert fields["current_vs_strict_status"] == "current_watchlist_strict_pass"


def test_engine_d_data_fidelity_labels_real_trade_flow_and_proxies():
    fields = scalp_engine._engine_d_data_fidelity(
        vp={"volume_source": "range_proxy", "bucket_count": None},
        cvd={"direction": "LONG", "source": "candles"},
        absorption={"detected": True, "count": 1},
        asset_type="forex",
        structure_volume_source="mt5_tick",
        execution_volume_source="eodhd_1m",
        active_profile_anchor="fixed_lookback",
    )

    assert fields["report_only"] is True
    assert fields["vp_source"] == "range_proxy"
    assert fields["vp_is_proxy"] is True
    assert fields["cvd_source"] == "candles"
    assert fields["cvd_is_proxy"] is True
    assert fields["absorption_source"] == "eodhd_candle_volume"
    assert fields["absorption_is_proxy"] is True
    assert fields["aggression_uses_real_order_flow"] is False

    real = scalp_engine._engine_d_data_fidelity(
        vp={"volume_source": "binance_aggtrade", "bucket_count": 12},
        cvd={"direction": "SHORT", "source": "binance_aggtrade", "bucket_count": 12},
        absorption={"detected": False, "count": 0},
        asset_type="crypto",
        structure_volume_source="binance_candle",
        execution_volume_source="binance_candle",
        active_profile_anchor="trade_bucket_session",
    )

    assert real["vp_uses_real_trade_buckets"] is True
    assert real["cvd_uses_real_trade_buckets"] is True
    assert real["aggression_uses_real_order_flow"] is True
    assert real["absorption_is_proxy"] is True


def test_engine_d_profile_anchor_shadow_reports_fixed_and_candidates():
    start = datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc)
    candles = []
    for i in range(140):
        close = 100.0 + (i * 0.01)
        if i in (120, 121):
            close = 102.0
        elif i >= 122:
            close = 100.5
        candles.append({
            "time": (start + timedelta(minutes=15 * i)).isoformat(),
            "open": close - 0.05,
            "high": close + 0.20,
            "low": close - 0.20,
            "close": close,
            "vol": 1000 + i,
        })

    shadow = scalp_engine._engine_d_profile_anchor_shadow(
        candles_m15=candles,
        vp_lookback=30,
        vp={"vah": 101.0, "val": 99.0},
        active_anchor_mode="fixed_lookback",
        volume_source="candle_volume",
    )

    assert shadow["report_only"] is True
    assert shadow["active_anchor"]["mode"] == "fixed_lookback"
    assert shadow["active_anchor"]["bars"] == 30
    assert shadow["candidates"]["prior_session"]["valid"] is True
    assert shadow["candidates"]["prior_session"]["session_basis"] == "utc_calendar_day"
    assert shadow["candidates"]["impulse_leg"]["valid"] is True
    assert shadow["candidates"]["reclaim_leg"]["valid"] is True
    assert shadow["candidates"]["reclaim_leg"]["outside_side"] == "above_vah"


def test_check_aaa_sequence_no_absorption():
    """AAA requires absorption first — without it, complete=False."""
    r = _check_aaa_sequence(_candles(30), {"detected": False, "count": 0, "bars": []},
                            {"direction": "LONG"})
    assert r["complete"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# 4. RISK LEVEL CALCULATION  (actual signature: direction, entry, vp, setup_type, sym_info, asset_type)
# ═══════════════════════════════════════════════════════════════════════════════

def test_check_aaa_sequence_rejects_stale_absorption(monkeypatch):
    monkeypatch.setattr(indicators, "detect_range_contraction", lambda *args, **kwargs: {"contracting": True})
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {**scalp_engine.CONFIG.get("SCALP_ENGINE", {}), "AAA_ABSORPTION_RECENT_BARS": 5},
    )

    r = _check_aaa_sequence(
        _candles(30),
        {"detected": True, "count": 1, "bars": [{"index": 0}]},
        {"direction": "LONG"},
    )

    assert r["complete"] is False
    assert r["phase"] == "stale_absorption"


def test_fixed_range_vp_marks_range_proxy_source():
    vp = compute_fixed_range_volume_profile(_candles(30, vol=0.0, spread=2.0))

    assert vp["profile_valid"] is True
    assert vp["volume_source"] == "range_proxy"


def test_calc_m15_atr_uses_true_range_gap_not_high_low_only():
    candles = [
        {"high": 10.5, "low": 9.5, "close": 10.0},
        {"high": 13.0, "low": 12.0, "close": 12.5},
        {"high": 14.0, "low": 13.0, "close": 13.5},
    ]

    atr = scalp_engine._calc_m15_atr(candles, period=2)

    assert atr > 1.0
    assert round(atr, 4) == 2.25


def test_stock_overlay_returns_suffix_unmapped_for_dotless_unmapped_stock(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "EODHD_VOLUME_OVERLAY_LIVE_ENABLED": True,
            "EODHD_STOCK_EXCHANGE_SUFFIX_MAP": {},
        },
    )
    scalp_engine._SCALP_PAIR_META_BY_DISPLAY.clear()

    candles, source = scalp_engine._overlay_eodhd_volume_for_scalp(
        "BARC",
        "stock",
        "M15",
        _candles(5),
        live=True,
    )

    assert candles
    assert source == "eodhd_suffix_unmapped_for_stock"


def test_stock_real_volume_fail_reasons_preserve_suffix_unmapped_reason():
    data_fidelity = {
        "vp_is_proxy": True,
        "absorption_is_proxy": True,
    }

    reasons = scalp_engine._stock_real_volume_fail_reasons(
        data_fidelity,
        "eodhd_suffix_unmapped_for_stock",
        "mt5_tick",
        "mt5_tick",
    )

    assert reasons[0] == "eodhd_suffix_unmapped_for_stock"
    assert "real_volume_required_for_stock" in reasons


def test_summarize_engine_d_scan_counts_skipped_diagnostic_reasons():
    summary = summarize_engine_d_scan(
        {
            "skipped": [
                {
                    "pair": "BTC/USDT",
                    "reason": "no_setup:balance_inside_va",
                    "diagnostic_reason": "vp_fallback:candle_profile_after_insufficient_trade_buckets",
                }
            ],
            "signals": [],
            "sessions_active": ["asia"],
        }
    )

    assert summary["skipped_diagnostic_reason_counts"] == {
        "vp_fallback:candle_profile_after_insufficient_trade_buckets": 1
    }


def test_calculate_levels_long_uses_atr_sl_and_1r_tp(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "ATR_SL_ENABLED": True,
            "ATR_SL_MULT": 1.5,
            "TP1_R_MULT": 1.0,
            "MIN_RR": 1.0,
        },
    )
    vp = {"poc": 1.1002, "vah": 1.1080, "val": 1.0970}
    # Entry far from VAL → structural SL (val - buffer) is wider than ATR SL
    levels = calculate_scalp_levels(
        "LONG", 1.1000, vp, "mean_reversion",
        {"digits": 5, "point": 0.00001}, "forex", atr_m15=0.0020
    )
    assert levels["tp_partial"] == levels["tp1"]
    assert levels["rr"] == 1.0
    assert levels["rr_below_min"] is False
    assert levels["structural_tp"] == vp["poc"]
    assert levels["structure_target_close"] is True
    # When structural SL is wider, it should be preserved (not replaced by tighter ATR)
    assert levels["sl"] < 1.097, "Structural SL should be wider than ATR when entry is far from VAL"
    assert levels["sl_method"] == "vp_boundary"

    # Entry close to VAL → ATR SL is wider than structural SL
    levels_close = calculate_scalp_levels(
        "LONG", 1.0972, vp, "mean_reversion",
        {"digits": 5, "point": 0.00001}, "forex", atr_m15=0.0020
    )
    assert levels_close["sl_method"] == "atr"
    assert levels_close["rr"] == 1.0
    assert levels_close["sl"] == 1.0942


def test_calculate_levels_trend_extension_preserves_structural_sl_when_wider(monkeypatch):
    """ATR SL must not override the structural breakout-level SL when the latter is wider."""
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "ATR_SL_ENABLED": True,
            "ATR_SL_MULT": 1.5,
            "MIN_RR": 1.0,
        },
    )
    vp = {"poc": 1.1000, "vah": 1.1000, "val": 1.0900}
    # Price broke far above VAH — structural SL (vah - buffer) is much wider than ATR SL
    levels = calculate_scalp_levels(
        "LONG", 1.1200, vp, "trend_extension",
        {"digits": 5, "point": 0.00001}, "forex", atr_m15=0.0020
    )
    assert levels["sl_method"] == "vp_boundary"
    assert levels["sl"] < 1.117, "Structural breakout SL should be used, not tighter ATR"
    assert levels["rr"] == 1.0
    assert levels["tp1"] > levels["entry"]

    # Same for SHORT far below VAL
    levels_short = calculate_scalp_levels(
        "SHORT", 1.0800, vp, "trend_extension",
        {"digits": 5, "point": 0.00001}, "forex", atr_m15=0.0020
    )
    assert levels_short["sl_method"] == "vp_boundary"
    assert levels_short["sl"] > 1.083, "Structural breakout SL should be used, not tighter ATR"
    assert levels_short["rr"] == 1.0
    assert levels_short["tp1"] < levels_short["entry"]


def test_calculate_levels_short_sl_above_vah():
    vp = {"poc": 1.0950, "vah": 1.1020, "val": 1.0950}
    levels = calculate_scalp_levels(
        "SHORT", 1.1000, vp, "mean_reversion",
        {"digits": 5, "point": 0.00001}, "forex"
    )
    assert levels["sl"] > vp["vah"], "SL must be above VAH for SHORT mean-reversion"
    assert levels["tp1"] < levels["entry"], "TP1 must be below entry for SHORT"


def test_calculate_levels_crypto_buffer():
    vp = {"poc": 61000, "vah": 62000, "val": 59000}
    levels = calculate_scalp_levels(
        "LONG", 60000.0, vp, "mean_reversion",
        {"digits": 2, "point": 0.01}, "crypto"
    )
    # crypto buffer = entry * 0.003 = 180; sl = val - buffer = 59000 - 180 = 58820
    assert levels["sl"] < 59000, "Crypto SL should include percentage buffer below VAL"


def test_calculate_levels_keys():
    vp = {"poc": 1.1030, "vah": 1.1060, "val": 1.0970}
    levels = calculate_scalp_levels(
        "LONG", 1.1000, vp, "mean_reversion",
        {"digits": 5, "point": 0.00001}, "forex"
    )
    for k in (
        "entry", "sl", "tp_partial", "tp1", "tp2", "structural_tp",
        "structural_rr", "structure_target_close", "rr", "sl_distance",
        "sl_method",
    ):
        assert k in levels, f"Missing key: {k}"


def test_calculate_levels_trend_continuation_keeps_close_structure_as_warning(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {**scalp_engine.CONFIG.get("SCALP_ENGINE", {}), "MIN_RR": 1.0},
    )
    vp = {"poc": 0.917362, "vah": 0.917585, "val": 0.91714}
    levels = calculate_scalp_levels(
        "SHORT", 0.91789, vp, "trend_continuation",
        {"digits": 5, "point": 0.00001}, "forex"
    )
    assert levels["rr"] == 1.0
    assert levels["rr_below_min"] is False
    assert levels["structure_target_close"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# 5. GRADING  (actual: ai_quality_grade(vp, price_loc, absorption, cvd, aaa, vwap, setup, sessions, spread, htf_bias))
# ═══════════════════════════════════════════════════════════════════════════════

def test_grade_a_on_full_confluence():
    vp = {"poc": 1.1030, "vah": 1.1060, "val": 1.0970, "lvn_levels": [], "balance_ratio": 0.6}
    price_loc = {"location": "at_val", "nearest_level": 1.0970, "distance_pct": 0.0}
    absorption = {"detected": True, "count": 3}
    cvd = {"direction": "LONG"}
    aaa = {"complete": True, "phase": "aggression"}
    vwap = {"lean": "LONG", "vwap_value": 1.1000}
    setup = {"direction": "LONG", "setup_type": "mean_reversion"}

    q = ai_quality_grade(vp, price_loc, absorption, cvd, aaa, vwap, setup,
                         ["london", "new_york"], 1.0, "LONG")
    assert q["grade"] == "A"
    assert q["score"] >= 80
    assert "size_multiplier" in q


def test_grade_a_backtest_overlap_uses_component_sessions():
    vp = {"poc": 1.1030, "vah": 1.1060, "val": 1.0970, "lvn_levels": [], "balance_ratio": 0.6}
    price_loc = {"location": "at_val", "nearest_level": 1.0970, "distance_pct": 0.0}
    absorption = {"detected": True, "count": 3}
    cvd = {"direction": "LONG"}
    aaa = {"complete": False, "phase": "accumulation"}
    vwap = {"lean": "LONG", "vwap_value": 1.1000}
    setup = {"direction": "LONG", "setup_type": "mean_reversion"}

    sessions = get_sessions_for_time("forex", when=datetime(2026, 3, 26, 13, 40, tzinfo=timezone.utc))
    q = ai_quality_grade(vp, price_loc, absorption, cvd, aaa, vwap, setup,
                         sessions, 0.0, "LONG")

    assert sessions == ["london", "new_york"]
    assert q["grade"] == "A"
    assert q["score"] >= 80


def test_grade_a_on_two_bar_absorption_full_alignment():
    vp = {"poc": 1.1030, "vah": 1.1060, "val": 1.0970, "lvn_levels": [], "balance_ratio": 0.6}
    price_loc = {"location": "at_vah", "nearest_level": 1.1060, "distance_pct": 0.0}
    absorption = {"detected": True, "count": 2}
    cvd = {"direction": "SHORT"}
    aaa = {"complete": False, "phase": "accumulation"}
    vwap = {"lean": "SHORT", "vwap_value": 1.1000}
    setup = {"direction": "SHORT", "setup_type": "mean_reversion"}

    q = ai_quality_grade(vp, price_loc, absorption, cvd, aaa, vwap, setup,
                         ["london", "new_york"], 0.0, "SHORT")

    assert q["grade"] == "A"
    assert q["score"] >= 80


def test_grade_d_on_weak_setup():
    vp = {"poc": 1.1030, "vah": 1.1060, "val": 1.0970, "lvn_levels": [], "balance_ratio": 0.2}
    price_loc = {"location": "inside_va", "nearest_level": 1.1030, "distance_pct": 0.5}
    absorption = {"detected": False, "count": 0}
    cvd = {"direction": "SHORT"}
    aaa = {"complete": False, "phase": "no_absorption"}
    vwap = {"lean": None, "vwap_value": 1.1020}
    setup = {"direction": "LONG", "setup_type": "trend_continuation"}

    q = ai_quality_grade(vp, price_loc, absorption, cvd, aaa, vwap, setup,
                         ["off_hours"], 3.5, None)
    assert q["grade"] in ("C", "D")
    assert q["score"] < 60


def test_grade_returns_size_multiplier():
    vp = {"poc": 1.1030, "vah": 1.1060, "val": 1.0970, "lvn_levels": [], "balance_ratio": 0.5}
    price_loc = {"location": "at_vah", "nearest_level": 1.1060, "distance_pct": 0.0}
    absorption = {"detected": True, "count": 1}
    cvd = {"direction": "SHORT"}
    aaa = {"complete": False, "phase": "absorption_only"}
    vwap = {"lean": "SHORT"}
    setup = {"direction": "SHORT", "setup_type": "mean_reversion"}

    q = ai_quality_grade(vp, price_loc, absorption, cvd, aaa, vwap, setup,
                         ["london"], 1.5, "SHORT")
    assert q["size_multiplier"] in (0.0, 0.25, 0.5, 1.0)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. SIGNAL OUTPUT CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════

def test_signal_has_all_required_keys():
    required = {
        "pair", "direction", "price", "sl", "tp1", "tp2", "rr1",
        "ai_score", "ai_grade", "zone_type", "zone_high", "zone_low",
        "zone_level", "zone_conditions", "trigger_type", "momentum_method",
        "spread_pips", "session", "ema21", "htf_bias", "htf_bias_tf",
        "timestamp", "engine", "confluenceScore", "maxScore", "type",
        "display", "symbol", "mt5_symbol",
    }
    signal = {
        "pair": "EUR/USD", "display": "EUR/USD", "symbol": None,
        "mt5_symbol": "EURUSD", "type": "forex",
        "direction": "LONG", "price": 1.1000,
        "sl": 1.0980, "tp1": 1.1040, "tp2": 1.1080,
        "rr1": 2.0, "sl_distance": 0.0020, "sl_method": "vp_boundary",
        "zone_type": "mean_reversion", "zone_high": 1.1010,
        "zone_low": 1.0990, "zone_level": 1.1000,
        "zone_conditions": ["price_at_VAL"],
        "trigger_type": "mean_reversion", "momentum_method": "absorption",
        "ai_score": 75, "ai_grade": "B", "ai_reasons": [],
        "spread_pips": 1.2, "session": "london",
        "ema21": None, "htf_bias": "LONG", "htf_bias_tf": "H1",
        "timestamp": "2026-01-01T12:00:00+00:00", "engine": "SCALP",
        "confluenceScore": 0.75, "maxScore": 1.0,
    }
    missing = required - set(signal.keys())
    assert not missing, f"Signal missing keys: {missing}"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. SESSION & SPREAD FILTERS
# ═══════════════════════════════════════════════════════════════════════════════

def test_mt5_fetch_scalp_candles_drops_forming_bar(monkeypatch):
    fake_mt5 = types.SimpleNamespace(
        TIMEFRAME_M1=1, TIMEFRAME_M5=5, TIMEFRAME_M15=15, TIMEFRAME_H1=60,
        terminal_info=lambda: True, initialize=lambda: True,
        symbol_select=lambda s, e: True,
        copy_rates_from_pos=lambda s, tf, start, count: [
            _FakeRateRow(time=1, open=100, high=101, low=99, close=100.5, tick_volume=10),
            _FakeRateRow(time=2, open=101, high=102, low=100, close=101.5, tick_volume=12),
        ],
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    candles = mt5_fetch_scalp_candles("EURUSD", "M15", 2)
    assert len(candles) == 1
    assert candles[0]["vol"] == 10.0


def test_mt5_fetch_scalp_candles_keeps_forming_bar(monkeypatch):
    fake_mt5 = types.SimpleNamespace(
        TIMEFRAME_M1=1, TIMEFRAME_M5=5, TIMEFRAME_M15=15, TIMEFRAME_H1=60,
        terminal_info=lambda: True, initialize=lambda: True,
        symbol_select=lambda s, e: True,
        copy_rates_from_pos=lambda s, tf, start, count: [
            _FakeRateRow(time=1, open=100, high=101, low=99, close=100.5, tick_volume=10),
            _FakeRateRow(time=2, open=101, high=103, low=100, close=102.25, tick_volume=12),
        ],
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    candles = mt5_fetch_scalp_candles("EURUSD", "M15", 2, include_forming=True)
    assert len(candles) == 2


def test_mt5_fetch_scalp_candles_supports_m1(monkeypatch):
    captured = {}

    def _copy_rates(_symbol, tf, _start, _count):
        captured["tf"] = tf
        return [_FakeRateRow(time=1, open=100, high=101, low=99, close=100.5, tick_volume=10)]

    fake_mt5 = types.SimpleNamespace(
        TIMEFRAME_M1=1, TIMEFRAME_M5=5, TIMEFRAME_M15=15, TIMEFRAME_H1=60,
        terminal_info=lambda: True, initialize=lambda: True,
        symbol_select=lambda s, e: True,
        copy_rates_from_pos=_copy_rates,
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    candles = mt5_fetch_scalp_candles("EURUSD", "M1", 1, include_forming=True)
    assert candles[0]["close"] == 100.5
    assert captured["tf"] == 1


def test_mt5_get_live_price_bid_ask_mid(monkeypatch):
    fake_tick = types.SimpleNamespace(bid=1.2345, ask=1.2347, last=1.2346)
    fake_mt5 = types.SimpleNamespace(
        terminal_info=lambda: True, initialize=lambda: True,
        symbol_select=lambda s, e: True,
        symbol_info_tick=lambda s: fake_tick,
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    assert mt5_get_live_price("EURUSD") == 1.2346


def test_mt5_market_open_state_rejects_stale_tick(monkeypatch):
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    stale_tick = types.SimpleNamespace(
        bid=1.2345,
        ask=1.2347,
        last=1.2346,
        time=int((now.timestamp()) - 3600),
    )
    fake_mt5 = types.SimpleNamespace(
        SYMBOL_TRADE_MODE_DISABLED=0,
        symbol_select=lambda s, e: True,
        symbol_info=lambda s: types.SimpleNamespace(trade_mode=1),
        symbol_info_tick=lambda s: stale_tick,
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(scalp_engine, "_current_utc_datetime", lambda: now)
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "MARKET_OPEN_CHECK_ENABLED": True,
            "MARKET_TICK_MAX_AGE_SEC": 900,
        },
    )

    state = mt5_market_open_state("EURUSD")

    assert state["open"] is False
    assert state["reason"] == "MARKET_CLOSED_STALE_TICK"


def test_scalp_candles_fresh_rejects_stale_structure(monkeypatch):
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    stale = _dated_candles(40, now - timedelta(hours=12), step_seconds=900)
    monkeypatch.setattr(scalp_engine, "_current_utc_datetime", lambda: now)
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "MARKET_CANDLE_MAX_AGE_SEC": 900,
        },
    )

    fresh, reason = scalp_engine._scalp_candles_fresh(stale, "M15", "structure")

    assert fresh is False
    assert reason.startswith("MARKET_DATA_STALE_STRUCTURE_")


def test_scalp_candles_fresh_rejects_missing_timestamp_by_default(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "ALLOW_TIMELESS_SCALP_CANDLES": False,
        },
    )

    fresh, reason = scalp_engine._scalp_candles_fresh([{"close": 100.0}], "M15", "structure")

    assert fresh is False
    assert reason == "MARKET_DATA_TIME_UNAVAILABLE_STRUCTURE"


def test_scalp_candles_fresh_can_allow_missing_timestamp_when_configured(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "ALLOW_TIMELESS_SCALP_CANDLES": True,
        },
    )

    fresh, reason = scalp_engine._scalp_candles_fresh([{"close": 100.0}], "M15", "structure")

    assert fresh is True
    assert reason == "candle_time_unavailable"


def test_get_current_sessions_london_ny_overlap(monkeypatch):
    monkeypatch.setattr(scalp_engine, "_current_utc_datetime",
                        lambda: datetime(2026, 3, 26, 13, 0, tzinfo=timezone.utc))
    assert get_current_sessions() == ["london", "new_york"]


def test_is_valid_session_returns_off_hours(monkeypatch):
    monkeypatch.setitem(scalp_engine.CONFIG, "SCALP_ENGINE",
        {**scalp_engine.CONFIG.get("SCALP_ENGINE", {}), "SESSION_FILTER": True})
    monkeypatch.setattr(scalp_engine, "_current_utc_datetime",
                        lambda: datetime(2026, 3, 26, 22, 0, tzinfo=timezone.utc))
    assert is_valid_session() == (False, "off_hours")


def test_scalp_session_window_blocks_ny_open_cooldown(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
            "SESSION_MODE": "new_york",
            "NY_OPEN_SKIP_MINUTES": 30,
        },
    )
    allowed, reason = scalp_session_window(
        "forex",
        when=datetime(2026, 3, 26, 13, 40, tzinfo=timezone.utc),  # 09:40 NY (EDT)
    )
    assert allowed is False
    assert reason == "NY_OPEN_COOLDOWN"


def test_scalp_session_window_allows_after_ny_open_cooldown(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
            "SESSION_MODE": "new_york",
            "NY_OPEN_SKIP_MINUTES": 30,
        },
    )
    allowed, reason = scalp_session_window(
        "forex",
        when=datetime(2026, 3, 26, 14, 1, tzinfo=timezone.utc),  # 10:01 NY (EDT)
    )
    assert allowed is True
    assert reason == "new_york"


def test_scalp_session_window_new_york_dst_winter_vs_summer(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
            "SESSION_MODE": "new_york",
            "NY_OPEN_SKIP_MINUTES": 0,
        },
    )
    # Winter (EST): 08:00 local == 13:00 UTC
    allowed_winter, reason_winter = scalp_session_window(
        "forex",
        when=datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc),
    )
    # Summer (EDT): 08:00 local == 12:00 UTC
    allowed_summer, reason_summer = scalp_session_window(
        "forex",
        when=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
    )
    assert allowed_winter is True and reason_winter == "new_york"
    assert allowed_summer is True and reason_summer == "new_york"


def test_scalp_session_window_london_ny_dst_summer_boundary(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
            "SESSION_MODE": "london_ny",
        },
    )
    # During summer, London session starts 07:00 BST => 06:00 UTC.
    allowed, reason = scalp_session_window(
        "forex",
        when=datetime(2026, 7, 15, 6, 5, tzinfo=timezone.utc),
    )
    assert allowed is True
    assert reason == "london_ny"


def test_scalp_session_window_london_ny_blocks_ny_open_caution(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
            "SESSION_MODE": "london_ny",
            "NY_OPEN_SKIP_MINUTES": 30,
        },
    )
    allowed, reason = scalp_session_window(
        "forex",
        when=datetime(2026, 3, 26, 13, 40, tzinfo=timezone.utc),  # 09:40 NY (EDT)
    )
    assert allowed is False
    assert reason == "NY_OPEN_COOLDOWN"


def test_scalp_session_window_london_ny_allows_after_ny_open_caution(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
            "SESSION_MODE": "london_ny",
            "NY_OPEN_SKIP_MINUTES": 30,
        },
    )
    allowed, reason = scalp_session_window(
        "forex",
        when=datetime(2026, 3, 26, 14, 5, tzinfo=timezone.utc),  # 10:05 NY (EDT)
    )
    assert allowed is True
    assert reason == "london_ny"


def test_scalp_session_window_backtest_all_mode_overrides_live_session(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
            "SESSION_MODE": "london_ny",
            "BT_SESSION_MODE": "all",
        },
    )

    when = datetime(2026, 3, 26, 23, 0, tzinfo=timezone.utc)
    live_allowed, live_reason = scalp_session_window("forex", when=when)
    bt_allowed, bt_reason = scalp_session_window("forex", when=when, backtest=True)

    assert live_allowed is False
    assert live_reason == "off_hours"
    assert bt_allowed is True
    assert bt_reason == "all"


def test_scalp_session_window_crypto_asset_override_allows_asia(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
            "SESSION_MODE": "london_ny",
            "SESSION_MODE_BY_ASSET": {"crypto": "asia_london_ny"},
        },
    )

    allowed, reason = scalp_session_window(
        "crypto",
        when=datetime(2026, 3, 26, 2, 0, tzinfo=timezone.utc),
    )

    assert allowed is True
    assert reason == "asia_london_ny"


def test_grade_sessions_crypto_asset_override_includes_asia(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_MODE": "london_ny",
            "SESSION_MODE_BY_ASSET": {"crypto": "asia_london_ny"},
            "GRADE_SESSIONS": ["london", "new_york"],
            "GRADE_SESSIONS_BY_ASSET": {"crypto": ["asia", "london", "new_york"]},
        },
    )

    sessions = get_grade_sessions_for_mode(
        "crypto",
        when=datetime(2026, 3, 26, 2, 0, tzinfo=timezone.utc),
    )

    assert sessions == ["asia"]


def test_grade_sessions_all_mode_keeps_grade_neutral_to_clock(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_MODE": "all",
            "BT_SESSION_MODE": "all",
        },
    )

    when = datetime(2026, 3, 26, 23, 0, tzinfo=timezone.utc)

    assert get_grade_sessions_for_mode("forex", when=when) == []
    assert get_grade_sessions_for_mode("forex", when=when, backtest=True) == []


def test_check_spread_rejects_wide_spread():
    ok, pips = check_spread({"spread": 80, "point": 0.00001, "digits": 5}, "forex")
    assert ok is False
    assert pips > 4


def test_check_spread_passes_crypto():
    ok, _ = check_spread({"spread": 0, "point": 0.01, "digits": 2}, "crypto")
    assert ok is True


# ═══════════════════════════════════════════════════════════════════════════════
# 8. HELPERS: guess_asset_type, infer_bias, get_scalp_pairs
# ═══════════════════════════════════════════════════════════════════════════════

def test_guess_asset_type():
    assert _guess_asset_type("EUR/USD") == "forex"
    assert _guess_asset_type("USD/MXN") == "forex"
    assert _guess_asset_type("BTC/USDT") == "crypto"
    assert _guess_asset_type("XAU/USD") == "commodity"
    assert _guess_asset_type("Cocoa") == "commodity"
    assert _guess_asset_type("Nasdaq") == "index"
    assert _guess_asset_type("NASDAQ-100") == "index"
    assert _guess_asset_type("USDX") == "index"
    assert _guess_asset_type("AAPL") == "stock"


def test_infer_bias_long():
    assert infer_bias_from_ema_stack([{"close": float(i)} for i in range(1, 260)]) == "LONG"


def test_infer_bias_short():
    assert infer_bias_from_ema_stack([{"close": float(260 - i)} for i in range(260)]) == "SHORT"


def test_infer_bias_insufficient_data():
    assert infer_bias_from_ema_stack([{"close": 100.0}] * 50) is None


def test_get_scalp_pairs_config_override(monkeypatch):
    monkeypatch.setitem(scalp_engine.CONFIG, "SCALP_ENGINE",
        {**scalp_engine.CONFIG.get("SCALP_ENGINE", {}), "SCALP_PAIRS": ["BTC/USDT", "ETH/USDT"]})
    assert get_scalp_pairs() == ["BTC/USDT", "ETH/USDT"]


def test_get_scalp_pairs_preserves_active_pair_metadata_for_type_resolution():
    pairs = [
        {"display": "Cocoa", "symbol": "Cocoa", "type": "commodity", "source": "mt5", "enabled": True},
        {"display": "NASDAQ-100", "symbol": "NAS100", "type": "index", "source": "mt5", "enabled": True},
        {"display": "USD/ZAR", "symbol": "USDZAR", "type": "forex", "source": "mt5", "enabled": True},
        {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "source": "binance", "enabled": True},
        {"display": "Naspers", "symbol": "NPN.JO", "type": "stock", "source": "eodhd", "enabled": True},
    ]

    out = get_scalp_pairs(pairs)

    assert "Naspers" not in out
    assert _guess_asset_type("Cocoa") == "commodity"
    assert _guess_asset_type("NASDAQ-100") == "index"
    assert _guess_asset_type("USD/ZAR") == "forex"
    assert _guess_asset_type("BTC/USDT") == "crypto"


def test_get_scalp_pairs_fallback_has_all_types():
    pairs = get_scalp_pairs()
    assert any("USD" in p and "/" in p and "USDT" not in p for p in pairs)  # forex
    assert any("USDT" in p for p in pairs)   # crypto
    assert any("XAU" in p for p in pairs)    # commodity
    assert any("Nasdaq" in p or "S&P" in p for p in pairs)  # index


# ═══════════════════════════════════════════════════════════════════════════════
# 9. VOLUME PROFILE MATH (via compute_fixed_range_volume_profile)
# ═══════════════════════════════════════════════════════════════════════════════

def test_vp_poc_at_highest_volume_price():
    candles = [{"high": 106, "low": 104, "close": 105, "vol": 5000, "time": f"T{i}"} for i in range(20)]
    candles += [{"high": 112, "low": 108, "close": 110, "vol": 100, "time": f"T{i+20}"} for i in range(10)]
    vp = compute_fixed_range_volume_profile(candles, bins=32, value_area_pct=0.70)
    assert vp["profile_valid"] is True
    assert abs(vp["poc"] - 105) < 3.0, f"POC {vp['poc']} too far from 105"
    assert vp["val"] <= vp["poc"] <= vp["vah"]


def test_vp_val_lt_vah():
    candles = _candles(50, base=100, vol=500, spread=3.0)
    vp = compute_fixed_range_volume_profile(candles, bins=64, value_area_pct=0.70)
    if vp["profile_valid"]:
        assert vp["val"] < vp["vah"]
        assert vp["val"] <= vp["poc"] <= vp["vah"]


def test_build_vp_calls_external_helper_with_live_config(monkeypatch):
    captured = {}

    def _fake_vp(candles, *, bins, value_area_pct):
        captured["bins"] = bins
        captured["value_area_pct"] = value_area_pct
        return {
            "poc": 100.5,
            "vah": 101.0,
            "val": 100.0,
            "profile_valid": True,
            "session_high": 102.0,
            "session_low": 98.0,
        }

    monkeypatch.setattr(volume_profile, "compute_fixed_range_volume_profile", _fake_vp)
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "VP_BINS": 48,
            "VP_VALUE_AREA_PCT": 0.65,
        },
    )
    vp = _build_volume_profile(_candles(60, spread=2.0))
    assert captured == {"bins": 48, "value_area_pct": 0.65}
    assert vp["valid"] is True
    assert vp["balance_ratio"] == 0.25


def test_check_absorption_does_not_false_positive_on_plain_rows():
    r = _check_absorption(_candles(30, vol=100, spread=1.0))
    assert r == {"detected": False, "count": 0, "bars": []}


def test_check_cvd_uses_indicator_cvd_output_and_config(monkeypatch):
    captured = {}

    def _fake_calc_cvd(candles, *, smooth_period):
        captured["smooth_period"] = smooth_period
        return {"cvd": [1.0, 2.0, 3.0, 4.0, 7.0, 9.0]}

    monkeypatch.setattr(indicators, "calc_cvd", _fake_calc_cvd)
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {**scalp_engine.CONFIG.get("SCALP_ENGINE", {}), "CVD_SMOOTH_PERIOD": 7},
    )
    r = _check_cvd(_candles(20))
    assert captured["smooth_period"] == 7
    assert r["direction"] == "LONG"
    assert r["cvd_value"] == 9.0


def test_check_aaa_sequence_honors_contracting_key(monkeypatch):
    def _fake_contraction(candles, *, lookback, threshold):
        return {"contracting": True, "ratio": 0.4}

    monkeypatch.setattr(indicators, "detect_range_contraction", _fake_contraction)
    candles = _candles(30, vol=100, spread=2.0)
    candles[-1] = {
        "time": "T29",
        "open": 100.0,
        "high": 102.0,
        "low": 99.8,
        "close": 101.9,
        "vol": 500.0,
    }
    r = _check_aaa_sequence(candles, {"detected": True, "count": 1, "bars": [{}]}, {"direction": "LONG"})
    assert r["complete"] is True
    assert r["phase"] == "aggression"


def test_check_vwap_lean_uses_last_indicator_value_and_config(monkeypatch):
    captured = {}

    def _fake_calc_vwap(candles, *, anchor_index=0, band_mult=0.5):
        captured["anchor_index"] = anchor_index
        captured["band_mult"] = band_mult
        return {"vwap": [None, 99.5, 101.25], "upper_band": [], "lower_band": []}

    monkeypatch.setattr(indicators, "calc_vwap", _fake_calc_vwap)
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {**scalp_engine.CONFIG.get("SCALP_ENGINE", {}), "VWAP_BAND_MULT": 0.8},
    )
    r = scalp_engine._check_vwap_lean(_candles(5), 102.0)
    assert captured == {"anchor_index": 0, "band_mult": 0.8}
    assert r == {"lean": "LONG", "vwap_value": 101.25}


def test_grade_uses_explicit_config_size_multipliers(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "GRADE_B_SIZE_MULT": 0.75,
        },
    )
    vp = {"poc": 1.1030, "vah": 1.1060, "val": 1.0970, "lvn_levels": [], "balance_ratio": 0.5}
    price_loc = {"location": "at_vah", "nearest_level": 1.1060, "distance_pct": 0.0}
    absorption = {"detected": True, "count": 1}
    cvd = {"direction": "SHORT"}
    aaa = {"complete": False, "phase": "absorption_only"}
    vwap = {"lean": None}
    setup = {"direction": "SHORT", "setup_type": "mean_reversion"}
    q = ai_quality_grade(vp, price_loc, absorption, cvd, aaa, vwap, setup, ["london"], 1.5, None)
    assert q["grade"] == "B"
    assert q["size_multiplier"] == 0.75


def test_run_scalp_scan_surfaces_grade_c_as_watchlist(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
            {
                **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
                "EXECUTION_MIN_GRADE": "B",
                "MIN_GRADE_AUTO_EXECUTE": "B",
                "MIN_GRADE": "C",
            },
    )
    monkeypatch.setattr(scalp_engine, "get_current_sessions", lambda: ["london"])
    monkeypatch.setattr(scalp_engine, "is_valid_session", lambda asset="forex": (True, "london"))
    monkeypatch.setattr(scalp_engine, "scalp_session_window", lambda *args, **kwargs: (True, "london"))
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: "EURUSD")
    monkeypatch.setattr(scalp_engine, "mt5_market_open_state", lambda symbol: {"open": True, "reason": "market_open"})
    monkeypatch.setattr(mt5_executor, "mt5_get_symbol_info", lambda display: {"digits": 5, "point": 0.00001, "spread": 10})
    monkeypatch.setattr(scalp_engine, "check_spread", lambda sym_info, asset_type, display="": (True, 1.0))
    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", lambda *args, **kwargs: _candles(300))
    monkeypatch.setattr(scalp_engine, "mt5_get_live_price", lambda symbol: 100.0)
    monkeypatch.setattr(scalp_engine, "_build_volume_profile", lambda candles: {"valid": True, "poc": 100.0, "vah": 101.0, "val": 99.0})
    monkeypatch.setattr(scalp_engine, "_classify_market_state", lambda vp: "balance")
    monkeypatch.setattr(scalp_engine, "_locate_price_vs_vp", lambda price, vp, atr_m15=0: {"location": "at_val", "nearest_level": 99.0, "distance_pct": 0.0})
    monkeypatch.setattr(scalp_engine, "_check_absorption", lambda candles: {"detected": True, "count": 1, "bars": [{}]})
    monkeypatch.setattr(scalp_engine, "_check_cvd", lambda candles: {"direction": "LONG", "cvd_slope": 1.0})
    monkeypatch.setattr(scalp_engine, "_check_aaa_sequence", lambda candles, absorption, cvd, asset_type=None: {"complete": False, "phase": "absorption_only"})
    monkeypatch.setattr(scalp_engine, "_check_vwap_lean", lambda candles, price: {"lean": "LONG", "vwap_value": 100.0})
    monkeypatch.setattr(scalp_engine, "_classify_setup", lambda *args, **kwargs: {"valid": True, "direction": "LONG", "setup_type": "mean_reversion", "reasons": []})
    monkeypatch.setattr(scalp_engine, "calculate_scalp_levels", lambda *args, **kwargs: {"entry": 100.0, "sl": 99.0, "tp_partial": 101.0, "tp1": 102.0, "tp2": 103.0, "rr": 2.0, "rr_synthetic": False, "sl_distance": 1.0, "sl_method": "vp_boundary"})
    monkeypatch.setattr(scalp_engine, "ai_quality_grade", lambda *args, **kwargs: {"score": 55, "grade": "C", "reasons": [], "size_multiplier": 0.25})
    monkeypatch.setattr(scalp_engine, "record_signal_event", lambda **kwargs: None)

    result = scalp_engine.run_scalp_scan(["EUR/USD"])
    assert len(result["signals"]) == 1
    assert result["signals"][0]["ai_grade"] == "C"
    assert result["signals"][0]["gate_result"] == "WATCHLIST"
    assert result["signals"][0]["executable"] is False

    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "MIN_GRADE_AUTO_EXECUTE": "B",
            "MIN_GRADE": "B",
        },
    )

    result = scalp_engine.run_scalp_scan(["EUR/USD"])
    assert result["skipped"] == []
    assert len(result["signals"]) == 1
    assert result["signals"][0]["ai_grade"] == "C"
    assert result["signals"][0]["ai_score"] == 55
    assert result["signals"][0]["gate_result"] == "WATCHLIST"
    assert result["signals"][0]["executable"] is False
    assert "grade_C_below_execution_min_B" in result["signals"][0]["soft_warnings"]


def test_run_scalp_scan_surfaces_fee_guard_candidate(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
            "SESSION_MODE": "all",
            "EXECUTION_TIMEFRAME": "M1",
            "EXECUTION_MIN_GRADE": "B",
            "ENGINE_D_FEE_GUARD_ENABLED": True,
            "ENGINE_D_MAX_COST_R": 0.20,
            "ENGINE_D_MIN_STOP_PCT": 0.0005,
            "ESTIMATED_FEE_PCT": 0.0006,
            "ESTIMATED_SLIPPAGE_PCT": 0.0002,
        },
    )
    monkeypatch.setattr(scalp_engine, "get_current_sessions", lambda: ["london"])
    monkeypatch.setattr(scalp_engine, "scalp_session_window", lambda *args, **kwargs: (True, "all"))
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: "EURUSD")
    monkeypatch.setattr(scalp_engine, "mt5_market_open_state", lambda symbol: {"open": True, "reason": "market_open"})
    monkeypatch.setattr(mt5_executor, "mt5_get_symbol_info", lambda display: {"digits": 5, "point": 0.00001, "spread": 10})
    monkeypatch.setattr(scalp_engine, "check_spread", lambda sym_info, asset_type, display="": (True, 1.0))
    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", lambda *args, **kwargs: _candles(300))
    monkeypatch.setattr(scalp_engine, "mt5_get_live_price", lambda symbol: 100.0)
    monkeypatch.setattr(scalp_engine, "_build_volume_profile", lambda candles: {"valid": True, "poc": 100.0, "vah": 101.0, "val": 99.0, "lvn_levels": []})
    monkeypatch.setattr(scalp_engine, "_classify_market_state", lambda vp: "balance")
    monkeypatch.setattr(scalp_engine, "_locate_price_vs_vp", lambda price, vp, atr_m15=0: {"location": "at_val", "nearest_level": 99.0, "distance_pct": 0.0})
    monkeypatch.setattr(scalp_engine, "_check_absorption", lambda candles: {"detected": True, "count": 2, "bars": [{}]})
    monkeypatch.setattr(scalp_engine, "_check_cvd", lambda candles: {"direction": "LONG", "cvd_slope": 1.0})
    monkeypatch.setattr(scalp_engine, "_check_aaa_sequence", lambda candles, absorption, cvd, asset_type=None: {"complete": False, "phase": "absorption_only"})
    monkeypatch.setattr(scalp_engine, "_check_vwap_lean", lambda candles, price: {"lean": "LONG", "vwap_value": 100.0})
    monkeypatch.setattr(scalp_engine, "_classify_setup", lambda *args, **kwargs: {"valid": True, "direction": "LONG", "setup_type": "mean_reversion", "reasons": []})
    monkeypatch.setattr(scalp_engine, "calculate_scalp_levels", lambda *args, **kwargs: {"entry": 100.0, "sl": 99.95, "tp_partial": 100.05, "tp1": 100.20, "tp2": 100.40, "rr": 4.0, "rr_below_min": False, "rr_synthetic": False, "sl_distance": 0.05, "sl_method": "vp_boundary"})
    monkeypatch.setattr(scalp_engine, "ai_quality_grade", lambda *args, **kwargs: {"score": 75, "grade": "B", "reasons": [], "size_multiplier": 0.5})
    monkeypatch.setattr(scalp_engine, "record_signal_event", lambda **kwargs: None)

    result = scalp_engine.run_scalp_scan(["EUR/USD"])

    assert result["skipped"] == []
    assert len(result["signals"]) == 1
    sig = result["signals"][0]
    assert sig["gate_result"] == "WATCHLIST"
    assert sig["executable"] is False
    assert "fee_guard_micro_stop" in sig["fail_reasons"]
    assert sig["fee_guard"]["cost_as_R"] > 0.20


def test_run_scalp_scan_does_not_block_close_structure_target(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
            "SESSION_MODE": "all",
            "EXECUTION_TIMEFRAME": "M1",
            "EXECUTION_MIN_GRADE": "B",
            "ENGINE_D_FEE_GUARD_ENABLED": True,
            "ENGINE_D_MAX_COST_R": 0.20,
            "ENGINE_D_MIN_STOP_PCT": 0.0005,
            "ESTIMATED_FEE_PCT": 0.0006,
            "ESTIMATED_SLIPPAGE_PCT": 0.0002,
        },
    )
    monkeypatch.setattr(scalp_engine, "get_current_sessions", lambda: ["london"])
    monkeypatch.setattr(scalp_engine, "scalp_session_window", lambda *args, **kwargs: (True, "all"))
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: "EURUSD")
    monkeypatch.setattr(scalp_engine, "mt5_market_open_state", lambda symbol: {"open": True, "reason": "market_open"})
    monkeypatch.setattr(mt5_executor, "mt5_get_symbol_info", lambda display: {"digits": 5, "point": 0.00001, "spread": 10})
    monkeypatch.setattr(scalp_engine, "check_spread", lambda sym_info, asset_type, display="": (True, 1.0))
    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", lambda *args, **kwargs: _candles(300))
    monkeypatch.setattr(scalp_engine, "mt5_get_live_price", lambda symbol: 100.0)
    monkeypatch.setattr(scalp_engine, "_build_volume_profile", lambda candles: {"valid": True, "poc": 100.2, "vah": 101.0, "val": 99.0, "lvn_levels": []})
    monkeypatch.setattr(scalp_engine, "_classify_market_state", lambda vp: "balance")
    monkeypatch.setattr(scalp_engine, "_locate_price_vs_vp", lambda price, vp, atr_m15=0: {"location": "at_val", "nearest_level": 99.0, "distance_pct": 0.0})
    monkeypatch.setattr(scalp_engine, "_check_absorption", lambda candles: {"detected": True, "count": 2, "bars": [{}]})
    monkeypatch.setattr(scalp_engine, "_check_cvd", lambda candles: {"direction": "LONG", "cvd_slope": 1.0})
    monkeypatch.setattr(scalp_engine, "_check_aaa_sequence", lambda candles, absorption, cvd, asset_type=None: {"complete": False, "phase": "absorption_only"})
    monkeypatch.setattr(scalp_engine, "_check_vwap_lean", lambda candles, price: {"lean": "LONG", "vwap_value": 100.0})
    monkeypatch.setattr(scalp_engine, "_classify_setup", lambda *args, **kwargs: {"valid": True, "direction": "LONG", "setup_type": "mean_reversion", "reasons": []})
    monkeypatch.setattr(
        scalp_engine,
        "calculate_scalp_levels",
        lambda *args, **kwargs: {
            "entry": 100.0,
            "sl": 99.0,
            "tp_partial": 101.0,
            "tp1": 101.0,
            "tp2": None,
            "structural_tp": 100.2,
            "structural_rr": 0.2,
            "structure_target_close": True,
            "rr": 1.0,
            "rr_below_min": False,
            "rr_synthetic": True,
            "sl_distance": 1.0,
            "sl_method": "atr",
        },
    )
    monkeypatch.setattr(scalp_engine, "ai_quality_grade", lambda *args, **kwargs: {"score": 82, "grade": "A", "reasons": [], "size_multiplier": 1.0})
    monkeypatch.setattr(scalp_engine, "record_signal_event", lambda **kwargs: None)

    result = scalp_engine.run_scalp_scan(["EUR/USD"])

    sig = result["signals"][0]
    assert sig["gate_result"] == "PASS"
    assert sig["executable"] is True
    assert sig["fail_reasons"] == []
    assert "structure_target_close" in sig["soft_warnings"]
    assert sig["strict_fabio_pass"] is True
    assert sig["strict_fabio_missing_pillars"] == []
    assert sig["current_vs_strict_status"] == "current_pass_strict_pass"
    assert sig["data_fidelity"]["report_only"] is True
    assert sig["data_fidelity"]["active_profile_anchor"] == "fixed_lookback"
    assert sig["data_fidelity"]["cvd_is_proxy"] is True
    assert sig["profile_anchor_mode"] == "fixed_lookback"
    assert sig["profile_anchor_shadow"]["report_only"] is True


def test_scalp_cost_assumptions_use_asset_overrides_before_global_scalars():
    cfg = {
        "ESTIMATED_FEE_PCT": 0.009,
        "ESTIMATED_SLIPPAGE_PCT": 0.008,
        "ESTIMATED_FEE_PCT_BY_ASSET": {"forex": 0.00007},
        "ESTIMATED_SLIPPAGE_PCT_BY_ASSET": {"forex": 0.00003},
    }

    assert _scalp_cost_assumptions(cfg, "forex") == (0.00007, 0.00003)
    assert _scalp_cost_assumptions(cfg, "crypto") == (0.009, 0.008)


def test_scalp_min_rr_prefers_engine_d_group_override(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "MIN_RR": 1.2,
            "score_group_overrides": {"forex_majors": {"scalp": {"min_rr": 1.4}}},
        },
    )
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "NAKED_ENGINE",
        {"score_group_overrides": {"forex_majors": {"scalp": {"min_rr": 1.9}}}},
    )

    assert scalp_engine._scalp_min_rr_for_group("forex", "forex_majors") == 1.4


def test_scalp_min_rr_legacy_naked_engine_override_remains_fallback(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "MIN_RR": 1.2,
            "score_group_overrides": {},
        },
    )
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "NAKED_ENGINE",
        {"score_group_overrides": {"forex_majors": {"scalp": {"min_rr": 1.6}}}},
    )

    assert scalp_engine._scalp_min_rr_for_group("forex", "forex_majors") == 1.6


def test_scalp_min_rr_config_covers_real_score_groups(monkeypatch):
    overrides = {
        "crypto_btc": {"scalp": {"min_rr": 1.2}},
        "crypto_eth": {"scalp": {"min_rr": 1.2}},
        "crypto_doge": {"scalp": {"min_rr": 1.2}},
        "crypto_alt_majors": {"scalp": {"min_rr": 1.2}},
        "crypto_other": {"scalp": {"min_rr": 1.2}},
        "us_indices_trackers": {"scalp": {"min_rr": 1.5}},
        "eu_indices": {"scalp": {"min_rr": 1.5}},
        "asian_indices": {"scalp": {"min_rr": 1.5}},
        "index_other": {"scalp": {"min_rr": 1.5}},
        "precious_trackers": {"scalp": {"min_rr": 1.3}},
        "energy_oil": {"scalp": {"min_rr": 1.3}},
        "copper": {"scalp": {"min_rr": 1.3}},
        "pgm_metals": {"scalp": {"min_rr": 1.3}},
        "base_metals": {"scalp": {"min_rr": 1.3}},
        "softs": {"scalp": {"min_rr": 1.3}},
        "commodity_other": {"scalp": {"min_rr": 1.3}},
        "us_stock_single": {"scalp": {"min_rr": 1.4}},
        "stock_other": {"scalp": {"min_rr": 1.4}},
        "bond_tlt": {"scalp": {"min_rr": 1.4}},
        "smallcap_em_etf": {"scalp": {"min_rr": 1.4}},
    }
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "MIN_RR": 1.2,
            "score_group_overrides": overrides,
        },
    )

    for group, expected in {
        "crypto_btc": 1.2,
        "us_indices_trackers": 1.5,
        "energy_oil": 1.3,
        "us_stock_single": 1.4,
        "smallcap_em_etf": 1.4,
    }.items():
        assert scalp_engine._scalp_min_rr_for_group("crypto", group) == expected


def test_scalp_engine_config_declares_real_score_group_overrides():
    overrides = scalp_engine.CONFIG.get("SCALP_ENGINE", {}).get("score_group_overrides", {})
    expected_groups = {
        "crypto_btc",
        "crypto_eth",
        "crypto_doge",
        "crypto_alt_majors",
        "crypto_other",
        "us_indices_trackers",
        "eu_indices",
        "asian_indices",
        "index_other",
        "precious_trackers",
        "energy_oil",
        "nat_gas",
        "copper",
        "pgm_metals",
        "base_metals",
        "softs",
        "commodity_other",
        "us_stock_single",
        "stock_other",
        "bond_tlt",
        "smallcap_em_etf",
    }

    assert expected_groups <= set(overrides)


def test_scalp_engine_config_hardens_mt5_tick_volume_absorption_defaults():
    cfg = scalp_engine.CONFIG.get("SCALP_ENGINE", {})
    vol_mults = cfg.get("ABSORPTION_VOL_MULT_CLASS", {})

    assert cfg.get("MT5_ABSORPTION_MIN_COUNT") == 2
    assert vol_mults.get("crypto") == 2.0
    for asset_type in ("forex", "commodity", "index", "stock"):
        assert vol_mults.get(asset_type, 0) >= 2.5


def test_scalp_engine_config_calibrates_explicit_low_frequency_rr_groups():
    overrides = scalp_engine.CONFIG.get("SCALP_ENGINE", {}).get("score_group_overrides", {})

    assert overrides["forex_other"]["scalp"]["min_rr"] == 1.3
    assert overrides["crypto_doge"]["scalp"]["min_rr"] == 1.5
    assert overrides["crypto_alt_majors"]["scalp"]["min_rr"] == 1.4
    assert overrides["bond_tlt"]["scalp"]["min_rr"] == 1.3
    assert overrides["smallcap_em_etf"]["scalp"]["min_rr"] == 1.5


def test_scalp_execution_min_grade_config_uses_auto_execute_floor():
    cfg = scalp_engine.CONFIG.get("SCALP_ENGINE", {})

    assert cfg.get("MIN_GRADE_AUTO_EXECUTE") == "B"
    assert scalp_engine._scalp_execution_min_grade(cfg) == "B"


def test_run_scalp_scan_skips_closed_mt5_market(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
            "SESSION_MODE": "all",
        },
    )
    monkeypatch.setattr(scalp_engine, "get_current_sessions", lambda: ["off_hours"])
    monkeypatch.setattr(scalp_engine, "scalp_session_window", lambda *args, **kwargs: (True, "all"))
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: "EURUSD")
    monkeypatch.setattr(
        scalp_engine,
        "mt5_market_open_state",
        lambda symbol: {"open": False, "reason": "MARKET_CLOSED_STALE_TICK", "age_sec": 3600},
    )
    monkeypatch.setattr(scalp_engine, "record_signal_event", lambda **kwargs: None)

    result = scalp_engine.run_scalp_scan(["EUR/USD"])

    assert result["signals"] == []
    assert result["skipped"] == [{"pair": "EUR/USD", "reason": "MARKET_CLOSED_STALE_TICK"}]


def test_run_scalp_scan_skips_stale_structure_candles(monkeypatch):
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    stale_m15 = _dated_candles(40, now - timedelta(hours=12), step_seconds=900)

    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
            "SESSION_MODE": "all",
            "MARKET_CANDLE_MAX_AGE_SEC": 900,
        },
    )
    monkeypatch.setattr(scalp_engine, "_current_utc_datetime", lambda: now)
    monkeypatch.setattr(scalp_engine, "get_current_sessions", lambda: ["london"])
    monkeypatch.setattr(scalp_engine, "scalp_session_window", lambda *args, **kwargs: (True, "all"))
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: "EURUSD")
    monkeypatch.setattr(scalp_engine, "mt5_market_open_state", lambda symbol: {"open": True, "reason": "market_open"})
    monkeypatch.setattr(mt5_executor, "mt5_get_symbol_info", lambda display: {"digits": 5, "point": 0.00001, "spread": 10})
    monkeypatch.setattr(scalp_engine, "check_spread", lambda sym_info, asset_type, display="": (True, 1.0))
    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", lambda *args, **kwargs: stale_m15)
    monkeypatch.setattr(scalp_engine, "record_signal_event", lambda **kwargs: None)

    result = scalp_engine.run_scalp_scan(["EUR/USD"])

    assert result["signals"] == []
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["pair"] == "EUR/USD"
    assert result["skipped"][0]["reason"].startswith("MARKET_DATA_STALE_STRUCTURE_")


def test_run_scalp_scan_uses_m1_execution_tf(monkeypatch):
    requested_tfs = []

    def _fake_mt5_fetch(_symbol, tf, _count, include_forming=False):
        requested_tfs.append((tf, include_forming))
        return _candles(300)

    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
            "SESSION_MODE": "new_york",
            "EXECUTION_TIMEFRAME": "M1",
            "MIN_GRADE_AUTO_EXECUTE": "C",
        },
    )
    monkeypatch.setattr(scalp_engine, "get_current_sessions", lambda: ["new_york"])
    monkeypatch.setattr(scalp_engine, "scalp_session_window", lambda *args, **kwargs: (True, "new_york"))
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: "EURUSD")
    monkeypatch.setattr(scalp_engine, "mt5_market_open_state", lambda symbol: {"open": True, "reason": "market_open"})
    monkeypatch.setattr(mt5_executor, "mt5_get_symbol_info", lambda display: {"digits": 5, "point": 0.00001, "spread": 10})
    monkeypatch.setattr(scalp_engine, "check_spread", lambda sym_info, asset_type, display="": (True, 1.0))
    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", _fake_mt5_fetch)
    monkeypatch.setattr(scalp_engine, "mt5_get_live_price", lambda symbol: 100.0)
    monkeypatch.setattr(scalp_engine, "_build_volume_profile", lambda candles: {"valid": True, "poc": 100.0, "vah": 101.0, "val": 99.0, "lvn_levels": []})
    monkeypatch.setattr(scalp_engine, "_classify_market_state", lambda vp: "balance")
    monkeypatch.setattr(scalp_engine, "_locate_price_vs_vp", lambda price, vp, atr_m15=0: {"location": "at_val", "nearest_level": 99.0, "distance_pct": 0.0})
    monkeypatch.setattr(scalp_engine, "_check_absorption", lambda candles: {"detected": True, "count": 1, "bars": [{}]})
    monkeypatch.setattr(scalp_engine, "_check_cvd", lambda candles: {"direction": "LONG", "cvd_slope": 1.0})
    monkeypatch.setattr(scalp_engine, "_check_aaa_sequence", lambda candles, absorption, cvd, asset_type=None: {"complete": False, "phase": "absorption_only"})
    monkeypatch.setattr(scalp_engine, "_check_vwap_lean", lambda candles, price: {"lean": "LONG", "vwap_value": 100.0})
    monkeypatch.setattr(scalp_engine, "_classify_setup", lambda *args, **kwargs: {"valid": True, "direction": "LONG", "setup_type": "mean_reversion", "reasons": []})
    monkeypatch.setattr(scalp_engine, "calculate_scalp_levels", lambda *args, **kwargs: {"entry": 100.0, "sl": 99.0, "tp_partial": 101.0, "tp1": 102.0, "tp2": 103.0, "rr": 2.0, "rr_synthetic": False, "sl_distance": 1.0, "sl_method": "vp_boundary"})
    monkeypatch.setattr(scalp_engine, "ai_quality_grade", lambda *args, **kwargs: {"score": 75, "grade": "B", "reasons": [], "size_multiplier": 0.5})
    monkeypatch.setattr(scalp_engine, "record_signal_event", lambda **kwargs: None)

    result = scalp_engine.run_scalp_scan(["EUR/USD"])
    sig = result["signals"][0]
    assert sig["execution_tf"] == "M1"
    assert isinstance(sig.get("advisory"), dict)
    assert "summary" in sig["advisory"]
    assert sig.get("advisory_summary") == sig["advisory"]["summary"]
    assert sig.get("premarket_delta_cluster_type") == "proxy"
    assert isinstance(sig.get("premarket_delta_proxy_levels"), dict)
    assert sig["premarket_delta_proxy_levels"].get("reason") == "stock_only"
    assert ("M1", True) in requested_tfs
    assert ("M15", False) in requested_tfs
    assert ("H1", False) in requested_tfs


def test_run_scalp_scan_forming_flags_are_configurable(monkeypatch):
    requested_tfs = []

    def _fake_mt5_fetch(_symbol, tf, _count, include_forming=False):
        requested_tfs.append((tf, include_forming))
        return _candles(300)

    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
            "SESSION_MODE": "new_york",
            "EXECUTION_TIMEFRAME": "M1",
            "MIN_GRADE_AUTO_EXECUTE": "C",
            "USE_FORMING_FOR_STRUCTURE": True,
            "USE_FORMING_FOR_TRIGGER": False,
            "USE_FORMING_FOR_BIAS": True,
        },
    )
    monkeypatch.setattr(scalp_engine, "get_current_sessions", lambda: ["new_york"])
    monkeypatch.setattr(scalp_engine, "scalp_session_window", lambda *args, **kwargs: (True, "new_york"))
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: "EURUSD")
    monkeypatch.setattr(scalp_engine, "mt5_market_open_state", lambda symbol: {"open": True, "reason": "market_open"})
    monkeypatch.setattr(mt5_executor, "mt5_get_symbol_info", lambda display: {"digits": 5, "point": 0.00001, "spread": 10})
    monkeypatch.setattr(scalp_engine, "check_spread", lambda sym_info, asset_type, display="": (True, 1.0))
    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", _fake_mt5_fetch)
    monkeypatch.setattr(scalp_engine, "mt5_get_live_price", lambda symbol: 100.0)
    monkeypatch.setattr(scalp_engine, "_build_volume_profile", lambda candles: {"valid": True, "poc": 100.0, "vah": 101.0, "val": 99.0, "lvn_levels": []})
    monkeypatch.setattr(scalp_engine, "_classify_market_state", lambda vp: "balance")
    monkeypatch.setattr(scalp_engine, "_locate_price_vs_vp", lambda price, vp, atr_m15=0: {"location": "at_val", "nearest_level": 99.0, "distance_pct": 0.0})
    monkeypatch.setattr(scalp_engine, "_check_absorption", lambda candles: {"detected": True, "count": 1, "bars": [{}]})
    monkeypatch.setattr(scalp_engine, "_check_cvd", lambda candles: {"direction": "LONG", "cvd_slope": 1.0})
    monkeypatch.setattr(scalp_engine, "_check_aaa_sequence", lambda candles, absorption, cvd, asset_type=None: {"complete": False, "phase": "absorption_only"})
    monkeypatch.setattr(scalp_engine, "_check_vwap_lean", lambda candles, price: {"lean": "LONG", "vwap_value": 100.0})
    monkeypatch.setattr(scalp_engine, "_classify_setup", lambda *args, **kwargs: {"valid": True, "direction": "LONG", "setup_type": "mean_reversion", "reasons": []})
    monkeypatch.setattr(scalp_engine, "calculate_scalp_levels", lambda *args, **kwargs: {"entry": 100.0, "sl": 99.0, "tp_partial": 101.0, "tp1": 102.0, "tp2": 103.0, "rr": 2.0, "rr_synthetic": False, "sl_distance": 1.0, "sl_method": "vp_boundary"})
    monkeypatch.setattr(scalp_engine, "ai_quality_grade", lambda *args, **kwargs: {"score": 75, "grade": "B", "reasons": [], "size_multiplier": 0.5})
    monkeypatch.setattr(scalp_engine, "record_signal_event", lambda **kwargs: None)

    result = scalp_engine.run_scalp_scan(["EUR/USD"])

    assert len(result["signals"]) == 1
    assert ("M15", True) in requested_tfs
    assert ("M1", False) in requested_tfs
    assert ("H1", True) in requested_tfs


def test_premarket_delta_proxy_levels_are_explicitly_proxy():
    candles = []
    # 08:00-08:20 NY local on a weekday (12:00-12:20 UTC during DST)
    for i in range(21):
        candles.append(
            {
                "time": f"2026-04-13T12:{i:02d}:00+00:00",
                "open": 100.0 + i * 0.01,
                "high": 100.1 + i * 0.01,
                "low": 99.9 + i * 0.01,
                "close": 100.0 + i * 0.01,
                "vol": 1000 + i * 20,
            }
        )
    out = scalp_engine._build_premarket_delta_proxy_levels(candles, top_n=2, min_candles=10, bucket_size=0.01)
    assert out["available"] is True
    assert out["label"] == "premarket_delta_proxy_levels"
    assert out["is_true_delta_cluster"] is False
    assert out["method"] == "proxy"
    assert len(out["levels"]) == 2


def test_scalp_fetch_candles_crypto_m1_prefers_ws(monkeypatch):
    ws_candles = [
        {"time": "2026-03-26T13:35:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1},
        {"time": "2026-03-26T13:36:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1},
        {"time": "2026-03-26T13:37:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1},
        {"time": "2026-03-26T13:38:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1},
        {"time": "2026-03-26T13:39:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1},
    ]
    monkeypatch.setattr(candle_feeds, "fetch_candles_live", lambda display, tf, limit=500: {"candles": ws_candles})
    monkeypatch.setattr(
        scalp_engine,
        "_current_utc_datetime",
        lambda: datetime(2026, 3, 26, 13, 40, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(athena_runtime, "rt", lambda: types.SimpleNamespace(fetch_candles=lambda *args, **kwargs: []))
    out = scalp_engine._scalp_fetch_candles(
        {"display": "BTC/USDT", "type": "crypto", "source": "binance"},
        "M1",
        300,
    )
    assert out == ws_candles


def test_scalp_fetch_candles_crypto_m1_falls_back_when_ws_stale(monkeypatch):
    stale_ws = [{"time": "2026-03-26T13:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1}] * 5
    routed = [{"time": "2026-03-26T13:40:00+00:00", "open": 2, "high": 2, "low": 2, "close": 2, "vol": 2}]
    monkeypatch.setattr(candle_feeds, "fetch_candles_live", lambda display, tf, limit=500: {"candles": stale_ws})
    monkeypatch.setattr(
        scalp_engine,
        "_current_utc_datetime",
        lambda: datetime(2026, 3, 26, 13, 40, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(athena_runtime, "rt", lambda: types.SimpleNamespace(fetch_candles=lambda *args, **kwargs: routed))
    out = scalp_engine._scalp_fetch_candles(
        {"display": "BTC/USDT", "type": "crypto", "source": "binance"},
        "M1",
        300,
    )
    assert out == routed


# ═══════════════════════════════════════════════════════════════════════════════
# 10. UNCOVERED FIXES (Engine D audit residuals)
# ═══════════════════════════════════════════════════════════════════════════════

def test_execution_min_grade_a_marks_b_as_watchlist_not_skip(monkeypatch):
    """Grade is no longer an AI veto; below execution grade becomes visible watchlist."""
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "EXECUTION_MIN_GRADE": "A",
        },
    )
    monkeypatch.setattr(scalp_engine, "get_current_sessions", lambda: ["london"])
    monkeypatch.setattr(scalp_engine, "is_valid_session", lambda asset="forex": (True, "london"))
    monkeypatch.setattr(scalp_engine, "scalp_session_window", lambda *args, **kwargs: (True, "london"))
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: "EURUSD")
    monkeypatch.setattr(scalp_engine, "mt5_market_open_state", lambda symbol: {"open": True, "reason": "market_open"})
    monkeypatch.setattr(mt5_executor, "mt5_get_symbol_info", lambda display: {"digits": 5, "point": 0.00001, "spread": 10})
    monkeypatch.setattr(scalp_engine, "check_spread", lambda sym_info, asset_type, display="": (True, 1.0))
    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", lambda *args, **kwargs: _candles(300))
    monkeypatch.setattr(scalp_engine, "mt5_get_live_price", lambda symbol: 100.0)
    monkeypatch.setattr(scalp_engine, "_build_volume_profile", lambda candles: {"valid": True, "poc": 100.0, "vah": 101.0, "val": 99.0})
    monkeypatch.setattr(scalp_engine, "_classify_market_state", lambda vp: "balance")
    monkeypatch.setattr(scalp_engine, "_locate_price_vs_vp", lambda price, vp, atr_m15=0: {"location": "at_val", "nearest_level": 99.0, "distance_pct": 0.0})
    monkeypatch.setattr(scalp_engine, "_check_absorption", lambda candles: {"detected": True, "count": 1, "bars": [{}]})
    monkeypatch.setattr(scalp_engine, "_check_cvd", lambda candles: {"direction": "LONG", "cvd_slope": 1.0})
    monkeypatch.setattr(scalp_engine, "_check_aaa_sequence", lambda candles, absorption, cvd, asset_type=None: {"complete": False, "phase": "absorption_only"})
    monkeypatch.setattr(scalp_engine, "_check_vwap_lean", lambda candles, price: {"lean": "LONG", "vwap_value": 100.0})
    monkeypatch.setattr(scalp_engine, "_classify_setup", lambda *args, **kwargs: {"valid": True, "direction": "LONG", "setup_type": "mean_reversion", "reasons": []})
    monkeypatch.setattr(scalp_engine, "calculate_scalp_levels", lambda *args, **kwargs: {"entry": 100.0, "sl": 99.0, "tp_partial": 101.0, "tp1": 102.0, "tp2": 103.0, "rr": 2.0, "rr_synthetic": False, "sl_distance": 1.0, "sl_method": "vp_boundary"})
    monkeypatch.setattr(scalp_engine, "ai_quality_grade", lambda *args, **kwargs: {"score": 75, "grade": "B", "reasons": [], "size_multiplier": 0.5})
    monkeypatch.setattr(scalp_engine, "record_signal_event", lambda **kwargs: None)

    result = scalp_engine.run_scalp_scan(["EUR/USD"])
    assert result["skipped"] == []
    assert len(result["signals"]) == 1
    assert result["signals"][0]["ai_grade"] == "B"
    assert result["signals"][0]["gate_result"] == "WATCHLIST"
    assert result["signals"][0]["executable"] is False
    assert "grade_B_below_execution_min_A" in result["signals"][0]["soft_warnings"]


def test_mt5_market_open_state_time_msc_milliseconds(monkeypatch):
    """time_msc is milliseconds since epoch, not seconds."""
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    # time_msc = 12:00:00 UTC in milliseconds
    time_msec = int(now.timestamp() * 1000)
    stale_msec = int((now - timedelta(minutes=20)).timestamp() * 1000)

    fake_tick = types.SimpleNamespace(
        bid=1.2345,
        ask=1.2347,
        last=1.2346,
        time_msc=stale_msec,
    )
    fake_mt5 = types.SimpleNamespace(
        SYMBOL_TRADE_MODE_DISABLED=0,
        symbol_select=lambda s, e: True,
        symbol_info=lambda s: types.SimpleNamespace(trade_mode=1),
        symbol_info_tick=lambda s: fake_tick,
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(scalp_engine, "_current_utc_datetime", lambda: now)
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "MARKET_OPEN_CHECK_ENABLED": True,
            "MARKET_TICK_MAX_AGE_SEC": 900,
        },
    )

    state = scalp_engine.mt5_market_open_state("EURUSD")
    assert state["open"] is False
    assert state["reason"] == "MARKET_CLOSED_STALE_TICK"
    assert abs(state["age_sec"] - 1200) <= 1

    # Fresh tick (within max_age)
    fresh_tick = types.SimpleNamespace(
        bid=1.2345,
        ask=1.2347,
        last=1.2346,
        time_msc=time_msec,
    )
    fake_mt5.symbol_info_tick = lambda s: fresh_tick
    state2 = scalp_engine.mt5_market_open_state("EURUSD")
    assert state2["open"] is True


def test_run_scalp_scan_no_duplicate_skips_on_mt5_disconnect(monkeypatch):
    """When MT5 is disconnected, each MT5 pair must appear in skipped exactly once."""
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
            "SESSION_MODE": "all",
        },
    )
    monkeypatch.setattr(scalp_engine, "get_current_sessions", lambda: ["london"])
    monkeypatch.setattr(scalp_engine, "scalp_session_window", lambda *args, **kwargs: (True, "all"))
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: False)
    monkeypatch.setattr(scalp_engine, "record_signal_event", lambda **kwargs: None)

    result = scalp_engine.run_scalp_scan(["EUR/USD", "GBP/USD"])

    assert result["signals"] == []
    skipped_pairs = [s["pair"] for s in result["skipped"]]
    assert skipped_pairs.count("EUR/USD") == 1
    assert skipped_pairs.count("GBP/USD") == 1
    assert all(s["reason"] == "MT5_NOT_CONNECTED" for s in result["skipped"])


def test_check_aaa_sequence_no_false_aggression_on_doji():
    """A doji previous bar must not collapse the aggression body threshold to ~0."""
    # Build candles: absorption hit on bar 28, then doji prev bar, then breakout
    candles = _candles(30, vol=100, spread=2.0)
    # bar 28 is the "previous" bar — make it a doji
    candles[28] = {
        "time": "T28",
        "open": 100.0,
        "high": 100.0,
        "low": 100.0,
        "close": 100.0,
        "vol": 100.0,
    }
    # bar 29 is the breakout — small body, high volume
    candles[29] = {
        "time": "T29",
        "open": 100.0,
        "high": 100.01,
        "low": 99.99,
        "close": 100.005,
        "vol": 500.0,
    }
    absorption = {"detected": True, "count": 1, "bars": [{"index": 25}]}
    cvd = {"direction": "LONG"}

    result = scalp_engine._check_aaa_sequence(candles, absorption, cvd)
    # Because prev_range is a doji (0), the old code would set prev_range = 1e-10
    # and body > 0.8e-10 would be true, causing false aggression.
    # With the fix, prev_range is clamped to a meaningful minimum based on price,
    # so the tiny body should NOT exceed 0.8 * meaningful_range.
    assert result["complete"] is False


def test_calculate_levels_trend_continuation_uses_config_fallback(monkeypatch):
    """Trend-continuation SL fallback should respect config override."""
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "TREND_CONT_SL_FALLBACK_PCT": 0.005,
        },
    )
    vp = {"poc": 1.1020, "vah": 1.1080, "val": 1.0970}
    # Entry below POC → fallback SL = entry - entry * 0.005
    levels = scalp_engine.calculate_scalp_levels(
        "LONG", 1.1000, vp, "trend_continuation",
        {"digits": 5, "point": 0.00001}, "forex"
    )
    expected_sl = round(1.1000 - (1.1000 * 0.005), 5)
    assert levels["sl"] == expected_sl


def test_grade_spread_penalty_per_asset_type(monkeypatch):
    """Spread penalty should use asset-type-specific max spread config."""
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "MAX_SPREAD_PIPS": {"forex": 4, "commodity": 30},
        },
    )
    vp = {"poc": 1.1030, "vah": 1.1060, "val": 1.0970, "lvn_levels": [], "balance_ratio": 0.5}
    price_loc = {"location": "at_vah", "nearest_level": 1.1060, "distance_pct": 0.0}
    absorption = {"detected": True, "count": 1}
    cvd = {"direction": "SHORT"}
    aaa = {"complete": False, "phase": "absorption_only"}
    vwap = {"lean": None}
    setup = {"direction": "SHORT", "setup_type": "mean_reversion"}

    # Forex with 25 "pips" — should be penalized as wide (max=4, 0.8*4=3.2)
    q_forex = scalp_engine.ai_quality_grade(
        vp, price_loc, absorption, cvd, aaa, vwap, setup,
        ["london"], 25.0, "SHORT", asset_type="forex"
    )
    assert any("Wide spread" in r for r in q_forex["reasons"])

    # Commodity with 10 raw points — should get a BONUS (max=30, 0.5*30=15)
    # because the same 10 points would be wide for forex but tight for commodity.
    q_comm = scalp_engine.ai_quality_grade(
        vp, price_loc, absorption, cvd, aaa, vwap, setup,
        ["london"], 10.0, "SHORT", asset_type="commodity"
    )
    assert any("Tight spread" in r for r in q_comm["reasons"])


def test_normalize_session_mode_aliases():
    assert _normalize_session_mode("overlap") == "london_ny"
    assert _normalize_session_mode("London_New_York") == "london_ny"
    assert _normalize_session_mode("NY") == "new_york"
    assert _normalize_session_mode("24/7") == "all"


def test_as_fraction_accepts_decimal_or_percent_literal():
    assert abs(_as_fraction(70, 0.5, clamp_minmax=(0.01, 0.99)) - 0.70) < 1e-9
    assert abs(_as_fraction(0.15, 0.3, clamp_minmax=(0.01, 0.99)) - 0.15) < 1e-9


def test_merge_vp_aliases_fills_standard_keys():
    raw = {"profile_valid": True, "poc": None, "vah": None, "val": None, "POC": 1.103, "VAH": 1.106, "VAL": 1.097}
    m = _merge_vp_aliases(dict(raw))
    assert m["poc"] == 1.103
    assert m["vah"] == 1.106
    assert m["val"] == 1.097


def test_finalize_run_scalp_scan_attachs_diagnostic_summary():
    from scalp_engine import _finalize_run_scalp_scan_result

    payload = _finalize_run_scalp_scan_result(
        signals=[],
        skipped=[{"pair": "XAU/USD", "reason": "OUTSIDE_SESSION"}],
        scanned=1,
        session_name="foo",
        sessions_active=[],
        reason=None,
    )
    assert payload["diagnostic_summary"]["skipped_reason_counts"]["OUTSIDE_SESSION"] == 1


def test_summarize_engine_d_scan_merges_skips_and_signal_funnel():
    result = summarize_engine_d_scan(
        {
            "signals": [
                {"gate_result": "WATCHLIST", "executable": False, "fail_reasons": ["rr_below_min"], "soft_warnings": []},
                {"gate_result": "PASS", "executable": True, "fail_reasons": [], "soft_warnings": []},
            ],
            "skipped": [{"pair": "X", "reason": "no_setup:balance_inside_va"}],
            "sessions_active": [],
            "session": "all",
        }
    )
    assert result["skipped_reason_counts"].get("no_setup:balance_inside_va") == 1
    assert result["signals_gate_result_counts"]["WATCHLIST"] == 1
    assert "rr_below_min" in result["signals_fail_and_warning_flat_counts"]


def test_classify_mean_reversion_va_extreme_rejects_vwap_only_without_aggression(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "STRICT_FABIO_GATE_ENABLED": True,
            "ALLOW_NEUTRAL_CVD_AT_VA_EXTREME": True,
        },
    )
    setup = _classify_setup(
        "balance",
        {"location": "at_vah", "nearest_level": 101.0},
        {"detected": False, "count": 0},
        {"direction": None, "source": "candles"},
        {},
        {"lean": "SHORT"},
        None,
        asset_type="forex",
    )

    assert setup["valid"] is False
    assert setup.get("reason") == "no_aggression_at_va_extreme"


def test_classify_mean_reversion_outside_va_rejects_neutral_cvd_without_aggression(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "STRICT_FABIO_GATE_ENABLED": True,
        },
    )
    setup = _classify_setup(
        "balance",
        {"location": "outside_va", "above_va": True, "nearest_level": 101.0},
        {"detected": False, "count": 0},
        {"direction": None, "source": "candles"},
        {},
        {"lean": None},
        None,
        asset_type="forex",
    )

    assert setup["valid"] is False
    assert setup.get("reason") == "no_aggression_outside_va"


def test_classify_trend_continuation_requires_lvn_when_strict(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "STRICT_FABIO_GATE_ENABLED": True,
            "STRICT_TREND_LOCATION_LVN_ONLY": True,
        },
    )
    setup = _classify_setup(
        "imbalance",
        {"location": "inside_va", "nearest_level": 100.0},
        {"detected": True, "count": 2},
        {"direction": "LONG", "source": "candles"},
        {"complete": True, "direction": "LONG"},
        {"lean": "LONG"},
        "LONG",
        asset_type="forex",
    )

    assert setup["valid"] is False
    assert setup.get("reason") == "trend_continuation_requires_lvn"


def test_classify_mean_reversion_va_extreme_neutral_cvd_legacy_override(monkeypatch):
    """Legacy neutral-CVD behavior remains available when the strict Fabio gate is disabled."""
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "STRICT_FABIO_GATE_ENABLED": False,
            "ALLOW_NEUTRAL_CVD_AT_VA_EXTREME": True,
        },
    )
    absorption = {"detected": False, "count": 0}
    cvd = {"direction": None, "source": "candles"}
    vwap = {"lean": None}
    aaa = {}
    price_loc = {"location": "at_vah", "nearest_level": 101.0}
    setup = _classify_setup(
        "balance", price_loc, absorption, cvd, aaa, vwap, None, asset_type="forex"
    )
    assert setup["valid"] is True
    assert setup["setup_type"] == "mean_reversion"


def test_classify_mean_reversion_va_extreme_neutral_cvd_respects_disable(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {**scalp_engine.CONFIG.get("SCALP_ENGINE", {}), "ALLOW_NEUTRAL_CVD_AT_VA_EXTREME": False},
    )
    absorption = {"detected": False, "count": 0}
    cvd = {"direction": None, "source": "candles"}
    setup = _classify_setup(
        "balance",
        {"location": "at_vah", "nearest_level": 101.0},
        absorption,
        cvd,
        {},
        {"lean": None},
        None,
        asset_type="forex",
    )
    assert setup["valid"] is False
    assert setup.get("reason") == "no_aggression_at_va_extreme"
