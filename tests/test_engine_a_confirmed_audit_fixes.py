import logging
import math
import sys
import types
from pathlib import Path

import pytest

from config import CONFIG
from indicators import calc_bb, calc_rsi, calc_stochastic_rsi
from scoring import _classify_signal, _get_30d_correlation, get_score_threshold


def test_a_only_scan_tier_demotes_below_live_conviction_floor(monkeypatch):
    monkeypatch.setitem(CONFIG, "AUTO_TRADE_MIN_CONVICTION", {"default": 0.50})
    monkeypatch.setitem(CONFIG, "AUTO_TRADE_A_ONLY_WEIGHT", {"default": 0.60, "crypto": 0.60})
    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "enabled": True}
    signal = {
        "confluenceScore": 2.0,
        "maxScore": 3.0,
        "scanThreshold": 2.0,
        "enginesAligned": False,
        "combinedConviction": 0.4,
    }

    tier, reason = _classify_signal(signal, pair)

    assert tier == "watchlist"
    assert "A-only auto gate requires about 2.50/3.0" in reason


def test_a_only_scan_tier_allows_scores_above_live_conviction_floor(monkeypatch):
    monkeypatch.setitem(CONFIG, "AUTO_TRADE_MIN_CONVICTION", {"default": 0.50})
    monkeypatch.setitem(CONFIG, "AUTO_TRADE_A_ONLY_WEIGHT", {"default": 0.60, "crypto": 0.60})
    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "enabled": True}
    signal = {
        "confluenceScore": 2.6,
        "maxScore": 3.0,
        "scanThreshold": 2.0,
        "enginesAligned": False,
        "combinedConviction": 0.52,
    }

    tier, reason = _classify_signal(signal, pair)

    assert tier == "trade"
    assert "A-only auto gate" not in reason


