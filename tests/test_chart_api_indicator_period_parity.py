"""Chart API indicator periods must match Engine A _resolve_* per score group."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from factor_scoring import _resolve_ema_periods, _resolve_rsi_period
from indicators import calc_rsi
from scoring import get_pair_score_group


def _synthetic_candles(n: int = 220) -> list[dict]:
    candles = []
    for idx in range(n):
        close = 100.0 + idx * 0.05
        candles.append(
            {
                "time": f"2026-05-20T{(idx % 24):02d}:00:00+00:00",
                "open": close - 0.2,
                "high": close + 0.4,
                "low": close - 0.4,
                "close": close,
                "volume": 1000 + idx,
                "confirmed": True,
            }
        )
    return candles


@pytest.mark.parametrize(
    ("pair", "expected_group", "asset_type"),
    [
        ({"symbol": "EURUSD", "type": "forex", "display": "EUR/USD"}, "forex_majors", "forex"),
        ({"symbol": "TRXUSDT", "type": "crypto", "display": "TRX/USDT"}, None, "crypto"),
    ],
)
def test_format_chart_candles_uses_group_rsi_period(pair, expected_group, asset_type):
    from athena_app.api import routes_market_data as routes

    candles = _synthetic_candles()
    group = expected_group or get_pair_score_group(pair)
    expected_rsi = _resolve_rsi_period(group, asset_type)
    expected_ema = _resolve_ema_periods(group, asset_type)

    periods = routes._resolve_chart_indicator_periods(pair)
    assert periods["rsi"] == expected_rsi
    assert periods["ema"] == expected_ema

    rows, meta = routes._format_chart_candles(
        candles,
        tf="H4",
        include_indicators=True,
        pair=pair,
    )
    assert meta.get("indicator_periods") == periods
    assert rows[-1]["rsi"] is not None
    assert rows[-1]["rsi14"] == rows[-1]["rsi"]

    closes = [row["c"] for row in rows]
    expected_last = calc_rsi(closes, expected_rsi)[-1]
    assert rows[-1]["rsi"] == pytest.approx(expected_last, rel=1e-9)


def test_tv_chart_panel_threads_rsi_period_into_study_snapshot():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "static/react-app/app/src/components/panels/TVChartPanel.tsx"
    text = source.read_text(encoding="utf-8")
    assert "indicatorPeriods" in text
    assert "buildStudyIndicatorDefs" in text
    assert "rsi_period" in text
    assert "buildChartStudySnapshot(candles, liveTick, backendTf, isCryptoChart, showVwapOnChart, emaPeriods, indicatorPeriods)" in text
    assert "computed_at" in text
    assert "engineBOverlayStale" in text
    assert "engine_a_vwap_filter_enabled" in text
