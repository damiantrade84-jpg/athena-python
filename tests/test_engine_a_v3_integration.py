from __future__ import annotations

import inspect
from pathlib import Path

from engine_c import compute_consensus, normalise_engine_a
from scanner import classify_signal
from scoring import _classify_signal as classify_pair_scan_signal


ROOT = Path(__file__).resolve().parents[1]


def _v3_signal(
    decision: str,
    *,
    qualified: bool,
    score_norm: float | None = None,
    b_score: float = 0.0,
    b_verdict: str = "CLEAR",
    b_passed: bool = False,
) -> dict:
    # Default continuous scores aligned with V3 decision semantics so the
    # fixture mirrors what evaluate_engine_a_v3 actually emits. score_norm
    # can be overridden to exercise the continuous-quality path.
    if score_norm is None:
        if decision == "TRADE":
            _sn, _cs, _conv = 0.78, 2.34, 0.78
        elif decision == "WATCH":
            _sn, _cs, _conv = 0.45, 1.35, 0.45
        else:
            _sn, _cs, _conv = 0.0, 0.0, 0.0
    else:
        _sn = float(score_norm)
        _cs = round(_sn * 3.0, 4)
        _conv = _sn
    sig = {
        "contractVersion": "3.1.0",
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
        "confluenceScore": _cs,
        "maxScore": 3.0,
        "scoreNorm": _sn,
        "conviction": _conv,
    }
    if b_score or b_verdict != "CLEAR" or b_passed:
        sig["engine_b"] = {
            "structural_verdict": b_verdict,
            "direction": "LONG" if b_verdict == "CLEAR" else None,
            "recommended_stop_loss": 1.09 if b_verdict == "CLEAR" else None,
            "recommended_take_profit": 1.11 if b_verdict == "CLEAR" else None,
        }
    return sig


def test_engine_c_uses_v3_decision_instead_of_legacy_scores():
    normalized = normalise_engine_a(_v3_signal("TRADE", qualified=True))

    assert normalized["has_signal"] is True
    assert normalized["has_partial_signal"] is False
    assert normalized["trade_enabled"] is True
    # score_norm now reflects V3's continuous quality, not a binary 1.0.
    assert normalized["score_norm"] == 0.78
    assert normalized["raw_score"] == 2.34
    assert normalized["max_score"] == 3.0
    assert normalized["confidence"] == 0.78
    assert normalized["setup_id"] == "fx_trend_pullback"
    assert normalized["horizon"] == "intraday"


def test_engine_c_v3_uses_continuous_score_norm_not_binary():
    """A borderline TRADE (scoreNorm 0.68) must NOT be inflated to 1.0.

    Previously the V3 branch collapsed every TRADE to score_norm=1.0, which
    inflated Engine C consensus conviction and produced HIGH tiers for
    borderline setups. The fix passes V3's continuous scoreNorm through.
    """
    borderline = normalise_engine_a(
        _v3_signal("TRADE", qualified=True, score_norm=0.68)
    )
    assert borderline["score_norm"] == 0.68
    assert borderline["has_signal"] is True  # decision gate stays binary

    strong = normalise_engine_a(
        _v3_signal("TRADE", qualified=True, score_norm=0.95)
    )
    assert strong["score_norm"] == 0.95

    weak_watch = normalise_engine_a(
        _v3_signal("WATCH", qualified=False, score_norm=0.32)
    )
    assert weak_watch["score_norm"] == 0.32
    assert weak_watch["has_partial_signal"] is True


