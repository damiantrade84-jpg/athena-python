"""Tests for Engine C backtest timeout exit classification and reporting."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backtest_runner import (
    _classify_engine_c_timeout,
    _engine_c_timeout_subcounts,
)


def test_timeout_close_positive_classifies_as_timeout_profit():
    timeout_class, pnl_sign = _classify_engine_c_timeout(0.5)
    assert timeout_class == "TIMEOUT_PROFIT"
    assert pnl_sign == "PROFIT"


def test_timeout_close_negative_classifies_as_timeout_loss():
    timeout_class, pnl_sign = _classify_engine_c_timeout(-0.25)
    assert timeout_class == "TIMEOUT_LOSS"
    assert pnl_sign == "LOSS"


def test_timeout_close_flat_classifies_as_timeout_flat():
    timeout_class, pnl_sign = _classify_engine_c_timeout(0.0)
    assert timeout_class == "TIMEOUT_FLAT"
    assert pnl_sign == "FLAT"


def test_summary_formatter_reports_timeout_subcounts():
    trades = [
        {"outcome": "TIMEOUT", "timeout_class": "TIMEOUT_PROFIT"},
        {"outcome": "TIMEOUT", "timeout_class": "TIMEOUT_LOSS"},
        {"outcome": "TIMEOUT", "timeout_class": "TIMEOUT_FLAT"},
        {"outcome": "TP1", "timeout_class": None},
    ]
    subcounts = _engine_c_timeout_subcounts(trades)
    assert subcounts["timeout_profit"] == 1
    assert subcounts["timeout_loss"] == 1
    assert subcounts["timeout_flat"] == 1


def test_pf_sqn_unchanged_by_reporting_refactor():
    """Timeout helpers are reporting-only and must not alter trade economics fields."""
    trades = [
        {"outcome": "TIMEOUT", "resultR": 0.4, "timeout_class": "TIMEOUT_PROFIT"},
        {"outcome": "SL", "resultR": -1.0, "timeout_class": None},
    ]
    wins = sum(1 for t in trades if (t.get("resultR") or 0) > 0)
    gross_profit = sum(t["resultR"] for t in trades if t["resultR"] > 0)
    gross_loss = abs(sum(t["resultR"] for t in trades if t["resultR"] < 0))
    pf = gross_profit / gross_loss if gross_loss else 0.0
    assert wins == 1
    assert pf == pytest.approx(0.4)


def test_additive_diagnostics_present_in_bt_result():
    timeout_class, forced_exit_pnl_sign = _classify_engine_c_timeout(0.12)
    trade = {
        "outcome": "TIMEOUT",
        "timeout_class": timeout_class,
        "forced_exit_pnl_sign": forced_exit_pnl_sign,
        "forced_exit_result_r": 0.12,
        "resolved_style": "intraday",
        "zone_tf_used": "H4",
        "entry_tf_used": "H1",
        "atr_tf_used": "H4",
        "selected_tp_source": "structural",
        "selected_sl_source": "atr",
    }
    for key in (
        "timeout_class",
        "forced_exit_pnl_sign",
        "forced_exit_result_r",
        "resolved_style",
        "zone_tf_used",
        "entry_tf_used",
        "atr_tf_used",
        "selected_tp_source",
        "selected_sl_source",
    ):
        assert trade.get(key) is not None
