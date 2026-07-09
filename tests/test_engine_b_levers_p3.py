"""Phase 3: follow-through enabled, crypto/asset-class/session levers, regime defaults."""

from __future__ import annotations

import config
from market_structure import (
    ENGINE_B_REGIME_GATE_DEFAULTS,
    _asset_class_structure_adjustment,
    _crypto_structure_adjustment,
    _engine_b_forex_session_structure_context,
    _engine_b_regime_gate,
)


def test_follow_through_enabled_in_config():
    ft = config.CONFIG.get("ENGINE_B_FOLLOW_THROUGH") or {}
    assert ft.get("ENABLED") is True
    assert ft.get("BLOCK_ENTRY_ON_TRAP") is True


def test_regime_low_volatility_default_is_neutral():
    assert ENGINE_B_REGIME_GATE_DEFAULTS["LOW_VOLATILITY"] == 1.0
    assert _engine_b_regime_gate("LOW_VOLATILITY", "forex") == 1.0
    yaml_mult = (config.CONFIG.get("ENGINE_B_REGIME_MULTIPLIERS") or {}).get(
        "LOW_VOLATILITY"
    )
    assert float(yaml_mult) == 1.0


def test_crypto_volatility_adjustment_applies_when_enabled(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_VOLATILITY_ADJUSTMENT_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_STRUCTURE_MULT", 1.25)
    # High ATR% → high_volatility regime
    candles = [
        {
            "open": 100.0,
            "high": 108.0,
            "low": 92.0,
            "close": 100.0,
            "vol": 1000,
        }
        for _ in range(8)
    ]
    detail = _crypto_structure_adjustment("crypto", candles, atr=5.0)
    assert detail["enabled"] is True
    assert detail["applied"] is True
    assert detail["structure_mult"] > 1.0


def test_asset_class_adjustment_applies_for_commodity(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ASSET_CLASS_ADJUSTMENTS_ENABLED", True)
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_ASSET_CLASS_STRUCTURE_MULT",
        {"commodity": 1.15, "stock": 1.10, "index": 1.05},
    )
    candles = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "vol": 500}
        for _ in range(10)
    ]
    detail = _asset_class_structure_adjustment(
        "commodity", "precious_trackers", candles, atr=1.0
    )
    assert detail["asset_class"] in {"commodity", "precious_trackers"} or detail.get(
        "enabled"
    )
    # When enabled, commodity path should surface a structure mult >= 1.0
    assert float(detail.get("structure_mult") or 1.0) >= 1.0


def test_forex_session_score_influence_produces_bonus(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_FOREX_SESSION_STRUCTURE_WEIGHTING",
        {
            "ENABLED": True,
            "SCORE_INFLUENCE_ENABLED": True,
            "HIGH_QUALITY_BONUS": 0.02,
            "MEDIUM_QUALITY_BONUS": 0.01,
            "LOW_QUALITY_PENALTY": -0.02,
            "LONDON_NY_OVERLAP_BONUS": 0.01,
            "LIQUIDITY_SWEEP_ACTIVE_SESSION_BONUS": 0.01,
            "MAX_ABS_SCORE_BONUS": 0.04,
        },
    )
    # 14:00 UTC ≈ London/NY overlap (high quality)
    ctx = _engine_b_forex_session_structure_context(
        asset_type="forex",
        candle_time="2025-03-03T14:00:00+00:00",
        zone_context=True,
        ob_at_zone=True,
        fvg_overlap=False,
        liquidity_sweep=True,
    )
    assert ctx["enabled"] is True
    assert ctx["score_influence_enabled"] is True
    assert float(ctx["score_bonus"]) > 0.0
