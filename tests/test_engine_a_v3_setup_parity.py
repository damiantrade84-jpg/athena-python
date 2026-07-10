"""Phase 1.1–1.3: setup TF / indicator periods / Wilder ATR parity with quant."""

from __future__ import annotations

import importlib
import importlib.util
from datetime import datetime, timedelta, timezone

import pytest

from engine_a_v3.levels import build_structural_levels
from engine_a_v3.profile import baseline_profile
from engine_a_v3.quant_scorer import _resolve_v3_entry_tf
from engine_a_v3.routing import route_specialist
from engine_a_v3.setups import (
    SetupPeriods,
    _atr,
    _resolve_setup_candle_frames,
    atr_for_levels,
    detect_setup,
)
from indicators import calc_atr


def _candles(
    *,
    count: int,
    start: datetime,
    step: timedelta,
    base: float,
    slope: float,
) -> list[dict]:
    rows = []
    for idx in range(count):
        close = base + slope * idx
        open_ = close - (0.3 if slope >= 0 else -0.3)
        rows.append(
            {
                "time": (start + step * idx).isoformat(),
                "open": open_,
                "high": max(open_, close) + 0.5,
                "low": min(open_, close) - 0.5,
                "close": close,
                "vol": 1_000 + idx,
            }
        )
    return rows


def _frame_bundle() -> dict[str, list[dict]]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return {
        "D1": _candles(count=260, start=start, step=timedelta(days=1), base=80, slope=0.2),
        "H4": _candles(count=300, start=start, step=timedelta(hours=4), base=90, slope=0.12),
        "H1": _candles(count=320, start=start, step=timedelta(hours=1), base=100, slope=0.05),
    }


