"""Focused Engine B backtest timeframe-wiring regression checks."""

from datetime import datetime, timedelta, timezone

import pytest

import backtest_runner


def test_engine_b_monitoring_uses_execution_series_and_h4_equivalent_hold() -> None:
    m15 = [{"time": "2026-07-15T10:00:00+00:00"}]
    h4 = [{"time": "2026-07-15T08:00:00+00:00"}]

    monitor_tf, monitor_candles, hold_multiplier = (
        backtest_runner._engine_b_monitoring_context(
            {"M15": m15, "H4": h4}, "M15"
        )
    )

    assert monitor_tf == "M15"
    assert monitor_candles is m15
    assert hold_multiplier == 16


def test_engine_b_monitoring_fails_closed_when_execution_series_is_missing() -> None:
    with pytest.raises(ValueError, match="M15"):
        backtest_runner._engine_b_monitoring_context({"H4": [{}]}, "M15")


def test_engine_b_context_is_capped_to_live_scan_limit() -> None:
    rows = [{"id": index} for index in range(12)]

    context = backtest_runner._bt_engine_b_live_context(
        rows,
        10,
        "M15",
        {"M15": 4},
    )

    assert [row["id"] for row in context] == [6, 7, 8, 9]


def test_preconfirmed_engine_b_context_skips_redundant_market_state_split(monkeypatch) -> None:
    import market_structure

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def bars(step: timedelta, count: int) -> list[dict]:
        return [
            {
                "time": (start + (step * index)).isoformat(),
                "open": 100.0 + (index * 0.01),
                "high": 101.0 + (index * 0.01),
                "low": 99.0 + (index * 0.01),
                "close": 100.2 + (index * 0.01),
                "vol": 1_000.0 + index,
            }
            for index in range(count)
        ]

    d1 = bars(timedelta(days=1), 80)
    h4 = bars(timedelta(hours=4), 120)
    h1 = bars(timedelta(hours=1), 240)
    m15 = bars(timedelta(minutes=15), 320)

    def _unexpected_split(*_args, **_kwargs):
        raise AssertionError("preconfirmed backtest context was split again")

    monkeypatch.setattr(
        market_structure,
        "_engine_b_confirmed_only_struct_candles",
        _unexpected_split,
    )
    result = market_structure.NakedEngine().precompute_structure_data(
        d1,
        h4,
        h1,
        current_price=102.0,
        atr=1.0,
        regime="TRENDING",
        asset_type="forex",
        enable_zone_registry=False,
        enable_profile_context=False,
        style="intraday",
        pair={
            "display": "EUR/USD",
            "symbol": "EURUSD",
            "type": "forex",
            "score_group": "forex_majors",
        },
        trade_bucket_historical_mode=True,
        role_candles={"D1": d1, "H4": h4, "H1": h1, "M15": m15},
        struct_candles_confirmed=True,
    )

    assert result.get("_error") is None
    assert result["forming_strip_diagnostics"]["reason"] == "preconfirmed_by_caller"
