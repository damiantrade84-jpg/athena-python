from __future__ import annotations

import inspect
from pathlib import Path

from engine_c import normalise_engine_a
from scanner import classify_signal
from scoring import _classify_signal as classify_pair_scan_signal


ROOT = Path(__file__).resolve().parents[1]


def _v3_signal(decision: str, *, qualified: bool) -> dict:
    return {
        "contractVersion": "3.0.0",
        "engine": "ENGINE_A_V3",
        "pair": "EUR/USD",
        "symbol": "EURUSD",
        "type": "forex",
        "family": "forex",
        "subclass": "majors",
        "horizon": "intraday",
        "setupId": "fx_trend_pullback",
        "decision": decision,
        "qualified": qualified,
        "direction": "LONG" if decision != "NO_SIGNAL" else None,
        "engineATradeEnabled": qualified,
        "price": 1.10 if decision != "NO_SIGNAL" else None,
        "sl": 1.09 if decision != "NO_SIGNAL" else None,
        "tp1": 1.11 if decision != "NO_SIGNAL" else None,
        "tp2": 1.12 if decision != "NO_SIGNAL" else None,
        "rr1": 1.0 if decision != "NO_SIGNAL" else None,
        "rejectionReasons": [] if decision == "TRADE" else ["confirmation_close_missing"],
        "scanDiagnostics": [],
        "eventRisk": {"hardBlock": False},
        "exchangeClosed": False,
        "isEnabled": True,
    }


def test_engine_c_uses_v3_decision_instead_of_legacy_scores():
    normalized = normalise_engine_a(_v3_signal("TRADE", qualified=True))

    assert normalized["has_signal"] is True
    assert normalized["has_partial_signal"] is False
    assert normalized["trade_enabled"] is True
    assert normalized["score_norm"] == 1.0
    assert normalized["setup_id"] == "fx_trend_pullback"
    assert normalized["horizon"] == "intraday"


def test_scanner_classifies_v3_trade_watch_and_no_signal_without_scores():
    pair = {"display": "EUR/USD", "type": "forex", "enabled": True}

    assert classify_signal(_v3_signal("TRADE", qualified=True), pair)[0] == "trade"
    assert classify_signal(_v3_signal("WATCH", qualified=False), pair)[0] == "watchlist"
    assert classify_signal(_v3_signal("NO_SIGNAL", qualified=False), pair)[0] == "skip"


def test_pair_scan_classifier_uses_v3_decision_without_nullable_score_comparison():
    pair = {"display": "EUR/USD", "type": "forex", "enabled": True}

    assert classify_pair_scan_signal(_v3_signal("TRADE", qualified=True), pair)[0] == "trade"
    assert classify_pair_scan_signal(_v3_signal("WATCH", qualified=False), pair)[0] == "watchlist"
    assert classify_pair_scan_signal(_v3_signal("NO_SIGNAL", qualified=False), pair)[0] == "skip"


def test_scanner_does_not_apply_legacy_strategy_gates_to_v3():
    pair = {"display": "EUR/USD", "type": "forex", "enabled": True}
    signal = _v3_signal("TRADE", qualified=True)
    signal.update(
        {
            "eventRisk": {"hardBlock": True},
            "macroEventRisk": {"blocked": True},
            "sentimentBlocked": True,
        }
    )

    assert classify_signal(signal, pair)[0] == "trade"


def test_active_scanner_has_no_legacy_analyze_fallback():
    source = inspect.getsource(__import__("scanner").analyze_pair)
    assert "athena_legacy" not in source
    assert "legacy fallback" not in source.lower()


def test_active_backtest_routes_to_v3_evaluator():
    source = (ROOT / "backtest_runner.py").read_text(encoding="utf-8")
    assert "run_v3_backtest(" in source
    backtest_start = source.index("def backtest_pair(")
    v3_return = source.index("return run_v3_backtest(", backtest_start)
    active_prefix = source[backtest_start:v3_return]
    assert "min_bars=230" not in active_prefix
    assert "min_bars=500" not in active_prefix
    assert "need 250+" not in active_prefix
    assert "need 260+" not in active_prefix
    consensus_start = source.index("def backtest_pair_consensus(")
    consensus_end = source.index("def backtest_pair_scalp(", consensus_start)
    consensus_source = source[consensus_start:consensus_end]
    assert "evaluate_engine_a_v3(" in consensus_source
    assert "res_a = calc_confluence(" not in consensus_source


def test_active_analyze_pair_returns_v3_as_sole_engine_path():
    # The legacy v2 factor-scoring fallback was removed; v3 is now the only
    # Engine A scoring path inside analyze_pair (it terminates with return _v3_signal).
    source = (ROOT / "athena.py").read_text(encoding="utf-8")
    analyze_start = source.index("def analyze_pair(")
    analyze_end = source.index("def _build_style_levels(", analyze_start)
    analyze_source = source[analyze_start:analyze_end]
    assert "_v3_signal = evaluate_engine_a_v3(" in analyze_source
    assert "_attach_v3_intermarket_confirmation(" in analyze_source
    assert "return _attach_v3_intermarket_confirmation(" in analyze_source
    # Guard against reintroducing the removed v2 fallback.
    assert "_asset_type = pair.get(\"type\", \"stock\")" not in analyze_source


def test_active_analyze_pair_fails_closed_when_freshness_validation_errors():
    source = (ROOT / "athena.py").read_text(encoding="utf-8")
    freshness_start = source.index("# Pre-scoring freshness gate")
    v3_return = source.index("_v3_signal = evaluate_engine_a_v3(", freshness_start)
    freshness_source = source[freshness_start:v3_return]

    assert "_v3_freshness_required = bool(CONFIG.get(\"PRE_SCORING_FRESHNESS_GATE_ENABLED\", True))" in freshness_source
    assert "FRESHNESS_VALIDATION_ERROR" in freshness_source
    assert "freshness check skipped" not in freshness_source


def test_auto_trader_blocks_engine_a_v3():
    source = (ROOT / "auto_trader.py").read_text(encoding="utf-8")
    assert "ENGINE_A_V3_MANUAL_DEMO_ONLY" in source
