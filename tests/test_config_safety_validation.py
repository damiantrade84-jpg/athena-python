"""Focused tests for critical trading-safety config validation."""

from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import (
    CONFIG,
    _REAL_ORDER_CONFIRM_ENV,
    _REAL_ORDER_CONFIRM_TOKEN,
    _fatal_config_validation,
    _unknown_top_level_config_keys,
    _validate_critical_safety_config,
    ConfigValidationError,
)


def _base_safety_config() -> dict:
    return {
        "REAL_ORDERS_ALLOWED": False,
        "PAPER_SOAK": {
            "ENABLED": True,
            "REAL_ORDERS_ALLOWED": False,
        },
        "EXECUTOR_MODE": "paper",
        "EXECUTION_ENABLED": True,
        "EXECUTION_SINGLE_LEG_ONLY": True,
        "AUTO_EXECUTE": False,
        "AUTO_TRADE_ENABLED": False,
        "RISK_ENGINE_ENABLED": True,
        "MT5_EXECUTION_ENABLED": True,
        "BYBIT_EXECUTION_ENABLED": True,
        "FX_FACTOR_SCANNER_ENABLED": True,
        "FX_FACTOR_EXECUTION_ENABLED": True,
        "FX_FACTOR_ALLOW_LIVE_EXECUTION": True,
        "FX_FACTOR_REQUIRE_VALIDATION_FOR_EXECUTION": True,
        "FX_FACTOR_STRICT_DATA_DEFAULT": True,
        "FX_FACTOR_SCAN_INCLUDE_BLOCKED_DEFAULT": True,
        "FX_FACTOR_SCAN_REFRESH_DECISIONS_DEFAULT": True,
        "BYBIT_LEVERAGE": 1,
        "RISK_PCT": 0.01,
        "MAX_RISK_PER_TRADE": 0.03,
        "MAX_PORTFOLIO_HEAT": 0.06,
        "MAX_OPEN_POSITIONS": 20,
        "MAX_CORRELATED_POSITIONS": 3,
        "DAILY_LOSS_LIMIT": 0.05,
        "DRAWDOWN_REDUCE_THRESHOLD": 0.10,
        "DRAWDOWN_STOP_THRESHOLD": 0.15,
    }


def test_critical_safety_config_rejects_bad_type():
    cfg = _base_safety_config()
    cfg["MAX_OPEN_POSITIONS"] = "20"

    with pytest.raises(ConfigValidationError):
        _validate_critical_safety_config(cfg, env={})


def test_critical_safety_config_rejects_missing_key():
    cfg = _base_safety_config()
    del cfg["MAX_RISK_PER_TRADE"]

    with pytest.raises(ConfigValidationError):
        _validate_critical_safety_config(cfg, env={})


def test_unknown_top_level_keys_are_reported():
    unknown = _unknown_top_level_config_keys(
        {
            "REAL_ORDERS_ALLOWED": False,
            "TYPO_REAL_ORDERS_ALLOWED": True,
        },
        known_keys=set(_base_safety_config()),
    )

    assert unknown == ["TYPO_REAL_ORDERS_ALLOWED"]


def test_real_orders_allowed_requires_explicit_env_confirmation():
    cfg = _base_safety_config()
    cfg["REAL_ORDERS_ALLOWED"] = True

    with pytest.raises(ConfigValidationError):
        _validate_critical_safety_config(cfg, env={})


def test_real_orders_allowed_accepts_exact_confirmation_token():
    cfg = copy.deepcopy(_base_safety_config())
    cfg["REAL_ORDERS_ALLOWED"] = True
    cfg["PAPER_SOAK"]["REAL_ORDERS_ALLOWED"] = True

    _validate_critical_safety_config(
        cfg,
        env={_REAL_ORDER_CONFIRM_ENV: _REAL_ORDER_CONFIRM_TOKEN},
    )


def test_signal_max_age_sec_default_matches_yaml():
    from config import CONFIG

    assert CONFIG["SIGNAL_MAX_AGE_SEC"] == 300


def test_drawdown_stop_enabled_false_rejected():
    cfg = _base_safety_config()
    cfg["DRAWDOWN_STOP_ENABLED"] = False

    with pytest.raises(ConfigValidationError):
        _validate_critical_safety_config(cfg, env={})


def test_pair_profile_disable_filters_is_fatally_rejected(monkeypatch):
    import config as config_module

    cfg = copy.deepcopy(CONFIG)
    cfg["PAIR_PROFILES"] = {"USD/MXN": {"disable_filters": ["obv"]}}
    messages: list[str] = []
    monkeypatch.setattr(
        config_module.log,
        "critical",
        lambda template, *args: messages.append(template % args if args else template),
    )

    with pytest.raises(ConfigValidationError):
        _fatal_config_validation(cfg)

    assert any(
        "PAIR_PROFILES['USD/MXN'].disable_filters is inactive in Engine A V3" in message
        for message in messages
    )
