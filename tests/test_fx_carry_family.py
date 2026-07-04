"""Tests for athena_fx.carry — broker-swap carry family."""
from __future__ import annotations

import pytest

from athena_fx.models import DIAG_MISSING_FRED, DIAG_MISSING_SWAP, FAMILY_CARRY
from athena_fx.carry import carry_family_score


def _valid_audit(**overrides) -> dict:
    base = {
        "symbol": "AUDJPY",
        "status": "ok",
        "net_carry_long_annualized": 0.012,
        "net_carry_short_annualized": 0.008,
        "carry_long_eligible": True,
        "carry_short_eligible": True,
        "markup_warning": False,
        "broker_markup_long": 0.001,
        "broker_markup_short": 0.002,
    }
    base.update(overrides)
    return base


def test_carry_blocked_when_audit_missing():
    result = carry_family_score(
        "AUDJPY", "2024-06-01", swap_audit_row=None, theoretical_carry=0.05,
    )
    assert result.status == "invalid"
    assert result.score is None
    assert result.direction == "neutral"
    assert any(d.code == DIAG_MISSING_SWAP for d in result.diagnostics)


def test_carry_blocked_when_audit_invalid():
    audit = _valid_audit(status="invalid")
    result = carry_family_score(
        "AUDJPY", "2024-06-01", swap_audit_row=audit, theoretical_carry=0.05,
    )
    assert result.status == "invalid"
    assert any(d.code == DIAG_MISSING_SWAP for d in result.diagnostics)


def test_carry_blocked_on_markup_when_config_true(monkeypatch):
    # Patch the CONFIG object the module holds (immune to config reloads
    # performed by unrelated tests earlier in the suite).
    from athena_fx import carry as carry_module

    monkeypatch.setitem(carry_module.CONFIG, "FX_FACTOR_CARRY_BLOCK_ON_MARKUP", True)
    audit = _valid_audit(markup_warning=True)
    result = carry_family_score(
        "AUDJPY", "2024-06-01", swap_audit_row=audit, theoretical_carry=0.01,
    )
    assert result.status == "ok"
    assert result.direction == "neutral"
    assert result.score == 0.0
    assert any(d.code == "broker_markup_high" for d in result.diagnostics)


def test_carry_bullish_when_net_above_threshold():
    audit = _valid_audit(
        net_carry_long_annualized=0.012,
        carry_long_eligible=True,
        carry_short_eligible=False,
    )
    result = carry_family_score(
        "AUDJPY", "2024-06-01", swap_audit_row=audit, theoretical_carry=0.01,
    )
    assert result.status == "ok"
    assert result.direction == "bullish"
    assert result.score == pytest.approx(0.012)
    assert result.score > 0
    assert result.family == FAMILY_CARRY


def test_theoretical_only_does_not_make_carry_eligible():
    result = carry_family_score(
        "AUDJPY", "2024-06-01", swap_audit_row=None, theoretical_carry=0.05,
    )
    assert result.status == "invalid"
    assert result.score is None


def test_fred_missing_warning_does_not_block_valid_audit():
    audit = _valid_audit(
        net_carry_long_annualized=0.012,
        carry_long_eligible=True,
        carry_short_eligible=False,
    )
    result = carry_family_score(
        "AUDJPY", "2024-06-01", swap_audit_row=audit, theoretical_carry=None,
    )
    assert result.status == "ok"
    assert result.direction == "bullish"
    assert any(d.code == DIAG_MISSING_FRED for d in result.diagnostics)
    assert result.subcomponents["theoretical_carry"] is None