def test_setup_primary_tf_follows_execution_tf_override(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(
        CONFIG.setdefault("ENGINE_A_SCORING_PROFILE", {}),
        "BY_SCORE_GROUP",
        {
            **((CONFIG.get("ENGINE_A_SCORING_PROFILE") or {}).get("BY_SCORE_GROUP") or {}),
            "forex_exotics": {"execution_tf": "H4"},
            "crypto_other": {"execution_tf": "H4"},
        },
    )
    candles = _frame_bundle()
    # Tag frames so we can prove which list was selected.
    for row in candles["H1"]:
        row["tf_tag"] = "H1"
    for row in candles["H4"]:
        row["tf_tag"] = "H4"
    for row in candles["D1"]:
        row["tf_tag"] = "D1"

    exotic = route_specialist(
        {"display": "USD/ZAR", "symbol": "USDZAR", "type": "forex", "score_group": "forex_exotics"}
    )
    crypto = route_specialist(
        {
            "display": "AAVE/USDT",
            "symbol": "AAVEUSDT",
            "type": "crypto",
            "score_group": "crypto_other",
        }
    )
    majors = route_specialist(
        {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "score_group": "forex_majors"}
    )

    assert _resolve_v3_entry_tf("forex_exotics", "forex", "intraday") == "H4"
    assert _resolve_v3_entry_tf("crypto_other", "crypto", "intraday") == "H4"

    primary_ex, context_ex, primary_tf_ex, context_tf_ex = _resolve_setup_candle_frames(
        exotic, "intraday", candles
    )
    primary_cr, _ctx_cr, primary_tf_cr, context_tf_cr = _resolve_setup_candle_frames(
        crypto, "intraday", candles
    )
    primary_mj, _ctx_mj, primary_tf_mj, context_tf_mj = _resolve_setup_candle_frames(
        majors, "intraday", candles
    )

    assert primary_tf_ex == "H4"
    assert context_tf_ex == "D1"
    assert primary_ex[-1]["tf_tag"] == "H4"
    assert context_ex[-1]["tf_tag"] == "D1"

    assert primary_tf_cr == "H4"
    assert context_tf_cr == "D1"
    assert primary_cr[-1]["tf_tag"] == "H4"

    assert primary_tf_mj == "H1"
    assert context_tf_mj == "H4"
    assert primary_mj[-1]["tf_tag"] == "H1"

    # detect_setup must not crash and must use the resolved frames internally.
    detect_setup(exotic, "intraday", candles)
    detect_setup(crypto, "intraday", candles)


@pytest.mark.parametrize(
    ("score_group", "asset_type", "horizon", "expected"),
    [
        ("forex_majors", "forex", "intraday", "H1"),
        ("forex_exotics", "forex", "intraday", "H4"),
        ("crypto_other", "crypto", "intraday", "H4"),
    ],
)
def test_public_v3_entry_timeframe_resolver_is_group_aware(
    score_group, asset_type, horizon, expected
):
    assert importlib.util.find_spec("engine_a_v3.timeframes") is not None
    resolver = importlib.import_module(
        "engine_a_v3.timeframes"
    ).resolve_v3_entry_timeframe
    assert resolver(score_group, asset_type, horizon) == expected


def test_invalid_v3_entry_timeframe_override_fails_closed(monkeypatch):
    from config import CONFIG

    by_group = dict(
        ((CONFIG.get("ENGINE_A_SCORING_PROFILE") or {}).get("BY_SCORE_GROUP") or {})
    )
    by_group["forex_exotics"] = {
        **dict(by_group.get("forex_exotics") or {}),
        "execution_tf": "M15",
    }
    monkeypatch.setitem(
        CONFIG.setdefault("ENGINE_A_SCORING_PROFILE", {}), "BY_SCORE_GROUP", by_group
    )

    assert _resolve_v3_entry_tf("forex_exotics", "forex", "intraday") is None


@pytest.mark.parametrize(
    ("display", "symbol", "asset_type"),
    [
        ("USD/ZAR", "USDZAR", "forex"),
        ("USD/MXN", "USDMXN", "forex"),
        ("USD/BRL", "USDBRL", "forex"),
        ("USD/INR", "USDINR", "forex"),
        ("AAVE/USDT", "AAVEUSDT", "crypto"),
        ("ALGO/USDT", "ALGOUSDT", "crypto"),
        ("ATOM/USDT", "ATOMUSDT", "crypto"),
        ("BCH/USDT", "BCHUSDT", "crypto"),
        ("ETC/USDT", "ETCUSDT", "crypto"),
        ("TRX/USDT", "TRXUSDT", "crypto"),
        ("XLM/USDT", "XLMUSDT", "crypto"),
        ("FIL/USDT", "FILUSDT", "crypto"),
        ("ICP/USDT", "ICPUSDT", "crypto"),
        ("HBAR/USDT", "HBARUSDT", "crypto"),
        ("SEI/USDT", "SEIUSDT", "crypto"),
    ],
)
def test_configured_h4_entry_pairs_route_to_h4(display, symbol, asset_type):
    route = route_specialist({"display": display, "symbol": symbol, "type": asset_type})
    assert route.score_group in {"forex_exotics", "crypto_other"}
    assert _resolve_v3_entry_tf(route.score_group, asset_type, "intraday") == "H4"


def test_setup_uses_group_ema_periods_forex_vs_crypto():
    forex_profile = baseline_profile("forex_majors", "intraday")
    crypto_profile = baseline_profile("crypto_btc", "intraday")
    forex_periods = SetupPeriods.from_mapping(dict(forex_profile.indicator_periods))
    crypto_periods = SetupPeriods.from_mapping(dict(crypto_profile.indicator_periods))

    assert forex_periods.ema_trend == 26
    assert forex_periods.ema_momentum == 60
    assert crypto_periods.ema_trend == 18
    assert crypto_periods.ema_momentum == 40
    assert forex_periods.ema_trend != crypto_periods.ema_trend


def test_levels_atr_matches_indicator_bundle():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = _candles(count=80, start=start, step=timedelta(hours=1), base=100, slope=0.05)
    # Add noise so TR mean != Wilder ATR.
    for idx, row in enumerate(rows):
        row["high"] = row["close"] + 0.8 + (idx % 5) * 0.15
        row["low"] = row["close"] - 0.6 - (idx % 3) * 0.1

    period = 14
    highs = [float(c["high"]) for c in rows]
    lows = [float(c["low"]) for c in rows]
    closes = [float(c["close"]) for c in rows]
    series = calc_atr(highs, lows, closes, period)
    expected = next(float(v) for v in reversed(series) if v is not None)

    assert atr_for_levels(rows, period) == expected
    assert _atr(rows, period) == expected

    # Simple TR mean must differ from Wilder on this noisy series.
    from statistics import fmean

    trs = []
    for previous, current in zip(rows[:-1], rows[1:]):
        high = float(current["high"])
        low = float(current["low"])
        prev_close = float(previous["close"])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    simple_mean = fmean(trs[-period:])
    assert abs(simple_mean - expected) > 1e-9

    levels = build_structural_levels(rows, direction="LONG", atr_period=period)
    assert levels is not None
    # Invalidation uses 0.8 * Wilder ATR when structural extreme is farther.
    structural = min(float(c["low"]) for c in rows[-20:])
    current = float(rows[-1]["close"])
    expected_inv = min(structural, current - 0.8 * expected)
    assert abs(levels.invalidation - expected_inv) < 1e-9
