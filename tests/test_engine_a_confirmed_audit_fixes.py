import logging
import math
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from config import CONFIG
from indicators import calc_bb, calc_rsi, calc_stochastic_rsi
from scoring import _classify_signal, _get_30d_correlation, get_score_threshold


def test_a_only_scan_tier_demotes_below_live_conviction_floor(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCAN_A_ONLY_AUTO_GATE_ENABLED", True)
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


def test_a_only_scan_tier_defaults_to_scan_floor_not_live_auto_gate(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCAN_A_ONLY_AUTO_GATE_ENABLED", False)
    monkeypatch.setitem(CONFIG, "AUTO_TRADE_MIN_CONVICTION", {"default": 0.50})
    monkeypatch.setitem(CONFIG, "AUTO_TRADE_A_ONLY_WEIGHT", {"default": 0.60, "crypto": 0.60})
    pair = {"display": "EUR/CHF", "symbol": "EURCHF", "type": "forex", "enabled": True}
    signal = {
        "confluenceScore": 2.38,
        "maxScore": 3.0,
        "scanThreshold": 2.1,
        "enginesAligned": False,
        "combinedConviction": 0.476,
    }

    tier, reason = _classify_signal(signal, pair)

    assert tier == "trade"
    assert reason == "Trade-ready"


def test_a_only_scan_tier_allows_scores_above_live_conviction_floor(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCAN_A_ONLY_AUTO_GATE_ENABLED", True)
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


def test_calc_confluence_passes_structure_result_into_factor_scoring(monkeypatch):
    import scoring

    captured = {}

    def fake_compute_factor_scores(*args, **kwargs):
        captured["structure_result"] = kwargs.get("structure_result")
        return {
            "final_score": 1.0,
            "direction": "LONG",
            "factor_scores": {"trend": 1.0, "momentum": 0.5},
            "weights": {"momentum": 0.4, "addon": 0.2, "base": 0.4},
            "regime": "TRENDING",
            "directional_score": 1.0,
            "nondirectional_score": 0.5,
            "feed_status": {},
            "correlation_adjustments": {},
            "disabled_factors": [],
            "directional_confidence_multiplier": 1.0,
            "factorDiagnostics": {},
        }

    def fake_confidence(**_kwargs):
        return {"confidence": 0.8}

    monkeypatch.setattr("factor_scoring.compute_factor_scores", fake_compute_factor_scores)
    monkeypatch.setattr("confidence_engine.compute_confidence", fake_confidence)
    monkeypatch.setitem(CONFIG, "RANGING", {"crypto": {"dead": 18}, "commodity": {"dead": 18}})

    structure_result = {"structural_verdict": "CLEAR"}
    out = scoring.calc_confluence(
        {"snap": {"adx": 30}},
        {"snap": {"adx": 30}},
        {"snap": {"adx": 30}},
        1.0,
        {},
        {"display": "ETH/USDT", "symbol": "ETHUSDT", "type": "crypto"},
        "neutral",
        structure_result=structure_result,
    )

    assert out["score"] == pytest.approx(1.0)
    assert captured["structure_result"] is structure_result


def test_build_oi_context_rejects_error_and_stale_payloads(monkeypatch):
    from factor_scoring import build_oi_context_for_factor_scoring

    monkeypatch.setitem(CONFIG, "ENGINE_A_OI_MAX_AGE_SEC", 300)
    d1 = [{"close": 100.0}, {"close": 110.0}]
    h1_snap = {"close": 112.0}

    assert build_oi_context_for_factor_scoring(
        {"error": True, "oiChange": 4.2, "ts": time.time()},
        d1,
        h1_snap,
    ) is None
    assert build_oi_context_for_factor_scoring(
        {"error": False, "oiChange": 4.2, "ts": time.time() - 301},
        d1,
        h1_snap,
    ) is None
    assert build_oi_context_for_factor_scoring(
        {"error": False, "oiChange": 4.2, "ts": 1_700_000_000, "point_in_time": True},
        d1,
        h1_snap,
    )["oi_change_pct"] == pytest.approx(4.2)
    assert build_oi_context_for_factor_scoring(
        {"error": False, "oiChange": 4.2, "ts": time.time()},
        d1,
        h1_snap,
    )["oi_change_pct"] == pytest.approx(4.2)


def test_backtest_confirmed_candles_strips_current_open_bucket():
    import backtest_runner

    now = datetime(2026, 5, 14, 12, 30, tzinfo=timezone.utc).timestamp()
    candles = [
        {"time": "2026-05-14T08:00:00+00:00", "close": 100.0},
        {"time": "2026-05-14T12:00:00+00:00", "close": 999.0},
    ]

    out = backtest_runner._bt_confirmed_candles(
        {"display": "BTC/USDT", "symbol": "BTCUSDT", "source": "binance", "type": "crypto"},
        "H4",
        candles,
        time_now=now,
    )

    assert out == [candles[0]]


def test_backtest_structure_context_helper_matches_live_adjustment(monkeypatch):
    import backtest_runner

    monkeypatch.setitem(CONFIG, "ENGINE_A_STRUCTURE_CONTEXT_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_STRUCTURE_CONTEXT_BONUS_CAP", 0.20)
    monkeypatch.setitem(CONFIG, "ENGINE_A_STRUCTURE_CONTEXT_MAX_MULT", 1.20)

    res = {"score": 1.5, "maxScoreOverride": 3.0, "votes": {}, "factorDiagnostics": {}}
    structure = {
        "structural_verdict": "CLEAR",
        "engine_b_independent_direction": "LONG",
        "zone_touched": True,
    }

    out = backtest_runner._apply_engine_a_structure_context_to_bt_result(
        res,
        structure_result=structure,
        direction="LONG",
        max_score=3.0,
    )

    assert out["score"] > res["score"]
    assert "explicitStructureContext" in out["factorDiagnostics"]


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
