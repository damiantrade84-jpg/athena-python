"""Tests for engine_c_ai weight verdict normalization (no xAI calls)."""

import pytest

import engine_c_ai
from engine_c_ai import (
    apply_engine_c_ai_weight_adjustment,
    get_engine_c_weight_verdict,
    normalize_engine_c_ai_weight_verdict,
)


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


def test_engine_c_ai_unpacks_shared_ai_helper(monkeypatch):
    monkeypatch.setattr(engine_c_ai, "get_ai_api_key", lambda _cfg: "key")
    monkeypatch.setattr(engine_c_ai, "create_ai_client", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(engine_c_ai, "get_ai_model", lambda *_args, **_kwargs: "model")

    def fake_call(*_args, **_kwargs):
        return (
            {
                "trust_verdict": "trust_b",
                "weight_recommendation": {"A": 0.3, "B": 0.7},
                "conviction_modifier": 0.05,
                "reasoning": "B checklist passed",
            },
            None,
        )

    monkeypatch.setattr("engine_b_ai._call_ai_with_retry", fake_call)
    monkeypatch.setattr(engine_c_ai, "_log_engine_c_ai_review", lambda **_kwargs: None)

    out = get_engine_c_weight_verdict(
        signal_a={"direction": "LONG"},
        signal_b={"direction": "LONG"},
        consensus_snapshot={"verdict": "ALIGNED"},
        confidence_b={"passed": True},
        regime="TRENDING",
        asset_type="forex",
    )

    assert out["trust_verdict"] == "trust_b"
    assert out["weight_recommendation"]["B"] == pytest.approx(0.7)
    assert "error" not in out


def test_engine_c_ai_skip_tier_forces_trade_false(monkeypatch):
    monkeypatch.setattr("engine_c.classify_conviction", lambda _conviction: ("SKIP", 0.0))
    result = {
        "trade": True,
        "conviction": 0.36,
        "decision_state": "execute",
        "components": {"a_norm": 0.3, "b_norm": 0.3},
        "engine_weights": {"A": 0.5, "B": 0.5},
    }
    verdict = {
        "trust_verdict": "trust_neither",
        "weight_recommendation": {"A": 0.5, "B": 0.5},
        "conviction_modifier": -0.15,
        "reasoning": "weak",
    }

    out = apply_engine_c_ai_weight_adjustment(result, verdict)

    assert out["tier"] == "SKIP"
    assert out["trade"] is False
    assert out["decision_state"] == "blocked"
    assert out["sizing_override"] == 0.0
