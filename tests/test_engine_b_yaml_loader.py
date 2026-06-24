import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine_b_yaml_loader import translate_engine_b_config


def _sample_doc():
    return {
        "base_thresholds": {
            "scalp": {"min_score": 4.0, "min_rr": 1.5, "fallback_rr": 2.0},
            "intraday": {"min_score": 4.5, "min_rr": 1.5, "fallback_rr": 2.0},
            "swing": {"min_score": 5.0, "min_rr": 2.0, "fallback_rr": 3.0},
        },
        "regime_multipliers": {
            "TRENDING": 0.95,
            "RANGING": 1.10,
            "HIGH_VOLATILITY": 1.10,
            "LOW_VOLATILITY": 0.90,
            "UNKNOWN": 1.0,
        },
        "regime_scaling_enabled": True,
        "crypto_aggtrade": {
            "mode": "degraded",
            "alignment_bonus": 0.5,
            "missing_penalty": 0.5,
        },
        "space_gate": {"rr_substitute_min_atr_floor": 0.5},
        "execution_levels": {
            "max_sl_pct": 0.02,
            "max_sl_atr_default": 3.0,
            "min_sl_atr_default": 0.75,
            "synthetic_fallback_rr_tp_enabled": True,
        },
        "volume_profile": {
            "trusted_groups": ["crypto_btc", "us_stock_single"],
        },
        "forex_session": {
            "asian_skip_enabled": True,
            "asian_active_currencies": ["JPY", "AUD"],
        },
        "group_overrides": [
            {"style": "intraday", "group": "forex_majors", "min_rr": 1.3},
            {
                "style": "swing",
                "group": "crypto_doge",
                "min_room_atr": 1.2,
                "min_rr": 1.9,
                "space_rr_substitute": True,
            },
        ],
    }


def test_translate_engine_b_yaml_maps_schema_to_runtime_config():
    out = translate_engine_b_config(_sample_doc())

    assert out["ENGINE_B_REGIME_MULTIPLIERS"]["RANGING"] == pytest.approx(1.10)
    assert out["ENGINE_B_REGIME_MULTIPLIERS_ENABLED"] is True
    assert out["ENGINE_B_CRYPTO_AGGTRADE_MODE"] == "degraded"
    assert out["ENGINE_B_CRYPTO_AGGTRADE_MISSING_PENALTY"] == pytest.approx(0.5)
    assert out["ENGINE_B_SPACE_RR_SUBSTITUTE_MIN_ATR_FLOOR"] == pytest.approx(0.5)
    assert out["ENGINE_B_MAX_SL_ATR_DEFAULT"] == pytest.approx(3.0)
    assert out["ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP"] is True
    assert out["ENGINE_B_PROFILE_TRUST_MODE"] == "score_group"
    assert out["ENGINE_B_PROFILE_TRUSTED_SCORE_GROUPS"] == ["crypto_btc", "us_stock_single"]
    assert out["ENGINE_B_FOREX_ASIAN_SESSION_SKIP_ENABLED"] is True
    assert out["ENGINE_B_ASIAN_ACTIVE_CURRENCIES"] == ["JPY", "AUD"]

    profiles = out["NAKED_ENGINE"]["style_profiles"]
    assert profiles["intraday"]["min_score"] == pytest.approx(4.5)
    assert profiles["swing"]["fallback_rr"] == pytest.approx(3.0)
    assert out["ENGINE_B_STYLE_MIN_SCORE_BY_STYLE"] == {
        "scalp": 4.0,
        "intraday": 4.5,
        "swing": 5.0,
    }

    overrides = out["NAKED_ENGINE"]["score_group_overrides"]
    assert overrides["forex_majors"]["intraday"]["min_rr"] == pytest.approx(1.3)
    assert overrides["crypto_doge"]["swing"]["min_room_atr"] == pytest.approx(1.2)
    assert overrides["crypto_doge"]["swing"]["space_rr_substitute"] is True


def test_translate_engine_b_yaml_rejects_invalid_aggtrade_mode():
    doc = _sample_doc()
    doc["crypto_aggtrade"]["mode"] = "hardmaybe"

    with pytest.raises(ValueError, match="crypto_aggtrade.mode"):
        translate_engine_b_config(doc)


def test_translate_engine_b_yaml_rejects_zero_regime_multiplier():
    doc = _sample_doc()
    doc["regime_multipliers"]["TRENDING"] = 0.0

    with pytest.raises(ValueError, match="regime_multipliers.TRENDING"):
        translate_engine_b_config(doc)
