"""Demo-only gate tests."""

from __future__ import annotations

import inspect

from athena_ase.gates.demo_only import assert_demo
from athena_ase.inference.predict import predict_batch, predict_one


def test_non_demo_mt5_returns_error_gate():
    result = assert_demo(executor_mode="paper", mt5_trade_mode=2)
    assert result.ok is False
    assert result.checks["mt5_demo"] is False


def test_non_testnet_bybit_returns_error_gate():
    result = assert_demo(executor_mode="paper", bybit_base_url="https://api.bybit.com")
    assert result.ok is False
    assert result.checks["bybit_testnet"] is False


def test_demo_gate_has_no_override_flag_in_result():
    result = assert_demo(executor_mode="paper", mt5_trade_mode=0, bybit_base_url="https://api-testnet.bybit.com")
    assert result.ok is True
    assert "override" not in result.reason.lower()


def test_inference_api_has_no_demo_gate_bypass():
    assert "skip_demo_gate" not in inspect.signature(predict_one).parameters
    assert "skip_demo_gate" not in inspect.signature(predict_batch).parameters