def test_pair_profile_min_confluence_cannot_lower_group_threshold(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_THRESHOLDS", {"default": 1.5})
    monkeypatch.setitem(CONFIG, "PAIR_PROFILES", {"XAU/USD": {"min_confluence": 1.05}})
    pair = {"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity"}

    assert get_score_threshold(pair) == pytest.approx(1.5)


def test_pair_profile_can_lower_threshold_only_when_explicitly_allowed(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_THRESHOLDS", {"default": 1.5})
    monkeypatch.setitem(
        CONFIG,
        "PAIR_PROFILES",
        {"XAU/USD": {"min_confluence": 1.05, "allow_lower_threshold": True}},
    )
    pair = {"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity"}

    assert get_score_threshold(pair) == pytest.approx(1.05)


def test_engine_a_scoring_candles_from_state_uses_confirmed_only_for_all_assets():
    from athena_app.services.candle_service import engine_a_scoring_candles_from_state

    confirmed = [{"time": 1, "close": 10.0}, {"time": 2, "close": 11.0}]
    forming = {"time": 3, "close": 99.0}
    state = {"confirmed": confirmed, "forming": forming, "is_live": True}

    candles = engine_a_scoring_candles_from_state(
        {"display": "BTC/USDT", "type": "crypto"},
        state,
    )

    assert candles == confirmed


def test_structure_first_entry_fails_closed_without_required_bos():
    from backtest_runner import _engine_a_structure_first_entry_passes

    ok, detail = _engine_a_structure_first_entry_passes(
        {"bos_confirmed": False, "choch_confirmed": False, "direction": "LONG"},
        "LONG",
        trigger_candles=[{"close": 1.0}] * 10,
        cfg={"enabled": True, "lookback_bars": 5, "require_bos": True, "require_choch": False},
    )

    assert ok is False
    assert detail["reason"] == "missing_required_structure"


def test_structure_first_entry_requires_recent_bos():
    from backtest_runner import _engine_a_structure_first_entry_passes

    stale, stale_detail = _engine_a_structure_first_entry_passes(
        {
            "bos_confirmed": True,
            "direction": "LONG",
            "bos_data": {"bos_bull_bar_index": 1},
        },
        "LONG",
        trigger_candles=[{"close": 1.0}] * 10,
        cfg={"enabled": True, "lookback_bars": 5, "require_bos": True, "require_choch": False},
    )
    recent, recent_detail = _engine_a_structure_first_entry_passes(
        {
            "bos_confirmed": True,
            "direction": "LONG",
            "bos_data": {"bos_bull_bar_index": 8},
        },
        "LONG",
        trigger_candles=[{"close": 1.0}] * 10,
        cfg={"enabled": True, "lookback_bars": 5, "require_bos": True, "require_choch": False},
    )

    assert stale is False
    assert stale_detail["reason"] == "structure_not_recent"
    assert recent is True
    assert recent_detail["bos_recent"] is True


def test_bollinger_bands_use_population_standard_deviation():
    data = [float(i) for i in range(1, 21)]
    out = calc_bb(data, 20, 2)
    mean = sum(data) / len(data)
    population_sd = math.sqrt(sum((x - mean) ** 2 for x in data) / len(data))

    assert out["upper"][-1] == pytest.approx(mean + 2 * population_sd)
    assert out["lower"][-1] == pytest.approx(mean - 2 * population_sd)


def _expected_stoch_rsi_from_wilder(closes, rsi_period=14, stoch_period=14, k_smooth=3, d_smooth=3):
    rsi_values = calc_rsi(closes, rsi_period)
    raw_k = [None] * len(rsi_values)
    for i in range(len(rsi_values)):
        window = rsi_values[i - stoch_period + 1 : i + 1]
        if i < stoch_period - 1 or any(v is None for v in window):
            continue
        lo = min(window)
        hi = max(window)
        raw_k[i] = 50.0 if hi == lo else ((rsi_values[i] - lo) / (hi - lo)) * 100

    k = [None] * len(raw_k)
    for i in range(len(raw_k)):
        window = raw_k[i - k_smooth + 1 : i + 1]
        if i >= k_smooth - 1 and all(v is not None for v in window):
            k[i] = sum(window) / k_smooth

    d = [None] * len(k)
    for i in range(len(k)):
        window = k[i - d_smooth + 1 : i + 1]
        if i >= d_smooth - 1 and all(v is not None for v in window):
            d[i] = sum(window) / d_smooth
    return {"k": k, "d": d}


def test_stochastic_rsi_uses_wilder_rsi_series():
    closes = [
        100, 101, 102, 101, 100, 99, 101, 103, 102, 104,
        105, 103, 102, 104, 106, 107, 105, 104, 103, 105,
        108, 109, 107, 106, 108, 110, 111, 109, 108, 110,
        112, 111, 113, 114, 112, 115,
    ]
    candles = [{"close": float(c)} for c in closes]
    expected = _expected_stoch_rsi_from_wilder([float(c) for c in closes])

    out = calc_stochastic_rsi(candles, 14, 14, 3, 3)

    assert out["k"][-1] == pytest.approx(expected["k"][-1])
    assert out["d"][-1] == pytest.approx(expected["d"][-1])


def test_addon_feed_exception_surfaces_error_status(monkeypatch):
    from factor_scoring import _asset_addon

    fake_carry = types.ModuleType("carry_feed")
    fake_carry._PAIR_CARRY_FORMULA = {"EUR/USD": object()}

    def boom(*_args, **_kwargs):
        raise RuntimeError("carry db locked")

    fake_carry.get_carry_z = boom
    monkeypatch.setitem(sys.modules, "carry_feed", fake_carry)

    value, addon_type, status = _asset_addon(
        {"type": "forex", "display": "EUR/USD"},
        "LONG",
        None,
        "2026-05-13T00:00:00Z",
    )

    assert value == pytest.approx(0.0)
    assert addon_type == "carry"
    assert status == "error"


def test_btc_correlation_heuristic_fallback_is_logged(caplog):
    caplog.set_level(logging.WARNING, logger="athena")

    corr = _get_30d_correlation(pair_display="ETH/USDT")

    assert corr == pytest.approx(0.90)
    assert "heuristic BTC correlation fallback" in caplog.text


def test_dead_engine_a_config_blocks_removed_from_runtime_config():
    assert "REGIME_WEIGHTS" not in CONFIG
    assert "FACTOR_SCORE_GROUP_MULTIPLIERS" not in CONFIG
    assert "CRYPTO_FACTOR_WEIGHT_CAPS" not in CONFIG
    assert "CRYPTO_TRANSITION_PENALTY" not in CONFIG
    assert "CRYPTO_TRANSITION_PENALTY_ENABLED" not in CONFIG

    indicator_weights = CONFIG.get("INDICATOR_WEIGHTS", {})
    assert set(indicator_weights).issubset({"trend", "momentum"})


def test_stock_advisory_flags_are_explicitly_diagnostic():
    assert "INSIDER_TRADING_ENABLED" not in CONFIG
    assert "FUNDAMENTALS_ENABLED" not in CONFIG
    assert CONFIG["INSIDER_TRADING_DIAGNOSTIC_ENABLED"] is True
    assert CONFIG["FUNDAMENTALS_DIAGNOSTIC_ENABLED"] is True


def test_dead_research_score_group_stub_removed():
    source = Path("factor_scoring.py").read_text(encoding="utf-8")

    assert "_infer_research_score_group" not in source


def test_momentum_quality_max_raw_is_not_hardcoded_constant():
    source = Path("factor_scoring.py").read_text(encoding="utf-8")

    assert "_max_raw = 0.50" not in source
