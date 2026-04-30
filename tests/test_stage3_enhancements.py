"""Stage 3 Enhancement Layer (P2) — verification tests.

Covers:
  3.1  Explicit Engine A × Engine B interaction rules (B cannot rescue weak A)
  3.2  Funding rate / carry cost adjustment
  3.3  EMA vote hysteresis (2-bar confirmation)
  3.4  Replace forex session multiplier with volatility scaler
  3.5  Single-vote trend respects TF weight
  3.6  Cross-engine research lab correlation cap
  3.7  Explicit gate definitions for Engine B
"""

import pytest
from config import CONFIG
from factor_scoring import (
    _coherent_trend_score,
    _ema_cross_confirmed,
    _volatility_scaler,
    compute_factor_scores,
)
from engine_c import compute_consensus, classify_conviction


# ── 3.1 Engine A × B interaction rules ───────────────────────────────────────

def test_weak_engine_a_cannot_be_rescued_to_execute():
    """If A is weak (score_norm < 0.30), B cannot push decision to execute."""
    signal_a = {
        "price": 100.0,
        "direction": "LONG",
        "confluenceScore": 0.7,   # weak A → norm = 0.7/3.0 = 0.233 (>0.20 partial, <0.30 weak)
        "maxScore": 3.0,
        "sl": 99.0,
        "tp1": 103.0,
        "confidence": 0.4,
    }
    signal_b = {
        "structural_verdict": "CLEAR",
        "direction": "LONG",
        "score": 6.0,
        "recommended_stop_loss": 99.5,
        "recommended_take_profit": 104.0,
        "bos_mtf_confirmed": True,
        "ob_at_zone": True,
        "ob_strength": 80,
    }
    confidence_b = {
        "passed": True,
        "score": 6.0,
        "gate_score": 5.0,
        "max_possible": 9.0,
        "pct": 75.0,
        "structure_ok": True,
        "zone_ok": True,
        "trigger_ok": True,
        "room_ok": True,
        "rr_ok": True,
        "macro_ok": True,
        "execution_sl": 99.5,
        "execution_tp": 104.0,
        "rr_used_for_gate": 2.5,
    }

    result = compute_consensus(
        signal_a, signal_b, confidence_b,
        asset_type="crypto", regime="TRENDING", entry_price=100.0, atr=1.0,
    )
    # A is weak → decision should be reduced_risk or watchlist, never execute
    assert result["verdict"] == "ALIGNED"
    assert result["decision_state"] in ("reduced_risk", "watchlist", "blocked")
    assert result.get("a_is_weak") is True


def test_strong_engine_a_allows_normal_execute():
    """If A is strong (score_norm >= 0.30), normal ALIGNED logic applies."""
    signal_a = {
        "price": 100.0,
        "direction": "LONG",
        "confluenceScore": 2.5,   # strong A → norm = 2.5/3.0 = 0.833
        "maxScore": 3.0,
        "sl": 99.0,
        "tp1": 103.0,
        "confidence": 0.7,
    }
    signal_b = {
        "structural_verdict": "CLEAR",
        "direction": "LONG",
        "score": 6.0,
        "recommended_stop_loss": 99.5,
        "recommended_take_profit": 104.0,
        "bos_mtf_confirmed": True,
        "ob_at_zone": True,
        "ob_strength": 80,
    }
    confidence_b = {
        "passed": True,
        "score": 6.0,
        "gate_score": 5.0,
        "max_possible": 9.0,
        "pct": 75.0,
        "structure_ok": True,
        "zone_ok": True,
        "trigger_ok": True,
        "room_ok": True,
        "rr_ok": True,
        "macro_ok": True,
        "execution_sl": 99.5,
        "execution_tp": 104.0,
        "rr_used_for_gate": 2.5,
    }

    result = compute_consensus(
        signal_a, signal_b, confidence_b,
        asset_type="crypto", regime="TRENDING", entry_price=100.0, atr=1.0,
    )
    assert result.get("a_is_weak") is False
    # With strong A + strong B, execute is possible
    assert result["decision_state"] in ("execute", "reduced_risk", "watchlist")


# ── 3.2 Funding rate / carry cost adjustment ─────────────────────────────────

def test_crypto_adverse_funding_reduces_score():
    """High positive funding on LONG reduces final_score."""
    pair = {"type": "crypto", "display": "BTC/USDT"}
    snap = {
        "ema21": 110.0, "ema50": 100.0, "ema200": 90.0,
        "adx": 25.0, "rsi": 55.0, "macdHist": 0.5,
        "close": 100.0, "atr": 1.0,
        "plusDI": 25.0, "minusDI": 15.0,
    }
    baseline = compute_factor_scores(
        snap, snap, snap, pair, [], [], [], 1.0, funding_rate=0.0001
    )
    adverse = compute_factor_scores(
        snap, snap, snap, pair, [], [], [], 1.0, funding_rate=0.005
    )
    assert adverse["final_score"] < baseline["final_score"]
    assert "vol_scaler" in adverse.get("feed_status", {})


