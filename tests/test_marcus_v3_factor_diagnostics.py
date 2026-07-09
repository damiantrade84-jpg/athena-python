"""Phase 2/3: Marcus V3 factor diagnostics + advisory confluence fallback."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

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
