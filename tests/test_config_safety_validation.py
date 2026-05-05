"""Focused tests for critical trading-safety config validation."""

from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import (
    _REAL_ORDER_CONFIRM_ENV,
    _REAL_ORDER_CONFIRM_TOKEN,
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
        "AUTO_EXECUTE": False,
        "AUTO_TRADE_ENABLED": False,
        "RISK_ENGINE_ENABLED": True,
        "MT5_EXECUTION_ENABLED": True,
        "BYBIT_EXECUTION_ENABLED": True,
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