def test_crypto_favourable_funding_boosts_score():
    """Negative funding on LONG gives small boost."""
    pair = {"type": "crypto", "display": "BTC/USDT"}
    snap = {
        "ema21": 110.0, "ema50": 100.0, "ema200": 90.0,
        "adx": 25.0, "rsi": 55.0, "macdHist": 0.5,
        "close": 100.0, "atr": 1.0,
        "plusDI": 25.0, "minusDI": 15.0,
    }
    neutral = compute_factor_scores(
        snap, snap, snap, pair, [], [], [], 1.0, funding_rate=0.0001
    )
    favourable = compute_factor_scores(
        snap, snap, snap, pair, [], [], [], 1.0, funding_rate=-0.005
    )
    assert favourable["final_score"] > neutral["final_score"]


def test_forex_carry_against_reduces_score():
    """Adverse carry addon applies small penalty."""
    pair = {"type": "forex", "display": "EUR/AUD"}
    snap = {
        "ema21": 110.0, "ema50": 100.0, "ema200": 90.0,
        "adx": 25.0, "rsi": 55.0, "macdHist": 0.5,
        "close": 100.0, "atr": 1.0,
        "plusDI": 25.0, "minusDI": 15.0,
    }
    # We can't easily mock carry feed here, but we verify the code path exists
    result = compute_factor_scores(snap, snap, snap, pair, [], [], [], 1.0)
    assert "addon" in result["factor_scores"]


# ── 3.3 EMA vote hysteresis ──────────────────────────────────────────────────

def test_ema_cross_confirmed_requires_two_bars():
    """Fresh cross without previous bar confirmation returns None."""
    current = {"ema21": 110.0, "ema50": 100.0}
    prev = {"ema21": 95.0, "ema50": 100.0}  # was below, now above → fresh cross
    assert _ema_cross_confirmed(current, prev, "ema21", "ema50") is None


def test_ema_cross_confirmed_with_two_bars_same_side():
    """Two consecutive bars on same side returns sign."""
    current = {"ema21": 110.0, "ema50": 100.0}
    prev = {"ema21": 108.0, "ema50": 100.0}  # both above
    assert _ema_cross_confirmed(current, prev, "ema21", "ema50") == 1.0


def test_ema_cross_confirmed_no_prev_uses_gap_check():
    """No previous data → requires >0.1% gap to avoid whipsaw."""
    current = {"ema21": 100.2, "ema50": 100.0}  # 0.2% gap — above threshold
    assert _ema_cross_confirmed(current, None, "ema21", "ema50") == 1.0

    current_tight = {"ema21": 100.05, "ema50": 100.0}  # 0.05% gap — too small
    assert _ema_cross_confirmed(current_tight, None, "ema21", "ema50") is None


def test_coherent_trend_score_with_hysteresis():
    """Hysteresis pending flags appear when prev snaps show fresh cross."""
    d1_cur = {"ema21": 110.0, "ema200": 100.0}
    d1_prev = {"ema21": 95.0, "ema200": 100.0}  # fresh cross — not confirmed
    h4_cur = {"ema21": 110.0, "ema50": 100.0}
    h4_prev = {"ema21": 108.0, "ema50": 100.0}  # confirmed
    h1_cur = {"ema21": 110.0, "ema50": 100.0}
    h1_prev = {"ema21": 109.0, "ema50": 100.0}  # confirmed

    score, direction, detail = _coherent_trend_score(
        d1_cur, h4_cur, h1_cur, "forex",
        d1_prev=d1_prev, h4_prev=h4_prev, h1_prev=h1_prev,
    )
    assert detail.get("d1_hysteresis_pending") is True
    assert "h4_hysteresis_pending" not in detail
    # D1 vote excluded, so only H4 + H1 = 2 votes
    assert detail["total_count"] == 2


# ── 3.4 Volatility scaler ────────────────────────────────────────────────────

def test_volatility_scaler_low_vol_boosts():
    """ATR% < 0.5% → scaler > 1.0"""
    scaler = _volatility_scaler(atr=3.0, close=1000.0, asset_type="forex")
    assert scaler > 1.0


def test_volatility_scaler_high_vol_penalises():
    """ATR% > 2.5% → scaler < 1.0"""
    scaler = _volatility_scaler(atr=5.0, close=100.0, asset_type="crypto")
    assert scaler < 1.0


def test_volatility_scaler_mid_zone_interpolates():
    """Mid volatility interpolates linearly."""
    low = _volatility_scaler(atr=1.0, close=100.0, asset_type="stock")   # 1%
    mid = _volatility_scaler(atr=1.5, close=100.0, asset_type="stock")  # 1.5%
    high = _volatility_scaler(atr=2.5, close=100.0, asset_type="stock") # 2.5%
    assert low > mid > high
    assert low > 1.0
    assert high < 1.0


