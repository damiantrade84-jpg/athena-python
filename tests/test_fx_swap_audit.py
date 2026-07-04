"""Tests for athena_fx.swap_audit — hand-calculated AUDJPY fixture."""
from __future__ import annotations

import pytest

from athena_fx.models import DIAG_MISSING_CONVERSION, DIAG_MISSING_SWAP
from athena_fx import store as fx_store
from athena_fx.swap_audit import (
    CURRENCY_DEPOSIT,
    DISABLED,
    INTEREST_CURRENT,
    POINTS,
    REOPEN_CURRENT,
    build_swap_audit_row,
    run_swap_audit,
)


def _audjpy_meta(**overrides) -> dict:
    base = {
        "symbol": "AUDJPY",
        "base_currency": "AUD",
        "quote_currency": "JPY",
        "account_currency": "USD",
        "point": 0.001,
        "trade_tick_size": 0.001,
        "trade_tick_value": 0.6802,
        "trade_contract_size": 100_000,
        "swap_mode": POINTS,
        "swap_long": 0.55,
        "swap_short": -1.95,
        "swap_rollover3days": 3,
        "broker_timestamp": "2026-01-15T12:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_audjpy_points_mode_hand_calculation(monkeypatch):
    meta = _audjpy_meta()
    row = build_swap_audit_row(
        meta,
        conversion_rates={"AUDUSD": 0.66},
        generated_at="2026-01-15T12:00:00+00:00",
    )

    assert row.status == "ok"
    assert row.point_value_per_lot == pytest.approx(0.6802, rel=1e-3)

    swap_cash_long = 0.55 * 0.6802
    assert row.swap_cash_long_per_lot == pytest.approx(swap_cash_long, rel=1e-4)

    notional = 100_000 * 0.66
    assert row.notional_value_account_ccy == pytest.approx(notional, rel=1e-4)

    daily_long = swap_cash_long / notional
    assert row.daily_swap_return_long == pytest.approx(daily_long, rel=1e-6)
    assert row.annualized_swap_return_long == pytest.approx(daily_long * 365, rel=1e-6)
    assert row.weekly_swap_cash_long_per_lot == pytest.approx(swap_cash_long * 7, rel=1e-4)

    # ~0.2069% annualized < default 0.5% threshold
    assert row.carry_long_eligible is False

    # Patch the CONFIG object the module holds (immune to config reloads
    # performed by unrelated tests earlier in the suite).
    from athena_fx import swap_audit as swap_audit_module

    monkeypatch.setitem(
        swap_audit_module.CONFIG, "FX_FACTOR_CARRY_MIN_NET_ANNUAL", 0.001
    )
    row2 = build_swap_audit_row(
        meta,
        conversion_rates={"AUDUSD": 0.66},
        generated_at="2026-01-15T12:00:00+00:00",
    )
    assert row2.carry_long_eligible is True


def test_currency_deposit_mode():
    meta = _audjpy_meta(
        swap_mode=CURRENCY_DEPOSIT,
        swap_long=1.25,
        swap_short=-0.80,
    )
    row = build_swap_audit_row(meta, conversion_rates={"AUDUSD": 0.66})
    assert row.status == "ok"
    assert row.swap_cash_long_per_lot == pytest.approx(1.25)
    assert row.swap_cash_short_per_lot == pytest.approx(-0.80)


def test_interest_current_mode():
    meta = _audjpy_meta(swap_mode=INTEREST_CURRENT, swap_long=2.5, swap_short=-1.0)
    row = build_swap_audit_row(meta, conversion_rates={"AUDUSD": 0.66})
    assert row.status == "ok"
    assert row.daily_swap_return_long == pytest.approx(2.5 / 100.0 / 360.0, rel=1e-8)
    assert row.daily_swap_return_short == pytest.approx(-1.0 / 100.0 / 360.0, rel=1e-8)
    assert row.annualized_swap_return_long == pytest.approx(
        row.daily_swap_return_long * 365, rel=1e-6
    )


def test_unsupported_swap_mode():
    meta = _audjpy_meta(swap_mode=REOPEN_CURRENT)
    row = build_swap_audit_row(meta, conversion_rates={"AUDUSD": 0.66})
    assert row.status == "invalid"
    assert any(d.code == "unsupported_swap_mode" for d in row.diagnostics)


def test_missing_conversion_rate():
    meta = _audjpy_meta()
    row = build_swap_audit_row(meta, conversion_rates={})
    assert row.conversion_warning is True
    assert row.status == "invalid"
    assert any(d.code == DIAG_MISSING_CONVERSION for d in row.diagnostics)
    assert row.daily_swap_return_long is None
    assert row.notional_value_account_ccy is None


def test_g8_rate_symbol_lookup():
    from athena_fx.swap_audit import g8_rate_symbol

    assert g8_rate_symbol("EUR", "USD") == "EURUSD"
    assert g8_rate_symbol("USD", "JPY") == "USDJPY"
    assert g8_rate_symbol("JPY", "USD") == "USDJPY"
    assert g8_rate_symbol("CAD", "JPY") == "CADJPY"


def test_default_conversion_rate_fn_uses_cache(monkeypatch):
    from athena_fx.swap_audit import (
        _default_conversion_rate_fn,
        clear_mt5_conversion_rate_cache,
    )

    clear_mt5_conversion_rate_cache()
    calls: list[str] = []

    def fake_fetch(account: str) -> dict[str, float]:
        calls.append(account)
        return {"EURUSD": 1.08, "AUDUSD": 0.66}

    monkeypatch.setattr(
        "athena_fx.swap_audit.fetch_mt5_g8_conversion_rates",
        fake_fetch,
    )
    meta = {"account_currency": "USD"}
    rates1 = _default_conversion_rate_fn("EURUSD", meta)
    rates2 = _default_conversion_rate_fn("GBPUSD", meta)
    assert rates1 == {"EURUSD": 1.08, "AUDUSD": 0.66}
    assert rates2 == rates1
    assert calls == ["USD"]
    clear_mt5_conversion_rate_cache()


def test_disabled_swap_mode():
    meta = _audjpy_meta(swap_mode=DISABLED, swap_long=5.0, swap_short=-5.0)
    row = build_swap_audit_row(meta, conversion_rates={"AUDUSD": 0.66})
    assert row.status == "ok"
    assert row.swap_cash_long_per_lot == 0.0
    assert row.carry_long_eligible is False
    assert any(d.code == "swap_disabled" for d in row.diagnostics)


@pytest.fixture
def fx_db(tmp_path, monkeypatch):
    db = str(tmp_path / "fx_test.db")
    monkeypatch.setenv("ATHENA_FX_DB_PATH", db)
    return db


def test_run_swap_audit_persistence_readback(fx_db):
    meta = _audjpy_meta()

    def fake_fetch(sym: str):
        assert sym == "AUDJPY"
        return meta

    rows = run_swap_audit(
        ["AUDJPY"],
        fetch_fn=fake_fetch,
        conversion_rate_fn=lambda sym, m: {"AUDUSD": 0.66},
        theoretical_carry_fn=lambda sym: None,
        db_path=fx_db,
    )
    assert len(rows) == 1
    assert rows[0].status == "ok"

    loaded = fx_store.load_latest_swap_audit(symbol="AUDJPY", db_path=fx_db)
    assert len(loaded) == 1
    assert loaded[0]["symbol"] == "AUDJPY"
    assert loaded[0]["status"] == "ok"
    assert loaded[0]["daily_swap_return_long"] == pytest.approx(
        rows[0].daily_swap_return_long, rel=1e-6
    )


def test_run_swap_audit_missing_metadata(fx_db):
    rows = run_swap_audit(
        ["EURUSD"],
        fetch_fn=lambda sym: None,
        db_path=fx_db,
    )
    assert len(rows) == 1
    assert rows[0].status == "invalid"
    assert any(d.code == DIAG_MISSING_SWAP for d in rows[0].diagnostics)
