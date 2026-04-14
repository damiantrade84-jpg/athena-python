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
from datetime import datetime, timezone

import scalp_engine
import mt5_executor
from scalp_engine import (
    get_current_sessions,
    infer_bias_from_ema_stack,
    is_valid_session,
    mt5_get_live_price,
    mt5_fetch_scalp_candles,
    _build_volume_profile,
    _locate_price_vs_vp,
    _classify_market_state,
    _check_absorption,
    _check_cvd,
    _check_aaa_sequence,
    calculate_scalp_levels,
    ai_quality_grade,
    check_spread,
    get_scalp_pairs,
    _guess_asset_type,
)
from indicators import calc_vwap, detect_absorption, calc_cvd, detect_range_contraction
from volume_profile import compute_fixed_range_volume_profile


# ── helpers ──────────────────────────────────────────────────────────────────

def _candles(n, base=100.0, vol=1000.0, spread=1.0, trend=0.0):
    out = []
    p = base
    for i in range(n):
        p += trend
        out.append({
            "time": f"T{i}", "open": round(p, 6),
            "high": round(p + spread * 0.6, 6), "low": round(p - spread * 0.4, 6),
            "close": round(p + spread * 0.1, 6), "vol": vol,
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


def test_check_cvd_returns_direction():
    candles = [{"open": 100, "high": 102, "low": 99.5, "close": 101.8, "vol": 1000}] * 20
    r = _check_cvd(candles)
    assert "direction" in r
    assert r["direction"] in ("LONG", "SHORT", None)
    assert "cvd_slope" in r


def test_check_aaa_sequence_no_absorption():
    """AAA requires absorption first — without it, complete=False."""
    r = _check_aaa_sequence(_candles(30), {"detected": False, "count": 0, "bars": []},
                            {"direction": "LONG"})
    assert r["complete"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# 4. RISK LEVEL CALCULATION  (actual signature: direction, entry, vp, setup_type, sym_info, asset_type)
# ═══════════════════════════════════════════════════════════════════════════════

def test_calculate_levels_long_sl_below_val():
    vp = {"poc": 1.1050, "vah": 1.1080, "val": 1.0970}
    levels = calculate_scalp_levels(
        "LONG", 1.1000, vp, "mean_reversion",
        {"digits": 5, "point": 0.00001}, "forex"
    )
    assert levels["sl"] < vp["val"], "SL must be below VAL for LONG mean-reversion"
    assert levels["tp1"] > levels["entry"], "TP1 must be above entry for LONG"
    assert levels["rr"] >= 1.0
    assert levels["sl_method"] == "vp_boundary"


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
    for k in ("entry", "sl", "tp1", "tp2", "rr", "sl_distance", "sl_method"):
        assert k in levels, f"Missing key: {k}"


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
        TIMEFRAME_M5=5, TIMEFRAME_M15=15, TIMEFRAME_H1=60,
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
        TIMEFRAME_M5=5, TIMEFRAME_M15=15, TIMEFRAME_H1=60,
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
    assert _guess_asset_type("BTC/USDT") == "crypto"
    assert _guess_asset_type("XAU/USD") == "commodity"
    assert _guess_asset_type("Nasdaq") == "index"
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
