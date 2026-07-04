"""Tests for athena_fx.backtest — Phase 10 weekly factor backtest engine."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from athena_fx.backtest import (
    FxBacktestRequest,
    apply_exposure_caps,
    pip_size,
    realized_vol_annualized,
    run_fx_factor_backtest,
    vol_scaled_weight,
)
from athena_fx.models import (
    ACTION_BUY,
    ACTION_NO_TRADE,
    DIAG_MISSING_CARRY,
    FAMILY_CARRY,
    FAMILY_MOMENTUM,
)
from athena_fx import store as fx_store


def _generate_bars(
    start: str,
    num_trading_days: int,
    *,
    base_price: float = 1.1000,
    daily_return: float = 0.0005,
    volatility: float = 0.0,
    skip_dates: set[str] | None = None,
) -> list[dict]:
    """Generate ascending Mon–Fri OHLC bars."""
    skip = skip_dates or set()
    d = date.fromisoformat(start)
    bars: list[dict] = []
    price = base_price
    day_idx = 0
    while len(bars) < num_trading_days:
        if d.weekday() < 5:
            ds = d.isoformat()
            if ds not in skip:
                noise = volatility * ((day_idx % 7) - 3) * 0.001
                open_px = price
                close_px = price * (1.0 + daily_return + noise)
                high = max(open_px, close_px) * 1.0002
                low = min(open_px, close_px) * 0.9998
                bars.append(
                    {
                        "date": ds,
                        "open": round(open_px, 5 if base_price < 10 else 3),
                        "high": round(high, 5 if base_price < 10 else 3),
                        "low": round(low, 5 if base_price < 10 else 3),
                        "close": round(close_px, 5 if base_price < 10 else 3),
                    }
                )
                price = close_px
                day_idx += 1
        d += timedelta(days=1)
    return bars


def _decision(
    action: str,
    direction: str | None = None,
    *,
    aligned: list[str] | None = None,
    diagnostics: list[dict] | None = None,
    size_multiplier: float = 1.0,
) -> dict:
    return {
        "action": action,
        "direction": direction,
        "size_multiplier": size_multiplier,
        "gate_results": [],
        "aligned_families": aligned or [FAMILY_CARRY, FAMILY_MOMENTUM],
        "factor_scores": {},
        "diagnostics": diagnostics or [],
        "volatility_action": "allow",
    }


def _swap(symbol: str, long_daily: float = 0.00001) -> dict[str, float]:
    return {
        "long_daily": long_daily,
        "short_daily": -long_daily * 0.8,
        "rollover3days": 3,
    }


def _base_request(**overrides) -> FxBacktestRequest:
    base = {
        "start": "2024-01-01",
        "end": "2024-06-30",
        "symbols": ["EURUSD", "USDJPY"],
        "spread_pips_by_symbol": {"EURUSD": 0.8, "USDJPY": 0.9},
    }
    base.update(overrides)
    return FxBacktestRequest(**base)


@pytest.fixture
def fx_db(tmp_path, monkeypatch):
    db = str(tmp_path / "fx_test.db")
    monkeypatch.setenv("ATHENA_FX_DB_PATH", db)
    return db


def test_weekly_cadence_decide_fn_called_once_per_symbol_per_week(fx_db):
    bars_eur = _generate_bars("2024-01-01", 120, base_price=1.10, daily_return=0.0003)
    bars_jpy = _generate_bars("2024-01-01", 120, base_price=150.0, daily_return=0.0002, volatility=2.0)
    calls: list[tuple[str, str, bool]] = []

    def decide(symbol: str, as_of: str, has_pos: bool) -> dict:
        calls.append((symbol, as_of, has_pos))
        return _decision(ACTION_BUY, "LONG")

    req = _base_request()
    run_fx_factor_backtest(
        req,
        bars_by_symbol={"EURUSD": bars_eur, "USDJPY": bars_jpy},
        decide_fn=decide,
        swap_returns_by_symbol={
            "EURUSD": _swap("EURUSD"),
            "USDJPY": _swap("USDJPY"),
        },
    )

    fridays = [c[1] for c in calls if c[0] == "EURUSD"]
    assert all(date.fromisoformat(d).weekday() == 4 for d in fridays)
    n_weeks = len(fridays)
    assert len(calls) == n_weeks * 2
    assert len({c[1] for c in calls}) == n_weeks


def test_monday_execution_all_entries_on_monday(fx_db):
    bars_eur = _generate_bars("2024-01-01", 120, base_price=1.10)
    bars_jpy = _generate_bars("2024-01-01", 120, base_price=150.0)
    week_idx = {"n": 0}

    def decide(symbol: str, as_of: str, has_pos: bool) -> dict:
        if week_idx["n"] == 0:
            week_idx["n"] += 1
            return _decision(ACTION_BUY, "LONG")
        return _decision(ACTION_NO_TRADE, None)

    report = run_fx_factor_backtest(
        _base_request(),
        bars_by_symbol={"EURUSD": bars_eur, "USDJPY": bars_jpy},
        decide_fn=decide,
        swap_returns_by_symbol={"EURUSD": _swap("EURUSD"), "USDJPY": _swap("USDJPY")},
    )
    for t in report["trades"]:
        assert date.fromisoformat(t["entry_date"]).weekday() == 0


def test_monday_holiday_executes_next_trading_day(fx_db):
    first_friday = _first_friday_on_or_after("2024-01-01")
    d = date.fromisoformat(first_friday) + timedelta(days=3)
    monday_skip = {d.isoformat()}

    bars = _generate_bars("2024-01-01", 30, base_price=1.10, skip_dates=monday_skip)
    fired = {"ok": False}

    def decide(symbol: str, as_of: str, has_pos: bool) -> dict:
        fired["ok"] = True
        return _decision(ACTION_BUY, "LONG")

    report = run_fx_factor_backtest(
        _base_request(symbols=["EURUSD"], end="2024-02-15"),
        bars_by_symbol={"EURUSD": bars},
        decide_fn=decide,
        swap_returns_by_symbol={"EURUSD": _swap("EURUSD")},
    )
    assert fired["ok"]
    assert report["trades"]
    entry_wd = date.fromisoformat(report["trades"][0]["entry_date"]).weekday()
    assert entry_wd == 1


def test_no_friday_weekend_new_entries(fx_db):
    bars_eur = _generate_bars("2024-01-01", 120, base_price=1.10)
    bars_jpy = _generate_bars("2024-01-01", 120, base_price=150.0)

    def decide(symbol: str, as_of: str, has_pos: bool) -> dict:
        return _decision(ACTION_BUY, "LONG")

    report = run_fx_factor_backtest(
        _base_request(),
        bars_by_symbol={"EURUSD": bars_eur, "USDJPY": bars_jpy},
        decide_fn=decide,
        swap_returns_by_symbol={"EURUSD": _swap("EURUSD"), "USDJPY": _swap("USDJPY")},
    )
    for t in report["trades"]:
        wd = date.fromisoformat(t["entry_date"]).weekday()
        assert wd not in (4, 5, 6), f"entry_date on weekday {wd}"


def test_swap_accrual_multi_week_hold(fx_db):
    long_daily = 0.00005
    bars = _generate_bars("2024-01-01", 120, base_price=1.10, daily_return=0.0)
    hold_weeks = {"count": 0}

    def decide(symbol: str, as_of: str, has_pos: bool) -> dict:
        if hold_weeks["count"] < 3:
            hold_weeks["count"] += 1
            return _decision(ACTION_BUY, "LONG")
        return _decision(ACTION_NO_TRADE, None)

    report = run_fx_factor_backtest(
        _base_request(symbols=["EURUSD"]),
        bars_by_symbol={"EURUSD": bars},
        decide_fn=decide,
        swap_returns_by_symbol={"EURUSD": _swap("EURUSD", long_daily=long_daily)},
    )
    trade = next(t for t in report["trades"] if t["symbol"] == "EURUSD")
    entry_d = date.fromisoformat(trade["entry_date"])
    exit_d = date.fromisoformat(trade["exit_date"])
    swap_days = [b["date"] for b in bars if entry_d <= date.fromisoformat(b["date"]) <= exit_d]
    expected_units = sum(
        3 if date.fromisoformat(ds).weekday() == 2 else 1 for ds in swap_days
    )
    weight = trade["entry_weight"]
    assert trade["swap_pnl"] == pytest.approx(
        long_daily * expected_units * weight, rel=0.05
    )


def test_vol_scaled_sizing_higher_vol_gets_smaller_weight(fx_db):
    bars_low = _generate_bars("2024-01-01", 90, base_price=1.10, daily_return=0.0001, volatility=0.0)
    bars_high = _generate_bars("2024-01-01", 90, base_price=150.0, daily_return=0.0001, volatility=5.0)
    as_of = next(
        b["date"]
        for b in bars_low
        if b["date"] >= bars_low[63]["date"]
        and date.fromisoformat(b["date"]).weekday() == 4
    )

    def decide_once(symbol: str, as_of_date: str, has_pos: bool) -> dict:
        if as_of_date == as_of:
            return _decision(ACTION_BUY, "LONG")
        return _decision(ACTION_NO_TRADE, None)

    r_low = run_fx_factor_backtest(
        _base_request(symbols=["EURUSD"], end="2024-06-01"),
        bars_by_symbol={"EURUSD": bars_low},
        decide_fn=decide_once,
        swap_returns_by_symbol={"EURUSD": _swap("EURUSD")},
    )
    r_high = run_fx_factor_backtest(
        _base_request(symbols=["USDJPY"], end="2024-06-01"),
        bars_by_symbol={"USDJPY": bars_high},
        decide_fn=decide_once,
        swap_returns_by_symbol={"USDJPY": _swap("USDJPY")},
    )
    eur = r_low["trades"][0]
    jpy = r_high["trades"][0]
    assert eur["entry_weight"] > jpy["entry_weight"]
    rv_low = realized_vol_annualized({b["date"]: b for b in bars_low}, as_of)
    rv_high = realized_vol_annualized({b["date"]: b for b in bars_high}, as_of)
    assert rv_high > rv_low
    w_low = vol_scaled_weight(rv_low, 0.10, 0.02)
    w_high = vol_scaled_weight(rv_high, 0.10, 0.02)
    cap = 0.02
    assert eur["entry_weight"] == pytest.approx(min(w_low, cap), rel=0.05)
    assert jpy["entry_weight"] == pytest.approx(min(w_high, cap), rel=0.05)


def test_exposure_caps_currency_and_total_bind(fx_db):
    weights = {
        "EURUSD": (0.02, "LONG"),
        "USDJPY": (0.02, "LONG"),
    }
    capped = apply_exposure_caps(weights, pair_risk_cap=0.02, currency_risk_cap=0.04, total_risk_cap=0.10)
    assert abs(capped["EURUSD"][0]) <= 0.02
    assert abs(capped["USDJPY"][0]) <= 0.02
    usd_touch = abs(capped["EURUSD"][0]) + abs(capped["USDJPY"][0])
    assert usd_touch <= 0.04 + 1e-9


def test_integration_stable_report_and_persistence(fx_db):
    bars_eur = _generate_bars("2024-01-01", 120, base_price=1.10)
    bars_jpy = _generate_bars("2024-01-01", 120, base_price=150.0)

    def decide(symbol: str, as_of: str, has_pos: bool) -> dict:
        if as_of.endswith("-05") or as_of.endswith("-12"):
            return _decision(ACTION_BUY, "LONG")
        return _decision(ACTION_NO_TRADE, None)

    kwargs = dict(
        request=_base_request(),
        bars_by_symbol={"EURUSD": bars_eur, "USDJPY": bars_jpy},
        decide_fn=decide,
        swap_returns_by_symbol={"EURUSD": _swap("EURUSD"), "USDJPY": _swap("USDJPY")},
    )
    r1 = run_fx_factor_backtest(**kwargs)
    r2 = run_fx_factor_backtest(**kwargs)
    assert r1["run_id"] == r2["run_id"]
    for key in ("trade_count", "win_rate", "turnover", "sharpe"):
        assert r1["summary"][key] == r2["summary"][key]

    loaded = fx_store.load_backtest_run(r1["run_id"], db_path=fx_db)
    assert loaded is not None
    assert loaded["report"]["run_id"] == r1["run_id"]


def test_strict_missing_carry_excludes_symbol(fx_db):
    bars = _generate_bars("2024-01-01", 60, base_price=1.10)

    def decide(symbol: str, as_of: str, has_pos: bool) -> dict:
        if symbol == "EURUSD":
            return _decision(
                ACTION_BUY,
                "LONG",
                diagnostics=[{"code": DIAG_MISSING_CARRY, "severity": "error", "message": "x"}],
            )
        return _decision(ACTION_BUY, "LONG")

    report = run_fx_factor_backtest(
        _base_request(strict_factor_data=True),
        bars_by_symbol={"EURUSD": bars, "USDJPY": _generate_bars("2024-01-01", 60, base_price=150.0)},
        decide_fn=decide,
        swap_returns_by_symbol={"EURUSD": _swap("EURUSD"), "USDJPY": _swap("USDJPY")},
    )
    codes = [d["code"] for d in report["diagnostics"]]
    assert DIAG_MISSING_CARRY in codes
    assert all(t["symbol"] != "EURUSD" for t in report["trades"])


def test_non_strict_missing_carry_still_trades_with_diagnostics(fx_db):
    bars = _generate_bars("2024-01-01", 40, base_price=1.10)

    def decide(symbol: str, as_of: str, has_pos: bool) -> dict:
        return _decision(
            ACTION_BUY,
            "LONG",
            diagnostics=[{"code": DIAG_MISSING_CARRY, "severity": "warning", "message": "x"}],
        )

    report = run_fx_factor_backtest(
        _base_request(symbols=["EURUSD"], end="2024-03-01", strict_factor_data=False),
        bars_by_symbol={"EURUSD": bars},
        decide_fn=decide,
        swap_returns_by_symbol={"EURUSD": _swap("EURUSD")},
    )
    assert any(d["code"] == DIAG_MISSING_CARRY for d in report["diagnostics"])
    assert any(t["symbol"] == "EURUSD" for t in report["trades"])


def test_cost_model_fill_price_and_missing_spread(fx_db):
    bars = _generate_bars("2024-01-01", 30, base_price=1.10)

    def decide(symbol: str, as_of: str, has_pos: bool) -> dict:
        return _decision(ACTION_BUY, "LONG")

    report = run_fx_factor_backtest(
        _base_request(symbols=["EURUSD"], end="2024-02-15", slippage_pips=0.3, cost_model_enabled=True),
        bars_by_symbol={"EURUSD": bars},
        decide_fn=decide,
        swap_returns_by_symbol={"EURUSD": _swap("EURUSD")},
    )
    trade = report["trades"][0]
    entry_date = trade["entry_date"]
    open_px = next(b["open"] for b in bars if b["date"] == entry_date)
    pip = pip_size("EURUSD")
    half = (0.8 / 2 + 0.3) * pip
    assert trade["entry_price"] == pytest.approx(open_px + half, rel=1e-4)

    report_skip = run_fx_factor_backtest(
        _base_request(symbols=["EURUSD"], end="2024-02-15", spread_pips_by_symbol={}),
        bars_by_symbol={"EURUSD": bars},
        decide_fn=decide,
        swap_returns_by_symbol={"EURUSD": _swap("EURUSD")},
    )
    assert any(d["code"] == "missing_spread" for d in report_skip["diagnostics"])
    assert report_skip["trades"] == []


def _first_friday_on_or_after(start: str) -> str:
    d = date.fromisoformat(start)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d.isoformat()


def test_pip_size_jpy_quote():
    assert pip_size("USDJPY") == 0.01
    assert pip_size("EURUSD") == 0.0001
