"""AI provider routing and config resolution regressions."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import (
    CONFIG,
    ai_runtime_descriptor,
    create_ai_client,
    get_ai_max_retries,
    get_ai_timeout_sec,
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
    assert desc["model"] == "grok-4.3"
    assert desc["key_configured"] is False


def test_ai_runtime_descriptor_uses_explicit_xai_config():
    cfg = {
        "AI_BASE_URL": "https://api.x.ai/v1",
        "AI_MODEL": "grok-4.3",
        "XAI_API_KEY": "abc",
    }

    desc = ai_runtime_descriptor(cfg)

    assert desc["provider"] == "xAI"
    assert desc["base_url"] == "https://api.x.ai/v1"
    assert desc["model"] == "grok-4.3"
    assert desc["key_configured"] is True


def test_ai_helpers_do_not_drift_to_kimi_when_grok_mode_is_intended():
    cfg = {
        "AI_BASE_URL": "https://api.x.ai/v1",
        "AI_MODEL": "grok-4.3",
        "XAI_MODEL": "grok-4.3",
        "VISION_MODEL": "grok-4.3",
        "DEBATE_MODEL": "grok-4.3",
    }

    assert get_ai_provider_label(cfg) == "xAI"
    assert get_ai_base_url(cfg) == "https://api.x.ai/v1"
    assert get_ai_model(cfg, "AI_MODEL", "fallback") == "grok-4.3"
    assert get_ai_model(cfg, "VISION_MODEL", "fallback") == "grok-4.3"
    assert get_ai_model(cfg, "DEBATE_MODEL", "fallback") == "grok-4.3"


def test_marcus_ai_timeout_default_is_bounded():
    assert get_ai_timeout_sec(CONFIG, "MARCUS_AI_TIMEOUT_SEC", fallback=30.0) == 30.0


def test_ai_timeout_resolver_uses_positive_specific_value_before_global():
    cfg = {
        "AI_REQUEST_TIMEOUT_SEC": 45,
        "MARCUS_AI_TIMEOUT_SEC": 12,
    }

    assert get_ai_timeout_sec(cfg, "MARCUS_AI_TIMEOUT_SEC", fallback=30.0) == 12.0


def test_ai_timeout_resolver_falls_back_from_invalid_specific_to_global():
    cfg = {
        "AI_REQUEST_TIMEOUT_SEC": 45,
        "MARCUS_AI_TIMEOUT_SEC": "bad",
    }

    assert get_ai_timeout_sec(cfg, "MARCUS_AI_TIMEOUT_SEC", fallback=30.0) == 45.0


def test_marcus_ai_sdk_retries_default_to_zero():
    assert get_ai_max_retries(CONFIG, "MARCUS_AI_SDK_MAX_RETRIES", fallback=0) == 0


def test_ai_retry_resolver_uses_non_negative_specific_value_before_global():
    cfg = {
        "AI_SDK_MAX_RETRIES": 2,
        "MARCUS_AI_SDK_MAX_RETRIES": 0,
    }

    assert get_ai_max_retries(cfg, "MARCUS_AI_SDK_MAX_RETRIES", fallback=2) == 0


def test_ai_retry_resolver_falls_back_from_invalid_specific_to_global():
    cfg = {
        "AI_SDK_MAX_RETRIES": 1,
        "MARCUS_AI_SDK_MAX_RETRIES": "bad",
    }

    assert get_ai_max_retries(cfg, "MARCUS_AI_SDK_MAX_RETRIES", fallback=2) == 1


def test_create_ai_client_accepts_explicit_sdk_retry_count():
    client = create_ai_client(
        {"AI_BASE_URL": "https://api.x.ai/v1"},
        api_key="test-key",
        max_retries=0,
    )

    assert client.max_retries == 0
