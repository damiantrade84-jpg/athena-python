"""Regression tests for the Engine B timeframe-structure audit fixes.

Covers:
  * M15 remains authoritative across every conditional-M5 asset group
  * M5 refinement data cannot block or veto an M15 trigger
  * Engine B swing keeps H4 as primary structure across score groups
  * Snapshot scoring profile preserves the policy structural/zone/ATR roles
  * volatility bands are compared on an H4-equivalent ATR%
  * macro sequence is not double-counted when bias TF == structure TF
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from engine_b_snapshot import resolve_engine_b_style_profile
from engine_b_quality import (
    compute_structure_alignment_score,
    macro_sequence_is_independent,
)
from market_structure import (
    NakedEngine,
    _DIAGNOSTIC_TRIGGER_TFS,
    _volatility_band_atr_pct,
    engine_b_live_trigger_kwargs,
    resolve_engine_b_tfs,
    resolve_engine_b_trigger_tf_calibration,
)
from timeframe_policy import resolve_timeframe_policy

_M5_REFINEMENT_SYMBOLS = [
    ("GBP/USD", "forex"),
    ("USD/JPY", "forex"),
    ("GBP/JPY", "forex"),
    ("XAU/USD", "commodity"),
    ("WTI Oil", "commodity"),
    ("SpotBrent", "commodity"),
    ("NASDAQ-100", "index"),
    ("US30", "index"),
    ("GER40", "index"),
    ("BTC/USDT", "crypto"),
    ("ETH/USDT", "crypto"),
    ("SOL/USDT", "crypto"),
    ("AAPL", "stock"),
    ("SPY", "etf"),
]


def _series(close, n=60, half=0.5):
    return [
        {
            "open": close,
            "high": close + half,
            "low": close - half,
            "close": close,
            "vol": 100.0,
            "volume": 100.0,
            "time": 1_700_000_000 + i * 60,
        }
        for i in range(n)
    ]


def _with_bull_engulfing(rows, half):
    """Bearish bar followed by a bullish engulfing bar."""
    rows = [dict(r) for r in rows]
    base = rows[-1]["close"]
    rows[-2] = {
        "open": base + half, "high": base + half * 1.1, "low": base - half * 1.1,
        "close": base - half, "vol": 100.0, "volume": 100.0, "time": rows[-2]["time"],
    }
    rows[-1] = {
        "open": base - half, "high": base + half * 2, "low": base - half * 1.2,
        "close": base + half * 1.8, "vol": 100.0, "volume": 100.0, "time": rows[-1]["time"],
    }
    return rows


# ── M5 reachability ──────────────────────────────────────────────────────────

def test_m5_remains_available_for_refinement_diagnostics():
    assert "M5" in _DIAGNOSTIC_TRIGGER_TFS


@pytest.mark.parametrize("symbol,asset", _M5_REFINEMENT_SYMBOLS)
def test_m5_policy_symbols_use_m15_as_authoritative_trigger(monkeypatch, symbol, asset):
    """Every conditional-M5 production profile must keep M5 refinement-only."""
    monkeypatch.setitem(config.CONFIG, "TF_POLICY_MODE", "enforced_demo")
    tfs = resolve_engine_b_tfs(asset, "intraday", symbol=symbol)
    assert tfs["trigger"] == "M15"
    assert tfs["execution"] == "M15"
    assert tfs["m5_policy"] == "conditional"
    assert tfs["execution_prerequisite"] == "M15"
    kwargs = engine_b_live_trigger_kwargs(
        {"entry_tf": tfs["trigger"]}, {"M5": [{}], "M15": [{}]}
    )
    assert kwargs.get("trigger_tf_override") == "M15"
    assert kwargs.get("role_candles") is not None


def test_resolve_engine_b_tfs_exposes_m5_policy_and_prerequisite(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "TF_POLICY_MODE", "enforced_demo")
    fast = resolve_engine_b_tfs("forex", "intraday", symbol="GBP/USD")
    assert fast["trigger"] == "M15"
    assert fast["m5_policy"] == "conditional"
    assert fast["execution_prerequisite"] == "M15"
    standard = resolve_engine_b_tfs("forex", "intraday", symbol="EUR/USD")
    assert standard["m5_policy"] == "disabled"
    assert standard["execution_prerequisite"] is None


def test_snapshot_profile_uses_d1_for_structural_scoring_zone_and_atr():
    """The pure snapshot scorer must consume the same D1 swing roles as live scans."""
    style, profile = resolve_engine_b_style_profile(
        "swing", "forex_exotics_restricted", "forex", symbol="USD/ZAR"
    )
    assert style == "swing"
    assert profile["bias_tf"] == "D1"
    assert profile["structure_tf"] == "D1"
    assert profile["zone_tf"] == "D1"
    assert profile["atr_tf"] == "D1"
    assert profile["setup_tf"] == "H4"
    assert profile["entry_tf"] == "H1"


# ── conditional M5 prerequisite ──────────────────────────────────────────────

def _run_m15_with_optional_m5(monkeypatch, role_candles):
    monkeypatch.setitem(config.CONFIG, "TF_POLICY_MODE", "enforced_demo")
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRIP_FORMING_STRUCT", False)
    engine = NakedEngine()
    pair = {"display": "GBP/USD", "type": "forex", "source": "mt5"}
    pre = engine.precompute_structure_data(
        _series(100.0, half=6.0),
        _series(100.0, half=4.0),
        _series(100.0, half=1.0),
        100.0,
        1.0,
        style="intraday",
        asset_type="forex",
        pair=pair,
        enable_zone_registry=False,
        **engine_b_live_trigger_kwargs({"entry_tf": "M15"}, role_candles),
    )
    if pre.get("_error"):
        return pre, None
    return pre, engine.analyze_structure_direction(pre, 100.0, "LONG")


def test_missing_m5_refinement_does_not_block_m15_trigger(monkeypatch):
    pre, res = _run_m15_with_optional_m5(
        monkeypatch,
        {
            "M15": _with_bull_engulfing(_series(100.0, half=0.2), 0.2),
        },
    )
    assert pre.get("_error") is None
    assert pre["_tfs"]["trigger"] == "M15"
    assert pre["m5_conditional_required"] is False
    assert res["trigger_timeframe"] == "M15"
    assert res["trigger_ok"] is True


def test_opposing_m5_refinement_cannot_veto_m15_trigger(monkeypatch):
    pre, res = _run_m15_with_optional_m5(
        monkeypatch,
        {
            "M5": _series(100.0, half=0.1),
            "M15": _with_bull_engulfing(_series(100.0, half=0.2), 0.2),
        },
    )
    assert pre.get("_error") is None
    assert pre["m5_conditional_required"] is False
    assert res["trigger_timeframe"] == "M15"
    assert res["trigger_ok"] is True


def test_missing_authoritative_m15_trigger_still_fails_closed(monkeypatch):
    pre, _ = _run_m15_with_optional_m5(
        monkeypatch,
        {"M5": _with_bull_engulfing(_series(100.0, half=0.1), 0.1)},
    )
    assert pre.get("_error") == "missing_required_trigger_timeframe:M15"


def test_missing_authoritative_m15_trigger_fails_closed_not_substituted(monkeypatch):
    """Engine B scalp keeps H4 structure; H1 must not replace its M15 trigger."""
    monkeypatch.setitem(config.CONFIG, "TF_POLICY_MODE", "enforced_demo")
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRIP_FORMING_STRUCT", False)
    pre = NakedEngine().precompute_structure_data(
        _series(100.0, half=6.0),
        _series(100.0, half=4.0),
        _series(100.0, half=1.0),
        100.0,
        1.0,
        style="scalp",
        asset_type="forex",
        pair={"display": "GBP/USD", "type": "forex", "source": "mt5"},
        enable_zone_registry=False,
    )
    assert pre.get("_error") == "missing_required_trigger_timeframe:M15"


# ── group-aware swing ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "symbol,asset,structure,setup,trigger",
    [
        ("EUR/USD", "forex", "D1", "H4", "H1"),
        ("XAU/USD", "commodity", "D1", "H4", "H1"),
        ("NASDAQ-100", "index", "D1", "H4", "H1"),
        ("BTC/USDT", "crypto", "D1", "H4", "H1"),
        ("EUR/GBP", "forex", "D1", "H4", "H1"),
        ("USD/ZAR", "forex", "D1", "H4", "H1"),
        ("XPT/USD", "commodity", "D1", "H4", "H1"),
        ("DOGE/USDT", "crypto", "D1", "H4", "H1"),
    ],
)
def test_engine_b_swing_is_group_aware(symbol, asset, structure, setup, trigger):
    policy = resolve_timeframe_policy(
        symbol, asset, None, "swing", None, engine_id="engine_b"
    )
    assert policy.structure_tf.value == structure
    assert policy.setup_tf.value == setup
    assert policy.trigger_tf.value == trigger
    assert policy.bias_tf.value == "D1"


# ── volatility band timeframe normalisation ──────────────────────────────────

def test_volatility_bands_normalise_to_h4_reference(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG, "ENGINE_B_VOLATILITY_BAND_TF_NORMALIZATION", True
    )
    h4_value, h4_scale = _volatility_band_atr_pct(0.01, "H4")
    assert h4_value == pytest.approx(0.01)
    assert h4_scale == pytest.approx(1.0)
    # H1 ATR% is ~half the H4 value, so it must be scaled up before banding.
    h1_value, h1_scale = _volatility_band_atr_pct(0.01, "H1")
    assert h1_scale == pytest.approx(2.0)
    assert h1_value == pytest.approx(0.02)
    # D1 ATR% is ~2.45x the H4 value, so it must be scaled down.
    d1_value, d1_scale = _volatility_band_atr_pct(0.01, "D1")
    assert d1_scale < 1.0
    assert d1_value < 0.01


def test_volatility_band_normalisation_is_reversible(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG, "ENGINE_B_VOLATILITY_BAND_TF_NORMALIZATION", False
    )
    value, scale = _volatility_band_atr_pct(0.01, "D1")
    assert value == pytest.approx(0.01)
    assert scale == pytest.approx(1.0)


# ── macro sequence double-count guard ────────────────────────────────────────

def test_macro_sequence_flagged_dependent_when_bias_tf_equals_structure_tf():
    assert macro_sequence_is_independent(
        {"structure_tf": "H1", "macro_sequence_tf": "H4"}
    )
    assert not macro_sequence_is_independent(
        {"structure_tf": "H4", "macro_sequence_tf": "H4"}
    )
    # Unknown timeframes keep the historic behaviour.
    assert macro_sequence_is_independent({"structure_tf": "H1"})


def test_structure_alignment_does_not_double_count_same_timeframe(monkeypatch):
    # Legacy ladder only: sequences earn alignment credit again when the
    # retired direction authority is explicitly restored.
    monkeypatch.setitem(
        config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", True
    )
    aligned_two_tf = compute_structure_alignment_score(
        {
            "current_swing_sequence": "HH_HL",
            "macro_swing_sequence": "HH_HL",
            "structure_tf": "H1",
            "macro_sequence_tf": "H4",
        },
        "LONG",
    )
    aligned_same_tf = compute_structure_alignment_score(
        {
            "current_swing_sequence": "HH_HL",
            "macro_swing_sequence": "HH_HL",
            "structure_tf": "H4",
            "macro_sequence_tf": "H4",
        },
        "LONG",
    )
    assert aligned_two_tf == pytest.approx(1.0)
    assert aligned_same_tf == pytest.approx(0.65)


def test_structure_alignment_sequence_earns_nothing_when_retired():
    aligned_two_tf = compute_structure_alignment_score(
        {
            "current_swing_sequence": "HH_HL",
            "macro_swing_sequence": "HH_HL",
            "structure_tf": "H1",
            "macro_sequence_tf": "H4",
        },
        "LONG",
    )
    assert aligned_two_tf == pytest.approx(0.0)


# ── per-timeframe trigger calibration ────────────────────────────────────────

def test_trigger_calibration_accepts_per_timeframe_subrows(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_TRIGGER_TF_CALIBRATION",
        {
            "forex_majors": {
                "trigger_atr": 10,
                "rejection_wick_body": 1.5,
                "M5": {"trigger_atr": 12},
            }
        },
    )
    flat = resolve_engine_b_trigger_tf_calibration("forex_majors", "forex", "M15")
    assert flat["trigger_atr"] == 10
    assert flat["rejection_wick_body"] == pytest.approx(1.5)
    tf_row = resolve_engine_b_trigger_tf_calibration("forex_majors", "forex", "M5")
    assert tf_row["trigger_atr"] == 12
    # Unlisted keys still inherit the flat group values.
    assert tf_row["rejection_wick_body"] == pytest.approx(1.5)
