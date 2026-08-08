"""Engine A scoring profile resolver tests."""

from __future__ import annotations

from pathlib import Path

from engine_a_scoring_profile import (
    resolve_engine_a_scoring_profile,
    scoring_profile_public_dict,
    snap_for_tf,
)


def test_default_profile_preserves_d1_h4_h1_stack():
    profile = resolve_engine_a_scoring_profile(
        score_group="forex_majors",
        asset_type="forex",
        style="swing",
    )
    assert profile["trend_timeframes"] == ["D1", "H4", "H1"]
    assert profile["momentum_tf"] == "H4"
    assert profile["regime_tf"] == "H4"
    assert profile["execution_tf"] == "D1"


def test_style_adjusts_trend_weights():
    swing = resolve_engine_a_scoring_profile(
        score_group="crypto_btc", asset_type="crypto", style="swing"
    )
    scalp = resolve_engine_a_scoring_profile(
        score_group="crypto_btc", asset_type="crypto", style="scalp"
    )
    assert swing["trend_weights"]["d1_ema_trend"] > scalp["trend_weights"]["d1_ema_trend"]
    assert scalp["trend_weights"]["ema_trend"] > swing["trend_weights"]["ema_trend"]
    assert scalp["execution_tf"] == "H1"
    assert swing["execution_tf"] == "D1"


def test_auto_and_explicit_styles_preserve_style_owned_timeframes():
    auto_exotic = resolve_engine_a_scoring_profile(
        score_group="forex_exotics", asset_type="forex", style="auto"
    )
    intraday_exotic = resolve_engine_a_scoring_profile(
        score_group="forex_exotics", asset_type="forex", style="intraday"
    )
    swing_exotic = resolve_engine_a_scoring_profile(
        score_group="forex_exotics", asset_type="forex", style="swing"
    )

    assert auto_exotic["style"] == "swing"
    assert auto_exotic["execution_tf"] == "D1"
    assert intraday_exotic["style"] == "intraday"
    assert intraday_exotic["execution_tf"] == "H4"
    assert swing_exotic["execution_tf"] == "D1"


def test_flat_group_timeframe_override_cannot_collapse_styles(monkeypatch):
    from config import CONFIG

    profile_cfg = CONFIG.setdefault("ENGINE_A_SCORING_PROFILE", {})
    by_group = dict(profile_cfg.get("BY_SCORE_GROUP") or {})
    by_group["forex_exotics"] = {
        **dict(by_group.get("forex_exotics") or {}),
        "execution_tf": "H1",
        "chart_tf": "H1",
    }
    monkeypatch.setitem(profile_cfg, "BY_SCORE_GROUP", by_group)

    intraday = resolve_engine_a_scoring_profile(
        score_group="forex_exotics", asset_type="forex", style="intraday"
    )
    swing = resolve_engine_a_scoring_profile(
        score_group="forex_exotics", asset_type="forex", style="swing"
    )

    assert intraday["execution_tf"] == "H4"
    assert intraday["chart_tf"] == "H4"
    assert swing["execution_tf"] == "D1"
    assert swing["chart_tf"] == "D1"


def test_snap_for_tf_maps_snaps():
    d1 = {"close": 1.0, "rsi": 55.0}
    h4 = {"close": 2.0, "rsi": 60.0}
    h1 = {"close": 3.0, "rsi": 65.0}
    profile = resolve_engine_a_scoring_profile(
        score_group="forex_majors", asset_type="forex", style="intraday"
    )
    assert snap_for_tf(profile, d1_snap=d1, h4_snap=h4, h1_snap=h1, tf="H4") is h4
    assert snap_for_tf(profile, d1_snap=d1, h4_snap=h4, h1_snap=h1, tf="H1") is h1


def test_energy_oil_intraday_override_does_not_replace_swing_style():
    intraday = resolve_engine_a_scoring_profile(
        score_group="energy_oil",
        asset_type="commodity",
        style="intraday",
    )
    swing = resolve_engine_a_scoring_profile(
        score_group="energy_oil", asset_type="commodity", style="swing"
    )
    assert intraday["trend_timeframes"] == ["D1", "H4", "H1"]
    assert intraday["trend_weights"] == {
        "d1_ema_trend": 0.20,
        "h4_ema_trend": 0.45,
        "ema_trend": 0.35,
    }
    assert swing["trend_weights"] == {
        "d1_ema_trend": 0.50,
        "h4_ema_trend": 0.30,
        "ema_trend": 0.20,
    }


def test_stock_and_index_score_groups_keep_style_specific_trend_weights():
    intraday_expected = {"d1_ema_trend": 0.40, "h4_ema_trend": 0.35, "ema_trend": 0.25}
    swing_expected = {"d1_ema_trend": 0.50, "h4_ema_trend": 0.30, "ema_trend": 0.20}
    cases = [
        ("us_stock_single", "stock"),
        ("stock_other", "stock"),
        ("us_indices_trackers", "index"),
        ("eu_indices", "index"),
        ("asian_indices", "index"),
        ("index_other", "index"),
    ]
    for score_group, asset_type in cases:
        intraday = resolve_engine_a_scoring_profile(
            score_group=score_group,
            asset_type=asset_type,
            style="intraday",
        )
        swing = resolve_engine_a_scoring_profile(
            score_group=score_group,
            asset_type=asset_type,
            style="swing",
        )
        assert intraday["trend_weights"] == intraday_expected
        assert swing["trend_weights"] == swing_expected


def test_config_keeps_single_engine_a_min_confidence_enabled_key():
    config_text = Path(__file__).resolve().parents[1].joinpath("config.yaml").read_text(encoding="utf-8")
    assert config_text.count("ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED:") == 1


def test_public_dict_camel_case_keys():
    profile = resolve_engine_a_scoring_profile(
        score_group="forex_majors", asset_type="forex", style="intraday"
    )
    public = scoring_profile_public_dict(profile)
    assert public["style"] == "intraday"
    assert public["trendTimeframes"] == ["D1", "H4", "H1"]
    assert public["executionTf"] == "H4"
