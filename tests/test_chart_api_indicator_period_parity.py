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


def test_format_chart_candles_excludes_forming_bar_from_indicators():
    """Engine A scores confirmed-only; a forming last bar must carry no indicator point."""
    from athena_app.api import routes_market_data as routes
    from indicators import calc_indicators_with_normalized
    from scoring import get_pair_score_group

    pair = {"symbol": "TRXUSDT", "type": "crypto", "display": "TRX/USDT"}
    candles = _synthetic_candles(60)
    candles[-1]["confirmed"] = False

    rows, meta = routes._format_chart_candles(
        candles,
        tf="H4",
        provider="bybit",
        category="linear",
        bybit_symbol="TRXUSDT",
        include_indicators=True,
        pair=pair,
    )

    assert meta["indicator_basis"] == "confirmed_only"
    assert meta["forming_bars_excluded"] == 1
    last = rows[-1]
    assert last["confirmed"] is False
    # Forming bar is still returned for price display but has no indicator point.
    assert last["c"] == candles[-1]["close"]
    for field in ("rsi", "ema_trend", "ema_momentum", "ema_long", "atr14", "adx14", "volume_ma"):
        assert last[field] is None, field

    # Last confirmed bar must match Engine A's confirmed-only bundle.
    score_group = get_pair_score_group(pair)
    engine = calc_indicators_with_normalized(candles[:-1], "crypto", score_group=score_group)
    snap = engine.get("snap") or {}
    prev = rows[-2]
    assert prev["rsi"] == snap.get("rsi")
    assert prev["ema_trend"] == snap.get("ema21")
    assert prev["atr14"] == snap.get("atr")


def test_format_chart_candles_sets_confirmed_without_provider():
    """Forex/generic path: confirmed flags inferred from ISO open time, no provider needed."""
    from datetime import datetime, timedelta, timezone

    from athena_app.api import routes_market_data as routes

    pair = {"symbol": "EURUSD", "type": "forex", "display": "EUR/USD"}
    now = datetime.now(timezone.utc)
    candles = []
    for idx in range(40):
        # Last bar opens 1h ago (H4 still forming); earlier bars fully closed.
        open_dt = now - timedelta(hours=1) - timedelta(hours=4) * (39 - idx)
        close = 1.10 + idx * 0.001
        candles.append(
            {
                "time": open_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                "open": close - 0.001,
                "high": close + 0.002,
                "low": close - 0.002,
                "close": close,
                "volume": 1000 + idx,
            }
        )

    rows, meta = routes._format_chart_candles(
        candles,
        tf="H4",
        include_indicators=True,
        pair=pair,
    )

    assert rows[-1]["confirmed"] is False
    assert rows[-2]["confirmed"] is True
    assert meta["forming_bars_excluded"] == 1
    assert rows[-1]["rsi"] is None
    assert rows[-2]["rsi"] is not None


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
