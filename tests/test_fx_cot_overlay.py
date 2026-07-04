"""Tests for athena_fx.cot_overlay — currency-leg COT crowding."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from athena_fx.models import DIAG_MISSING_COT
from athena_fx.cot_overlay import cot_overlay, currency_cot_percentile


def _pct_info(currency: str, percentile: float, report_date: str = "2024-06-04") -> dict:
    return {
        "currency": currency,
        "percentile": percentile,
        "net": 1000,
        "report_date": report_date,
        "n_weeks": 156,
        "cot_lag_days": 10,
    }


def test_audjpy_long_double_crowded_veto():
    with patch("athena_fx.cot_overlay.currency_cot_percentile") as mock_pct:
        mock_pct.side_effect = lambda ccy, as_of, **kw: {
            "AUD": _pct_info("AUD", 95.0),
            "JPY": _pct_info("JPY", 5.0),
        }.get(ccy)
        result = cot_overlay("AUDJPY", "LONG", "2024-06-15")
    assert result.pair_crowding_status == "double_crowded"
    assert result.action == "veto"
    assert result.modifier == 0.0
    assert mock_pct.call_count == 2


def test_one_leg_downweight():
    with patch("athena_fx.cot_overlay.currency_cot_percentile") as mock_pct:
        mock_pct.side_effect = lambda ccy, as_of, **kw: {
            "AUD": _pct_info("AUD", 95.0),
            "JPY": _pct_info("JPY", 50.0),
        }.get(ccy)
        result = cot_overlay("AUDJPY", "LONG", "2024-06-15")
    assert result.pair_crowding_status == "one_leg"
    assert result.action == "downweight"
    assert result.modifier < 0


def test_per_currency_legs_not_pair_level():
    with patch("athena_fx.cot_overlay.currency_cot_percentile") as mock_pct:
        mock_pct.side_effect = lambda ccy, as_of, **kw: _pct_info(ccy, 50.0)
        cot_overlay("AUDJPY", "LONG", "2024-06-15")
        called = {c.args[0] for c in mock_pct.call_args_list}
    assert called == {"AUD", "JPY"}


def test_as_of_forwarded_to_currency_cot_percentile():
    with patch("athena_fx.cot_overlay.currency_cot_percentile") as mock_pct:
        mock_pct.return_value = _pct_info("AUD", 50.0)
        mock_pct.side_effect = lambda ccy, as_of, **kw: _pct_info(ccy, 50.0)
        cot_overlay("AUDUSD", "LONG", "2023-01-10")
        for call in mock_pct.call_args_list:
            assert call.args[1] == "2023-01-10"


def test_cot_actions_never_create_and_crowded_modifier_non_positive():
    scenarios = [
        ("AUDJPY", "LONG", {"AUD": 95.0, "JPY": 5.0}),
        ("AUDJPY", "LONG", {"AUD": 95.0, "JPY": 50.0}),
        ("AUDJPY", "SHORT", {"AUD": 5.0, "JPY": 95.0}),
    ]
    for sym, direction, pcts in scenarios:
        with patch("athena_fx.cot_overlay.currency_cot_percentile") as mock_pct:
            base, quote = sym[:3], sym[3:]
            mock_pct.side_effect = lambda ccy, as_of, **kw: _pct_info(
                ccy, pcts[ccy],
            )
            result = cot_overlay(sym, direction, "2024-06-15")
        assert result.action in {"none", "downweight", "veto"}
        assert result.action != "create"
        if result.pair_crowding_status in {"one_leg", "double_crowded"}:
            assert result.modifier <= 0.0


def test_unknown_leg_missing_cot():
    with patch("athena_fx.cot_overlay.currency_cot_percentile") as mock_pct:
        mock_pct.side_effect = lambda ccy, as_of, **kw: (
            _pct_info("EUR", 50.0) if ccy == "EUR" else None
        )
        result = cot_overlay("EURUSD", "LONG", "2024-06-15")
    assert result.pair_crowding_status == "unknown"
    assert result.action == "none"
    assert any(d.code == DIAG_MISSING_COT for d in result.diagnostics)
