"""Phase 2/3: Marcus V3 factor diagnostics + advisory confluence fallback."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_context import build_ai_calibration_context
from ai_schemas import evaluate_engine_a_ai_advisory_rules

ATHENA_PATH = Path(__file__).resolve().parents[1] / "athena.py"


def _load_build_signal_message():
    src = ATHENA_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(ATHENA_PATH))
    fn_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_build_signal_message"
    )
    module = ast.Module(body=[fn_node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "CONFIG": {"LEARNING_MIN_TRADES": 5},
        "json": json,
        "log": SimpleNamespace(debug=lambda *args, **kwargs: None),
        "fetch_dxy_context": lambda: None,
        "fetch_yield_curve": lambda: None,
        "fetch_div_split_context": lambda: None,
    }
    exec(compile(module, str(ATHENA_PATH), "exec"), namespace)
    return namespace["_build_signal_message"]


def test_build_signal_message_emits_v3_components():
    build_msg = _load_build_signal_message()
    signal = {
        "pair": "EUR/USD",
        "display": "EUR/USD",
        "type": "forex",
        "direction": "LONG",
        "price": 1.1,
        "sl": 1.09,
        "tp1": 1.12,
        "tp2": 1.14,
        "rr1": 2.0,
        "rr2": 4.0,
        "slPct": 0.9,
        "confluenceScore": 1.8,
        "confluenceThreshold": 1.5,
        "maxScore": 3.0,
        "setupId": "TREND_PULLBACK",
        "decision": "WATCH",
        "qualified": False,
        "factorScores": {
            "trend": 0.7,
            "momentum": 0.5,
            "location": 0.4,
            "volume": 0.3,
        },
        "factorWeights": {
            "trend": 0.35,
            "momentum": 0.25,
            "location": 0.25,
            "volume": 0.15,
        },
        "factorDiagnostics": {
            "minDirectionalFailed": False,
            "components": {
                "trend": {
                    "signal": 1.0,
                    "quality": 0.8,
                    "weight": 0.35,
                    "contribution": 0.28,
                    "available": True,
                },
                "momentum": {
                    "signal": 1.0,
                    "quality": 0.6,
                    "weight": 0.25,
                    "contribution": 0.15,
                    "available": True,
                },
            },
        },
    }
    msg = build_msg(signal, None, "intraday", {"intraday": "H4+H1"})
    assert "=== FACTOR DIAGNOSTICS ===" in msg
    assert "Engine A V3 components" in msg
    assert "trend: signal=" in msg
    assert "Do not require legacy directionalScore" in msg
    assert "V3 setup/decision: setupId=TREND_PULLBACK" in msg
    assert "V3 confluence: score=1.8" in msg


def test_advisory_rules_fallback_to_v3_confluence_threshold():
    out = evaluate_engine_a_ai_advisory_rules(
        ai_result={},
        signal={
            "confluenceScore": 1.6,
            "confluenceThreshold": 1.5,
            "sl": 100.0,
            "tp1": 103.0,
            "price": 101.0,
        },
        news_ctx=None,
        min_rr=1.0,
    )
    assert out["advisory_rule_score"] is not None
    assert out["advisory_rule_score"] > 0
    assert out["advisory_rule_bucket"] != "score_unavailable"


def test_xau_v3_review_uses_available_metrics_without_legacy_defaults():
    signal = {
        "pair": "XAU/USD",
        "display": "XAU/USD",
        "symbol": "GC=F",
        "type": "commodity",
        "direction": "SHORT",
        "style": "swing",
        "price": 4030.515,
        "sl": 4062.21,
        "tp1": 3998.82,
        "tp2": 3981.5638,
        "rr1": 1.0,
        "rr2": 1.5444,
        "confluenceScore": 1.8963,
        "confluenceThreshold": 1.5,
        "maxScore": 3.0,
        "scoreNorm": 0.6321,
        "conviction": 0.6321,
        "decision": "TRADE",
        "qualified": True,
        "setupId": "precious_h4_d1_continuation",
        "min_rr": 2.0,
        "MAX_SL_PCT": 0.1,
        "atrValue": 35.75229,
        "riskAtr": 35.75229,
        "atrDiagnostics": {"atr_value": 35.75229},
        "factorScores": {"trend": -0.94, "momentum": 0.0072},
        "factorDiagnostics": {
            "adxValue": 26.29,
            "trendState": "DEVELOPING",
            "trendCoherence": {"d1": "DOWN", "h4": "DOWN"},
            "components": {
                "trend": {
                    "signal": -1.0,
                    "quality": 0.94,
                    "weight": 0.3225,
                    "contribution": 0.303,
                    "available": True,
                }
            },
        },
        "engine_b": {
            "score": None,
            "atr": 35.75229,
            "structural_verdict": "CLEAR",
            "current_swing_sequence": "LH_LL",
            "macro_swing_sequence": "LH_LL",
        },
        "engine_b_score": 5.43,
        "engine_b_pct": 77.1,
        "_threshold_audit_b_conf": {
            "score": 5.4286,
            "max_possible": 7.04,
            "engine_b_canonical_actionable": False,
        },
    }

    context = build_ai_calibration_context(
        signal, "Engine A factor/confluence signal", "swing"
    )
    assert context["engine_a"]["liveThreshold"] == 1.5
    assert context["engine_a"]["thresholdProgressPct"] == pytest.approx(84.7014)
    assert context["engine_a"]["trendState"] == "DEVELOPING"
    assert context["trade_risk"]["atr"] == 35.75229

    message = _load_build_signal_message()(
        signal, None, "swing", {"swing": "D1+H4"}
    )
    assert "Conviction: MEDIUM (V3 conviction 0.6321)" in message
    assert "Regime: DEVELOPING (ADX value 26.29; percentile/label unavailable)" in message
    assert "Dashboard confluence label: Strong" in message
    assert "Trend coherence: 2/2 TFs agree (ratio=1.0, dominant=DOWN)" in message
    assert "Engine A V3 conviction/scoreNorm: 0.6321" in message
    assert "ATR: 35.75229" in message
    assert "Volume ratio: data unavailable" in message
    assert "Engine B Final Score: 5.43 / 7.04 (77.1%)" in message
    assert "Engine B Actionable: NO" in message
    assert "Volume ratio: 1.0x avg" not in message
