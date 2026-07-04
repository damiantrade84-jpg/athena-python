"""Tests for athena_fx.derivatives_adapter — factor input resolution."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from athena_fx.models import DIAG_MISSING_CARRY, StrictFactorDataError
from athena_fx import store as fx_store
from athena_fx.derivatives_adapter import resolve_factor_derivatives


def _recent_iso(days_ago: int = 1) -> str:
    return (
        datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    ).replace(microsecond=0).isoformat()


def _seed_reer(db_path: str, currency: str, n_months: int = 30) -> None:
    rows = []
    for i in range(n_months):
        year = 2023 + i // 12
        month_num = i % 12 + 1
        obs = f"{year}-{month_num:02d}-01"
        avail = f"{year}-{month_num:02d}-28"
        rows.append({
            "currency": currency,
            "observation_month": obs,
            "reer_value": 100.0 + i * 0.5,
            "available_date": avail,
            "lag_method": "conservative",
            "source": "test",
        })
    fx_store.save_reer_observations(rows, db_path=db_path)


def _seed_swap_audit(db_path: str, symbol: str = "EURUSD") -> None:
    payload = {
        "symbol": symbol,
        "status": "ok",
        "generated_at": _recent_iso(1),
        "daily_swap_return_long": 0.00001,
        "daily_swap_return_short": -0.000008,
        "swap_rollover3days": 3,
        "base_currency": "EUR",
        "quote_currency": "USD",
        "account_currency": "USD",
        "point": 0.00001,
        "trade_tick_value": 1.0,
        "trade_tick_size": 0.00001,
        "trade_contract_size": 100_000,
        "swap_mode": 1,
        "swap_long": 0.5,
        "swap_short": -0.5,
    }
    fx_store.save_swap_audit_rows([type("R", (), {"to_dict": lambda self: payload})()], db_path=db_path)


@pytest.fixture
def fx_db(tmp_path, monkeypatch):
    db = str(tmp_path / "fx_deriv.db")
    monkeypatch.setenv("ATHENA_FX_DB_PATH", db)
    return db


def test_valid_data_status_ok(fx_db, monkeypatch):
    _seed_reer(fx_db, "EUR", 30)
    _seed_reer(fx_db, "USD", 30)
    _seed_swap_audit(fx_db, "EURUSD")

    monkeypatch.setattr("carry_feed.get_carry_z", lambda display, as_of_date=None: 1.25)
    monkeypatch.setattr("carry_feed.get_carry_differential", lambda display: 1.5)
    monkeypatch.setattr("cot_feed.get_cot_z", lambda display, as_of_date=None: -0.8)

    result = resolve_factor_derivatives("EURUSD", "2026-01-15", strict=False, db_path=fx_db)
    assert result.status == "ok"
    assert result.carry_z == pytest.approx(1.25)
    assert result.cot_z == pytest.approx(-0.8)
    assert result.swap_audit_valid is True
    missing = {d.code for d in result.diagnostics if d.severity in ("warning", "error")}
    assert DIAG_MISSING_CARRY not in missing
    assert "missing_cot" not in missing
    assert "missing_reer" not in missing
    assert "missing_swap" not in missing


def test_missing_carry_degraded_not_silent_zero(fx_db, monkeypatch):
    _seed_reer(fx_db, "EUR", 30)
    _seed_reer(fx_db, "USD", 30)
    _seed_swap_audit(fx_db, "EURUSD")

    monkeypatch.setattr("carry_feed.get_carry_z", lambda display, as_of_date=None: 0.0)
    monkeypatch.setattr("carry_feed.get_carry_differential", lambda display: 1.5)
    monkeypatch.setattr("cot_feed.get_cot_z", lambda display, as_of_date=None: 1.0)

    result = resolve_factor_derivatives("EURUSD", "2026-01-15", strict=False, db_path=fx_db)
    assert result.status == "degraded"
    assert result.carry_z is None
    assert any(d.code == DIAG_MISSING_CARRY for d in result.diagnostics)


def test_strict_raises_on_missing(fx_db, monkeypatch):
    _seed_reer(fx_db, "EUR", 30)
    _seed_reer(fx_db, "USD", 30)

    monkeypatch.setattr("carry_feed.get_carry_z", lambda display, as_of_date=None: 0.0)
    monkeypatch.setattr("carry_feed.get_carry_differential", lambda display: None)
    monkeypatch.setattr("cot_feed.get_cot_z", lambda display, as_of_date=None: 0.0)

    with pytest.raises(StrictFactorDataError) as exc:
        resolve_factor_derivatives("EURUSD", "2026-01-15", strict=True, db_path=fx_db)
    codes = {d.code for d in exc.value.diagnostics}
    assert DIAG_MISSING_CARRY in codes or "missing_swap" in codes


def test_live_backtest_parity(fx_db, monkeypatch):
    _seed_reer(fx_db, "EUR", 30)
    _seed_reer(fx_db, "USD", 30)
    _seed_swap_audit(fx_db, "EURUSD")

    monkeypatch.setattr("carry_feed.get_carry_z", lambda display, as_of_date=None: 0.75)
    monkeypatch.setattr("carry_feed.get_carry_differential", lambda display: 2.0)
    monkeypatch.setattr("cot_feed.get_cot_z", lambda display, as_of_date=None: 0.5)

    live = resolve_factor_derivatives("EURUSD", "2026-01-15", strict=False, db_path=fx_db)
    backtest = resolve_factor_derivatives("EURUSD", "2026-01-15", strict=False, db_path=fx_db)
    assert live.to_dict() == backtest.to_dict()
