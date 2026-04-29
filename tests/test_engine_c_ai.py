"""Tests for engine_c_ai weight verdict normalization (no xAI calls)."""

import pytest

from engine_c_ai import normalize_engine_c_ai_weight_verdict


def test_normalize_clamps_conviction_modifier():
    raw = {
        "trust_verdict": "trust_a",
        "rationale": "momentum=trend factor X",
        "weight_recommendation": {"A": 0.9, "B": 0.2},
        "conviction_modifier": 0.99,
        "reasoning": "test",
    }
    out = normalize_engine_c_ai_weight_verdict(raw, w_min=0.2, w_max=0.8)
    assert out["conviction_modifier"] == 0.15
    assert out["weight_recommendation"]["A"] == pytest.approx(0.8, rel=1e-3)
    assert out["weight_recommendation"]["B"] == pytest.approx(0.2, rel=1e-3)
    wa = out["weight_recommendation"]["A"]
    wb = out["weight_recommendation"]["B"]
    assert abs(wa + wb - 1.0) < 0.02


def test_normalize_preserves_error_payload():
    err = {"error": "API key not configured", "trust_verdict": None}
    out = normalize_engine_c_ai_weight_verdict(err)
    assert out["error"] == "API key not configured"


def test_normalize_invalid_input():
    out = normalize_engine_c_ai_weight_verdict(None)
    assert out.get("error") == "invalid_payload"


def test_normalize_trust_aliases():
    raw = {
        "trust_verdict": "TRUST_B",
        "rationale": "",
        "weight_recommendation": {"A": 0.5, "B": 0.5},
        "conviction_modifier": -0.5,
        "reasoning": "",
    }
    out = normalize_engine_c_ai_weight_verdict(raw)
    assert out["trust_verdict"] == "trust_b"
    assert out["conviction_modifier"] == -0.15
