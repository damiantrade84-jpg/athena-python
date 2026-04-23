"""AI provider routing and config resolution regressions."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import (
    ai_runtime_descriptor,
    get_ai_api_key,
    get_ai_base_url,
    get_ai_model,
    get_ai_provider_label,
)


def test_get_ai_api_key_prefers_xai_over_moonshot(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-live-key")
    monkeypatch.setenv("MOONSHOT_API_KEY", "moonshot-key")

    assert get_ai_api_key({}) == "xai-live-key"


def test_get_ai_api_key_uses_moonshot_as_legacy_fallback(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("MOONSHOT_API_KEY", "moonshot-key")

    assert get_ai_api_key({}) == "moonshot-key"


def test_ai_runtime_descriptor_defaults_to_xai_grok(monkeypatch):
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)

    desc = ai_runtime_descriptor({})

    assert desc["provider"] == "xAI"
    assert desc["base_url"] == "https://api.x.ai/v1"
    assert desc["model"] == "grok-4-1-fast-reasoning"
    assert desc["key_configured"] is False


def test_ai_runtime_descriptor_uses_explicit_xai_config():
    cfg = {
        "AI_BASE_URL": "https://api.x.ai/v1",
        "AI_MODEL": "grok-4-1-fast-reasoning",
        "XAI_API_KEY": "abc",
    }

    desc = ai_runtime_descriptor(cfg)

    assert desc["provider"] == "xAI"
    assert desc["base_url"] == "https://api.x.ai/v1"
    assert desc["model"] == "grok-4-1-fast-reasoning"
    assert desc["key_configured"] is True


def test_ai_helpers_do_not_drift_to_kimi_when_grok_mode_is_intended():
    cfg = {
        "AI_BASE_URL": "https://api.x.ai/v1",
        "AI_MODEL": "grok-4-1-fast-reasoning",
        "XAI_MODEL": "grok-4-1-fast-reasoning",
        "VISION_MODEL": "grok-4-1-fast-reasoning",
        "DEBATE_MODEL": "grok-4-1-fast-reasoning",
    }

    assert get_ai_provider_label(cfg) == "xAI"
    assert get_ai_base_url(cfg) == "https://api.x.ai/v1"
    assert get_ai_model(cfg, "AI_MODEL", "fallback") == "grok-4-1-fast-reasoning"
    assert get_ai_model(cfg, "VISION_MODEL", "fallback") == "grok-4-1-fast-reasoning"
    assert get_ai_model(cfg, "DEBATE_MODEL", "fallback") == "grok-4-1-fast-reasoning"
