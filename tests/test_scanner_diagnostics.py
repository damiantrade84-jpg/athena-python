import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scanner import annotate_signal_for_scan


def test_annotate_signal_for_scan_includes_forex_zero_score_diagnostics():
    signal = {
        "confluenceScore": 0.0,
        "trendState": "NONE",
        "warnings": [],
        "factorScores": {
            "zero_score_reasons": [
                "hurst_veto_trend",
                "trend_gate_adx_below_min",
                "breakout_inactive",
            ]
        },
    }
    pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex", "enabled": True}

    out = annotate_signal_for_scan(
        signal,
        pair,
        threshold=0.85,
        ds_ctx={},
        earnings_ctx={},
        closed_exchanges=set(),
        news_ctx={},
    )

    codes = [d.get("code") for d in out.get("scanDiagnostics", [])]
    details = [d.get("detail") for d in out.get("scanDiagnostics", [])]

    assert "low_confluence" in codes
    assert "forex_hurst_veto_trend" in codes
    assert "forex_trend_gate_adx_below_min" in codes
    assert "forex_breakout_inactive" in codes
    assert "Hurst veto blocked the trend path" in details
    assert "Trend gate blocked: ADX below minimum" in details


def test_annotate_signal_for_scan_skips_forex_zero_diagnostics_for_nonzero_scores():
    signal = {
        "confluenceScore": 0.79,
        "trendState": "TREND_PULLBACK",
        "warnings": [],
        "factorScores": {
            "zero_score_reasons": ["hurst_veto_trend"]
        },
    }
    pair = {"display": "NZD/USD", "symbol": "NZDUSD=X", "type": "forex", "enabled": True}

    out = annotate_signal_for_scan(
        signal,
        pair,
        threshold=0.85,
        ds_ctx={},
        earnings_ctx={},
        closed_exchanges=set(),
        news_ctx={},
    )

    codes = [d.get("code") for d in out.get("scanDiagnostics", [])]

    assert "low_confluence" in codes
    assert "forex_hurst_veto_trend" not in codes
