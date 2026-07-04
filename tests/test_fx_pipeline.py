"""Integration tests for athena_fx.pipeline — live/backtest parity (Phase 1)."""
from __future__ import annotations

import datetime as dt
import math

import pytest

import carry_feed
import cot_feed

from athena_fx import pipeline as fx_pipeline
from athena_fx import store as fx_store
from athena_fx.models import (
    ACTION_BUY,
    ACTION_NO_TRADE,
    CotOverlayResult,
    FAMILY_CARRY,
    FAMILY_MOMENTUM,
    FAMILY_VALUE,
)

AS_OF = "2026-06-26"  # a Friday


@pytest.fixture()
def fx_db(tmp_path, monkeypatch):
    db = str(tmp_path / "fx_factor_test.db")
    monkeypatch.setenv("ATHENA_FX_DB_PATH", db)
    return db


def _trading_days(end: str, count: int) -> list[str]:
    days: list[str] = []
    cur = dt.date.fromisoformat(end)
    while len(days) < count:
        if cur.weekday() < 5:
            days.append(cur.isoformat())
        cur -= dt.timedelta(days=1)
    return list(reversed(days))


def _seed_total_return(symbol: str, *, drift: float, db_path: str) -> None:
    dates = _trading_days(AS_OF, 300)
    rows = []
    long_idx = 100.0
    short_idx = 100.0
    for i, date in enumerate(dates):
        spot = drift + 0.001 * math.sin(i / 7.0)
        long_tr = spot + 0.00005
        short_tr = -spot + 0.00002
        long_idx *= 1.0 + long_tr
        short_idx *= 1.0 + short_tr
        rows.append({
            "date": date,
            "symbol": symbol,
            "spot_return": spot,
            "long_swap_return": 0.00005,
            "short_swap_return": 0.00002,
            "long_total_return": long_tr,
            "short_total_return": short_tr,
            "long_total_return_index": long_idx,
            "short_total_return_index": short_idx,
            "data_quality_status": "ok",
        })
    fx_store.save_total_return_rows(rows, db_path=db_path)


def _seed_swap_audit(symbol: str, db_path: str) -> None:
    fx_store.save_swap_audit_rows([{
        "symbol": symbol,
        "generated_at": f"{AS_OF}T00:00:00+00:00",
        "status": "ok",
        "net_carry_long_annualized": 0.02,
        "net_carry_short_annualized": -0.03,
        "carry_long_eligible": True,
        "carry_short_eligible": False,
        "broker_markup_long": 0.004,
        "broker_markup_short": 0.05,
        "markup_warning": False,
    }], db_path=db_path)


def _seed_reer(db_path: str) -> None:
    rows = []
    for ccy, terminal_z_sign in (("AUD", -1.0), ("JPY", +1.0), ("USD", 0.0)):
        for i in range(70):
            month = (dt.date(2020, 1, 1) + dt.timedelta(days=31 * i)).strftime("%Y-%m")
            base_value = 100.0
            # Push the final observations away from the mean to set the z sign.
            offset = terminal_z_sign * (8.0 if i >= 67 else 0.0)
            rows.append({
                "currency": ccy,
                "observation_month": month,
                "reer_value": base_value + offset + (i % 5) * 0.4,
                "available_date": "2026-06-01",
                "lag_method": "conservative",
                "source": "fixture",
            })
    fx_store.save_reer_observations(rows, db_path=db_path)