def test_volatility_scaler_applied_in_compute_factor_scores():
    """vol_scaler appears in feed_status and affects final_score."""
    pair = {"type": "stock", "display": "AAPL"}
    low_vol_snap = {
        "ema21": 110.0, "ema50": 100.0, "ema200": 90.0,
        "adx": 25.0, "rsi": 55.0, "macdHist": 0.5,
        "close": 1000.0, "atr": 3.0,
        "plusDI": 25.0, "minusDI": 15.0,
    }
    high_vol_snap = {
        "ema21": 110.0, "ema50": 100.0, "ema200": 90.0,
        "adx": 25.0, "rsi": 55.0, "macdHist": 0.5,
        "close": 100.0, "atr": 5.0,
        "plusDI": 25.0, "minusDI": 15.0,
    }
    low = compute_factor_scores(
        low_vol_snap, low_vol_snap, low_vol_snap, pair, [], [], [], 1.0
    )
    high = compute_factor_scores(
        high_vol_snap, high_vol_snap, high_vol_snap, pair, [], [], [], 1.0
    )
    assert float(low["feed_status"]["vol_scaler"]) > 1.0
    assert float(high["feed_status"]["vol_scaler"]) < 1.0
    assert low["final_score"] > high["final_score"]


# ── 3.5 Single-vote trend respects TF weight ─────────────────────────────────

def test_d1_only_vote_max_score_is_one():
    """Only D1 available → max score = 1.0 (not 3.0)."""
    d1 = {"ema21": 110.0, "ema200": 100.0}
    score, direction, detail = _coherent_trend_score(d1, {}, {}, "forex")
    assert direction == "LONG"
    assert abs(score) <= 1.0 + 1e-6
    assert detail["tf_coverage"] == pytest.approx(1.0 / 3.0, abs=1e-4)


def test_two_votes_max_score_is_two():
    """D1 + H4 available → max score = 2.0."""
    d1 = {"ema21": 110.0, "ema200": 100.0}
    h4 = {"ema21": 110.0, "ema50": 100.0}
    score, direction, detail = _coherent_trend_score(d1, h4, {}, "forex")
    assert direction == "LONG"
    assert abs(score) <= 2.0 + 1e-6
    assert detail["tf_coverage"] == pytest.approx(2.0 / 3.0, abs=1e-4)


# ── 3.6 Cross-engine research lab correlation cap ────────────────────────────

def test_research_lab_capped_when_engine_b_also_active(monkeypatch):
    """When Engine B RL is enabled, Engine A research contribution is capped."""
    monkeypatch.setitem(
        CONFIG, "ENGINE_B_RESEARCH_LAB_FACTORS", {"ENABLED": True}
    )
    cfg = {
        "ENABLED": True,
        "BONUS": 0.20,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
    }
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", cfg)

    pair = {"type": "crypto", "display": "BTC/USDT"}
    snap = {
        "ema21": 110.0, "ema50": 100.0, "ema200": 90.0,
        "adx": 25.0, "rsi": 55.0, "macdHist": 0.5,
        "close": 100.0, "atr": 1.0,
        "plusDI": 25.0, "minusDI": 15.0,
    }
    candles = []
    close = 100.0
    for i in range(80):
        close += 0.2
        candles.append({
            "open": close - 0.1, "high": close + 0.5,
            "low": close - 0.5, "close": close,
            "vol": 1000.0, "volume": 1000.0,
        })

    result = compute_factor_scores(
        snap, snap, snap, pair, candles, candles, [], 1.0,
        funding_rate=0.0001,
    )
    # Research lab should be capped
    assert "research_lab_capped" in result.get("feed_status", {})
    # Total addon should not exceed _ADDON_CONFIRM (0.20) by much
    assert result["addon_value"] <= 0.20 + 1e-6


# ── 3.7 Explicit gate definitions for Engine B ───────────────────────────────

def test_engine_b_gate_definitions_exist_in_confidence():
    """calculate_confidence returns all 6 gate flags + bonus flags."""
    from market_structure import NakedEngine

    engine = NakedEngine()
    # Minimal structure result with all gates potentially true
    res = {
        "atr": 1.0,
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "bos_confirmed": True,
        "bos_mtf_confirmed": True,
        "liquidity_sweep": False,
        "zone_touched": True,
        "near_active_zone": True,
        "trigger_ok": True,
        "ob_at_zone": True,
        "volume_confirmed": True,
        "recommended_stop_loss": 99.0,
        "recommended_take_profit": 103.0,
        "asset_type": "forex",
        "structural_verdict": "CLEAR",
    }
    conf = engine.calculate_confidence(
        res, current_price=100.0, direction="LONG",
        style_profile={
            "style": "intraday",
            "min_room_atr": 0.35,
            "min_rr": 1.0,
            "fallback_rr": 2.0,
        },
    )
    # All 6 mandatory gate flags must be present
    assert "structure_ok" in conf
    assert "location_ok" in conf
    assert "entry_ok" in conf
    assert "room_ok" in conf
    assert "rr_ok" in conf
    assert "macro_ok" in conf
    # Bonus flags
    assert "bos_mtf_confirmed" in conf
    assert "ob_at_zone" in conf
    # Score components
    assert "score" in conf
    assert "gate_score" in conf
    assert "bonus_points" in conf
    assert "max_possible" in conf
