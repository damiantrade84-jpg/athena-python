"""AI provider routing and config resolution regressions."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
ROOT = Path(__file__).resolve().parents[1]

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
    get_ai_review_provider,
    resolve_ai_review_runtime,
)


def test_get_ai_api_key_prefers_xai_over_moonshot_for_grok(monkeypatch):
    monkeypatch.delenv("AI_REVIEW_PROVIDER", raising=False)
    monkeypatch.setenv("XAI_API_KEY", "xai-live-key")
    monkeypatch.setenv("MOONSHOT_API_KEY", "moonshot-key")

    assert get_ai_api_key({}, provider="grok") == "xai-live-key"


def test_get_ai_api_key_uses_moonshot_as_legacy_grok_fallback(monkeypatch):
    monkeypatch.delenv("AI_REVIEW_PROVIDER", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("MOONSHOT_API_KEY", "moonshot-key")

    assert get_ai_api_key({}, provider="grok") == "moonshot-key"


def test_ai_runtime_descriptor_defaults_to_grok45(monkeypatch):
    monkeypatch.delenv("AI_REVIEW_PROVIDER", raising=False)
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_REVIEW_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)

    desc = ai_runtime_descriptor({})

    assert desc["provider"] == "xAI"
    assert desc["selectedProvider"] == "grok"
    assert desc["base_url"] == "https://api.x.ai/v1"
    assert desc["model"] == "grok-4.5"
    assert desc["key_configured"] is False


def test_ai_runtime_descriptor_uses_explicit_xai_config():
    cfg = {
        "AI_REVIEW_PROVIDER": "grok",
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
        "AI_REVIEW_PROVIDER": "grok",
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


def test_provider_resolver_returns_openai_when_selected(monkeypatch):
    monkeypatch.delenv("AI_REVIEW_PROVIDER", raising=False)
    cfg = {
        "AI_REVIEW_PROVIDER": "openai",
        "OPENAI_REVIEW_MODEL": "gpt-5.5",
        "OPENAI_BASE_URL": "https://api.openai.com/v1",
    }

    assert get_ai_review_provider(cfg) == "openai"
    assert get_ai_provider_label(cfg) == "OpenAI"
    assert get_ai_base_url(cfg) == "https://api.openai.com/v1"
    assert get_ai_model(cfg, provider="openai") == "gpt-5.5"


def test_startup_chart_review_defaults_use_grok():
    assert CONFIG["AI_REVIEW_PROVIDER"] == "grok"
    assert CONFIG["OPENAI_REVIEW_ENABLED"] is True
    assert CONFIG["AI_CHART_REVIEW"]["DEFAULT_PROVIDER"] == "grok"
    assert CONFIG["AI_SCALP_CHART_REVIEW"]["DEFAULT_PROVIDER"] == "grok"
    assert CONFIG["AI_CHART_REVIEW"]["OPENAI_REVIEW_ENABLED"] is True
    assert CONFIG["AI_SCALP_CHART_REVIEW"]["OPENAI_REVIEW_ENABLED"] is True


def test_openai_missing_key_has_clear_runtime_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = {"AI_REVIEW_PROVIDER": "openai", "OPENAI_API_KEY": ""}
    runtime = resolve_ai_review_runtime(cfg)

    assert runtime["provider"] == "openai"
    assert runtime["api_key"] == ""
    assert runtime["providerFailure"]["provider"] == "openai"
    assert "API key not configured" in runtime["providerFailure"]["error"]

    client = create_ai_client(cfg, api_key="", provider="openai")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY not configured"):
        client.chat.completions.create(
            model="gpt-5.5",
            messages=[{"role": "user", "content": "hello"}],
        )


def test_explicit_fallback_provider_is_marked(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = {
        "AI_REVIEW_PROVIDER": "openai",
        "AI_REVIEW_FALLBACK_PROVIDERS": "claude,grok",
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "claude-key",
    }

    runtime = resolve_ai_review_runtime(cfg)

    assert runtime["selectedProvider"] == "openai"
    assert runtime["provider"] == "claude"
    assert runtime["api_key"] == "claude-key"
    assert runtime["fallbackUsed"] is True
    assert runtime["providerFailure"]["provider"] == "openai"


def test_ai_review_provider_endpoint_updates_runtime_config(tmp_path, monkeypatch):
    from athena_app.api.routes_ai_agent import register_ai_agent_routes

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = dict(CONFIG)
    cfg["AI_REVIEW_PROVIDER"] = "grok"
    cfg["AI_REVIEW_FALLBACK_PROVIDERS"] = ""
    cfg["OPENAI_API_KEY"] = ""
    cfg["AI_CHART_REVIEW"] = dict(CONFIG["AI_CHART_REVIEW"])
    cfg["AI_SCALP_CHART_REVIEW"] = dict(CONFIG["AI_SCALP_CHART_REVIEW"])
    app = Flask(__name__)
    register_ai_agent_routes(
        app,
        SimpleNamespace(
            CONFIG=cfg,
            AUDIT_DB=str(tmp_path / "audit.db"),
            json_safe=lambda x: x,
            last_scan_results=lambda: {"signals": []},
        ),
    )

    post = app.test_client().post("/api/ai-review/provider", json={"provider": "openai"})
    assert post.status_code == 200
    body = post.get_json()
    assert body["selectedProvider"] == "openai"
    assert body["provider"] == "openai"
    assert body["keyConfigured"] is False
    assert cfg["AI_REVIEW_PROVIDER"] == "openai"
    assert cfg["AI_CHART_REVIEW"]["DEFAULT_PROVIDER"] == "openai"
    assert cfg["AI_SCALP_CHART_REVIEW"]["DEFAULT_PROVIDER"] == "openai"

    get = app.test_client().get("/api/ai-review/provider")
    assert get.status_code == 200
    assert get.get_json()["selectedProvider"] == "openai"


def test_known_ai_review_paths_use_shared_provider_resolver():
    files = [
        ROOT / "athena_app/api/routes_ai_chart_review.py",
        ROOT / "athena_app/api/routes_ai_scalp_chart_review.py",
        ROOT / "athena_app/api/routes_ai_agent.py",
        ROOT / "athena.py",
        ROOT / "engine_b_ai.py",
        ROOT / "engine_c_ai.py",
        ROOT / "ai_trade_chat.py",
        ROOT / "ai_lee_confirmation.py",
    ]
    for path in files:
        source = path.read_text(encoding="utf-8")
        assert "get_ai_review_provider" in source or "resolve_ai_review_runtime" in source


def test_marcus_ai_timeout_default_is_bounded():
    assert get_ai_timeout_sec(CONFIG, "MARCUS_AI_TIMEOUT_SEC", fallback=60.0) == 90.0


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


def test_openai_responses_adapter_forwards_json_schema_response_format(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    captured = {}

    class _FakeResponses:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text='{"ok": true}', model="gpt-5.5")

    class _FakeOpenAI:
        def __init__(self, **_kwargs):
            self.responses = _FakeResponses()
            self.max_retries = 0

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_FakeOpenAI))
    client = create_ai_client(
        {
            "AI_REVIEW_PROVIDER": "openai",
            "OPENAI_REVIEW_MODEL": "gpt-5.5",
            "OPENAI_API_KEY": "test-openai-key",
        },
        provider="openai",
    )

    client.chat.completions.create(
        model="gpt-5.5",
        messages=[{"role": "user", "content": "return json"}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "example_schema",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                },
            },
        },
    )

    assert captured["text"]["format"]["type"] == "json_schema"
    assert captured["text"]["format"]["name"] == "example_schema"
    assert captured["text"]["format"]["strict"] is True


def test_openai_responses_json_object_injects_json_hint_when_input_missing_keyword(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    captured = {}

    class _FakeResponses:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text='{"grade": "B"}', model="gpt-5.5")

    class _FakeOpenAI:
        def __init__(self, **_kwargs):
            self.responses = _FakeResponses()
            self.max_retries = 0

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_FakeOpenAI))
    client = create_ai_client(
        {
            "AI_REVIEW_PROVIDER": "openai",
            "OPENAI_REVIEW_MODEL": "gpt-5.5",
            "OPENAI_API_KEY": "test-openai-key",
        },
        provider="openai",
    )

    client.chat.completions.create(
        model="gpt-5.5",
        messages=[
            {"role": "system", "content": "Output ONLY valid JSON."},
            {"role": "user", "content": "Pair: MSFT | Direction: LONG"},
        ],
        response_format={"type": "json_object"},
    )

    assert captured["text"]["format"]["type"] == "json_object"
    user_text = captured["input"][-1]["content"][-1]["text"]
    assert "json" in user_text.lower()
    assert "Pair: MSFT | Direction: LONG" in user_text


def test_openai_responses_json_object_does_not_duplicate_when_present(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    captured = {}

    class _FakeResponses:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text='{"grade": "B"}', model="gpt-5.5")

    class _FakeOpenAI:
        def __init__(self, **_kwargs):
            self.responses = _FakeResponses()
            self.max_retries = 0

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_FakeOpenAI))
    client = create_ai_client(
        {
            "AI_REVIEW_PROVIDER": "openai",
            "OPENAI_REVIEW_MODEL": "gpt-5.5",
            "OPENAI_API_KEY": "test-openai-key",
        },
        provider="openai",
    )

    original_user = "Return JSON with grade field."
    client.chat.completions.create(
        model="gpt-5.5",
        messages=[{"role": "user", "content": original_user}],
        response_format={"type": "json_object"},
    )

    user_text = captured["input"][-1]["content"][-1]["text"]
    assert user_text == original_user