@pytest.fixture()
def seeded(fx_db, monkeypatch):
    _seed_total_return("AUDJPY", drift=0.002, db_path=fx_db)
    _seed_total_return("AUDUSD", drift=0.0015, db_path=fx_db)
    _seed_total_return("USDJPY", drift=0.001, db_path=fx_db)
    _seed_swap_audit("AUDJPY", fx_db)
    _seed_reer(fx_db)
    monkeypatch.setattr(carry_feed, "get_carry_z", lambda d, as_of_date=None: 1.2)
    monkeypatch.setattr(carry_feed, "get_carry_differential", lambda d: 0.03)
    monkeypatch.setattr(cot_feed, "get_cot_z", lambda d, as_of_date=None: 0.8)
    monkeypatch.setattr(
        fx_pipeline,
        "cot_overlay",
        lambda sym, direction, as_of, db_path=None: CotOverlayResult(
            symbol=sym,
            as_of_date=as_of,
            base_currency_cot_percentile=55.0,
            quote_currency_cot_percentile=45.0,
            pair_crowding_status="none",
            action="none",
            modifier=0.0,
            cot_release_used="2026-06-23",
            cot_lag_days=3,
        ),
    )
    return fx_db


def _fixture_bars() -> list[dict]:
    bars = []
    price = 95.0
    for date in _trading_days(AS_OF, 30):
        price *= 1.002
        bars.append({
            "date": date,
            "open": price * 0.999,
            "high": price * 1.004,
            "low": price * 0.996,
            "close": price,
        })
    return bars


def test_full_state_with_valid_data_produces_buy(seeded):
    state = fx_pipeline.compute_symbol_state(
        "AUDJPY", AS_OF, bars=_fixture_bars(), db_path=seeded,
    )
    decision = state["decision"]
    families = state["families"]
    assert families[FAMILY_MOMENTUM].direction == "bullish"
    assert families[FAMILY_CARRY].direction == "bullish"
    assert families[FAMILY_VALUE].direction == "bullish"
    assert decision.action == ACTION_BUY
    assert set(decision.aligned_families) >= {FAMILY_CARRY, FAMILY_MOMENTUM}
    assert decision.total_score is not None
    # Vol/cost never contribute to score: caps 35+35+20+10.
    assert decision.total_score <= 100.0


def test_live_and_backtest_paths_produce_identical_decisions(seeded, monkeypatch):
    # decide_for_backtest resolves bars via backtest_data; patch it to the
    # same fixture bars used for the "live" surface.
    monkeypatch.setattr(
        "athena_fx.backtest_data.load_daily_bars",
        lambda symbols, start, end, db_path=None: (
            {"AUDJPY": _fixture_bars()}, []
        ),
    )
    live_state = fx_pipeline.compute_symbol_state(
        "AUDJPY", AS_OF, strict=True, bars=_fixture_bars(), db_path=seeded,
    )
    live_dict = live_state["decision"].to_dict()
    backtest_dict = fx_pipeline.decide_for_backtest("AUDJPY", AS_OF)
    assert backtest_dict == live_dict


def test_missing_factor_data_is_explicit_not_silent(fx_db, monkeypatch):
    # Empty DB + dead feeds: decision must be NO_TRADE with explicit
    # missing_* diagnostics (regression for silent derivatives=None-style
    # neutrality).
    monkeypatch.setattr(carry_feed, "get_carry_z", lambda d, as_of_date=None: 0.0)
    monkeypatch.setattr(carry_feed, "get_carry_differential", lambda d: None)
    monkeypatch.setattr(cot_feed, "get_cot_z", lambda d, as_of_date=None: 0.0)
    state = fx_pipeline.compute_symbol_state("AUDJPY", AS_OF, strict=True, db_path=fx_db)
    decision = state["decision"]
    assert decision.action == ACTION_NO_TRADE
    codes = {d.code for d in decision.diagnostics}
    assert "missing_carry" in codes
    assert "missing_swap" in codes
    assert "missing_reer" in codes


def test_refresh_decisions_persists(seeded):
    result = fx_pipeline.refresh_decisions(["AUDJPY"], AS_OF, db_path=seeded)
    assert result["decisions_saved"] == 1
    assert result["family_scores_saved"] == 3
    stored = fx_store.load_decisions(as_of_date=AS_OF, symbol="AUDJPY", db_path=seeded)
    assert len(stored) == 1
    assert stored[0]["symbol"] == "AUDJPY"
