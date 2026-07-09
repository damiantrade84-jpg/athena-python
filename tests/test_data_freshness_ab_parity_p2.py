"""Phase 2: A/B freshness parity, follow-through confirmed-only, trusted aggTrade."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import config
from athena_app.services.data_freshness import (
    d1_consumed_by_score_group,
    pre_scoring_allows_confirmed_only_stale_1,
)
from market_structure import (
    NakedEngine,
    _follow_through_candle_series,
    engine_b_profile_trusted_score_groups,
)
from scanner import _engine_b_scan_freshness_stale_tfs


def test_unknown_score_group_requires_d1_fail_closed():
    assert d1_consumed_by_score_group(None, "intraday") is True
    assert d1_consumed_by_score_group("", "intraday") is True
    assert d1_consumed_by_score_group("totally_unknown_group_xyz", "intraday") is True


def test_confirmed_only_stale_1_allowed_but_stale_multi_still_blocks_engine_b():
    pair = {"display": "BTC/USDT", "type": "crypto", "source": "binance"}
    assert pre_scoring_allows_confirmed_only_stale_1(pair) is True

    with patch(
        "athena_app.services.market_state.candle_freshness_diagnostic"
    ) as mock_diag:
        mock_diag.side_effect = lambda _pair, tf, _candles, **_: {
            "stalenessSeverity": (
                "stale_1_bucket"
                if tf == "H1"
                else ("stale_multi_bucket" if tf == "H4" else "fresh")
            ),
            "bucketLag": 3 if tf == "H4" else 1,
        }
        stale, _diag = _engine_b_scan_freshness_stale_tfs(
            pair,
            [{"time": 1}] * 5,
            [{"time": 2}] * 5,
            [{"time": 3}] * 5,
            config={"ENGINE_B_SCAN_FRESHNESS_GATE": True},
        )
    assert "H1:stale_1_bucket" not in stale
    assert "H4:stale_multi_bucket" in stale


def test_follow_through_confirmed_only_drops_newest_bar(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FOLLOW_THROUGH_CONFIRMED_ONLY", True)
    candles = [{"close": float(i)} for i in range(10)]
    series = _follow_through_candle_series(candles)
    assert len(series) == 9
    assert series[-1]["close"] == 8.0


def test_follow_through_confirmed_only_can_be_disabled(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FOLLOW_THROUGH_CONFIRMED_ONLY", False)
    candles = [{"close": float(i)} for i in range(10)]
    series = _follow_through_candle_series(candles)
    assert len(series) == 10


def _crypto_conf_res(**overrides):
    base = {
        "atr": 1.0,
        "asset_type": "crypto",
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "bos_confirmed": True,
        "liquidity_sweep": False,
        "choch_confirmed": False,
        "zone_touched": True,
        "near_active_zone": False,
        "trigger_ok": True,
        "bos_volume_confirmed": True,
        "strong_close": True,
        "inside_break_candle": False,
        "engulfing_candle": False,
        "ob_at_zone": True,
        "breaker_block": False,
        "bos_mtf_confirmed": False,
        "distance_to_res": 5.0,
        "distance_to_sup": 5.0,
        "recommended_stop_loss": 99.0,
        "recommended_take_profit": 105.0,
        "aggtrade_required": True,
        "aggtrade_available": False,
        "aggtrade_reason": "insufficient_trade_buckets",
        "engine_b_data_fidelity": {
            "vp_uses_real_trade_buckets": False,
            "cvd_uses_real_trade_buckets": False,
        },
    }
    base.update(overrides)
    return base


def _style_profile(score_group: str):
    return {
        "style": "intraday",
        "min_score": 4.0,
        "min_room_atr": 0.35,
        "min_rr": 1.0,
        "fallback_rr": 2.0,
        "require_macro_align": False,
        "score_group": score_group,
    }


def test_trusted_crypto_group_uses_required_aggtrade_mode(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_AGGTRADE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_REQUIRE_AGGTRADE_FOR_PASS", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_AGGTRADE_MODE", "degraded")
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_AGGTRADE_TRUSTED_MODE", "required")
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_PROFILE_TRUSTED_SCORE_GROUPS",
        list(engine_b_profile_trusted_score_groups()) or ["crypto_btc"],
    )

    out = NakedEngine().calculate_confidence(
        _crypto_conf_res(),
        current_price=100.0,
        direction="LONG",
        style_profile=_style_profile("crypto_btc"),
    )
    assert out["aggtrade_mode"] == "required"
    assert out["passed"] is False
    assert "aggtrade_required_for_crypto" in (out.get("failed_gate_names") or []) or (
        "engine_b_aggtrade_required_false" in (out.get("hard_fail_reasons") or [])
    )


def test_non_trusted_crypto_keeps_degraded_aggtrade(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_AGGTRADE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_REQUIRE_AGGTRADE_FOR_PASS", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_AGGTRADE_MODE", "degraded")
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_AGGTRADE_TRUSTED_MODE", "required")
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_AGGTRADE_MISSING_PENALTY", 0.5)

    out = NakedEngine().calculate_confidence(
        _crypto_conf_res(),
        current_price=100.0,
        direction="LONG",
        style_profile=_style_profile("crypto_meme_untrusted_xyz"),
    )
    assert out["aggtrade_mode"] == "degraded"
    assert out["passed"] is True
    assert out["aggtrade_missing_penalty"] == pytest.approx(0.5)
