"""Chart API indicators vs Engine A indicator bundle on the same Bybit candles."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from indicators import calc_indicators_with_normalized


def _bybit_candles(count: int = 40) -> list[dict]:
    rows = []
    for idx in range(count):
        close = 100.0 + idx
        volume = 1000.0 + idx
        rows.append(
            {
                "open_time": 1_779_300_000_000 + idx * 14_400_000,
                "time": f"2026-05-{20 + idx // 6:02d}T{(idx % 6) * 4:02d}:00:00+00:00",
                "open": close - 1.0,
                "high": close + 1.0,
                "low": close - 2.0,
                "close": close,
                "volume": volume,
                "vol": volume,
                "turnover": volume * close,
                "confirmed": True,
                "provider": "bybit",
                "category": "linear",
            }
        )
    return rows


def test_chart_indicator_bundle_matches_calc_indicators_on_bybit_candles():
    from athena_app.api import routes_market_data as routes
    from factor_scoring import _resolve_rsi_period
    from scoring import get_pair_score_group

    pair = {"symbol": "TRXUSDT", "type": "crypto", "display": "TRX/USDT"}
    candles = _bybit_candles(40)
    score_group = get_pair_score_group(pair)
    rows, meta = routes._format_chart_candles(
        candles,
        tf="H4",
        provider="bybit",
        category="linear",
        bybit_symbol="TRXUSDT",
        include_indicators=True,
        pair=pair,
    )
    assert "cumulative_turnover_divided_by_cumulative_volume" in str(meta.get("vwap_formula") or "")
    assert meta["indicator_periods"]["rsi"] == _resolve_rsi_period(score_group, "crypto")

    engine = calc_indicators_with_normalized(candles, "crypto", score_group=score_group)
    snap = engine.get("snap") or {}

    last = rows[-1]
    assert last["provider"] == "bybit"
    assert last["turnover"] > 0
    assert last["volume_ma"] is not None
    assert last["vwap"] is not None
    assert last["ema_trend"] == snap.get("ema21")
    assert last["ema_momentum"] == snap.get("ema50")
    assert last["rsi14"] == snap.get("rsi")
    assert last["atr14"] == snap.get("atr")
    assert last["adx14"] == snap.get("adx")
