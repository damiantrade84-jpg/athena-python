"""tests/test_athena.py — Integration tests for Sentinel Pro modules.

Run: python -m pytest tests/ -v
"""
import pytest
import math

# ── Indicator unit tests ─────────────────────────────────────────────────────

from indicators import (
    calc_ema, calc_sma, calc_rsi, calc_macd, calc_atr, calc_adx, calc_bb,
    calc_stochastic, calc_fib, calc_indicators, calc_levels,
    calc_adx_momentum, calc_adx_percentile, calc_atr_percentile,
    calc_rsi_divergence, calc_weinstein_stage, calc_fib_proximity,
)


class TestEMA:
    def test_basic_ema(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        result = calc_ema(data, 3)
        assert result[0] is None
        assert result[1] is None
        assert result[2] == pytest.approx(2.0)  # SMA seed
        assert result[-1] is not None
        assert result[-1] > result[2]  # EMA should increase with rising data

    def test_ema_short_input(self):
        result = calc_ema([1.0, 2.0], 5)
        assert all(v is None for v in result)


class TestSMA:
    def test_basic_sma(self):
        data = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = calc_sma(data, 3)
        assert result[0] is None
        assert result[1] is None
        assert result[2] == pytest.approx(20.0)
        assert result[4] == pytest.approx(40.0)


class TestRSI:
    def test_rsi_range(self):
        # Monotonically increasing prices → RSI should be high
        data = [float(i) for i in range(1, 50)]
        result = calc_rsi(data, 14)
        valid = [v for v in result if v is not None]
        assert len(valid) > 0
        assert all(0 <= v <= 100 for v in valid)
        assert valid[-1] > 70  # strong uptrend

    def test_rsi_short_input(self):
        result = calc_rsi([1.0, 2.0], 14)
        assert all(v is None for v in result)


class TestMACD:
    def test_macd_structure(self):
        data = [float(i) for i in range(1, 60)]
        result = calc_macd(data)
        assert "macd" in result
        assert "signal" in result
        assert "hist" in result
        assert len(result["macd"]) == len(data)


class TestATR:
    def test_atr_positive(self):
        n = 30
        h = [10.0 + i * 0.1 for i in range(n)]
        l = [9.0 + i * 0.1 for i in range(n)]
        c = [9.5 + i * 0.1 for i in range(n)]
        result = calc_atr(h, l, c, 14)
        valid = [v for v in result if v is not None]
        assert len(valid) > 0
        assert all(v > 0 for v in valid)


class TestBB:
    def test_bb_bands_order(self):
        data = [float(i) for i in range(1, 30)]
        result = calc_bb(data, 20, 2)
        # At the end, upper > mid > lower
        assert result["upper"][-1] > result["mid"][-1] > result["lower"][-1]


class TestStochastic:
    def test_stochastic_range(self):
        candles = [{"high": 10 + i, "low": 9 + i, "close": 9.5 + i} for i in range(30)]
        result = calc_stochastic(candles, 14, 3, 3)
        valid_k = [v for v in result["k"] if v is not None]
        assert len(valid_k) > 0
        assert all(0 <= v <= 100 for v in valid_k)


class TestFib:
    def test_fib_levels(self):
        candles = [{"high": 100 + i * 0.5, "low": 90 + i * 0.3, "close": 95 + i * 0.4} for i in range(60)]
        result = calc_fib(candles)
        assert result["highest"] > result["lowest"]
        assert result["fib382"] > result["fib618"]  # 38.2% closer to high
        assert result["fib500"] > result["fib618"]


class TestCalcLevels:
    def test_long_levels(self):
        result = calc_levels(100.0, 2.0, "LONG", "forex")
        assert result["sl"] < 100.0   # SL below price for LONG
        assert result["tp1"] > 100.0  # TP above price for LONG
        assert result["tp2"] > result["tp1"]
        assert result["rr1"] > 0
        assert result["rr2"] > result["rr1"]

    def test_short_levels(self):
        result = calc_levels(100.0, 2.0, "SHORT", "crypto")
        assert result["sl"] > 100.0   # SL above price for SHORT
        assert result["tp1"] < 100.0  # TP below price for SHORT
        assert result["tp2"] < result["tp1"]


class TestCalcIndicators:
    def test_full_indicator_set(self):
        candles = [{"open": 100 + i * 0.1, "high": 101 + i * 0.1,
                    "low": 99 + i * 0.1, "close": 100.5 + i * 0.1, "vol": 1000}
                   for i in range(260)]
        result = calc_indicators(candles)
        snap = result["snap"]
        assert snap["ema21"] is not None
        assert snap["ema50"] is not None
        assert snap["ema200"] is not None
        assert snap["rsi"] is not None
        assert snap["atr"] is not None
        assert snap["adx"] is not None


# ── Config tests ─────────────────────────────────────────────────────────────

from config import CONFIG, validate_config


class TestConfig:
    def test_required_keys(self):
        required = ["RISK_PCT", "SL_ATR_MULT", "TP1_ATR_MULT", "TP2_ATR_MULT",
                     "D1_CANDLES", "H4_CANDLES", "H1_CANDLES", "MIN_CONFLUENCE"]
        for k in required:
            assert k in CONFIG, f"Missing CONFIG key: {k}"

    def test_asset_class_coverage(self):
        classes = {"crypto", "forex", "commodity", "stock", "index"}
        for key in ("RISK_MULT", "RANGING", "ATR_CLASS", "RSI_BOUNDS",
                     "BT_MIN", "MIN_CONFLUENCE_CLASS"):
            assert classes == set(CONFIG[key].keys()), f"{key} missing classes"

    def test_validate_config_no_crash(self):
        # Should not raise on valid config
        validate_config(CONFIG)


# ── Scoring tests ────────────────────────────────────────────────────────────

from scoring import get_session, detect_div, apply_correlation_cap, _build_event_risk, _classify_signal
from config import _json_safe

class TestGetSession:
    def test_returns_dict(self):
        s = get_session()
        assert "name" in s
        assert "quality" in s
        assert s["quality"] in ("high", "medium", "low")


class TestApplyCorrelationCap:
    def test_warns_on_cluster_overflow(self):
        signals = [
            {"pair": "XAU/USD", "warnings": []},
            {"pair": "XAG/USD", "warnings": []},
        ]
        result = apply_correlation_cap(signals)
        # Second metals pair should get a correlation warning
        assert any("CORR CAP" in w for w in result[1]["warnings"])

    def test_no_warning_single_pair(self):
        signals = [{"pair": "XAU/USD", "warnings": []}]
        result = apply_correlation_cap(signals)
        assert not any("CORR CAP" in w for w in result[0]["warnings"])


class TestDetectDiv:
    def test_no_crash_on_short_data(self):
        candles = [{"close": 100, "high": 101, "low": 99, "vol": 1000}] * 5
        result = detect_div(candles, candles, candles)
        assert isinstance(result, list)


class TestScanClassification:
    def test_trade_signal_requires_enabled_and_unblocked(self):
        pair = {"type": "stock", "enabled": True}
        signal = {
            "scanThreshold": 6.0,
            "confluenceScore": 6.8,
            "eventRisk": {"hardBlock": False},
            "exchangeClosed": False,
            "trendState": "TRENDING",
            "scanDiagnostics": [],
        }
        tier, reason = _classify_signal(signal, pair)
        assert tier == "trade"
        assert reason == "Trade-ready"

    def test_disabled_pair_becomes_watchlist(self):
        pair = {"type": "stock", "enabled": False}
        signal = {
            "scanThreshold": 6.0,
            "confluenceScore": 6.8,
            "eventRisk": {"hardBlock": False},
            "exchangeClosed": False,
            "trendState": "TRENDING",
            "scanDiagnostics": [{"code": "inactive_pair", "detail": "Pair not auto-enabled for live trading"}],
        }
        tier, reason = _classify_signal(signal, pair)
        assert tier == "watchlist"
        assert "not auto-enabled" in reason

    def test_event_block_forces_watchlist(self):
        pair = {"symbol": "AAPL.US", "type": "stock", "enabled": True}
        risk = _build_event_risk(
            pair,
            {},
            {"AAPL.US": {"date": "2026-03-07", "daysTo": 1}},
            set(),
        )
        assert risk["hardBlock"] is True
        signal = {
            "scanThreshold": 6.0,
            "confluenceScore": 7.2,
            "eventRisk": risk,
            "exchangeClosed": False,
            "trendState": "TRENDING",
            "scanDiagnostics": [{"code": "event_risk", "detail": "Earnings in 1 day(s)"}],
        }
        tier, reason = _classify_signal(signal, pair)
        assert tier == "watchlist"
        assert "Earnings" in reason


class TestJsonSafety:
    def test_non_finite_floats_are_sanitized(self):
        payload = {
            "atr": float("nan"),
            "score": float("inf"),
            "signals": [{"ema200Slope": float("-inf"), "pair": "EUR/USD"}],
        }
        cleaned = _json_safe(payload)
        assert cleaned["atr"] is None
        assert cleaned["score"] is None
        assert cleaned["signals"][0]["ema200Slope"] is None