def test_engine_c_v3_consensus_reflects_actual_score_norm():
    """compute_consensus ALIGNED conviction must reflect V3's real scoreNorm.

    Regression for the DOGE/USDT HIGH-inflation class of bug: a V3 TRADE with
    scoreNorm=0.72 and a confirming Engine B (norm 0.5) should produce
    conviction = 0.72*0.4 + 0.5*0.6 = 0.588 (MEDIUM), not the old binary
    1.0*0.4 + 0.5*0.6 = 0.70 (HIGH).
    """
    signal_a = _v3_signal("TRADE", qualified=True, score_norm=0.72)
    signal_b = {
        "structural_verdict": "CLEAR",
        "direction": "LONG",
        "recommended_stop_loss": 1.09,
        "recommended_take_profit": 1.115,
    }
    confidence_b = {
        "score": 2.5,
        "max_possible": 5.0,
        "pct": 50.0,
        "passed": True,
        "structure_ok": True,
        "zone_ok": True,
        "trigger_ok": True,
        "execution_sl": 1.09,
        "execution_tp": 1.115,
    }
    result = compute_consensus(
        signal_a=signal_a,
        signal_b=signal_b,
        confidence_b=confidence_b,
        regime="TRENDING",
        entry_price=1.10,
        atr=0.002,
        asset_type="forex",
    )
    assert result["verdict"] == "ALIGNED"
    assert result["direction"] == "LONG"
    # Key invariant: conviction is blended from the real V3 a_norm (0.72),
    # NOT inflated to 1.0. Meta-learner may tilt weights slightly off the
    # 0.40/0.60 base, so assert the tier + range rather than an exact value.
    assert result["components"]["a_norm"] == 0.72
    assert 0.55 <= result["conviction"] < 0.70  # MEDIUM, not HIGH
    assert result["tier"] == "MEDIUM"


def test_engine_c_v3_consensus_still_high_when_score_norm_genuinely_high():
    """A strong V3 TRADE (scoreNorm 0.95) with strong B can still reach HIGH."""
    signal_a = _v3_signal("TRADE", qualified=True, score_norm=0.95)
    signal_b = {
        "structural_verdict": "CLEAR",
        "direction": "LONG",
        "recommended_stop_loss": 1.09,
        "recommended_take_profit": 1.13,
    }
    confidence_b = {
        "score": 4.5,
        "max_possible": 5.0,
        "pct": 90.0,
        "passed": True,
        "structure_ok": True,
        "zone_ok": True,
        "trigger_ok": True,
        "execution_sl": 1.09,
        "execution_tp": 1.13,
    }
    result = compute_consensus(
        signal_a=signal_a,
        signal_b=signal_b,
        confidence_b=confidence_b,
        regime="TRENDING",
        entry_price=1.10,
        atr=0.002,
        asset_type="forex",
    )
    assert result["verdict"] == "ALIGNED"
    # 0.95*0.4 + 0.9*0.6 = 0.38 + 0.54 = 0.92 → HIGH.
    assert result["tier"] == "HIGH"
    assert result["components"]["a_norm"] == 0.95


def test_engine_c_v3_falls_back_to_decision_tier_when_scores_missing():
    """Stub V3 payloads without score fields still normalize safely."""
    stub = _v3_signal("TRADE", qualified=True)
    for _key in ("scoreNorm", "conviction", "confluenceScore", "maxScore"):
        stub.pop(_key, None)
    normalized = normalise_engine_a(stub)
    assert normalized["has_signal"] is True
    # Fallback: decision-tier binary mapping.
    assert normalized["score_norm"] == 1.0


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
    signal["sentimentBlocked"] = True

    assert classify_signal(signal, pair)[0] == "trade"


def test_scanner_applies_event_risk_hard_blocks_to_v3():
    # e3235d2e: eventRisk/macroEventRisk hard blocks are enforced on V3
    # (demote to watchlist, fail-closed) — unlike legacy sentiment gates.
    pair = {"display": "EUR/USD", "type": "forex", "enabled": True}
    signal = _v3_signal("TRADE", qualified=True)
    signal.update(
        {
            "eventRisk": {"hardBlock": True},
            "macroEventRisk": {"blocked": True},
        }
    )

    assert classify_signal(signal, pair)[0] == "watchlist"


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


def test_active_analyze_pair_attaches_engine_a_atr_provenance():
    source = (ROOT / "athena.py").read_text(encoding="utf-8")
    analyze_start = source.index("def analyze_pair(")
    analyze_end = source.index("def _build_style_levels(", analyze_start)
    analyze_source = source[analyze_start:analyze_end]

    assert "_attach_engine_a_v3_atr_provenance(" in analyze_source
    assert analyze_source.count("_attach_engine_a_v3_atr_provenance(") >= 3


def test_auto_trader_blocks_engine_a_v3():
    source = (ROOT / "auto_trader.py").read_text(encoding="utf-8")
    assert "ENGINE_A_V3_MANUAL_DEMO_ONLY" in source
