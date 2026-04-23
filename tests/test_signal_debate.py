"""Focused tests for current signal debate payload semantics."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from signal_debate import _signal_max_score, run_signal_debate


def test_signal_max_score_prefers_explicit_value():
    assert _signal_max_score({"maxScore": 1.0, "confluenceScore": 2.5}) == 1.0


def test_signal_max_score_uses_current_one_point_scale_when_score_is_unit_interval():
    assert _signal_max_score({"confluenceScore": 0.8}) == 1.0


def test_signal_max_score_falls_back_to_current_three_point_engine_a_scale():
    assert _signal_max_score({"confluenceScore": 1.8}) == 3.0


def test_run_signal_debate_skips_when_no_shared_ai_key(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.setattr("signal_debate.get_ai_api_key", lambda _cfg: "")

    result = run_signal_debate({"pair": "EUR/USD", "confluenceScore": 1.2})

    assert result["grade"] == "SKIP"
    assert result["allowed"] is True
    assert "No AI API key configured" in result["reasoning"]


def test_run_signal_debate_uses_shared_client_and_grok_model(monkeypatch):
    captured = {}

    monkeypatch.setattr("signal_debate.get_ai_api_key", lambda _cfg: "xai-key")
    monkeypatch.setattr(
        "signal_debate.create_ai_client",
        lambda _cfg, api_key=None: captured.setdefault("client", {"api_key": api_key}) or captured["client"],
    )
    monkeypatch.setattr(
        "signal_debate.get_ai_model",
        lambda _cfg, preferred_key, fallback: "grok-4-1-fast-reasoning",
    )
    monkeypatch.setattr(
        "signal_debate._get_debate_case",
        lambda _client, _context, _direction, side, model, _temp: {
            "conviction": 7 if side == "BULL" else 4,
            "key_arguments": [model],
        },
    )
    monkeypatch.setattr(
        "signal_debate._get_judge_verdict",
        lambda _client, _context, _direction, _bull, _bear, model, _temp: {
            "grade": "WEAK_GO",
            "score_adjustment": 0.1,
            "reasoning": model,
        },
    )

    result = run_signal_debate(
        {
            "pair": "EUR/USD",
            "display": "EUR/USD",
            "direction": "LONG",
            "confluenceScore": 1.2,
            "price": 1.1,
            "sl": 1.09,
            "tp1": 1.12,
            "tp2": 1.13,
            "votes": {},
            "warnings": [],
        }
    )

    assert captured["client"]["api_key"] == "xai-key"
    assert result["grade"] == "WEAK_GO"
    assert result["allowed"] is True
    assert result["reasoning"] == "grok-4-1-fast-reasoning"
