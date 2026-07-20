"""Stage 4 verification: Engine A adapter data plumbing + anti-lookahead.

The scoring-identity property itself (prefix-copy vs as-of-index snapshot
cache producing byte-identical signals) is already covered by
tests/test_engine_a_v3_backtest_parity.py::
test_vectorized_snapshot_cache_matches_prefix_indicator_path_exactly, which
Stage 4 kept passing after making that cache path always-on (no separate
toggle exists anymore to re-diff against, by design). These tests cover the
adapter-level concerns: candle loading shape and anti-lookahead through the
now-always-on cache path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from athena_backtest.engines.engine_a import run_engine_a_backtest


def _bars(*, timeframe: str, count: int) -> list[dict]:
    step = {"D1": timedelta(days=1), "H4": timedelta(hours=4), "H1": timedelta(hours=1)}[timeframe]
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    out = []
    for index in range(count):
        center = 100.0 + (index % 13) * 0.1
        out.append(
            {
                "time": (start + index * step).isoformat(),
                "open": center,
                "high": center + 0.8,
                "low": center - 0.8,
                "close": center + 0.1,
                "vol": 1000.0 + index,
            }
        )
    return out


def _pair() -> dict:
    return {
        "display": "EUR/USD",
        "symbol": "EURUSD=X",
        "type": "forex",
        "source": "mt5",
        "score_group": "forex_majors",
        "enabled": True,
    }


def test_anti_lookahead_future_mutation_does_not_change_past_signal():
    candles = {
        "D1": _bars(timeframe="D1", count=300),
        "H4": _bars(timeframe="H4", count=600),
        "H1": _bars(timeframe="H1", count=1000),
    }
    pair = _pair()

    result_a = run_engine_a_backtest(pair, horizon="intraday", candles=candles, collect_funnel=True)

    mutated = {tf: [dict(c) for c in rows] for tf, rows in candles.items()}
    for c in mutated["H1"][900:]:
        c["close"] = float(c["close"]) * 5.0
        c["high"] = float(c["high"]) * 5.0
    result_b = run_engine_a_backtest(pair, horizon="intraday", candles=mutated, collect_funnel=True)

    trades_a_before_900 = [t for t in result_a["trades"] if True]
    trades_b_before_900 = [t for t in result_b["trades"] if True]
    # Trades decided well before the mutated tail must be identical between runs.
    early_a = [t for t in trades_a_before_900 if t["date"] < mutated["H1"][850]["time"]]
    early_b = [t for t in trades_b_before_900 if t["date"] < mutated["H1"][850]["time"]]
    assert early_a == early_b


def test_engine_a_adapter_returns_funnel_and_timing_fields():
    candles = {
        "D1": _bars(timeframe="D1", count=250),
        "H4": _bars(timeframe="H4", count=400),
        "H1": _bars(timeframe="H1", count=500),
    }
    result = run_engine_a_backtest(_pair(), horizon="intraday", candles=candles, collect_funnel=True)
    assert "funnel" in result
    assert result["funnel"]["barsEvaluated"] > 0
    assert result["entryTimeframe"] in ("H1", "H4", "D1")
