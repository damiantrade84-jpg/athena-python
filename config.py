"""config.py — Sentinel Pro configuration loading and validation.

CONFIG is built from hard-coded defaults then overlaid with ``config.yaml``.
Optional ``config.local.yaml`` (gitignored) is deep-merged on top for machine-local
overrides (e.g. demo broker testing) without changing tracked defaults.
Import CONFIG from here; never import from athena.py directly.
"""

import math
import os
import logging
from types import SimpleNamespace

log = logging.getLogger("sentinel")


def scan_duplicate_top_level_yaml_keys(yaml_text: str) -> dict[str, list[int]]:
    """Return {key: [line numbers]} for keys that appear more than once at YAML root.

    Indented (nested) keys are ignored. Used to catch duplicate scalars in config.yaml
    where the last value silently wins in PyYAML.
    """
    import re
    from collections import defaultdict

    key_lines: dict[str, list[int]] = defaultdict(list)
    for i, line in enumerate(yaml_text.splitlines(), 1):
        if not line.strip():
            continue
        if line[0] in " \t":
            continue
        head = line.split("#", 1)[0].rstrip()
        if not head:
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:", head)
        if m:
            key_lines[m.group(1)].append(i)
    return {k: v for k, v in key_lines.items() if len(v) > 1}

AI_API_KEY_PLACEHOLDER = "YOUR_XAI_API_KEY"
_LEGACY_AI_API_KEY_PLACEHOLDER = "YOUR_MOONSHOT_API_KEY"
_AI_BASE_URL_DEFAULT = "https://api.x.ai/v1"
_AI_MODEL_DEFAULT = os.environ.get("AI_MODEL", "gpt-5.5")
_OPENAI_BASE_URL_DEFAULT = "https://api.openai.com/v1"
_OPENAI_REVIEW_MODEL_DEFAULT = "gpt-5.5"
_AI_REVIEW_PROVIDER_DEFAULT = "openai"
_CLAUDE_REVIEW_MODEL_DEFAULT = "claude-opus-4-7"
_AI_REVIEW_PROVIDER_ALIASES = {
    "": "",
    "default": "",
    "none": "",
    "xai": "grok",
    "grok": "grok",
    "grok/xai": "grok",
    "anthropic": "claude",
    "claude": "claude",
    "openai": "openai",
    "chatgpt": "openai",
    "gpt": "openai",
}
_AI_REVIEW_PROVIDER_LABELS = {
    "grok": "xAI",
    "claude": "Claude",
    "openai": "OpenAI",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _deep_merge_dict(base: dict, overrides: dict) -> dict:
    """Recursively merge nested dicts while preserving unspecified defaults."""
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _clean_ai_value(value: object) -> str:
    text = str(value or "").strip()
    if text in {
        "",
        AI_API_KEY_PLACEHOLDER,
        _LEGACY_AI_API_KEY_PLACEHOLDER,
    }:
        return ""
    return text


def normalize_ai_review_provider(value: object) -> str:
    text = str(value or "").strip().lower()
    return _AI_REVIEW_PROVIDER_ALIASES.get(text, text)


def normalize_chart_review_primary_engine(value: object) -> str:
    text = str(value or "A").strip().upper()
    return "B" if text == "B" else "A"


def _provider_from_base_url(base_url: str) -> str:
    url = str(base_url or "").strip().lower()
    if "api.openai.com" in url or "openai" in url:
        return "openai"
    if "anthropic" in url or "claude" in url:
        return "claude"
    if "api.x.ai" in url or "x.ai" in url or "grok" in url:
        return "grok"
    if "moonshot" in url:
        return "grok"
    return ""


def get_ai_review_provider(
    cfg: dict | None = None,
    requested: object | None = None,
) -> str:
    cfg = CONFIG if cfg is None else cfg
    requested_provider = normalize_ai_review_provider(requested)
    if requested_provider:
        return requested_provider
    candidates = (
        os.environ.get("AI_REVIEW_PROVIDER", ""),
        cfg.get("AI_REVIEW_PROVIDER", ""),
        os.environ.get("AI_PROVIDER", ""),
        cfg.get("AI_PROVIDER", ""),
        os.environ.get("LLM_PROVIDER", ""),
        cfg.get("LLM_PROVIDER", ""),
        os.environ.get("HERMES_PROVIDER", ""),
        cfg.get("HERMES_PROVIDER", ""),
        os.environ.get("AI_VISION_PROVIDER", ""),
        cfg.get("AI_VISION_PROVIDER", ""),
    )
    for candidate in candidates:
        provider = normalize_ai_review_provider(candidate)
        if provider:
            return provider
    base_hint = str(os.environ.get("AI_BASE_URL", "") or cfg.get("AI_BASE_URL", "") or "").strip()
    return _provider_from_base_url(base_hint) or _AI_REVIEW_PROVIDER_DEFAULT


def get_ai_review_fallback_providers(cfg: dict | None = None) -> list[str]:
    cfg = CONFIG if cfg is None else cfg
    raw = (
        os.environ.get("AI_REVIEW_FALLBACK_PROVIDERS", "")
        or str(cfg.get("AI_REVIEW_FALLBACK_PROVIDERS", "") or "")
    )
    out: list[str] = []
    for part in str(raw).split(","):
        provider = normalize_ai_review_provider(part)
        if provider and provider not in out:
            out.append(provider)
    return out


def get_ai_api_key(cfg: dict | None = None, provider: object | None = None) -> str:
    cfg = CONFIG if cfg is None else cfg
    resolved_provider = get_ai_review_provider(cfg, requested=provider)
    if resolved_provider == "openai":
        candidates = (
            os.environ.get("OPENAI_API_KEY", ""),
            cfg.get("OPENAI_API_KEY", ""),
        )
    elif resolved_provider == "claude":
        candidates = (
            os.environ.get("ANTHROPIC_API_KEY", ""),
            cfg.get("ANTHROPIC_API_KEY", ""),
        )
    else:
        candidates = (
            os.environ.get("XAI_API_KEY", ""),
            cfg.get("XAI_API_KEY", ""),
            os.environ.get("MOONSHOT_API_KEY", ""),
            cfg.get("MOONSHOT_API_KEY", ""),
        )
    for candidate in candidates:
        cleaned = _clean_ai_value(candidate)
        if cleaned:
            return cleaned
    return ""


def get_ai_provider_label(cfg: dict | None = None, provider: object | None = None) -> str:
    resolved_provider = get_ai_review_provider(cfg, requested=provider)
    if resolved_provider in _AI_REVIEW_PROVIDER_LABELS:
        return _AI_REVIEW_PROVIDER_LABELS[resolved_provider]
    base_url = str(get_ai_base_url(cfg, provider=provider) or "").strip().lower()
    return base_url or "Unknown"


def ai_key_configured(cfg: dict | None = None) -> bool:
    cfg = CONFIG if cfg is None else cfg
    if get_ai_api_key(cfg):
        return True
    for provider in get_ai_review_fallback_providers(cfg):
        if get_ai_api_key(cfg, provider=provider):
            return True
    return False


def resolve_ai_review_runtime(
    cfg: dict | None = None,
    requested: object | None = None,
) -> dict:
    cfg = CONFIG if cfg is None else cfg
    selected = get_ai_review_provider(cfg, requested=requested)
    candidates = [selected]
    for fallback in get_ai_review_fallback_providers(cfg):
        if fallback not in candidates:
            candidates.append(fallback)

    first_failure: dict | None = None
    for provider in candidates:
        key = get_ai_api_key(cfg, provider=provider)
        if key:
            return {
                "selectedProvider": selected,
                "provider": provider,
                "api_key": key,
                "fallbackUsed": provider != selected,
                "fallback_used": provider != selected,
                "providerFailure": first_failure,
                "provider_failure": first_failure,
            }
        if provider == selected and first_failure is None:
            first_failure = {
                "provider": provider,
                "error": f"{provider} API key not configured",
                "providerStatus": "failed_auth",
            }

    return {
        "selectedProvider": selected,
        "provider": selected,
        "api_key": "",
        "fallbackUsed": False,
        "fallback_used": False,
        "providerFailure": first_failure,
        "provider_failure": first_failure,
    }


def get_ai_base_url(cfg: dict | None = None, provider: object | None = None) -> str:
    cfg = CONFIG if cfg is None else cfg
    resolved_provider = normalize_ai_review_provider(provider)
    if not resolved_provider:
        env_provider = normalize_ai_review_provider(
            os.environ.get("AI_REVIEW_PROVIDER", "") or cfg.get("AI_REVIEW_PROVIDER", "")
        )
        resolved_provider = env_provider or _AI_REVIEW_PROVIDER_DEFAULT
    if resolved_provider == "openai":
        return (
            str(os.environ.get("OPENAI_BASE_URL", "") or "").strip()
            or str(cfg.get("OPENAI_BASE_URL", "") or "").strip()
            or _OPENAI_BASE_URL_DEFAULT
        )
    return (
        str(os.environ.get("AI_BASE_URL", "") or "").strip()
        or str(cfg.get("AI_BASE_URL", "") or "").strip()
        or _AI_BASE_URL_DEFAULT
    )


def get_ai_model(
    cfg: dict | None = None,
    preferred_key: str = "AI_MODEL",
    fallback: str = "grok-4.3",
    provider: object | None = None,
) -> str:
    cfg = CONFIG if cfg is None else cfg
    resolved_provider = get_ai_review_provider(cfg, requested=provider)
    candidates = []
    if resolved_provider == "openai":
        candidates.extend(
            [
                os.environ.get("OPENAI_REVIEW_MODEL", ""),
                cfg.get("OPENAI_REVIEW_MODEL", ""),
                cfg.get("OPENAI_MODEL", ""),
                _OPENAI_REVIEW_MODEL_DEFAULT,
            ]
        )
    elif resolved_provider == "claude":
        candidates.extend(
            [
                os.environ.get("CLAUDE_MODEL", ""),
                cfg.get("CLAUDE_MODEL", ""),
                cfg.get("ANTHROPIC_MODEL", ""),
                _CLAUDE_REVIEW_MODEL_DEFAULT,
            ]
        )
    if preferred_key:
        candidates.extend(
            [
                os.environ.get(preferred_key, ""),
                cfg.get(preferred_key, ""),
            ]
        )
    candidates.extend(
        [
            os.environ.get("AI_MODEL", ""),
            cfg.get("AI_MODEL", ""),
            cfg.get("XAI_MODEL", ""),
            fallback,
        ]
    )
    for candidate in candidates:
        cleaned = str(candidate or "").strip()
        if cleaned:
            return cleaned
    return fallback


def get_ai_timeout_sec(
    cfg: dict | None = None,
    preferred_key: str = "AI_REQUEST_TIMEOUT_SEC",
    fallback: float = 30.0,
) -> float:
    cfg = CONFIG if cfg is None else cfg
    candidates = []
    if preferred_key:
        candidates.append(cfg.get(preferred_key))
    candidates.append(cfg.get("AI_REQUEST_TIMEOUT_SEC"))
    for candidate in candidates:
        try:
            timeout = float(candidate)
        except (TypeError, ValueError):
            continue
        if timeout > 0:
            return timeout
    return float(fallback)


def get_ai_max_retries(
    cfg: dict | None = None,
    preferred_key: str = "AI_SDK_MAX_RETRIES",
    fallback: int = 2,
) -> int:
    cfg = CONFIG if cfg is None else cfg
    candidates = []
    if preferred_key:
        candidates.append(cfg.get(preferred_key))
    candidates.append(cfg.get("AI_SDK_MAX_RETRIES"))
    for candidate in candidates:
        try:
            retries = int(candidate)
        except (TypeError, ValueError):
            continue
        if retries >= 0:
            return retries
    return max(0, int(fallback))


def _chat_response(text: str, *, model: str):
    message = SimpleNamespace(content=text, parsed=None)
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(message=message)],
    )


def _response_text(resp) -> str:
    direct = getattr(resp, "output_text", None)
    if isinstance(direct, str):
        return direct
    output = getattr(resp, "output", None)
    if isinstance(resp, dict):
        direct = resp.get("output_text")
        if isinstance(direct, str):
            return direct
        output = resp.get("output")
    parts: list[str] = []
    for item in output or []:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        for block in content or []:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
            if btype in ("output_text", "text") and isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _responses_text_config_from_response_format(response_format) -> dict | None:
    if not isinstance(response_format, dict):
        return None
    fmt_type = str(response_format.get("type") or "").strip()
    if fmt_type == "json_object":
        return {"format": {"type": "json_object"}}
    if fmt_type == "text":
        return {"format": {"type": "text"}}
    if fmt_type != "json_schema":
        return None

    schema_cfg = response_format.get("json_schema")
    if not isinstance(schema_cfg, dict):
        schema_cfg = response_format
    name = str(schema_cfg.get("name") or "").strip()
    schema = schema_cfg.get("schema")
    if not name or not isinstance(schema, dict):
        return None
    out = {
        "type": "json_schema",
        "name": name,
        "schema": schema,
    }
    if "description" in schema_cfg:
        out["description"] = str(schema_cfg.get("description") or "")
    if "strict" in schema_cfg:
        out["strict"] = bool(schema_cfg.get("strict"))
    return {"format": out}


def _as_openai_response_content(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    out: list[dict] = []
    if not isinstance(content, list):
        return [{"type": "input_text", "text": str(content or "")}]
    for part in content:
        if isinstance(part, str):
            out.append({"type": "input_text", "text": part})
            continue
        if not isinstance(part, dict):
            out.append({"type": "input_text", "text": str(part)})
            continue
        ptype = str(part.get("type") or "").strip()
        if ptype in ("text", "input_text"):
            out.append({"type": "input_text", "text": str(part.get("text") or "")})
        elif ptype in ("image_url", "input_image"):
            image_url = part.get("image_url")
            if isinstance(image_url, dict):
                image_url = image_url.get("url")
            image_url = image_url or part.get("image_url")
            if image_url:
                out.append({"type": "input_image", "image_url": str(image_url)})
    return out


def _messages_to_openai_responses_input(messages) -> tuple[str | None, list[dict]]:
    instructions: list[str] = []
    response_input: list[dict] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user").strip().lower()
        content = msg.get("content") or ""
        if role == "system":
            if isinstance(content, str):
                instructions.append(content)
            else:
                instructions.append(" ".join(part.get("text", "") for part in content if isinstance(part, dict)))
            continue
        response_input.append(
            {
                "role": "assistant" if role == "assistant" else "user",
                "content": _as_openai_response_content(content),
            }
        )
    return ("\n\n".join(p for p in instructions if p).strip() or None), response_input


_JSON_OBJECT_INPUT_HINT = "Respond with valid JSON only."


def _responses_input_contains_json_keyword(response_input: list[dict]) -> bool:
    for msg in response_input:
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and "json" in text.lower():
                return True
    return False


def _ensure_json_keyword_in_responses_input(response_input: list[dict]) -> None:
    """OpenAI Responses API requires 'json' in input when using json_object format."""
    if _responses_input_contains_json_keyword(response_input):
        return
    if not response_input:
        response_input.append(
            {
                "role": "user",
                "content": [{"type": "input_text", "text": _JSON_OBJECT_INPUT_HINT}],
            }
        )
        return
    last = response_input[-1]
    content = list(last.get("content") or [])
    if content and isinstance(content[-1], dict) and content[-1].get("type") == "input_text":
        existing = str(content[-1].get("text") or "")
        content[-1] = {
            "type": "input_text",
            "text": f"{existing}\n\n{_JSON_OBJECT_INPUT_HINT}".strip(),
        }
    else:
        content.append({"type": "input_text", "text": _JSON_OBJECT_INPUT_HINT})
    last["content"] = content


class _BetaChatCompletionsCompat:
    def parse(self, **_kwargs):
        raise NotImplementedError("structured chat parse is not available for this provider adapter")


class _OpenAIResponsesChatCompletionsCompat:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        if not self._owner.api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")
        model = str(kwargs.get("model") or self._owner.model or _OPENAI_REVIEW_MODEL_DEFAULT)
        instructions, response_input = _messages_to_openai_responses_input(kwargs.get("messages") or [])
        max_output_tokens = int(
            kwargs.get("max_output_tokens")
            or kwargs.get("max_tokens")
            or self._owner.max_output_tokens
            or 12000
        )
        reasoning = kwargs.get("reasoning")
        if not isinstance(reasoning, dict):
            reasoning = {"effort": self._owner.reasoning_effort}
        request_payload = {
            "model": model,
            "input": response_input,
            "reasoning": reasoning,
            "max_output_tokens": max_output_tokens,
        }
        if instructions:
            request_payload["instructions"] = instructions
        text_config = kwargs.get("text")
        if isinstance(text_config, dict):
            request_payload["text"] = text_config
        else:
            response_text_config = _responses_text_config_from_response_format(
                kwargs.get("response_format")
            )
            if response_text_config:
                request_payload["text"] = response_text_config
        text_format_type = (
            (request_payload.get("text") or {}).get("format") or {}
        ).get("type")
        if text_format_type == "json_object":
            _ensure_json_keyword_in_responses_input(response_input)
            request_payload["input"] = response_input
        timeout = kwargs.get("timeout") or self._owner.timeout_seconds
        resp = self._owner._client.responses.create(**request_payload, timeout=timeout)
        text = _response_text(resp)
        model_used = str(getattr(resp, "model", "") or model)
        return _chat_response(text, model=model_used)


class _OpenAIResponsesCompatClient:
    def __init__(self, *, cfg: dict, api_key: str, max_retries: int | None = None):
        import openai

        self.api_key = _clean_ai_value(api_key)
        self.model = get_ai_model(cfg, provider="openai")
        self.reasoning_effort = str(
            os.environ.get("OPENAI_REVIEW_REASONING_EFFORT", "")
            or cfg.get("OPENAI_REVIEW_REASONING_EFFORT", "high")
            or "high"
        ).strip().lower()
        self.max_output_tokens = int(
            os.environ.get("OPENAI_REVIEW_MAX_OUTPUT_TOKENS", "")
            or cfg.get("OPENAI_REVIEW_MAX_OUTPUT_TOKENS", 12000)
            or 12000
        )
        self.timeout_seconds = float(
            os.environ.get("OPENAI_REVIEW_TIMEOUT_SECONDS", "")
            or cfg.get("OPENAI_REVIEW_TIMEOUT_SECONDS", 120)
            or 120
        )
        client_kwargs = {
            "api_key": self.api_key or "missing-openai-api-key",
            "base_url": get_ai_base_url(cfg, provider="openai"),
        }
        if max_retries is not None:
            client_kwargs["max_retries"] = max(0, int(max_retries))
        self._client = openai.OpenAI(**client_kwargs)
        self.max_retries = getattr(self._client, "max_retries", max_retries)
        self.chat = SimpleNamespace(
            completions=_OpenAIResponsesChatCompletionsCompat(self)
        )
        self.beta = SimpleNamespace(
            chat=SimpleNamespace(completions=_BetaChatCompletionsCompat())
        )


def _as_anthropic_content(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    out: list[dict] = []
    if not isinstance(content, list):
        return [{"type": "text", "text": str(content or "")}]
    for part in content:
        if isinstance(part, str):
            out.append({"type": "text", "text": part})
            continue
        if not isinstance(part, dict):
            out.append({"type": "text", "text": str(part)})
            continue
        ptype = str(part.get("type") or "").strip()
        if ptype in ("text", "input_text"):
            out.append({"type": "text", "text": str(part.get("text") or "")})
        elif ptype in ("image_url", "input_image"):
            image_url = part.get("image_url")
            if isinstance(image_url, dict):
                image_url = image_url.get("url")
            if isinstance(image_url, str) and image_url.startswith("data:image/") and ";base64," in image_url:
                head, raw = image_url.split(",", 1)
                media_type = head.split(":", 1)[1].split(";", 1)[0]
                out.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": raw,
                        },
                    }
                )
    return out


def _messages_to_anthropic(messages) -> tuple[str | None, list[dict]]:
    instructions: list[str] = []
    anth_messages: list[dict] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user").strip().lower()
        content = msg.get("content") or ""
        if role == "system":
            if isinstance(content, str):
                instructions.append(content)
            continue
        anth_messages.append(
            {
                "role": "assistant" if role == "assistant" else "user",
                "content": _as_anthropic_content(content),
            }
        )
    return ("\n\n".join(p for p in instructions if p).strip() or None), anth_messages


class _AnthropicChatCompletionsCompat:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        if not self._owner.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured")
        model = str(kwargs.get("model") or self._owner.model or _CLAUDE_REVIEW_MODEL_DEFAULT)
        system, messages = _messages_to_anthropic(kwargs.get("messages") or [])
        request_payload = {
            "model": model,
            "max_tokens": int(kwargs.get("max_tokens") or 1200),
            "messages": messages,
        }
        if system:
            request_payload["system"] = system
        temperature = kwargs.get("temperature")
        if temperature is not None:
            request_payload["temperature"] = float(temperature)
        resp = self._owner._client.messages.create(**request_payload)
        text = "".join(
            block.text for block in getattr(resp, "content", []) if getattr(block, "type", None) == "text"
        )
        model_used = str(getattr(resp, "model", "") or model)
        return _chat_response(text, model=model_used)


class _AnthropicChatCompatClient:
    def __init__(self, *, cfg: dict, api_key: str, max_retries: int | None = None):
        import anthropic

        self.api_key = _clean_ai_value(api_key)
        self.model = get_ai_model(cfg, provider="claude")
        self._client = anthropic.Anthropic(api_key=self.api_key or "missing-anthropic-api-key")
        self.max_retries = max_retries
        self.chat = SimpleNamespace(completions=_AnthropicChatCompletionsCompat(self))
        self.beta = SimpleNamespace(
            chat=SimpleNamespace(completions=_BetaChatCompletionsCompat())
        )


def create_ai_client(
    cfg: dict | None = None,
    api_key: str | None = None,
    max_retries: int | None = None,
    provider: object | None = None,
):
    import openai

    cfg = CONFIG if cfg is None else cfg
    resolved_provider = get_ai_review_provider(cfg, requested=provider)
    resolved_key = _clean_ai_value(api_key) or get_ai_api_key(cfg, provider=resolved_provider)
    if resolved_provider == "openai":
        return _OpenAIResponsesCompatClient(
            cfg=cfg,
            api_key=resolved_key,
            max_retries=max_retries,
        )
    if resolved_provider == "claude":
        return _AnthropicChatCompatClient(
            cfg=cfg,
            api_key=resolved_key,
            max_retries=max_retries,
        )
    client_kwargs = {
        "api_key": resolved_key,
        "base_url": get_ai_base_url(cfg, provider=resolved_provider),
    }
    if max_retries is not None:
        client_kwargs["max_retries"] = max(0, int(max_retries))
    return openai.OpenAI(**client_kwargs)


def ai_runtime_descriptor(
    cfg: dict | None = None,
    preferred_model_key: str = "AI_MODEL",
    fallback_model: str = "grok-4.3",
) -> dict:
    resolved_cfg = CONFIG if cfg is None else cfg
    provider = get_ai_review_provider(resolved_cfg)
    return {
        "provider": get_ai_provider_label(resolved_cfg),
        "selectedProvider": provider,
        "base_url": get_ai_base_url(resolved_cfg),
        "model": get_ai_model(
            resolved_cfg,
            preferred_key=preferred_model_key,
            fallback=fallback_model,
            provider=provider,
        ),
        "key_configured": ai_key_configured(resolved_cfg),
    }

PAIR_PROFILE_VOTES = {
    "d1_trend",
    "h1_ema",
    "d1_adx",
    "h4_macd",
    "h4_oscillator",
    "volume",
    "funding",
    "session",
    "h4_fib",
    "h1_bb",
    "weinstein",
    "divergence",
    "aroon",
}
PAIR_PROFILE_FILTERS = {
    "weinstein",
    "session",
    "regime_transition",
    "obv",
    "funding",
    "squeeze",
    "mean_revert",
    "btc_bias",
    "divergence_warning",
}

# ── Load YAML overrides ──────────────────────────────────────────────────────
_yaml_overrides: dict = {}
_engine_b_load_started = False
try:
    import yaml as _yaml

    _cfg_dir = os.path.dirname(os.path.abspath(__file__))
    _cfg_path = os.path.join(_cfg_dir, "config.yaml")
    if os.path.exists(_cfg_path):
        with open(_cfg_path, "r", encoding="utf-8") as _f:
            _raw_yaml = _f.read()
        _dupes = scan_duplicate_top_level_yaml_keys(_raw_yaml)
        if _dupes:
            for _k, _lines in sorted(_dupes.items()):
                log.critical(
                    "[CFG] Duplicate top-level key %r in config.yaml (lines %s) — last value wins; remove duplicates",
                    _k,
                    _lines,
                )
            if os.environ.get("ATHENA_CONFIG_STRICT", "").strip().lower() in (
                "1",
                "true",
                "yes",
            ):
                raise ValueError(
                    f"config.yaml has duplicate top-level keys: {sorted(_dupes.keys())}"
                )
        _yaml_overrides = _yaml.safe_load(_raw_yaml) or {}
        log.info(f"Loaded config.yaml ({len(_yaml_overrides)} keys)")

    _local_path = os.path.join(_cfg_dir, "config.local.yaml")
    if os.path.exists(_local_path):
        with open(_local_path, "r", encoding="utf-8") as _lf:
            _raw_local = _lf.read()
        _ldupes = scan_duplicate_top_level_yaml_keys(_raw_local)
        if _ldupes:
            for _k, _lines in sorted(_ldupes.items()):
                log.warning(
                    "[CFG] Duplicate top-level key %r in config.local.yaml (lines %s) — last value wins; remove duplicates",
                    _k,
                    _lines,
                )
        _local_doc = _yaml.safe_load(_raw_local)
        if isinstance(_local_doc, dict):
            _yaml_overrides = _deep_merge_dict(_yaml_overrides, _local_doc)
            log.info("Merged config.local.yaml over config.yaml (%d top-level keys)", len(_yaml_overrides))
        elif _local_doc is not None:
            log.warning("[CFG] config.local.yaml root must be a mapping — ignoring file")
    _engine_b_path = os.path.join(_cfg_dir, "engine_b_config.yaml")
    if os.path.exists(_engine_b_path):
        from engine_b_yaml_loader import load_engine_b_yaml

        _engine_b_load_started = True
        _engine_b_overrides = load_engine_b_yaml(_engine_b_path)
        _engine_b_load_started = False
        # Capture config.yaml + config.local.yaml score_group_overrides BEFORE the
        # engine_b_config.yaml merge. engine_b_config.yaml is deep-merged last and
        # would otherwise shadow these values, silently reverting dashboard min_rr
        # edits (persisted to config.yaml via /api/scalp-group-rr) on every reload.
        # Re-assert precedence after the merge so config.yaml stays authoritative
        # for groups it defines, while engine_b_config.yaml still seeds groups that
        # config.yaml does not cover. No effect today (values match); durability fix.
        _pre_engine_b_ne = _yaml_overrides.get("NAKED_ENGINE")
        _pre_engine_b_sgo = None
        if isinstance(_pre_engine_b_ne, dict) and isinstance(
            _pre_engine_b_ne.get("score_group_overrides"), dict
        ):
            _pre_engine_b_sgo = _pre_engine_b_ne["score_group_overrides"]
        _yaml_overrides = _deep_merge_dict(_yaml_overrides, _engine_b_overrides)
        if _pre_engine_b_sgo:
            _ne_merged = _yaml_overrides.setdefault("NAKED_ENGINE", {})
            if not isinstance(_ne_merged, dict):
                _ne_merged = {}
                _yaml_overrides["NAKED_ENGINE"] = _ne_merged
            _ne_merged["score_group_overrides"] = _deep_merge_dict(
                _ne_merged.get("score_group_overrides") or {}, _pre_engine_b_sgo
            )
        log.info(
            "Merged engine_b_config.yaml into runtime config (%d top-level keys)",
            len(_engine_b_overrides),
        )
except ImportError:
    pass  # pyyaml optional
except Exception as _e:
    if _engine_b_load_started:
        raise
    log.warning(f"config.yaml load failed: {_e}")

# ── Default CONFIG ───────────────────────────────────────────────────────────
CONFIG: dict = {
    "MOONSHOT_API_KEY": os.environ.get("MOONSHOT_API_KEY", AI_API_KEY_PLACEHOLDER),
    "XAI_API_KEY": os.environ.get("XAI_API_KEY", ""),
    "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
    "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
    "AI_BASE_URL": os.environ.get("AI_BASE_URL", _AI_BASE_URL_DEFAULT),
    "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL", _OPENAI_BASE_URL_DEFAULT),
    "AI_REVIEW_PROVIDER": normalize_ai_review_provider(
        os.environ.get("AI_REVIEW_PROVIDER", "")
        or os.environ.get("AI_PROVIDER", "")
        or os.environ.get("LLM_PROVIDER", "")
        or os.environ.get("HERMES_PROVIDER", "")
        or os.environ.get("AI_VISION_PROVIDER", "")
        or _AI_REVIEW_PROVIDER_DEFAULT
    ),
    "AI_REVIEW_FALLBACK_PROVIDERS": os.environ.get(
        "AI_REVIEW_FALLBACK_PROVIDERS", ""
    ),
    "OPENAI_REVIEW_ENABLED": _env_bool("OPENAI_REVIEW_ENABLED", True),
    "OPENAI_REVIEW_MODEL": os.environ.get(
        "OPENAI_REVIEW_MODEL", _OPENAI_REVIEW_MODEL_DEFAULT
    ),
    "OPENAI_REVIEW_REASONING_EFFORT": os.environ.get(
        "OPENAI_REVIEW_REASONING_EFFORT", "high"
    ),
    "OPENAI_REVIEW_MAX_OUTPUT_TOKENS": _env_int(
        "OPENAI_REVIEW_MAX_OUTPUT_TOKENS", 12000
    ),
    "OPENAI_REVIEW_TIMEOUT_SECONDS": _env_int(
        "OPENAI_REVIEW_TIMEOUT_SECONDS", 240
    ),
    "OPENAI_REVIEW_SDK_MAX_RETRIES": _env_int(
        "OPENAI_REVIEW_SDK_MAX_RETRIES", 0
    ),
    "AI_MODEL": _AI_MODEL_DEFAULT,
    "XAI_MODEL": os.environ.get("XAI_MODEL", _AI_MODEL_DEFAULT),
    "CLAUDE_MODEL": os.environ.get("CLAUDE_MODEL", _CLAUDE_REVIEW_MODEL_DEFAULT),
    "LOTTERY_AI_MODEL": os.environ.get("LOTTERY_AI_MODEL", ""),  # empty → use AI_MODEL for /api/lottery/ai-analysis
    "LOTTERY_AI_PROVIDER": os.environ.get("LOTTERY_AI_PROVIDER", "openai"),
    "LOTTERY_AI_MAX_TOKENS": _env_int("LOTTERY_AI_MAX_TOKENS", 4000),
    "LOTTERY_AI_REASONING_EFFORT": os.environ.get("LOTTERY_AI_REASONING_EFFORT", "low"),
    "LOTTERY_AI_TIMEOUT_SEC": _env_int("LOTTERY_AI_TIMEOUT_SEC", 0) or None,
    "DEBATE_MODEL": os.environ.get("DEBATE_MODEL", _AI_MODEL_DEFAULT),
    "VISION_MODEL": os.environ.get("VISION_MODEL", _AI_MODEL_DEFAULT),
    "NEWS_SENTIMENT_AI_PROVIDER": os.environ.get("NEWS_SENTIMENT_AI_PROVIDER", "openai"),
    "NEWS_SENTIMENT_MODEL": os.environ.get("NEWS_SENTIMENT_MODEL", _AI_MODEL_DEFAULT),
    "AI_REQUEST_TIMEOUT_SEC": 30.0,
    "DEBATE_AI_TIMEOUT_SEC": 30.0,
    "MARCUS_AI_PROVIDER": os.environ.get("MARCUS_AI_PROVIDER", "openai"),
    "MARCUS_AI_TIMEOUT_SEC": 90.0,
    "MARCUS_AI_MAX_TOKENS": _env_int("MARCUS_AI_MAX_TOKENS", 10000),
    "MARCUS_AI_REASONING_EFFORT": os.environ.get("MARCUS_AI_REASONING_EFFORT", "low"),
    "MARCUS_AI_SDK_MAX_RETRIES": 0,
    "AI_MARCUS_TWO_STAGE_ENABLED": False,
    "AI_AGENT_PACKET_ENABLED": True,
    "AI_SIMILAR_SETUPS_ENABLED": True,
    "AI_CONTRADICTION_DETECTOR_ENABLED": True,
    "AI_MARKET_INTELLIGENCE_ENABLED": True,
    "AI_MARKET_INTELLIGENCE_TTL_SECONDS": 1800,
    "AI_MARKET_INTELLIGENCE_FAIL_OPEN": True,
    # Min RR thresholds injected into Marcus AI calibration context (advisory review only).
    # Matches deterministic gate floors; overridable via config.yaml.
    "AI_REVIEW_MIN_RR_BY_STYLE": {
        "scalp": 1.5,
        "intraday": 1.5,
        "swing": 2.0,
    },
    "AI_STRATEGIST_ENABLED": True,
    "AI_STRATEGIST_PRE_TRADE_ENABLED": False,
    "AI_STRATEGIST_MORNING_BRIEF_ENABLED": True,
    "AI_STRATEGIST_WEEKLY_RETRO_ENABLED": True,
    "VISION_FRESHNESS_POLICY": {
        "missing_chart_timestamp": "downgrade_to_review",
        "missing_latest_candle_ts": "downgrade_to_review",
        "stale_action": "reject_for_execution_context",
        "max_age_sec": {
            "M1": 180,
            "M5": 900,
            "M15": 1800,
            "H1": 5400,
            "H4": 18000,
            "D1": 97200,
        },
    },
    "AI_CHART_REVIEW": {
        "ENABLED": _env_bool("AI_CHART_REVIEW_ENABLED", False),
        "DEFAULT_PROVIDER": normalize_ai_review_provider(
            os.environ.get("AI_CHART_REVIEW_DEFAULT_PROVIDER", "")
            or os.environ.get("AI_REVIEW_PROVIDER", "")
            or _AI_REVIEW_PROVIDER_DEFAULT
        )
        or _AI_REVIEW_PROVIDER_DEFAULT,
        "ANTHROPIC_MODEL": os.environ.get(
            "AI_CHART_REVIEW_ANTHROPIC_MODEL", "claude-opus-4-7"
        ),
        "OPENAI_MODEL": os.environ.get(
            "OPENAI_CHART_REVIEW_MODEL",
            os.environ.get("OPENAI_REVIEW_MODEL", _OPENAI_REVIEW_MODEL_DEFAULT),
        ),
        "MAX_TOKENS": _env_int("AI_CHART_REVIEW_MAX_TOKENS", 1500),
        "XAI_MAX_TOKENS": _env_int("AI_CHART_REVIEW_XAI_MAX_TOKENS", 8000),
        "TIMEOUT_SEC": _env_int("AI_CHART_REVIEW_TIMEOUT_SEC", 120),
        "XAI_JSON_MODE": _env_bool("AI_CHART_REVIEW_XAI_JSON_MODE", False),
        "REQUIRE_SCREENSHOT": True,
        "REQUIRE_ATR_DIAGNOSTICS": False,
        "REQUIRE_FRESHNESS_DIAGNOSTICS": False,
        "MAX_IMAGE_BYTES": 2 * 1024 * 1024,
        "PERSIST_REVIEWS": True,
        "OPENAI_REVIEW_ENABLED": _env_bool("OPENAI_REVIEW_ENABLED", True),
        "ALLOW_OPENAI_PROVIDER": _env_bool(
            "AI_CHART_REVIEW_ALLOW_OPENAI_PROVIDER",
            _env_bool("OPENAI_REVIEW_ENABLED", True),
        ),
        "ALLOW_DUAL_PROVIDER": _env_bool("AI_CHART_REVIEW_ALLOW_DUAL_PROVIDER", False),
        # Dual-provider reconciler (Phase 0). Primary wins on disagreement
        # unless tiebreaker is "secondary" or "fail_closed". Advisory-only;
        # entryAllowedNow is always downgraded to false if EITHER provider
        # says no (fail-closed).
        "AI_DUAL_PROVIDER_PRIMARY": "openai",
        "AI_DUAL_PROVIDER_SECONDARY": "claude",
        "AI_DUAL_PROVIDER_TIEBREAKER": "primary",
        "AI_DUAL_PROVIDER_TIMEOUT_SEC": 60.0,
        "MAX_DISPLACEMENT_ATR_MULTIPLE": 1.0,
        "MISMATCH_WARN_MAX_SECONDS": 120,
        "ATR_FRESHNESS_MAX_AGE_SEC": None,
        "DEDUP_WINDOW_SECONDS": 60,
        # AI strategy-playbook layer is additive and read-only; it never
        # approves, sizes, or blocks trades. Default ON.
        "STRATEGY_LAYER_ENABLED": _env_bool(
            "AI_CHART_REVIEW_STRATEGY_LAYER_ENABLED", True
        ),
        "STRATEGY_LAYER_OHLCV_LIMIT": _env_int(
            "AI_CHART_REVIEW_STRATEGY_LAYER_OHLCV_LIMIT", 80
        ),
        "PRIMARY_ENGINE": os.environ.get("AI_CHART_REVIEW_PRIMARY_ENGINE", "A"),
    },
    "SUGGESTED_TRADE_MONITOR": {
        "ENABLED": _env_bool("SUGGESTED_TRADE_MONITOR_ENABLED", True),
        "ALERT_ONLY": True,
        "RUNNER_ENABLED": _env_bool("SUGGESTED_TRADE_MONITOR_RUNNER_ENABLED", True),
        "POLL_SECONDS": _env_int("SUGGESTED_TRADE_MONITOR_POLL_SECONDS", 5),
        "DEFAULT_EXPIRY_SECONDS": _env_int(
            "SUGGESTED_TRADE_MONITOR_DEFAULT_EXPIRY_SECONDS", 1800
        ),
        "MAX_ACTIVE_WATCHES": _env_int("SUGGESTED_TRADE_MONITOR_MAX_ACTIVE_WATCHES", 25),
    },
    "TIMEFRAME_ROUTING": {
        "ENABLED": True,
        "DEFAULT": {
            "D1": {"entry_tf": "H4", "execution_tf": "H1"},
            "H4": {"entry_tf": "H1", "execution_tf": "M15"},
            "H1": {"entry_tf": "M15", "execution_tf": "M15"},
            "M15": {"entry_tf": "M15", "execution_tf": "M5"},
            "M5": {"entry_tf": "M5", "execution_tf": "M5"},
            "M1": {"entry_tf": "M1", "execution_tf": "M1"},
        },
        "BY_ASSET_GROUP": {
            "forex": {
                "H4": {"entry_tf": "H1", "execution_tf": "M15"},
                "H1": {"entry_tf": "M15", "execution_tf": "M15"},
            },
            "crypto": {
                "H4": {"entry_tf": "H1", "execution_tf": "M15"},
                "H1": {"entry_tf": "M15", "execution_tf": "M15"},
            },
            "commodities": {
                "H4": {"entry_tf": "H1", "execution_tf": "M15"},
                "H1": {"entry_tf": "M15", "execution_tf": "M15"},
            },
            "indices": {
                "H4": {"entry_tf": "H1", "execution_tf": "M15"},
                "H1": {"entry_tf": "M15", "execution_tf": "M15"},
            },
            "stocks": {
                "D1": {"entry_tf": "H4", "execution_tf": "H1"},
                "H4": {"entry_tf": "H1", "execution_tf": "M15"},
            },
        },
    },
    "AI_SCALP_CHART_REVIEW": {
        "ENABLED": _env_bool("AI_SCALP_CHART_REVIEW_ENABLED", False),
        "DEFAULT_PROVIDER": normalize_ai_review_provider(
            os.environ.get("AI_SCALP_CHART_REVIEW_DEFAULT_PROVIDER", "")
            or os.environ.get("AI_REVIEW_PROVIDER", "")
            or _AI_REVIEW_PROVIDER_DEFAULT
        )
        or _AI_REVIEW_PROVIDER_DEFAULT,
        "ANTHROPIC_MODEL": os.environ.get(
            "AI_SCALP_CHART_REVIEW_ANTHROPIC_MODEL",
            os.environ.get("AI_CHART_REVIEW_ANTHROPIC_MODEL", "claude-opus-4-7"),
        ),
        "OPENAI_MODEL": os.environ.get(
            "OPENAI_SCALP_CHART_REVIEW_MODEL",
            os.environ.get("OPENAI_REVIEW_MODEL", _OPENAI_REVIEW_MODEL_DEFAULT),
        ),
        "MAX_TOKENS": _env_int("AI_SCALP_CHART_REVIEW_MAX_TOKENS", 1500),
        "REQUIRE_SCREENSHOT": True,
        "MAX_IMAGE_BYTES": 2 * 1024 * 1024,
        "PERSIST_REVIEWS": True,
        "OPENAI_REVIEW_ENABLED": _env_bool("OPENAI_REVIEW_ENABLED", True),
        "ALLOW_OPENAI_PROVIDER": _env_bool(
            "AI_SCALP_CHART_REVIEW_ALLOW_OPENAI_PROVIDER",
            _env_bool("OPENAI_REVIEW_ENABLED", True),
        ),
        "DEDUP_WINDOW_SECONDS": 60,
        "EXECUTE_REQUIRES_AI_REVIEW": _env_bool(
            "AI_SCALP_CHART_REVIEW_EXECUTE_REQUIRES_AI_REVIEW", True
        ),
        "EXECUTE_AI_REVIEW_MAX_AGE_SECONDS": _env_int(
            "AI_SCALP_CHART_REVIEW_EXECUTE_MAX_AGE_SECONDS", 300
        ),
        "STRATEGY_LAYER_ENABLED": _env_bool(
            "AI_SCALP_CHART_REVIEW_STRATEGY_LAYER_ENABLED", True
        ),
        "STRATEGY_LAYER_OHLCV_LIMIT": _env_int(
            "AI_SCALP_CHART_REVIEW_STRATEGY_LAYER_OHLCV_LIMIT", 80
        ),
        "MAX_CHART_AGE_SECONDS": _env_int("AI_SCALP_CHART_REVIEW_MAX_CHART_AGE_SECONDS", 120),
        "REJECT_ON_SEVERE_SNAPSHOT_MISMATCH": _env_bool(
            "AI_SCALP_CHART_REVIEW_REJECT_ON_SEVERE_SNAPSHOT_MISMATCH", True
        ),
        "SESSION_CONTEXT": {
            "ENABLED": _env_bool("AI_SCALP_CHART_REVIEW_SESSION_CONTEXT_ENABLED", True),
            "NY_PREMARKET_START_ET": "08:00",
            "NY_OPEN_START_ET": "09:30",
            "NY_OPEN_END_ET": "10:30",
            "NY_MIDDAY_END_ET": "14:00",
            "NY_AFTERNOON_END_ET": "15:30",
            "NY_CLOSE_END_ET": "16:00",
            "LONDON_START_LOCAL": "07:00",
            "LONDON_END_LOCAL": "16:00",
            "ASIA_START_UTC_HOUR": 1,
            "ASIA_END_UTC_HOUR": 9,
            "PROFIT_LOCK_NET_R": 2.0,
            "ATR_PCT_LOW_MAX": 0.08,
            "ATR_PCT_NORMAL_MAX": 0.15,
            "ATR_PCT_HIGH_MAX": 0.30,
        },
    },
    "AI_PROMPT_STORE_CLEANUP_ENABLED": True,
    "AI_PROMPT_STORE_RETENTION_DAYS": 90,
    "AI_PROMPT_STORE_MIN_DELETE_AGE_DAYS": 7,
    "AI_PROMPT_STORE_MAX_BYTES": 1073741824,
    "AI_PROMPT_STORE_CLEANUP_INTERVAL_SEC": 86400,
    # External prompt store (Phase 0). Default-off; surfaces fall back to
    # their hardcoded prompts when disabled. See prompt_store.py.
    "AI_PROMPT_STORE_ENABLED": False,
    "AI_PROMPT_STORE_DIR": "prompts",
    # SSE streaming for advisory AI surfaces (Phase 0). Default-off; routes
    # fall back to non-streaming endpoints when disabled. See ai_streaming.py.
    "AI_STREAMING_ENABLED": False,
    # Vision path unification (Phase 0). Default-off; when enabled, the legacy
    # /api/chart-analysis response is stamped with deprecation metadata that
    # points clients to /api/ai/chart-review. The legacy response shape is
    # unchanged so existing clients keep working.
    "AI_VISION_LEGACY_ROUTE_DEPRECATED": False,
    # Strict Pydantic schema enforcement for AI responses (Phase 0). Default-off;
    # when disabled, raw dicts pass through unchanged. When enabled, schema
    # validation failures fall back to caller-supplied safe defaults.
    "AI_STRICT_SCHEMA_ENFORCEMENT": False,
    # Local semantic memory (Phase 1). Default-off; uses dependency-free
    # hashing-trick embeddings stored in SQLite. NEVER calls external APIs to
    # embed trade data. See ai_memory.py.
    "AI_MEMORY_ENABLED": False,
    "AI_MEMORY_DB_PATH": "",
    # Native LLM function-calling planner for the AI agent (Phase 2). Default-
    # off; when disabled, the keyword-based plan_tool_calls() is used. When
    # enabled, the LLM proposes read-only tool calls which are re-validated
    # against READ_ONLY_TOOL_NAMES before execution. See ai_agent_planner.py.
    "AI_AGENT_NATIVE_PLANNING_ENABLED": False,
    "AI_AGENT_MAX_STEPS": 8,
    # Phase 3 — Eval + feedback loop. All default-off, advisory-only, no
    # auto-apply. Golden-set eval harness (tests/ai_eval/golden_set.py +
    # tools/run_prompt_eval.py), drift detector (ai_drift_detector.py), and
    # the weekly prompt-quality report section.
    "AI_PROMPT_EVAL_ENABLED": False,
    "AI_DRIFT_DETECTION_ENABLED": False,
    "AI_DRIFT_DECISION_JS_THRESHOLD": 0.20,
    "AI_DRIFT_ENTRY_RATE_DELTA": 0.15,
    "AI_DRIFT_CONFIDENCE_DELTA": 0.10,
    "AI_DRIFT_MIN_SAMPLES": 10,
    # Phase 4 — Activate dormant surfaces. All default-off, advisory-only,
    # fail-closed, full audit. Strategist LLM narrative + ASE narrator.
    # (Engine C AI weight verdict and Lee LLM narrative are pre-existing
    # code paths gated by ENGINE_C_AI_WEIGHT_VERDICT_ENABLED and
    # AI_LEE_CONFIRMATION_LLM_ENABLED respectively.)
    "AI_STRATEGIST_LLM_ENABLED": False,
    "AI_ASE_NARRATOR_ENABLED": False,
    # Phase 5 — Reasoning depth. All default-off, advisory-only, fail-closed.
    # Multi-role debate, tree-of-thought, self-critique, cross-engine
    # consistency. Multi-role debate honors FORCE_DEBATE_DOWNGRADE_ONLY.
    "AI_MULTI_ROLE_DEBATE_ENABLED": False,
    "DEBATE_ROLES": ["risk_officer", "macro_strategist"],
    "AI_TOT_ENABLED": False,
    "AI_TOT_MIN_SCORE": 7.5,
    "AI_TOT_BRANCHES": 3,
    "AI_SELF_CRITIQUE_ENABLED": False,
    # Phase 6 — UX + surfacing. All default-off, advisory-only, fail-closed.
    # Citations/grounding tags, confidence calibration, AI disagreement viz,
    # what-if replay (read-only, no execution).
    "AI_CITATIONS_ENABLED": False,
    "AI_CONFIDENCE_CALIBRATION_ENABLED": False,
    "AI_DISAGREEMENT_VIZ_ENABLED": False,
    "AI_DISAGREEMENT_CONF_SPREAD": 0.25,
    "AI_WHATIF_ENABLED": False,
    # Phase 7 — Research Lab AI. All default-off, advisory-only, fail-closed,
    # no auto-apply. Agentic research loops, AI-driven parameter discovery
    # with human-approval queue, multi-run synthesis. Never touches live
    # config.yaml.
    "AI_RESEARCH_LOOP_ENABLED": False,
    "AI_RESEARCH_MAX_STEPS": 4,
    "AI_PARAM_DISCOVERY_ENABLED": False,
    "AI_MULTI_RUN_SYNTHESIS_ENABLED": False,
    "AI_MULTI_RUN_SYNTHESIS_LLM_NARRATE": False,
    "NEWS_SENTIMENT_CONFLUENCE_ENABLED": False,
    "NEWS_SENTIMENT_CACHE_TTL_SEC": 900,
    "NEWS_SENTIMENT_SCORE_IMPACT": 0.06,
    "NEWS_SENTIMENT_ATTACH_SUMMARY": True,
    "NEWS_SENTIMENT_SKIP_UNREACHABLE_SCAN_ROWS": True,
    "NEWS_SENTIMENT_MACRO_CONTEXT_ENABLED": True,
    "NEWS_SENTIMENT_MAX_ARTICLE_AGE_HOURS": 72,
    "NEWS_SENTIMENT_MAX_ARTICLES_TOTAL": 12,
    "NEWS_SENTIMENT_MAX_ARTICLES_PER_SOURCE": 6,
    "NEWS_SENTIMENT_USE_EODHD_NORMALIZED": False,
    "NEWS_SENTIMENT_MAJOR_EVENT_MODE": "advisory",
    "NEWS_SENTIMENT_MAX_DELTA": 0.30,
    # Group-aware overrides for news sentiment score adjustment (post-Engine-A, advisory only).
    # Keys: score_group (e.g. "forex_majors", "precious_trackers") or asset_type or "default".
    # When absent/empty, falls back to the scalar NEWS_SENTIMENT_* globals above.
    "NEWS_SENTIMENT_IMPACT_BY_CLASS": {},
    "NEWS_SENTIMENT_MAX_DELTA_BY_CLASS": {},
    "NEWS_SENTIMENT_DRIVER_MAP": {
        "XAU/USD": [
            "tag:usd",
            "tag:dxy",
            "tag:fed-rates",
            "tag:rates",
            "tag:cpi",
            "tag:pce",
            "tag:nfp",
            "tag:real-yields",
            "tag:safe-haven",
            "tag:geopolitics",
            "tag:central-bank-demand",
        ],
        "XAUUSD": [
            "tag:usd",
            "tag:dxy",
            "tag:fed-rates",
            "tag:rates",
            "tag:cpi",
            "tag:pce",
            "tag:nfp",
            "tag:real-yields",
            "tag:safe-haven",
            "tag:geopolitics",
            "tag:central-bank-demand",
        ],
        "commodity": [
            "tag:inflation",
            "tag:safe-haven",
        ],
    },
    "NEWS_PAIR_CAP": 40,  # Max pairs for EODHD per-pair news + word-weights in news cache refresh
    "AI_STRUCTURED_OUTPUTS": True,
    "AI_TEMPERATURE": 0.3,
    "AI_VISION_TEMPERATURE": 0.2,  # /api/chart-analysis factual mode (override in config.yaml if needed)
    "AI_VISION_CAN_UPGRADE_TRADE": False,  # Vision CONFIRM cannot create trade=True; downgrade only by default
    # When False, debate/vision sources classified as neutral (e.g. SKIP/REVIEW) block execution_allowed.
    "AI_NEUTRAL_ALLOWS_EXECUTION": False,
    "ENGINE_B_PROFILE_SCORING_ENABLED": True,
    "ENGINE_B_PROFILE_TRUST_MODE": "score_group",
    "ENGINE_B_PROFILE_TRUSTED_ASSET_TYPES": ["crypto", "stock"],
    "ENGINE_B_PROFILE_TRUSTED_SCORE_GROUPS": [
        "crypto_btc",
        "crypto_eth",
        "crypto_alt_majors",
        "crypto_doge",
        "crypto_other",
        "us_stock_single",
        "stock_other",
        "smallcap_em_etf",
        # C2: index ETFs, precious/energy/bond trackers have real RTH volume
        # (SPY/QQQ/DAX ETFs, GLD/SLV, USO/XLE, TLT) — VP scoring was disabled
        # despite reliable volume (audit MED #18).
        "us_indices_trackers",
        "eu_indices",
        "asian_indices",
        "precious_trackers",
        "energy_oil",
        "bond_tlt",
    ],
    "ENGINE_B_CRYPTO_AGGTRADE_ENABLED": True,
    "ENGINE_B_CRYPTO_REQUIRE_AGGTRADE_FOR_PASS": True,
    "ENGINE_B_CRYPTO_AGGTRADE_CVD_SCORE_ENABLED": True,
    "ENGINE_B_FAST_FVG_DETECTION": True,
    "CHART_VISION_DATASET_ENABLED": False,
    "CHART_VISION_V2_SHADOW_ENABLED": False,
    "CRYPTOPANIC_KEY": os.environ.get("CRYPTOPANIC_KEY", ""),
    "FINNHUB_KEY": os.environ.get("FINNHUB_KEY", ""),
    "RISK_PCT": 0.01,
    "SL_ATR_MULT": 1.5,
    "TP1_ATR_MULT": 2.0,
    "TP2_ATR_MULT": 3.5,
    "DAILY_LOSS_LIMIT": 0.05,  # Kill switch: halt trading after losing 5% of account in a day
    "VOLUME_THRESHOLD": 1.5,
    "VOLUME_THRESHOLD_BACKTEST": 1.2,
    "BT_NUM_VARIANTS_TRIED": 1,
    "BT_BOOTSTRAP_CI_ITERATIONS": 1000,
    "BT_MIN_TRADES_FOR_PSR_RELIABILITY": 30,
    "BT_LOW_SAMPLE_SQ_WARN_TRADES": 30,
    # Research/backtest-only exit baselines. These keys must not be used by
    # live scanner, risk, broker, guardian, or timed-exit runtime code.
    "BACKTEST_EXIT_MODE": "triple_barrier",
    "BACKTEST_BASELINE_R": {
        "sl_r": 1.0,
        "tp_r": 1.5,
        "max_hold_bars": {"M15": 12, "H1": 24, "H4": 18, "D1": 10},
    },
    "BACKTEST_ATR_BASELINE": {
        "atr_length": 14,
        "sl_atr": 1.0,
        "tp_atr": 1.5,
        "max_hold_bars": {"M15": 12, "H1": 24, "H4": 18, "D1": 10},
    },
    "BACKTEST_TRIPLE_BARRIER": {
        "target_source": "atr",
        "atr_length": 14,
        "sl_mult": 1.0,
        "tp_mult": 1.5,
        "max_hold_bars": {"M15": 12, "H1": 24, "H4": 18, "D1": 10},
        "same_bar_policy": "sl_first",
    },
    "BT_SLIPPAGE_MODEL": {
        "ENABLED": True,
        "K1_TICK_MULT": 1.0,
        "K2_ATR_IMPACT": 0.10,
        "DEFAULT_QTY_ADV_RATIO": 0.001,
        "MAX_SLIPPAGE_PCT": 0.01,
    },
    "ADX_TREND_MIN": 25,
    # analyze_pair: fetch then drop last (forming) bar. H4/H1 align with /api/candles max (1000).
    # D1=1001 so closed D1 bars after drop ≈1000; tune in config.yaml. Lower = faster scans, chart diverges.
    "D1_CANDLES": 1001,
    "H4_CANDLES": 1000,
    "H1_CANDLES": 1000,
    # Skip /api/eod-bulk-last-day/{EXCH} for these ticker suffixes (404 on e.g. COMM).
    "CANDLE_BUILDER_BULK_D1_SKIP_EXCHANGES": ["COMM"],
    "SCAN_MAX_WORKERS": 3,
    "SCAN_DEBUG_CANDLE_META": False,
    "MT5_BROKER_UTC_OFFSET": 3,
    "FOREX_H4_RESAMPLE_OFFSET_HOURS": 1.0,
    "MT5_CANDLE_FALLBACK_ENABLED": False,
    "ENGINE_A_CRYPTO_LEVELS_FEED": "bybit",
    "ENGINE_A_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK": False,
    "ENGINE_A_CRYPTO_DERIVATIVES_FEED": "bybit",
    "ENGINE_A_CRYPTO_DERIVATIVES_BINANCE_FALLBACK": True,
    "ENGINE_A_OI_MAX_AGE_SEC": 300,
    "ENGINE_A_BT_OI_MAX_AGE_MS": 86400000,
    "ENGINE_A_CRYPTO_BT_LEVEL_ATR_USE_SIGNAL_FEED": False,
    "ENGINE_A_CRYPTO_SIGNAL_FEED": "bybit",
    "ENGINE_A_CRYPTO_LATE_TREND_DIAGNOSTICS_ENABLED": True,
    "ENGINE_A_CRYPTO_LATE_TREND_ADJUSTMENT_ENABLED": True,
    "ENGINE_A_CRYPTO_ADX_STRONG_THRESHOLD": 25,
    "ENGINE_A_CRYPTO_LATE_TREND_MULT": 0.85,
    "ENGINE_A_CRYPTO_VWAP_EXTENSION_ATR_WARN": 3.0,
    "ENGINE_A_CRYPTO_VOLUME_MA_LOOKBACK": 20,
    "ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED": True,
    "ENGINE_A_TRADE_MIN_CONFIDENCE": 0.60,
    "ENGINE_A_STRUCTURAL_SL_FLOOR_ATR": True,
    "ENGINE_A_STRUCTURAL_SL_VALIDATE_SIDE": True,
    "ENGINE_A_BTC_CORR_WARN_ON_MISSING_SERIES": False,
    "ENGINE_AB_CRYPTO_SIGNAL_FEED": "bybit",
    "ENGINE_AB_CRYPTO_SIGNAL_FEED_FALLBACK": False,
    "ENGINE_A_MULTI_EXCHANGE_FUNDING": {
        "ENABLED": True,
        "BINANCE_WEIGHT": 0.5,
        "BYBIT_WEIGHT": 0.5,
    },
    "ENGINE_B_CRYPTO_LEVELS_FEED": "bybit",
    "ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK": False,
    "ENGINE_B_CRYPTO_BT_LEVEL_ATR_USE_SIGNAL_FEED": False,
    "ENGINE_B_CRYPTO_AGGTRADE_MODE": "degraded",
    "ENGINE_B_CRYPTO_AGGTRADE_TRUSTED_MODE": "required",
    "ENGINE_B_CRYPTO_AGGTRADE_MISSING_PENALTY": 0.5,
    "ENGINE_B_SWEEP_LOOKBACK_BARS": 5,
    # Defaults True: synthetic-RR TP fallback rescues otherwise-valid Engine B
    # signals whose structural target falls short of min_rr (or whose structural
    # TP is missing/wrong-side). Code defaults must agree with config.yaml so
    # deployments without overlay do not silently disable the rescue path.
    "ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP": True,
    # When True, Engine B execution SL must fit risk_engine MAX_SL_PCT (clamp to ATR or fail).
    "ENGINE_B_ENFORCE_MAX_SL_PCT": True,
    "NAKED_MAX_DAILY": 3,
    "ENGINE_B_BT_EXIT_POLICY": "fixed_target_be",
    "ENGINE_B_BT_SCALE_OUT_TP1_SIZE": 0.5,
    "ENGINE_B_BT_MOVE_SL_TO_BE_AFTER_TP1": True,
    "ENGINE_B_BT_RUNNER_ENABLED": True,
    # Scale-out plan guards (dual-TP exit plan). TP1 below this RR triggers a
    # tighter-SL retry; if no sane SL/TP1 pair exists the plan falls back to
    # the legacy single-TP levels (trade still flows through unchanged gates).
    "ENGINE_B_TP1_MIN_RR": {"default": 0.3},
    # Minimum runner room for TP2 in ATR multiples, per style. 0 disables.
    "ENGINE_B_TP2_MIN_ROOM_ATR": {"default": 0.5, "swing": 0.75},
    # True: insufficient TP2 room drops the scale-out plan (single-TP legacy).
    # False: TP2 is floored to the minimum room distance instead.
    "ENGINE_B_TP2_ROOM_FAIL_CLOSED": True,
    # Backtest: runner TP2 fill requires a confirmed structural break (a bar
    # CLOSE beyond TP1) before a TP2 touch counts.
    "ENGINE_B_BT_RUNNER_REQUIRES_BREAK": True,
    # Engine B exit-mode selector (parallels ENGINE_A_EXIT_MODE_*). The default
    # matches today's live behavior: fixed broker bracket (static). The exit
    # strategy (single vs scale-out) comes from resolve_engine_b_execution_levels;
    # the mode decides how the runner leg is managed (see exit_policy).
    "ENGINE_B_EXIT_MODE_GLOBAL_DEFAULT": "traditional_static",
    "ENGINE_B_EXIT_MODE_BY_SCORE_GROUP": {},
    # Live Engine B scale-out (Bybit paper/demo): default OFF — live order
    # behavior is unchanged until explicitly enabled. When on, a reduce-only
    # partial banks the TP1 clip and the position TP carries the TP2 runner.
    "ENGINE_B_LIVE_SCALE_OUT_ENABLED": False,
    "ENGINE_B_LIVE_SCALE_OUT_TP1_SIZE": 0.5,
    # ── Phase 3 calibration placeholders — NOT wired into level resolution ──
    # Values mirror the current NAKED_ENGINE style-profile min_rr rows (forex
    # groups override to 1.3/1.8). They only become active after the
    # calibrate_engine_b_min_rr report is reviewed and new values are
    # explicitly approved and wired into resolve_engine_b_execution_levels.
    "ENGINE_B_MIN_RR_INTRADAY_FLOOR": 1.5,
    "ENGINE_B_MIN_RR_SWING_FLOOR": 2.0,
    "ENGINE_B_MIN_RR_BY_ASSET_CLASS": {
        "forex": {"intraday": 1.3, "swing": 1.8},
        "crypto": {"intraday": 1.5, "swing": 2.0},
        "stock": {"intraday": 1.5, "swing": 2.0},
    },
    # Backtest runner-trail proxy: chandelier-style ATR trail multiple for the
    # post-TP1 runner when the resolved exit mode is adaptive_trail.
    "ENGINE_B_BT_RUNNER_TRAIL_ATR_MULT": 2.0,
    # time_based exit mode horizon in entry-timeframe bars; 0 disables the
    # timed close (mode then behaves like the static fixed bracket).
    "ENGINE_B_TIME_EXIT_BARS": 0,
    # When True, Engine B skips forex on 22:00–07:00 UTC bars (backtest + live scan).
    "ENGINE_B_FOREX_ASIAN_SESSION_SKIP_ENABLED": True,
    "ENGINE_B_FOREX_PRE_ASIAN_SESSION_SKIP_ENABLED": True,
    # Diagnostics-first forex session weighting for Engine B structure context.
    # Disabled by default; score influence requires SCORE_INFLUENCE_ENABLED too.
    "ENGINE_B_FOREX_SESSION_STRUCTURE_WEIGHTING": {
        "ENABLED": False,
        "SCORE_INFLUENCE_ENABLED": False,
        "HIGH_QUALITY_BONUS": 0.02,
        "MEDIUM_QUALITY_BONUS": 0.01,
        "LOW_QUALITY_PENALTY": -0.02,
        "LONDON_NY_OVERLAP_BONUS": 0.01,
        "LIQUIDITY_SWEEP_ACTIVE_SESSION_BONUS": 0.01,
        "MAX_ABS_SCORE_BONUS": 0.04,
    },
    # Forex-only, default-off correction for the Engine B space gate:
    # when enabled, valid execution RR can satisfy the "room/rr" gate comment
    # without changing non-forex assets.
    "ENGINE_B_FOREX_RR_CAN_SATISFY_SPACE_GATE": False,
    # Asset-scoped version of the same correction. Defaults preserve historical
    # behavior; config.yaml enables paper-test assets explicitly.
    "ENGINE_B_RR_CAN_SATISFY_SPACE_GATE": {
        "default": False,
        "forex": False,
        "stock": False,
        "index": False,
        "commodity": False,
        "etf": False,
        "etf_bond": False,
        "crypto": False,
    },
    "ENGINE_B_SPACE_RR_SUBSTITUTE_MIN_ATR_FLOOR_ENABLED": False,
    "ENGINE_B_SPACE_RR_SUBSTITUTE_MIN_ATR_FLOOR": 0.5,
    # When a synthetic fallback TP replaced a structural TP that failed min_rr
    # (an opposing zone capped the structural target), that synthetic TP may not
    # substitute for the room gate — the wall is still in front of price.
    # Synthetic TPs created because no structural target existed still qualify.
    "ENGINE_B_SPACE_RR_SUBSTITUTE_BLOCK_CAPPED_STRUCTURAL_TP": True,
    "ENGINE_B_ATR_SL_CLAMPS_ENABLED": True,
    "ENGINE_B_MIN_SL_ATR_DEFAULT": 0.75,
    "ENGINE_B_MAX_SL_ATR_DEFAULT": 3.0,
    "ENGINE_B_ATR_SL_CLAMP_SCORE_GROUP_OVERRIDES": {},
    # Default-off non-forex/crypto structure tuning for stocks, indices, commodities, and ETFs.
    "ENGINE_B_ASSET_CLASS_ADJUSTMENTS_ENABLED": False,
    "ENGINE_B_ASSET_CLASS_VOLATILITY_AWARE_ENABLED": False,
    "ENGINE_B_ASSET_CLASS_STRUCTURE_MULT": {
        "stock": 1.10,
        "index": 1.05,
        "commodity": 1.15,
        "etf": 1.08,
        "etf_bond": 1.05,
    },
    "ENGINE_B_ASSET_CLASS_BOS_MIN_BREAK_ATR": {
        "forex": 0.05,
        "stock": 0.06,
        "index": 0.05,
        "commodity": 0.08,
        "etf": 0.05,
        "etf_bond": 0.04,
    },
    "ENGINE_B_EQUITY_SESSION_STRUCTURE_WEIGHTING": {
        "ENABLED": False,
        "SCORE_INFLUENCE_ENABLED": False,
        "HIGH_QUALITY_BONUS": 0.015,
        "MEDIUM_QUALITY_BONUS": 0.008,
        "OFF_HOURS_PENALTY": -0.015,
        "LIQUIDITY_SWEEP_ACTIVE_SESSION_BONUS": 0.008,
        "MAX_ABS_SCORE_BONUS": 0.03,
    },
    # When True, Engine B room gate requires structural distance; unknown distance fails closed.
    "ENGINE_B_ROOM_GATE_REQUIRE_DISTANCE": True,
    "ENGINE_B_CANONICAL_ACTIONABILITY": {
        "ENABLED": True,
        "ENFORCE_ROUTING": False,
        "ALLOW_WARN_IN_RESEARCH": False,
        "ALLOW_RR1_BELOW_MIN_IN_RESEARCH": False,
        # Learning context is prompt/advisory evidence by default. Set to
        # "reject" only after explicit validation that the cohort has a
        # safety-critical negative edge and should hard-block Engine B entries.
        "LEARNING_NEGATIVE_MODE": "diagnostic",
        "INCLUDE_REJECT_IN_AI_REVIEW": False,
        # CSV audits are useful for investigations but expensive inside
        # broad scans because each candidate appends multiple report rows.
        "AUDIT_CSV_ENABLED": False,
        "STRUCTURE_AUDIT_CSV": "reports/engine_b_structure_gate_audit.csv",
        "RECONCILIATION_AUDIT_CSV": "reports/engine_b_actionability_reconciliation.csv",
    },
    "BINANCE_KLINE_WS_INTERVALS": ["1m", "5m", "15m", "1h", "4h", "1d"],
    "MIN_CONFLUENCE": 1.0,
    "RISK_MULT": {
        "commodity": 1.2,
        "crypto": 0.8,
        "forex": 0.6,
        "index": 0.6,
        "stock": 0.6,
    },
    # Round-trip fee per trade (entry + exit) as fraction of notional.
    # Bybit taker=0.055%×2=0.11%, forex ECN~0.02%×2=0.04%, stocks~0.03%×2=0.06%
    "FEE_PCT": {
        "crypto": 0.0011,
        "forex": 0.0004,
        "commodity": 0.0004,
        "stock": 0.0006,
        "index": 0.0004,
    },
    "RANGING": {
        "crypto": {"dead": 18, "choppy": 23},
        "commodity": {"dead": 18, "choppy": 23},
        "forex": {"dead": 18, "choppy": 23},
        "stock": {"dead": 16, "choppy": 21},
        "index": {"dead": 16, "choppy": 21},
    },
    # ATR_CLASS: fallback when no style is set. Primary path is STYLE_ATR_MULTS below.
    "ATR_CLASS": {
        "forex": {"sl": 1.2, "tp1": 2.0, "tp2": 3.0},
        "commodity": {"sl": 1.5, "tp1": 2.5, "tp2": 4.0},
        "index": {"sl": 1.5, "tp1": 2.5, "tp2": 4.0},
        "stock": {"sl": 1.5, "tp1": 2.5, "tp2": 4.0},
        "etf": {"sl": 1.2, "tp1": 2.0, "tp2": 3.5},
        "etf_bond": {"sl": 0.9, "tp1": 1.5, "tp2": 2.5},
        "crypto": {"sl": 2.0, "tp1": 3.5, "tp2": 5.0},
    },
    # Style-specific ATR multipliers — calibrated to industry benchmarks:
    #   quantstock.org, bestmt4ea.com, atrindicator.com, luxalgo.com, fxnx.com,
    #   cryptotrading-guide.com (2026), fxpremiere.com (XAU/USD), tapbit.com (2026).
    #
    # ATR timeframe reference per style:
    #   Scalp   → H1 ATR  (EUR/USD H1 ATR ≈ 12 pips)
    #   Intraday → H4 ATR  (EUR/USD H4 ATR ≈ 40 pips)
    #   Swing    → D1 ATR  (EUR/USD D1 ATR ≈ 70-90 pips; crypto also D1)
    #
    # Industry benchmark SL ranges:
    #   Scalp (H1):     1.0–1.5× ATR minimum
    #   Intraday (H4):  1.5–2.0× ATR minimum
    #   Swing (D1):     1.5–2.0× ATR (forex/stock); 1.5–2.5× ATR (crypto/commodity)
    #
    # RR ratios maintained across all styles:
    #   Scalp:    tp1/sl = 1.5:1, tp2/sl = 2.5:1
    #   Intraday: tp1/sl = 2.0:1, tp2/sl = 3.33:1
    #   Swing:    tp1/sl = 1.67:1, tp2/sl = 2.67:1
    #
    # TP1: quick partial exit at ~1.5–2× risk (take 50–70% here).
    # TP2: runner target — move SL to breakeven after TP1 fills.
    "STYLE_ATR_MULTS": {
        "scalp": {
            # H1 ATR ref. Industry min 1.0–1.5× ATR. Previous 0.50 (6 pip SL on EURUSD) was
            # sub-spread on ECN brokers and guaranteed noise stop-outs.
            "forex":     {"sl": 1.00, "tp1": 1.50, "tp2": 2.50},
            "crypto":    {"sl": 1.20, "tp1": 1.80, "tp2": 3.00},
            "stock":     {"sl": 1.00, "tp1": 1.50, "tp2": 2.50},
            # Broad ETFs (SPY/QQQ/IWM/EEM/sector ETFs) are smoother than single stocks.
            "etf":       {"sl": 0.80, "tp1": 1.30, "tp2": 2.20},
            # Bond ETFs (TLT) — lowest realised vol, tightest stops.
            "etf_bond":  {"sl": 0.60, "tp1": 1.00, "tp2": 1.80},
            "commodity": {"sl": 1.20, "tp1": 1.80, "tp2": 3.00},
            "index":     {"sl": 1.00, "tp1": 1.50, "tp2": 2.50},
        },
        "intraday": {
            # H4 ATR ref. Industry min 1.5–2.0× ATR. Previous 0.75 (30 pip SL on EURUSD)
            # absorbable by a single 5-minute news spike.
            # tp2 set to 4.50 (3.0 RR with sl=1.50) so swing tp2 is strictly wider.
            "forex":     {"sl": 1.50, "tp1": 3.00, "tp2": 4.50},
            "crypto":    {"sl": 1.50, "tp1": 3.00, "tp2": 4.50},
            "stock":     {"sl": 1.50, "tp1": 3.00, "tp2": 4.50},
            "etf":       {"sl": 1.20, "tp1": 2.40, "tp2": 3.60},
            "etf_bond":  {"sl": 0.90, "tp1": 1.80, "tp2": 2.70},
            "commodity": {"sl": 2.00, "tp1": 4.00, "tp2": 6.00},
            "index":     {"sl": 1.50, "tp1": 3.00, "tp2": 4.50},
        },
        "swing": {
            # D1 ATR ref for all (crypto now also D1 — see LEVEL_ATR_PRIORITY below).
            # Crypto sl reduced from 2.0 to 1.5 because previous H4-based 2.0× produced
            # SLs of 400–600+ pips on altcoins (e.g. XRP ~$0.57 with H4 ATR ~$0.022).
            # All swing sl values ≥ 1.80 to keep scalp < intraday < swing ordering
            # with the same atr reference (intraday sl = 1.50).
            "forex":     {"sl": 1.80, "tp1": 3.00, "tp2": 5.00},
            "crypto":    {"sl": 1.50, "tp1": 2.50, "tp2": 4.00},
            "stock":     {"sl": 1.80, "tp1": 3.00, "tp2": 5.00},
            "etf":       {"sl": 1.40, "tp1": 2.40, "tp2": 4.00},
            "etf_bond":  {"sl": 1.10, "tp1": 1.80, "tp2": 3.00},
            "commodity": {"sl": 1.80, "tp1": 3.00, "tp2": 5.00},
            "index":     {"sl": 1.80, "tp1": 3.00, "tp2": 5.00},
        },
    },
    "LEVEL_ATR_PRIORITY": {
        "default": {
            "scalp": ["H1", "H4", "D1"],
            "intraday": ["H4", "H1", "D1"],
            "swing": ["D1", "H4", "H1"],
        },
        "crypto": {
            "scalp": ["H1", "H4", "D1"],
            "intraday": ["H4", "H1", "D1"],
            # Changed from ["H4","D1","H1"]: crypto swing now uses D1 ATR first,
            # consistent with all other asset classes. Coordinated with sl reduction
            # from 2.0→1.5 so the wider D1 ATR doesn't produce larger stops.
            "swing": ["D1", "H4", "H1"],
        },
    },
    # Regime scaling factors applied inside calc_levels (indicators.py).
    # Applied multiplicatively to sl_mult, tp1_mult, tp2_mult.
    # Reduced HIGH_VOLATILITY from 1.35 to 1.15 — the old 35% SL inflation compounded
    # with wide base multipliers to produce structurally absurd SL distances on altcoins.
    # Reduced TRENDING from 1.25 to 1.10 — slight breathing room is sufficient.
    # Exposed here (not hardcoded in indicators.py) so yaml overrides are possible.
    "CALC_LEVELS_REGIME_FACTOR": {
        0: 1.10,   # TRENDING — slight breathing room for trend continuation
        1: 1.00,   # RANGING — base multipliers apply unchanged
        2: 1.15,   # HIGH_VOLATILITY — modest widening; was 1.35 (too aggressive)
        3: 0.90,   # LOW_VOLATILITY — tighter stops in compressed environments
    },
    # Rolling windows for normalization (bars)
    "NORMALIZATION_LOOKBACK": {
        "crypto": 300,
        "forex": 400,
        "commodity": 300,
        "stock": 350,
        "index": 350,
    },
    # Realized volatility lookback and annualization factors (per asset class)
    "REALIZED_VOL_LOOKBACK": 30,
    "REALIZED_VOL_ANNUALIZATION": {
        "crypto": 365,
        "forex": 252,
        "stock": 252,
        "index": 252,
        "commodity": 252,
    },
    "ADX_TREND_MIN_CLASS": {
        "crypto": 15,
        "forex": 20,
        "commodity": 25,
        "stock": 25,
        "index": 25,
    },
    "COUNTER_TREND_PEN": {
        "crypto": -1.0,
        "forex": -1.0,
        "commodity": -1.0,
        "stock": -1.0,
        "index": -1.0,
    },
    # Per-asset-class RSI bounds calibrated to observed volatility:
    #   Crypto: 80/20 — crypto trends are stronger and more persistent; 70/30
    #           fires too often in normal bull markets, overstating momentum.
    #   Forex: 70/30 — standard TA-Lib (confirmed OANDA/LiteFinance/Altrady 2024).
    #   Commodity: 75/25 — gold/oil have sharper reversals than forex.
    #   Stock/Index: 70/30 — standard equity regime.
    "RSI_BOUNDS": {
        "crypto": {"ob": 80, "os": 20},
        "forex": {"ob": 70, "os": 30},
        "commodity": {"ob": 75, "os": 25},
        "stock": {"ob": 70, "os": 30},
        "index": {"ob": 70, "os": 30},
    },
    "MACRO_LOOKBACK": {
        "crypto": 15,
        "forex": 30,
        "commodity": 50,
        "stock": 50,
        "index": 50,
    },
    "WEINSTEIN_LOOKBACK": {
        "crypto": 60,
        "forex": 100,
        "commodity": 150,
        "stock": 150,
        "index": 150,
    },
    # Stage 4.2: BT_MIN / BT_MIN_GROUP / BACKTEST_USE_BT_MIN_THRESHOLDS deleted.
    # Single source of truth: scoring.py profile/pair/group resolver plus 3-tier fallback.
    "RESEARCH_MODE": False,
    "BACKTEST_EVENT_RISK_GATING": False,
    "BACKTEST_SENTIMENT_GATING": False,
    "ASE_BT_ENABLED": True,
    "ASE_BT_LOOKBACK_DAYS": 365,
    "ASE_BT_HORIZONS": ["intraday", "swing"],
    "ASE_ARBITRATION": {
        "MODE": "weighted",
        "STRENGTH_FLOOR": 0.3,
    },
    "ASE_MONITOR": {
        "MIN_SAMPLES": 5,
        "MAX_LIVE_ROWS": 200,
    },
    "ASE_PAYOFF": {
        "WIN_QUANTILE": "q50",
        "REALIZED_CAP_ENABLED": False,
        "REALIZED_CAP": 1.0,
        "USE_REALIZED_EXIT_R": True,
        "REALIZED_MIN_TRADES": 5,
    },
    "ASE_CARRY": {
        "CVR_SCALE": {"forex": 10.0, "crypto": 6.0, "default": 10.0},
        "SLOPE_ENABLED": False,
    },
    "ASE_INTRADAY_FAMILIES": ["forex", "crypto", "commodity"],
    "ASE_LAYER1_VOL_REGIME_FILTER": {
        "ENABLED": False,
        "EXTREME_ORDINAL": 2,
        "MIN_STRENGTH": 0.5,
    },
    "ASE_XSEC_CONTINUOUS": False,
    "ASE_XSEC": {"Z_CAP": 2.0},
    "ASE_FEATURES_V2_ENABLED": False,
    "ASE_MICROSTRUCTURE": {"ENABLED": False},
    # PHASE 3: Engine C Backtest Exit Controls - explicit config for MAX_HOLD and BE parameters
    "ENGINE_C_BT_EXIT": {
        "forex": {
            "intraday": {"max_hold_bars": 30, "be_arm_rr": 1.5, "be_min_target_rr": 2.0},
            "swing": {"max_hold_bars": 60, "be_arm_rr": 1.5, "be_min_target_rr": 2.0},
            "scalp": {"max_hold_bars": 12, "be_arm_rr": 1.5, "be_min_target_rr": 2.0},
        },
        "crypto": {
            "intraday": {"max_hold_bars": 40, "be_arm_rr": 1.5, "be_min_target_rr": 2.0},
            "swing": {"max_hold_bars": 80, "be_arm_rr": 1.5, "be_min_target_rr": 2.0},
            "scalp": {"max_hold_bars": 12, "be_arm_rr": 1.5, "be_min_target_rr": 2.0},
        },
        "stock": {
            "intraday": {"max_hold_bars": 24, "be_arm_rr": 1.5, "be_min_target_rr": 1.5},
            "swing": {"max_hold_bars": 50, "be_arm_rr": 1.5, "be_min_target_rr": 1.5},
            "scalp": {"max_hold_bars": 8, "be_arm_rr": 1.5, "be_min_target_rr": 1.5},
        },
        "commodity": {
            "intraday": {"max_hold_bars": 24, "be_arm_rr": 1.5, "be_min_target_rr": 1.8},
            "swing": {"max_hold_bars": 50, "be_arm_rr": 1.5, "be_min_target_rr": 1.8},
            "scalp": {"max_hold_bars": 10, "be_arm_rr": 1.5, "be_min_target_rr": 1.8},
        },
        "index": {
            "intraday": {"max_hold_bars": 24, "be_arm_rr": 1.5, "be_min_target_rr": 1.8},
            "swing": {"max_hold_bars": 50, "be_arm_rr": 1.5, "be_min_target_rr": 1.8},
            "scalp": {"max_hold_bars": 10, "be_arm_rr": 1.5, "be_min_target_rr": 1.8},
        },
    },
    # Full-scan cross-sectional quantile (see scanner.compute_scan_quantile_floors).
    "SCAN_QUANTILE_ENABLED": False,
    "SCAN_QUANTILE_MIN_SAMPLES": 5,
    "SCAN_QUANTILE_EXCLUDE_TYPES": ["crypto"],
    "SCAN_QUANTILE_TOP_FRACTION": {
        "default": 0.20,
        "crypto": 0.18,
        "forex": 0.22,
        "stock": 0.15,
        "index": 0.15,
        "commodity": 0.18,
    },
    "CALIBRATION_DIAGNOSTICS_ENABLED": False,
    "CALIBRATION_DIAGNOSTICS_PATH": "logs/calibration_diagnostics/calibration_events.jsonl",
    "RESEARCH_VOL_TARGETING_ENABLED": False,
    "RESEARCH_VOL_TARGET_ANNUALIZED": 0.12,
    "RESEARCH_VOL_TARGET_MAX_MULTIPLIER": 1.50,
    "RESEARCH_VOL_TARGET_MIN_MULTIPLIER": 0.25,
    "RESEARCH_VOL_TARGET_LOOKBACK": 20,
    # MIN_CONFLUENCE_GROUP removed. Engine A live/backtest thresholds are resolved
    # in scoring.py from profile overrides, pair/group config, then 3-tier fallback.
    # Factor scoring gates — see factor_scoring.py
    "ENGINE_A_PAIR_THRESHOLDS": {},
    "ENGINE_A_SCORE_GROUP_THRESHOLDS": {
        "default": 1.5,
        "forex_majors": 2.1,
        "forex_crosses": 2.1,
        "forex_other": 2.1,
        "forex_exotics": 1.7,
        "crypto_btc": 2.0,
        "crypto_eth": 2.0,
        "crypto_alt_majors": 2.0,
        "crypto_doge": 2.0,
        "crypto_other": 2.2,
        "precious_trackers": 1.5,
        "energy_oil": 1.5,
        "nat_gas": 2.0,
        "copper": 1.5,
        "pgm_metals": 1.5,
        "base_metals": 1.5,
        "softs": 1.7,
        "commodity_other": 1.5,
        "us_indices_trackers": 1.5,
        "eu_indices": 1.5,
        "asian_indices": 1.5,
        "index_other": 1.5,
        "us_stock_single": 1.5,
        "bond_tlt": 1.5,
        "smallcap_em_etf": 1.5,
        "stock_other": 1.5,
        "unknown": 1.5,
    },
    "ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED": True,
    "ENGINE_A_PAIR_MIN_CONFIDENCE": {},
    "ENGINE_A_CRYPTO_LATE_TREND_CONF_MULT": 0.85,
    "ENGINE_A_SCORE_GROUP_MIN_CONFIDENCE": {
        "default": 0.55,
        "forex_majors": 0.52,
        "forex_crosses": 0.52,
        "forex_other": 0.52,
        "forex_exotics": 0.48,
        "crypto_btc": 0.58,
        "crypto_eth": 0.58,
        "crypto_alt_majors": 0.63,
        "crypto_doge": 0.61,
        "crypto_other": 0.63,
        "precious_trackers": 0.55,
        "energy_oil": 0.56,
        "nat_gas": 0.58,
        "copper": 0.55,
        "pgm_metals": 0.55,
        "base_metals": 0.55,
        "softs": 0.56,
        "commodity_other": 0.55,
        "us_indices_trackers": 0.54,
        "eu_indices": 0.54,
        "asian_indices": 0.54,
        "index_other": 0.54,
        "us_stock_single": 0.54,
        "bond_tlt": 0.54,
        "smallcap_em_etf": 0.54,
        "stock_other": 0.54,
        "unknown": 0.55,
    },
    "ENGINE_A_ATR_LEVEL_CLASS_BY_DISPLAY": {
        "SPY": "etf",
        "QQQ": "etf",
        "TQQQ": "etf",
        "SQQQ": "etf",
        "GLD": "etf",
        "TLT": "etf_bond",
        "IWM": "etf",
        "EEM": "etf",
        "DIA": "etf",
        "GDX": "etf",
        "SOXX": "etf",
        "XLF": "etf",
        "XLE": "etf",
        "SLV": "etf",
        "USO": "etf",
    },
    "ENGINE_A_ATR_LEVEL_CLASS_BY_SCORE_GROUP": {},
    "ENGINE_A_COT_ADDON_ASSET_TYPES": ["commodity", "index", "stock"],
    "ADX_MISSING_BOTH_ABORT": True,
    "FACTOR_MIN_DIRECTIONAL": 0.25,  # Skip if abs(dir_score) < this (near-directionless signal)
    "FACTOR_DIRECTIONAL_SOFT_SPAN": 0.20,  # Smooth transition width for directional confidence
    "FACTOR_MIN_DIRECTIONAL_CRYPTO": 0.20,
    "FACTOR_DIRECTIONAL_SOFT_SPAN_CRYPTO": 0.30,
    "FACTOR_FOREX_SESSION_MULT": {
        "ENABLED": False,
        "BY_QUALITY": {"high": 1.0, "medium": 0.95, "low": 0.85},
    },
    # Engine A class/score_group overrides. Disabled by default so the unified
    # factor formula keeps historical behavior unless a class is explicitly tuned.
    "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED": False,
    "MT5_INTRADAY_CALENDAR_GAP_GRACE_BUCKETS": {"H4": 30, "H1": 120, "default": 24},
    "MT5_INTRADAY_CALENDAR_GAP_TYPES": ["stock", "index", "commodity", "etf", "etf_bond"],
    "ENGINE_A_MIN_D1_BARS": 220,
    "ENGINE_A_MIN_H4_BARS": 50,
    "ENGINE_A_MIN_H1_BARS": 50,
    "ENGINE_A_V3_BACKTEST": {
        "SPREAD_BPS": 2.0,
        "COMMISSION_BPS": 1.0,
        "SLIPPAGE_BPS": 1.0,
        "SWAP_BPS_PER_DAY": 0.5,
        "MAX_HOLD_BARS": 24,
    },
    "ENGINE_A_V3_DEMO_UNVALIDATED_ENABLED": False,
    "ENGINE_A_V3_PROFILE_TTL_DAYS": 90,
    # Best-effort JSONL journal of live V3 signals for offline live-vs-backtest
    # reconciliation (engine_a_v3/shadow_journal.py). Diagnostic only: gates
    # nothing, blocks nothing, fails silently. Off by default (disk growth).
    "ENGINE_A_V3_SHADOW_JOURNAL_ENABLED": False,
    # Per-group thesis selector for the v3 forex specialist. "trend" keeps the
    # trend-pullback/breakout setups; "mean_reversion" swaps in the range-fade
    # (counter-trend) specialist. Reversible; default preserves existing behavior.
    "ENGINE_A_V3_FOREX_THESIS": "trend",
    "ENGINE_A_V3_SETUP_UPGRADE": {
        "ENABLED": True,
        "MIN_CONFLUENCE_FRAC": 0.75,
    },
    "ENGINE_A_V3_MEAN_REVERSION": {
        "Z_ENTRY": 1.5,
        "EFF_MAX": 0.35,
        "LOOKBACK": 20,
    },
    "ENGINE_A_V3_DIRECTIONAL_RAMP": {
        "ENABLED": True,
        "APPLY_TO_CONFLUENCE": True,
    },
    "ENGINE_A_V3_QUANT_SESSION_GATE": {
        "ENABLED": True,
        "MIN_SCORE": 0.40,
    },
    "ENGINE_A_V3_MR_OPPOSITION_GUARD": {
        "ENABLED": True,
        "MIN_OPPOSE_QUALITY": 0.55,
    },
    "ENGINE_A_V3_STRUCTURAL_SL_FLOOR_ATR_MULT": 0.35,
    "ENGINE_A_V3_LEGACY_FILTERS": {
        "ENABLED": True,
        "FOREX_EMA_CLUSTER": True,
        "CRYPTO_LATE_TREND": True,
        "BLOCKED_TREND_STATES": True,
    },
    "ENGINE_A_V3_COMMODITY_SETUP_PARAMS": {},
    "ENGINE_A_V3_EQUITY_VOLUME_FLOOR": {
        "ENABLED": True,
        "MIN_QUALITY": 0.15,
    },
    "ENGINE_A_V3_CRYPTO_DERIV_GUARD": {
        "ENABLED": False,
        "REQUIRE_FRESH": True,
    },
    "ENGINE_A_V3_SUBSYSTEMS": {
        "ENABLED": True,
        "WEIGHTS_BY_FAMILY": {},
    },
    "ENGINE_A_V3_MOMENTUM_BLEND": {
        "ENABLED": True,
        "RSI_WEIGHT": 0.35,
        "DI_WEIGHT": 0.35,
        "MACD_SLOPE_WEIGHT": 0.30,
    },
    "ENGINE_A_V3_VOLATILITY_GATING": {
        "ENABLED": True,
        "TP2_R_MIN": 1.5,
        "TP2_R_MAX": 2.5,
        "ATR_PCT_LOW": 0.3,
        "ATR_PCT_HIGH": 0.9,
    },
    "ENGINE_A_V3_TREND_HEALTH": {
        "ENABLED": True,
        "STALE_ALIGNMENT_BARS": 8,
        "BAR_PENALTY": 0.03,
        "FLOOR": 0.75,
        "ADX_WEAKENING_PENALTY": 0.15,
    },
    "ENGINE_A_SCORING_PROFILE": {
        "ENABLED": True,
        "DEFAULT_BY_CLASS": {
            "default": {
                "trend_layers": [
                    {"tf": "D1", "weight_key": "d1_ema_trend", "fast_ema": "ema21", "slow_ema": "ema200"},
                    {"tf": "H4", "weight_key": "h4_ema_trend", "fast_ema": "ema21", "slow_ema": "ema50"},
                    {"tf": "H1", "weight_key": "ema_trend", "fast_ema": "ema21", "slow_ema": "ema50"},
                ],
                "momentum_tf": "H4",
                "regime_tf": "H4",
            },
        },
        "BY_STYLE": {
            "swing": {
                "trend_weights": {"d1_ema_trend": 0.50, "h4_ema_trend": 0.30, "ema_trend": 0.20},
                "execution_tf": "D1",
                "chart_tf": "D1",
            },
            "intraday": {
                "trend_weights": {"d1_ema_trend": 0.42, "h4_ema_trend": 0.33, "ema_trend": 0.25},
                "execution_tf": "H4",
                "chart_tf": "H4",
            },
            "scalp": {
                "trend_weights": {"d1_ema_trend": 0.25, "h4_ema_trend": 0.35, "ema_trend": 0.40},
                "execution_tf": "H1",
                "chart_tf": "H1",
            },
        },
        "BY_SCORE_GROUP": {
            "energy_oil": {
                "trend_layers": [
                    {"tf": "H4", "weight_key": "h4_ema_trend", "fast_ema": "ema21", "slow_ema": "ema50"},
                    {"tf": "H1", "weight_key": "ema_trend", "fast_ema": "ema21", "slow_ema": "ema50"},
                ],
                "trend_weights": {"h4_ema_trend": 0.55, "ema_trend": 0.45},
                "momentum_tf": "H4",
                "regime_tf": "H4",
            },
            "commodity_other": {
                "trend_layers": [
                    {"tf": "H4", "weight_key": "h4_ema_trend", "fast_ema": "ema21", "slow_ema": "ema50"},
                    {"tf": "H1", "weight_key": "ema_trend", "fast_ema": "ema21", "slow_ema": "ema50"},
                ],
                "trend_weights": {"h4_ema_trend": 0.55, "ema_trend": 0.45},
                "momentum_tf": "H4",
                "regime_tf": "H4",
            },
        },
    },
    "ENGINE_A_FACTOR_WEIGHTS_BY_CLASS": {},
    "ENGINE_A_DIRECTIONAL_RAMP_BY_CLASS": {},
    # Minimum weighted vote margin before Engine A emits a concrete direction.
    # 0.0 = disabled (legacy exact-tie only).
    "ENGINE_A_DIRECTION_MIN_MARGIN": 0.05,
    # Contra-trend intermarket/BTC conflict surfacing (default off).
    "ENGINE_A_DIRECTION_INTERMARKET_CONFLICT_GUARD_ENABLED": True,
    "ENGINE_A_DIRECTION_INTERMARKET_CONFLICT_MIN_SCORE": -0.12,
    "ENGINE_A_DIRECTION_BTC_CONFLICT_GUARD_ENABLED": True,
    "ENGINE_A_DIRECTION_BTC_CONFLICT_MIN_CORRELATION": 0.80,
    # EMA cross gap when no previous bar is available (default 0.001 = 0.1%).
    "ENGINE_A_EMA_CROSS_MIN_GAP": 0.001,
    # Advisory reversal-pending when fresh crosses align before 2-bar confirm.
    "ENGINE_A_REVERSAL_PENDING_ENABLED": True,
    "ENGINE_A_REVERSAL_PENDING_MIN_TF": 2,
    "ENGINE_A_ADDON_UNSUPPORTED_SPLIT_BY_CLASS": {},
    "ENGINE_A_CONVICTION_FLOOR_BY_CLASS": {},
    "ENGINE_A_ADX_SOURCE_BY_CLASS": {},
    "ENGINE_A_DI_ALIGNMENT_MULT_BY_CLASS": {},
    "ENGINE_A_DI_ALIGNMENT_GAP_THRESHOLDS": {"BALANCED": 5.0, "OPPOSED": 15.0},
    "ENGINE_A_RESEARCH_LAB_FACTORS_BY_CLASS": {},
    "ENGINE_A_VOLATILITY_REGIME_ADJUSTMENT_ENABLED": False,
    "ENGINE_A_VOLATILITY_REGIME_MULTIPLIERS": {},
    "ENGINE_A_VOLATILITY_REGIME_MULT_BOUNDS": {"min": 0.85, "max": 1.15},
    "CANDLE_UNDERSTANDING_ENABLED": True,
    "CANDLE_UNDERSTANDING_REPORT_ONLY": True,
    "CANDLE_UNDERSTANDING_LIVE_WEIGHT": 0.0,
    "CANDLE_UNDERSTANDING": {
        "HA_TREND_STREAK_MIN": 3,
        "SWEEP_WICK_MIN_RANGE_RATIO": 0.25,
        "ABSORPTION_VOL_RATIO_MIN": 1.75,
        "ABSORPTION_MAX_BODY_RATIO": 0.35,
    },
    "ENGINE_A_CANDLE_TIMEFRAME_CONFIDENCE": {
        "D1": 1.00,
        "H4": 0.85,
        "H1": 0.65,
        "M15": 0.45,
        "M5": 0.30,
        "M1": 0.20,
    },
    "ENGINE_D_CANDLE_EXECUTION_CONFIDENCE": {
        "M15": 0.80,
        "M5": 1.00,
        "M3": 1.00,
        "M1": 0.90,
    },
    "ENGINE_A_EQUITY_SESSION_LIQUIDITY_WEIGHTING": {
        "ENABLED": False,
        "ASSET_TYPES": ["stock", "index", "etf", "etf_bond"],
        "ACTIVE_UTC_START_HOUR": 13.5,
        "ACTIVE_UTC_END_HOUR": 20.0,
        "ACTIVE_MULT": 1.02,
        "OFF_HOURS_MULT": 0.98,
    },
    "ENGINE_A_STRUCTURE_CONTEXT_ENABLED": False,
    "ENGINE_A_CORRELATED_OVERLAY_GUARD_ENABLED": True,
    "ENGINE_A_CORRELATED_OVERLAY_MAX_TOTAL_UPLIFT": 0.20,
    "ENGINE_A_FOREX_EMA_CLUSTER_DIAGNOSTICS_ENABLED": True,
    "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED": True,
    "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MODE": "multiplier",
    "ENGINE_A_FOREX_EMA_CLUSTER_MAX_SCORE_CAP": 2.0,
    "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MULT": 0.85,
    "ENGINE_A_FOREX_EMA_CLUSTER_MIN_COHERENCE_FOR_NO_PENALTY": 0.85,
    "ENGINE_A_FOREX_EMA_CLUSTER_REQUIRE_ADX_RISING_FOR_NO_PENALTY": False,
    "ENGINE_A": {
        "structure_first_entry": {
            "enabled": False,
            "lookback_bars": 5,
            "require_bos": True,
            "require_choch": False,
        },
    },
    "FACTOR_ADDON_CONFIRM_MAX": 0.20,
    "FACTOR_ADDON_AGAINST_MIN": -0.25,
    "CARRY_STATIC_RATES_AS_OF": "2026-06-07",
    "CARRY_STATIC_RATES_MAX_AGE_DAYS": 30,
    "FACTOR_FUNDING_USE_ZSCORE": False,
    "FACTOR_FUNDING_ZSCORE_WINDOW": 30,
    "FACTOR_FUNDING_Z_THRESHOLD": 1.0,
    "FACTOR_FUNDING_ZSCORE_MIN_STD": 1e-6,
    "FACTOR_CRYPTO_ADDON_COMBO_CONFIRM_CAP": 0.25,
    "FACTOR_CRYPTO_ADDON_COMBO_AGAINST_CAP": -0.20,
    "EODHD_COMMODITY_TICKERS": {
        "WTI Oil": "WTICOUSD.FOREX",
        "Brent Oil": "BRENTOIL.FOREX",
        "Nat Gas": "NATGASUSD.FOREX",
        "Copper": "XCUUSD.FOREX",
        "Aluminium": "ALI.COMM",
        "Lead": "PB.COMM",
        "Nickel": "NI.COMM",
        "Zinc": "ZNC.COMM",
        "Cattle": "LE.COMM",
        "Cocoa": "CC.COMM",
        "Coffee": "KC.COMM",
        "Corn": "ZC.COMM",
        "Cotton": "CT.COMM",
        "Soybeans": "ZS.COMM",
        "Sugar": "SB.COMM",
        "Wheat": "ZW.COMM",
    },
    "CRYPTO_LIVE_MICROSTRUCTURE_SCORING_ENABLED": True,
    "REGIME_SMOOTHING_BARS": 3,  # Consecutive bars required before committing to a regime change
    # Per-indicator weights within each factor group (multiplied with correlation weights).
    # Missing keys default to 1.0. Set to 0.0 to disable an indicator.
    "INDICATOR_WEIGHTS": {
        "trend": {
            "crypto": {"d1_ema_trend": 0.5, "h4_ema_trend": 0.3, "ema_trend": 0.2},
            "forex": {"d1_ema_trend": 0.5, "h4_ema_trend": 0.3, "ema_trend": 0.2},
            "commodity": {
                "d1_ema_trend": 0.34,
                "h4_ema_trend": 0.33,
                "ema_trend": 0.33,
            },
            "stock": {"d1_ema_trend": 0.4, "h4_ema_trend": 0.35, "ema_trend": 0.25},
            "index": {"d1_ema_trend": 0.4, "h4_ema_trend": 0.35, "ema_trend": 0.25},
        },
        "momentum": {
            "default": {"rsi_z": 0.6, "macdLine_z": 0.4},
            "crypto": {
                "rsi_z": 0.5,
                "macdLine_z": 0.3,
                "volume_momentum_spread": 0.2,
            },
        },
    },
    # Indicator correlation control
    "INDICATOR_CORRELATION_ENABLED": False,  # Expensive O(n²); enable manually for live deep analysis
    "INDICATOR_CORRELATION_WINDOW": 200,
    # Microstructure datafeed configuration
    "MICROSTRUCTURE": {
        "exchanges": ["binance", "bybit"],
        "orderbook_depth": {"binance": 20, "bybit": 50},
        "update_interval_seconds": 1,
        "liquidity_wall_threshold": 5,
    },
    # Log Engine C ALIGNED+tradeable rows to shadow_signals (no broker); Performance tab reads them.
    "SHADOW_LEDGER_ENABLED": True,
    "MICROSTRUCTURE_FEEDS_ENABLED": True,
    "MICROSTRUCTURE_BYBIT_FEEDS_ENABLED": True,
    "MICROSTRUCTURE_BINANCE_FEEDS_ENABLED": False,
    "MARKET_DATA_WS_SSL_VERIFY": True,
    "BYBIT_TIME_SYNC_ENABLED": False,
    "BYBIT_RECV_WINDOW_MS": 30000,
    "CRYPTO_LIVE_TICK_PROVIDER_PREFERENCE": "bybit_ws",
    "BYBIT_WS_ENABLED": True,
    "BYBIT_REST_FALLBACK_ENABLED": True,
    "BYBIT_WS_STALE_AFTER_SECONDS": 10,
    "BYBIT_REST_FALLBACK_WARN": True,
    "PAIR_PROFILES": {},
    "BT_AUTO_TOGGLE": False,  # If False, backtest will never enable/disable live pairs
    "BT_PERCENTILE_FILTER": False,  # If False, disable rolling percentile floor in backtest
    # ── Execution engine ────────────────────────────────────────────────────
    "REAL_ORDERS_ALLOWED": False,
    "PAPER_SOAK": {
        "ENABLED": True,
        "REAL_ORDERS_ALLOWED": False,
    },
    "EXECUTOR_MODE": "paper",
    "EXECUTION_ENABLED": True,  # Master switch — enabled for demo live-level testing
    "EXECUTION_SINGLE_LEG_ONLY": True,  # One broker position per symbol per execution
    # Re-fetch H1/H4/D1 candle metadata right before risk/guardian during execute paths.
    "QUICK_EXEC_PREFETCH_CANDLE_META": False,
    # Rebuild candleFreshness/consistency/dataFreshness before risk (poisoned client fields).
    "EXECUTION_HYDRATE_CANDLE_QUALITY": True,
    # MT5 D1: one read-only refetch for small stale-history windows before risk evaluates freshness.
    "MT5_D1_FETCH_RETRY_ENABLED": True,
    "MT5_D1_FETCH_RETRY_MAX_LAG": 7,
    # MT5 D1: small multi-bucket lag vs wall clock (weekends/holidays) downgraded to non-blocking severity.
    "MT5_D1_CALENDAR_GAP_GRACE_BUCKETS": 4,
    # Legacy alias — used if MT5_D1_CALENDAR_GAP_GRACE_BUCKETS is omitted in YAML.
    "FOREX_D1_MULTI_BUCKET_CALENDAR_GAP_GRACE_BUCKETS": 4,
    # Types that must not use D1 gap grace (24/7 markets).
    "MT5_D1_CALENDAR_GAP_EXCLUDE_TYPES": ["crypto"],
    "AUTO_EXECUTE": False,  # Auto-execute after AI grade (manual click only when False)
    "RISK_ENGINE_ENABLED": True,
    "MT5_EXECUTION_ENABLED": True,
    "BYBIT_EXECUTION_ENABLED": True,
    "FX_FACTOR_SCANNER_ENABLED": True,
    "FX_FACTOR_EXECUTION_ENABLED": True,
    "FX_FACTOR_ALLOW_LIVE_EXECUTION": True,
    "FX_FACTOR_REQUIRE_VALIDATION_FOR_EXECUTION": True,
    "FX_FACTOR_STRICT_DATA_DEFAULT": True,
    "FX_FACTOR_SCAN_INCLUDE_BLOCKED_DEFAULT": True,
    "FX_FACTOR_SCAN_REFRESH_DECISIONS_DEFAULT": True,
    "BYBIT_LEVERAGE": 1,
    # eToro adapter scaffold (default-safe/off; no live wiring by default)
    "ETORO": {
        "ENABLED": False,
        "DEMO_MODE": True,
        "BASE_URL": "https://public-api.etoro.com",
        "REQUEST_TIMEOUT_SEC": 10,
    },
    "AUTO_EXECUTE_MIN_SCORE": 8.0,  # Minimum confluence score for auto-execute
    "AUTO_EXECUTE_MIN_GRADE": "B",  # Minimum AI grade for auto-execute
    "MAX_PORTFOLIO_HEAT": 0.06,  # 6% total risk across all positions
    "MAX_OPEN_POSITIONS": 10,  # Max simultaneous open trades
    "OPEN_TRADES": {
        # When false (default), /api/open-trades-timed skips candle-based SL recompute
        # on each poll so broker P&L refreshes stay fast. Use /api/position-sl-diagnostic
        # for on-demand Engine A SL recompute.
        "SL_RECOMPUTE_ON_POLL": False,
    },
    "MAX_CORRELATED_POSITIONS": 10,  # Max positions in same correlation cluster
    "SIGNAL_MAX_AGE_SEC": 300,  # Reject signals older than 5 minutes (matches config.yaml)
    "MAX_RISK_PER_TRADE": 0.03,  # Hard cap: never risk > 3% on single trade
    "MAX_SL_PCT": {
        "forex":     0.025,
        "crypto":    0.08,
        "commodity": 0.04,
        "index":     0.04,
        "stock":     0.08,
        "etf":       0.08,
        "etf_bond":  0.08,
    },
    "MAX_SL_PCT_SCORE_GROUP_OVERRIDES": {},
    "MAX_SL_PCT_SYMBOL_OVERRIDES": {},
    # Bybit linear USDT perps reject TP triggers below ~10% of mark (retCode 10001).
    "BYBIT_MIN_TP_PCT_OF_MARK": 0.10,
    "MAX_TP_PCT": {
        "crypto": 0.50,
        "forex": 0.50,
        "commodity": 0.50,
        "index": 0.50,
        "stock": 0.50,
        "etf": 0.50,
        "etf_bond": 0.50,
        "default": 0.50,
    },
    "DATA_FRESHNESS_GATES": {
        "WARN_ON_STALE_SCAN": True,
        "BLOCK_EXECUTION_ON_STALE": True,
        "BLOCK_TIMEFRAMES": ["H1", "H4", "D1"],
        "BLOCK_SEVERITIES": [
            "missing_current_bucket",
            "stale_1_bucket",
            "stale_multi_bucket",
            "error_path_mismatch",
            "error_offset_mismatch",
        ],
    },
    # Paper fail-closed live-quote / broker-tick / ATR / spread-drift gates.
    "LIVE_PRICE_MAX_AGE_SEC": {
        "forex": 60,
        "commodity": 60,
        "index": 60,
        "stock": 90,
        "etf": 90,
        "crypto": 15,
    },
    "LIVE_PRICE_BLOCK_EXECUTION": True,
    "LIVE_PRICE_BLOCK_EXECUTION_ON_UNKNOWN_AGE": True,
    "MAX_BROKER_TICK_AGE_SEC": {"mt5": 5, "bybit": 3},
    "MAX_EXECUTION_SPREAD_PCT": {
        "forex": 0.0005,
        "commodity": 0.0015,
        "index": 0.001,
        "stock": 0.002,
        "etf": 0.002,
        "crypto": 0.002,
    },
    "MAX_SIGNAL_DRIFT_PCT": {
        "forex": 0.0015,
        "commodity": 0.003,
        "index": 0.002,
        "stock": 0.003,
        "etf": 0.003,
        "crypto": 0.005,
    },
    "ATR_FRESHNESS": {
        "ENABLED": True,
        "BLOCK_EXECUTION_ON_STALE_ATR": True,
        "MAX_AGE_SECONDS": {
            "M1": 180,
            "M5": 600,
            "M15": 1800,
            "H1": 7200,
            "H4": 18000,
            "D1": 172800,
        },
    },
    "DRAWDOWN_REDUCE_THRESHOLD": 0.10,  # At 10% drawdown, halve position sizes
    "DRAWDOWN_STOP_THRESHOLD": 0.15,  # At 15% drawdown, reject ALL new trades
    "DRAWDOWN_STOP_ENABLED": True,  # Set false only for paper/debug — disables stop + size reduction
    # ── Auto-Trade Bot ────────────────────────────────────────────────────────
    "AUTO_TRADE_ENABLED": False,  # Master toggle (also togglable via UI/API)
    "AUTO_TRADE_MIN_SCORE": {  # Informational scan floor; live gate is AUTO_TRADE_MIN_CONVICTION
        "crypto": 2.0,
        "forex": 2.1,
        "commodity": 1.8,
        "stock": 1.8,
        "index": 1.8,
    },
    "AUTO_TRADE_MIN_CONVICTION": {  # Live auto-execute gate on executionConvictionEffective
        "default": 0.50,
    },
    "AUTO_TRADE_A_ONLY_WEIGHT": {
        "default": 0.60,
        "crypto": 0.60,
    },
    "ENGINE_A_SCAN_A_ONLY_AUTO_GATE_ENABLED": False,
    "AUTO_TRADE_MAX_DAILY": 2,  # Max auto-trades per calendar day (UTC)
    "AUTO_TRADE_MAX_PER_SCAN": 1,  # Max executions per single scan run
    "AUTO_TRADE_SIZING_OVERRIDE": 1.0,  # Legacy multiplier; volume follows EXECUTION_VOLUME.AUTO_TRADE_MODE
    "EXECUTION_VOLUME": {
        "DEFAULT_MODE": "min_lot",
        "AUTO_TRADE_MODE": "min_lot",
        "MANUAL_ALLOW_INCREASE": True,
        "MIN_LOT_OVERRIDE": {
            "forex": 0.01,
            "commodity": 0.01,
            "crypto": None,
            "stock": None,
        },
    },
    "AUTO_TRADE_SCAN_INTERVAL_MIN": 30,  # Scan every N minutes (30 = twice per hour)
    "AUTO_TRADE_SCHEDULER_MODE": "confirmed_close",
    "AUTO_TRADE_EXECUTION_GRACE_MIN": 120,
    "AUTO_TRADE_ALLOW_INTRABAR_REVIEW": True,
    "AUTO_TRADE_ALLOW_INTRABAR_EXECUTION": False,
    "AUTO_TRADE_SESSIONS": {  # Sessions per asset class; "always" = 24/7
        "forex": ["london", "new_york", "london_ny_overlap"],
        "crypto": ["always"],
        "stock": ["jse", "us_regular"],
        "commodity": ["always"],
        "index": ["always"],
    },
    "AUTO_TRADE_BLOCKED_TREND_STATES": {
        "default": ["DEAD RANGING", "RANGING"],
        "crypto": ["DEAD RANGING"],
        "forex": [],
        "commodity": ["DEAD RANGING", "RANGING"],
        "stock": ["DEAD RANGING", "RANGING"],
        "index": ["DEAD RANGING", "RANGING"],
    },
    # Engine A trend-state block list for live scan tier classification AND
    # backtest entry. Research 2026-04-18 (n=393, 4 crypto + 4 forex, intraday)
    # showed DEAD RANGING (WR 18.5%, avgR -0.59R) and DEVELOPING (WR 32.7%,
    # avgR -0.19R) are reliably unprofitable. Accepts a flat list (global)
    # or a per-asset-class dict with optional "default" fallback.
    "ENGINE_A_BLOCKED_TREND_STATES": ["DEAD RANGING", "DEVELOPING"],
    "AUTO_TRADE_BLOCKED_REGIMES": {
        "default": ["RANGING"],
        "crypto": [],
        "forex": [],
        "commodity": ["RANGING"],
        "stock": ["RANGING"],
        "index": ["RANGING"],
    },
    "SIGNAL_DEBATE_ENABLED": True,
    "SENTIMENT_GATE_ENABLED": True,
    "AUTO_TRADE_SENTIMENT_GATE_MODE": "severe_opposition_only",
    "SENTIMENT_BLOCK_THRESHOLD": 0.4,
    "SENTIMENT_ALIGN_THRESHOLD": 0.3,
    "SENTIMENT_API_FAIL_CLOSED": False,
    "SENTIMENT_GATE_SKIP_WHEN_NEWS_APPLIED": False,
    "EVENT_RISK_ENABLED": True,
    "EVENT_RISK_HOURS": 4,
    "EVENT_RISK_API_FAIL_CLOSED": False,
    "AUTO_TRADE_INTERMARKET_SEVERE_BLOCK": False,
    "AUTO_TRADE_CHART_AI_GATE_ENABLED": False,
    "AUTO_TRADE_CHART_AI_GATE_MODE": "advisory",
    "AUTO_TRADE_CHART_AI_MAX_AGE_SEC": 180,
    "AUTO_SCALP_ENABLED": False,
    "AUTO_SCALP_REQUIRE_FRESH_AI_ENTRY_NOW": True,
    "AUTO_SCALP_MAX_DAILY": 1,
    # ── AI Self-Learning ──────────────────────────────────────────────────────
    "LEARNING_ENABLED": True,  # Extract learning data after each trade closes
    "LEARNING_MIN_TRADES": 5,  # Min trades before context injected into AI
    "LEARNING_LOOKBACK_DAYS": 90,  # Days of history to query for context
    "META_ANALYSIS_ENABLED": True,  # Weekly meta-analysis via configured AI provider
    "EODHD_EARNINGS_CALENDAR_ENABLED": False,  # Disabled by default; unsupported on many EODHD plans
    "INSIDER_TRADING_DIAGNOSTIC_ENABLED": True,
    "FUNDAMENTALS_DIAGNOSTIC_ENABLED": True,
    # ── Engine B AI Controls ─────────────────────────────────────────────────
    "ENGINE_B_NEWS_CONTEXT_ENABLED": True,  # Feed news into Engine B AI advisory (not checklist)
    "ENGINE_B_ZONE_PERSISTENCE": False,  # Persist Engine B OB/FVG registry to zones.db
    "ENGINE_B_USE_FORMING_FOR_STRUCTURE": False,
    "ENGINE_B_USE_FORMING_FOR_TRIGGER": True,
    "AI_ON_DEMAND_ONLY": True,  # AI runs on user-initiated actions only, not auto-scans
    # ── Engine C B-side fallback controls ─────────────────────────────────────
    "ENGINE_C_B_ONLY_MULT": 0.65,  # Scale B-only conviction when A has no signal
    "ENGINE_C_B_CONFLICT_OVERRIDE_ENABLED": True,  # Allow strong B to override weak opposing A
    "ENGINE_C_B_CONFLICT_MIN_SCORE": 0.70,  # Minimum B normalized score for conflict override
    "ENGINE_C_A_CONFLICT_MAX_SCORE": 0.45,  # Max opposing A normalized score for B override
    "ENGINE_C_B_CONFLICT_PENALTY": 0.85,  # Penalty applied to B score during conflict override
    "ENGINE_C_EXEC_MIN_RR": 1.0,
    "ENGINE_C_A_DEFAULT_MAX_SCORE": 3.0,
    "ENGINE_C_A_DEFAULT_MAX_SCORE_BY_TYPE": {},
    "ENGINE_B_BT_STRUCTURE_GATE_ENABLED": True,
    "ENGINE_B_STRUCTURE_GATE_ENABLED": True,
    "ENGINE_B_SCAN_CONFIRMATION_GATE_ENABLED": False,
    # Scan-only: let Engine B re-check its own naked-structure direction when
    # the Engine A direction blocks B. Default-off to preserve historical scan behavior.
    "ENGINE_B_SCAN_INDEPENDENT_DIRECTION_ENABLED": False,
    # When both LONG and SHORT pass gates, require this score gap before emitting.
    # 0.0 = disabled (legacy max-score pick).
    "ENGINE_B_DIRECTION_MIN_SCORE_GAP": 0.5,
    # Block emit when independent structural opinion strongly opposes chosen side.
    "ENGINE_B_DIRECTION_INDEPENDENT_CONFLICT_GUARD_ENABLED": True,
    "ENGINE_B_DIRECTION_INDEPENDENT_CONFLICT_MIN_CONFIDENCE": "HIGH",
    "ENGINE_B_STRUCTURE_REQUIRE_ALIGN_OR_BOS_MTF": True,
    "ENGINE_B_ROOM_NO_WALL_REQUIRE_MTF": True,
    "ENGINE_B_MACRO_SHORT_HISTORY_FAIL_CLOSED": True,
    "ENGINE_B_CHOCH_INVALIDATION_BARS": 5,
    "ENGINE_B_INDEPENDENT_DIRECTION_SCORE_THRESHOLD": 0.15,
    "ENGINE_B_FOLLOW_THROUGH": {
        "ENABLED": True,
        "DIAGNOSTICS_ENABLED": True,
        "BLOCK_ENTRY_ON_TRAP": True,
        "MAX_BONUS": 1.5,
        "MIN_PENALTY": -0.5,
    },
    "ENGINE_B_FOLLOW_THROUGH_CONFIRMED_ONLY": True,
    "ENGINE_B_ZONE_PEAK_DISTANCE": {
        "H1": 3,
        "H4": 5,
        "D1": 5,
    },
    # Scan-only: surface passed B-only structures as watchlist rows when A is
    # below its scan threshold. This does not grant execution permission.
    "ENGINE_B_SCAN_B_ONLY_WATCHLIST_ENABLED": False,
    # Full-scan diagnostics: attach per-row engine_b_scan_gate_funnel payload (report-only).
    "ENGINE_B_SCAN_GATE_FUNNEL_ENABLED": True,
    # Persist funnel extracts + summaries under logs/engine_b_gate_funnel (fail-open).
    # Env ENGINE_B_SCAN_GATE_FUNNEL_PERSIST=0 disables, =1 forces on.
    "ENGINE_B_SCAN_GATE_FUNNEL_PERSIST_ENABLED": True,
    "ENGINE_B_SCAN_GATE_FUNNEL_OUTPUT_DIR": "logs/engine_b_gate_funnel",
    # Scan-only surfacing for Engine B structures that are blocked by the final trigger.
    # Default-off: execution and Engine B pass gates remain unchanged unless explicitly enabled.
    "ENGINE_B_STRUCTURE_READY_WATCHLIST": {
        "ENABLED": False,
        "MIN_SCORE_RATIO": 0.85,
        "REQUIRE_TRIGGER_MISSING": True,
        "REQUIRE_STRUCTURE_OK": True,
        "REQUIRE_LOCATION_CONTEXT": True,
        "FOREX_GATE_NEAR_MISS_ENABLED": False,
        "ASSET_GATE_NEAR_MISS_ENABLED": False,
        "FOREX_MIN_GATE_CONFIRMATIONS": 3,
    },
    # Default OFF — Engine A scan rows must keep their Engine A SL/TP1/TP2
    # even when an Engine B execution overlay is attached. Engine B overlay
    # levels are always stored separately on the signal (engine_b_execution_*).
    # Setting this True only opts in Engine B-only rows (or Engine C rows that
    # explicitly select B execution levels) — Engine A is protected by the
    # engine-identity gate in scanner._apply_engine_b_scan_levels regardless.
    "ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS": False,
    "ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED": False,
    "ENGINE_B_STRUCTURE_SCORE_COMPONENT_BONUSES": {
        "zone_proximity": 0.08,
        "ob_at_zone": 0.05,
        "fvg_overlap": 0.05,
        "liquidity_sweep": 0.04,
        "independent_direction_aligned": 0.04,
        "independent_direction_opposed": -0.08,
    },
    "ENGINE_B_STRUCTURE_SCORE_MULT_BOUNDS": {
        "min": 0.85,
        "max": 1.20,
    },
    "ENGINE_B_STRUCTURE_INFLUENCE_LEVEL": "standard",
    "ENGINE_B_STRUCTURE_INFLUENCE_LEVEL_MULTIPLIERS": {
        "conservative": 0.75,
        "standard": 1.0,
        "enhanced": 1.25,
    },
    "ENGINE_B_STRUCTURE_SCORE_CONFLUENCE": {
        "ENABLED": False,
        "BONUS": 0.03,
    },
    "ENGINE_B_CRYPTO_VOLATILITY_ADJUSTMENT_ENABLED": True,
    "ENGINE_B_CRYPTO_STRUCTURE_MULT": 1.25,
    "ENGINE_B_CRYPTO_BOS_MIN_BREAK_ATR": 0.10,
    # ── Engine B (Naked Scalp) ────────────────────────────────────────────────
    "NAKED_ENGINE": {
        "aggtrade_cvd_bonus": 1.0,
        "zone_multipliers": {
            "TRENDING": {"upper": 0.3, "lower": 1.0, "sl": 1.5},
            "RANGING": {"upper": 0.5, "lower": 1.2, "sl": 1.0},
            "HIGH_VOLATILITY": {"upper": 0.4, "lower": 1.5, "sl": 1.8},
            "LOW_VOLATILITY": {"upper": 0.2, "lower": 0.8, "sl": 1.0},
        },
        "structural_tp_buffer_atr_mult": 0.25,
        "d1_pd_array_conflict_window_atr_mult": 3.0,
        "rejection_wick_body_ratio": 1.2,
        "sweep_wick_atr_mult": 0.3,
        "equal_extrema_atr_mult": 0.15,
        "structural_tp_min_distance_atr": 0.5,
        "commodity_swing_force_macro_align": False,
        "style_profiles": {
            "scalp": {
                "min_score": 4.0,
                "min_rr": 1.5,
                "fallback_rr": 2.0,
                "require_macro_align": False,
            },
            "intraday": {
                "min_score": 4.5,
                "min_rr": 1.5,
                "fallback_rr": 2.0,
                "require_macro_align": False,
            },
            "swing": {
                "min_score": 5.0,
                "min_rr": 2.0,
                "fallback_rr": 3.0,
                "require_macro_align": False,
            },
        },
        # Stage 2.5: Collapsed Engine B group overrides.
        # Only 4 groups retain overrides; all others use base profiles.
        "score_group_overrides": {
            "forex_majors": {
                "intraday": {"min_rr": 1.3},
                "swing": {"min_rr": 1.8},
            },
            "forex_crosses": {
                "intraday": {"min_rr": 1.3},
                "swing": {"min_rr": 1.8},
            },
            "forex_exotics": {
                "scalp": {"min_room_atr": 0.5, "min_rr": 1.5},
                "intraday": {"min_room_atr": 0.85, "min_rr": 1.5},
                "swing": {"min_room_atr": 1.2, "min_rr": 2.0},
            },
            "nat_gas": {
                "scalp": {"min_score": 4.0},
                "intraday": {"min_score": 4.5, "min_rr": 1.6, "min_room_atr": 0.6},
                "swing": {"min_score": 5.0, "min_rr": 2.0, "min_room_atr": 1.0},
            },
            "energy_oil": {
                "intraday": {"min_rr": 1.3, "min_room_atr": 0.5},
                "swing": {"min_rr": 1.8, "min_room_atr": 0.8},
            },
            "crypto_doge": {
                "scalp": {"min_score": 4.0, "min_room_atr": 0.5},
                "intraday": {"min_score": 4.5, "min_room_atr": 0.9, "min_rr": 1.5},
                "swing": {"min_score": 5.0, "min_room_atr": 1.2, "min_rr": 1.9},
            },
        },
    },
    "ENGINE_B_REGIME_MULTIPLIERS": {
        "TRENDING": 0.95,
        "RANGING": 1.10,
        "HIGH_VOLATILITY": 1.10,
        "LOW_VOLATILITY": 1.0,
    },
    # True: regime adjusts Engine B min_score via ENGINE_B_REGIME_MULTIPLIERS.
    # False: min_score stays at style profile base (multiplier forced to 1.0).
    "ENGINE_B_REGIME_MULTIPLIERS_ENABLED": True,
    # False (default): regime multipliers do not raise/lower the mandatory gate floor.
    "ENGINE_B_REGIME_MULTIPLIERS_APPLY_TO_MIN_SCORE": False,
    # True (default): regime multipliers scale discretionary bonus points only.
    "ENGINE_B_REGIME_MULTIPLIERS_APPLY_TO_BONUSES": True,
    "ENGINE_B_STYLE_MIN_SCORE_DIFFERENTIATION_ENABLED": True,
    "ENGINE_B_STYLE_MIN_SCORE_BY_STYLE": {
        "scalp": 4.0,
        "intraday": 4.5,
        "swing": 5.0,
    },
    "ENGINE_B_WEIGHTED_SCORING": {
        "ENABLED": True,
        "COMPONENT_MAX": {
            "structure_alignment": 1.0,
            "ob_confluence": 1.0,
            "fvg_confluence": 1.0,
            "liquidity_proximity": 0.75,
            "bos_followthrough": 1.5,
            "volume_confirmation": 1.0,
            "profile_reaction": 1.0,
            "session_context": 0.5,
            "orderflow": 1.0,
        },
        "COMPONENT_WEIGHTS": {
            "structure_alignment": 0.22,
            "ob_confluence": 0.14,
            "fvg_confluence": 0.12,
            "liquidity_proximity": 0.08,
            "bos_followthrough": 0.16,
            "volume_confirmation": 0.08,
            "profile_reaction": 0.10,
            "session_context": 0.05,
            "orderflow": 0.05,
        },
        "REGIME_COMPONENT_MULT": {
            "RANGING": {
                "fvg_confluence": 1.15,
                "ob_confluence": 1.10,
                "bos_followthrough": 0.95,
            },
            "TRENDING": {
                "bos_followthrough": 1.15,
                "volume_confirmation": 1.10,
                "fvg_confluence": 0.95,
            },
            "HIGH_VOLATILITY": {
                "liquidity_proximity": 1.10,
                "ob_confluence": 1.05,
            },
            "LOW_VOLATILITY": {
                "bos_followthrough": 0.90,
            },
        },
    },
    "ENGINE_B_SUBSYSTEMS": {
        "ENABLED": True,
        "WEIGHTS_BY_FAMILY": {
            "forex": {"carry": 0.6, "sentiment": 0.4},
            "commodity": {"carry": 0.7, "sentiment": 0.3},
            "crypto": {},
        },
    },
    "FOREX_ENGINE": {
        "hurst_gate_enabled": True,
        "hurst_gate_threshold": 0.52,
        "trend_gate_adx_source": "d1",
        "trend_gate_adx_min": 20.0,
        "trend_margin_min": 0.003,
        "adx_confirm_min": 22.0,
        "h1_ema_entry_filter": True,
        "trend_support_weights": {
            "momentum": 0.15,
            "adx": 0.10,
            "carry": 0.05,
        },
        "score_group_adjustments": {
            "forex_majors": {
                "momentum_mult": 1.0,
                "adx_mult": 1.0,
                "carry_mult": 1.0,
                "score_mult": 1.0,
            },
            "forex_crosses": {
                "momentum_mult": 1.1,
                "adx_mult": 1.05,
                "carry_mult": 0.85,
                "score_mult": 1.0,
            },
            "forex_exotics": {
                "momentum_mult": 1.15,
                "adx_mult": 1.15,
                "carry_mult": 0.9,
                "score_mult": 0.95,
            },
        },
    },
}

# Engine B has its own backtest-exit namespace so Engine C tuning cannot
# silently alter Engine B behavior.
CONFIG["ENGINE_B_BT_EXIT"] = _deep_merge_dict({}, CONFIG.get("ENGINE_C_BT_EXIT", {}))

# Apply YAML overrides — deep-merge dicts, overwrite scalars
_CONFIG_DEFAULT_KEYS = set(CONFIG.keys())
_KNOWN_YAML_ONLY_KEYS = {
    "ADAPTIVE_KELLY_ENABLED",
    "ADAPTIVE_WEIGHTS_ENABLED",
    "AUTO_EXIT_ON_DECAY",
    "BACKTEST_DISABLE_HURST_GATE",
    "BACKTEST_MAX_WORKERS",
    "BACKTEST_LOOKBACK_DAYS",
    "BT_VECTORIZED",
    "CONDUCTOR",
    "CONFIDENCE_THRESHOLD",
    "ENGINE_A_MEAN_REVERSION",
    "ENGINE_A_RESEARCH_LAB_FACTORS",
    "ENGINE_B_BT_STRUCTURE_GATE_ENABLED",
    "ENGINE_B_BOS_LOOKBACK_BARS",
    "ENGINE_B_BOS_VOLUME_FOR_TICKVOL",
    "ENGINE_B_CRYPTO_LEVELS_FEED",
    "ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK",
    "ENGINE_B_FOREX_ASIAN_SESSION_SKIP_ENABLED",
    "ENGINE_B_CHOCH_STRICT",
    "ENGINE_B_SCAN_CONFIRMATION_GATE_ENABLED",
    "ENGINE_B_SCAN_GATE_FUNNEL_ENABLED",
    "ENGINE_B_STRUCTURAL_SL_USE_STYLE_ATR_MULTS",
    "ENGINE_A_STRUCTURAL_SL_FLOOR_ATR",
    "ENGINE_A_STRUCTURAL_SL_VALIDATE_SIDE",
    "ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT",
    "ENGINE_A_EXIT_MODE_BY_SCORE_GROUP",
    "ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP",
    "ENGINE_A_ATR_ADX_PERIODS_BY_CLASS",
    "ENGINE_A_TIME_EXIT_BARS",
    "ENGINE_B_FOLLOW_THROUGH",
    "ENGINE_B_FOREX_ADX_MIN",
    "ENGINE_B_RESEARCH_LAB_FACTORS",
    "ENGINE_B_STRUCTURE_GATE_ENABLED",
    "ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS",
    "ENGINE_C_AI_WEIGHT_ADJUST_ENABLED",
    "ENGINE_C_AI_WEIGHT_MAX",
    "ENGINE_C_AI_WEIGHT_MIN",
    "ENGINE_C_AI_WEIGHT_VERDICT_ENABLED",
    "ENGINE_C_META_BLEND",
    "ENGINE_C_EXEC_MIN_RR",
    "ENGINE_C_A_DEFAULT_MAX_SCORE",
    "ENGINE_C_A_DEFAULT_MAX_SCORE_BY_TYPE",
    "FACTOR_ADX_HARD_FAIL_CLASS",
    "FACTOR_ADDON_AGAINST_MIN",
    "FACTOR_ADDON_CONFIRM_MAX",
    "FACTOR_CONVICTION_FLOOR",
    "CARRY_STATIC_RATES_AS_OF",
    "CARRY_STATIC_RATES_MAX_AGE_DAYS",
    "FACTOR_FUNDING_BASELINE",
    "FACTOR_FUNDING_NOISE_BAND",
    "FACTOR_FUNDING_USE_ZSCORE",
    "FACTOR_FUNDING_ZSCORE_WINDOW",
    "FACTOR_FUNDING_Z_THRESHOLD",
    "FACTOR_FUNDING_ZSCORE_MIN_STD",
    "FACTOR_FUNDING_Z_REF",
    "FACTOR_FUNDING_SCALE",
    "FACTOR_OI_REF_PCT",
    "FACTOR_OI_USE_SMOOTHED",
    "FOREX_SESSION_FILTER",
    "INTERMARKET_CONFIRMATION",
    "KELLY_FRACTION",
    "LIVE_DASHBOARD",
        "NAKED_MAX_DAILY",
    "NEWS_BG_INTERVAL_SEC",
    "NEWS_PAIR_CACHE_TTL_SEC",
    "POSITION_SIZE_CONFIDENCE_SCALING",
    "SCALP_ENGINE",
    "SERVER_TZ_OFFSET_HOURS",
    "TELEGRAM",
    "THRESHOLD_AUDIT",
    "TIMED_EXIT",
    "VISION_MODIFIERS",
}

for _k, _v in _yaml_overrides.items():
    if _k in CONFIG and isinstance(CONFIG[_k], dict) and isinstance(_v, dict):
        CONFIG[_k] = _deep_merge_dict(CONFIG[_k], _v)
    else:
        CONFIG[_k] = _v


def get_d1_resample_offset_hours() -> float:
    """MT5 daily bar grid offset (hours, UTC-aligned policy layer).

    **Precedence:** top-level ``config.yaml`` key ``D1_RESAMPLE_OFFSET_HOURS`` wins when
    present. Otherwise inherits ``SCALP_ENGINE.D1_RESAMPLE_OFFSET_HOURS`` (legacy nest).
    Missing / invalid → ``0.0`` (UTC 00:00 daily buckets).
    """
    raw = CONFIG.get("D1_RESAMPLE_OFFSET_HOURS")
    if raw is None:
        se = CONFIG.get("SCALP_ENGINE")
        if isinstance(se, dict):
            raw = se.get("D1_RESAMPLE_OFFSET_HOURS")
    try:
        if raw is None:
            return 0.0
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def get_mt5_fetch_stale_unshifted_age_sec() -> float:
    """Max age (seconds) for newest MT5 bar before refusing tick-TZ heuristic in fetch_mt5."""
    try:
        v = CONFIG.get("MT5_FETCH_STALE_UNSHIFTED_AGE_SEC")
        if v is None:
            return float(36 * 3600)
        out = float(v)
        return out if out > 0 else float(36 * 3600)
    except (TypeError, ValueError):
        return float(36 * 3600)


# =============================================================================
# AI SAFETY COMPILE-TIME CONSTANTS (Audit CRIT-001, CRIT-002, CRIT-004, CRIT-005)
# These cannot be overridden by environment variables or YAML.
# Changing them requires a code review and deployment.
# =============================================================================


class AISafetyConstants:
    """Master kill switches for AI-mediated execution paths."""

    DISABLE_AI_VISION_UPGRADE_PATH: bool = True
    FORCE_DEBATE_DOWNGRADE_ONLY: bool = True
    FORCE_ZERO_TEMP_ON_GATES: bool = True
    DEBATE_FAILURE_DEFAULTS_TO_BLOCK: bool = True

    @classmethod
    def startup_safety_check(cls) -> None:
        """Call once after CONFIG is loaded. Aborts if production invariants are violated."""
        if not cls.DISABLE_AI_VISION_UPGRADE_PATH:
            raise RuntimeError(
                "FATAL: DISABLE_AI_VISION_UPGRADE_PATH is False. "
                "Chart Vision upgrade path is a CRITICAL safety risk. "
                "See audit finding CRIT-001."
            )
        if not cls.FORCE_DEBATE_DOWNGRADE_ONLY:
            raise RuntimeError(
                "FATAL: FORCE_DEBATE_DOWNGRADE_ONLY is False. "
                "Debate positive adjustments are a CRITICAL safety risk. "
                "See audit finding CRIT-002."
            )


class AITemperatureConfig:
    """Per-surface sampling temperature. Execution gates use 0.0 when forced."""

    MARCUS_TEMPERATURE: float = 0.25
    ENGINE_B_AI_TEMPERATURE: float = 0.15
    DEBATE_JUDGE_TEMPERATURE: float = 0.0
    VISION_TEMPERATURE: float = 0.0
    DECAY_TEMPERATURE: float = 0.0
    DEBATE_BULL_TEMPERATURE: float = 0.2
    DEBATE_BEAR_TEMPERATURE: float = 0.2

    @classmethod
    def get_temperature(cls, surface: str) -> float:
        """Return temperature for a named AI surface; respects AISafetyConstants."""
        mapping = {
            "marcus": cls.MARCUS_TEMPERATURE,
            "engine_b_ai": cls.ENGINE_B_AI_TEMPERATURE,
            "vision": cls.VISION_TEMPERATURE,
            "debate_bull": cls.DEBATE_BULL_TEMPERATURE,
            "debate_bear": cls.DEBATE_BEAR_TEMPERATURE,
            "debate_judge": cls.DEBATE_JUDGE_TEMPERATURE,
            "decay": cls.DECAY_TEMPERATURE,
        }
        temp = float(mapping.get(surface, 0.0))
        cfg = CONFIG
        # Optional YAML overrides (numeric only); gates still forced to 0.0 below.
        override_keys = {
            "marcus": "AI_TEMPERATURE",
            "vision": "AI_VISION_TEMPERATURE",
            "debate_bull": "DEBATE_BULL_TEMPERATURE",
            "debate_bear": "DEBATE_BEAR_TEMPERATURE",
            "debate_judge": "DEBATE_JUDGE_TEMPERATURE",
            "decay": "DECAY_AI_TEMPERATURE",
            "engine_b_ai": "ENGINE_B_AI_TEMPERATURE",
        }
        ok = override_keys.get(surface)
        if ok and ok in cfg:
            try:
                temp = float(cfg.get(ok))
            except (TypeError, ValueError):
                pass

        if AISafetyConstants.FORCE_ZERO_TEMP_ON_GATES and surface in (
            "vision",
            "debate_judge",
            "decay",
        ):
            if temp != 0.0:
                log.warning(
                    "[AI_SAFETY] Temperature override: surface=%s forced to 0.0 "
                    "(was %.4f) per AISafetyConstants.FORCE_ZERO_TEMP_ON_GATES — Audit CRIT-005.",
                    surface,
                    temp,
                )
            return 0.0
        return temp


def validate_config(cfg: dict) -> None:
    """Warn on mis-typed or dangerous CONFIG values after YAML overrides are applied."""
    import os

    _report_unknown_top_level_config_keys(_yaml_overrides)
    if not cfg.get("ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", False):
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            log.warning(
                "[CFG] ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED is False — "
                "per-score-group indicator differentiation (EMA/RSI/MACD/weights/floors) "
                "is inactive; config.yaml normally sets this to true"
            )
    carry_as_of = str(cfg.get("CARRY_STATIC_RATES_AS_OF", "") or "").strip()
    if carry_as_of:
        try:
            from datetime import datetime, timezone

            as_of_dt = datetime.strptime(carry_as_of, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            max_age = max(1, int(cfg.get("CARRY_STATIC_RATES_MAX_AGE_DAYS", 30) or 30))
            age_days = (datetime.now(timezone.utc) - as_of_dt).days
            if age_days > max_age:
                log.warning(
                    "[CFG] CARRY_STATIC_RATES_AS_OF=%s is %d days old (max %d) — "
                    "refresh carry_feed static fallbacks when FRED is unreachable",
                    carry_as_of,
                    age_days,
                    max_age,
                )
        except (TypeError, ValueError):
            log.warning("[CFG] CARRY_STATIC_RATES_AS_OF must be YYYY-MM-DD, got %r", carry_as_of)
    for k in (
        "RISK_PCT",
        "SL_ATR_MULT",
        "TP1_ATR_MULT",
        "TP2_ATR_MULT",
        "ADX_TREND_MIN",
        "MIN_CONFLUENCE",
    ):
        v = cfg.get(k)
        if not isinstance(v, (int, float)):
            log.warning(f"[CFG] {k} expected number, got {type(v).__name__!r}")
        elif v <= 0:
            log.warning(f"[CFG] {k}={v} is non-positive — check config.yaml")
    for k in ("D1_CANDLES", "H4_CANDLES", "H1_CANDLES"):
        v = cfg.get(k)
        if not isinstance(v, int) or v < 10:
            log.warning(f"[CFG] {k}={v} is too low — minimum 10 candles required")
    try:
        float(cfg.get("FOREX_H4_RESAMPLE_OFFSET_HOURS", 2.0) or 2.0)
    except (TypeError, ValueError):
        log.warning("[CFG] FOREX_H4_RESAMPLE_OFFSET_HOURS must be numeric")
    if cfg.get("RISK_PCT", 0) > 0.05:
        log.warning(
            f"[CFG] RISK_PCT={cfg['RISK_PCT']:.1%} exceeds 5% — verify this is intentional"
        )
    _asset_classes = {"crypto", "forex", "commodity", "stock", "index"}
    for sub_key in (
        "RISK_MULT",
        "RANGING",
        "ATR_CLASS",
        "ADX_TREND_MIN_CLASS",
        "COUNTER_TREND_PEN",
        "RSI_BOUNDS",
    ):
        missing = _asset_classes - set(cfg.get(sub_key, {}).keys())
        if missing:
            log.warning(f"[CFG] {sub_key} missing asset classes: {missing}")
    pair_profiles = cfg.get("PAIR_PROFILES", {}) or {}
    if not isinstance(pair_profiles, dict):
        log.warning(
            f"[CFG] PAIR_PROFILES must be a dict, got {type(pair_profiles).__name__!r}"
        )
        return
    aggtrade_required = bool(cfg.get("ENGINE_B_CRYPTO_REQUIRE_AGGTRADE_FOR_PASS", False))
    aggtrade_mode = str(cfg.get("ENGINE_B_CRYPTO_AGGTRADE_MODE", "degraded") or "degraded").lower()
    aggtrade_trusted_mode = str(
        cfg.get("ENGINE_B_CRYPTO_AGGTRADE_TRUSTED_MODE", "required") or "required"
    ).lower()
    if (
        aggtrade_required
        and aggtrade_mode != "required"
        and aggtrade_trusted_mode != "required"
    ):
        log.warning(
            "[CFG] ENGINE_B_CRYPTO_REQUIRE_AGGTRADE_FOR_PASS is true but "
            "ENGINE_B_CRYPTO_AGGTRADE_MODE=%r and TRUSTED_MODE=%r — "
            "aggtrade will not hard-block passes",
            aggtrade_mode,
            aggtrade_trusted_mode,
        )
    for profile_name, profile in pair_profiles.items():
        if not isinstance(profile, dict):
            log.warning(f"[CFG] PAIR_PROFILES[{profile_name!r}] must be a dict")
            continue
        disabled_votes = set(profile.get("disabled_votes", []) or [])
        unknown_votes = sorted(disabled_votes - PAIR_PROFILE_VOTES)
        if unknown_votes:
            log.warning(
                f"[CFG] PAIR_PROFILES[{profile_name!r}] unknown disabled_votes: {unknown_votes}"
            )
        disabled_filters = set(profile.get("disable_filters", []) or [])
        unknown_filters = sorted(disabled_filters - PAIR_PROFILE_FILTERS)
        if unknown_filters:
            log.warning(
                f"[CFG] PAIR_PROFILES[{profile_name!r}] unknown disable_filters: {unknown_filters}"
            )
        weight_overrides = profile.get("weight_overrides", {}) or {}
        if not isinstance(weight_overrides, dict):
            log.warning(
                f"[CFG] PAIR_PROFILES[{profile_name!r}].weight_overrides must be a dict"
            )
            weight_overrides = {}
        for vote_name, weight in weight_overrides.items():
            if vote_name not in PAIR_PROFILE_VOTES:
                log.warning(
                    f"[CFG] PAIR_PROFILES[{profile_name!r}] unknown vote override: {vote_name!r}"
                )
                continue
            if vote_name in disabled_votes:
                log.warning(
                    f"[CFG] PAIR_PROFILES[{profile_name!r}] disables and overrides {vote_name!r}; disabled_votes wins"
                )
            try:
                float(weight)
            except (TypeError, ValueError):
                log.warning(
                    f"[CFG] PAIR_PROFILES[{profile_name!r}] invalid weight for {vote_name!r}: {weight!r}"
                )
        for numeric_key in ("min_confluence", "bt_min", "volume_threshold"):
            if numeric_key in profile:
                try:
                    float(profile[numeric_key])
                except (TypeError, ValueError):
                    log.warning(
                        f"[CFG] PAIR_PROFILES[{profile_name!r}] {numeric_key} must be numeric, got {profile[numeric_key]!r}"
                    )


# Stage 4.1: Fatal boot-time config validation layer.
# System refuses to start if any of these invariants are violated.
# These are assertions about the mathematical consistency of the config,
# not runtime checks.


class ConfigValidationError(SystemExit):
    """Raised when config invariants are violated at boot time."""
    pass


_MISSING = object()
_REAL_ORDER_CONFIRM_ENV = "ATHENA_REAL_ORDERS_CONFIRM"
_REAL_ORDER_CONFIRM_TOKEN = "I_UNDERSTAND_REAL_ORDER_RISK"


def _unknown_top_level_config_keys(
    yaml_overrides: dict | None,
    known_keys: set[str] | None = None,
) -> list[str]:
    """Return YAML top-level keys that have no default/schema entry."""
    if not isinstance(yaml_overrides, dict):
        return []
    known = known_keys or (_CONFIG_DEFAULT_KEYS | _KNOWN_YAML_ONLY_KEYS)
    return sorted(str(k) for k in yaml_overrides if k not in known)


def _report_unknown_top_level_config_keys(
    yaml_overrides: dict | None,
    known_keys: set[str] | None = None,
) -> list[str]:
    unknown = _unknown_top_level_config_keys(yaml_overrides, known_keys)
    if unknown:
        log.warning(
            "[CFG] Unknown top-level config key(s) accepted without schema defaults: %s",
            ", ".join(unknown),
        )
    return unknown


def _get_config_path(cfg: dict, path: tuple[str, ...]):
    cur = cfg
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _path_label(path: tuple[str, ...]) -> str:
    return ".".join(path)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


_CRITICAL_SAFETY_SCHEMA: dict[tuple[str, ...], dict] = {
    ("REAL_ORDERS_ALLOWED",): {"type": "bool"},
    ("PAPER_SOAK",): {"type": "dict"},
    ("PAPER_SOAK", "ENABLED"): {"type": "bool"},
    ("PAPER_SOAK", "REAL_ORDERS_ALLOWED"): {"type": "bool"},
    ("EXECUTOR_MODE",): {"type": "enum", "allowed": {"paper", "demo", "live"}},
    ("EXECUTION_ENABLED",): {"type": "bool"},
    ("EXECUTION_SINGLE_LEG_ONLY",): {"type": "bool"},
    ("AUTO_EXECUTE",): {"type": "bool"},
    ("AUTO_TRADE_ENABLED",): {"type": "bool"},
    ("RISK_ENGINE_ENABLED",): {"type": "bool", "must_be": True},
    ("MT5_EXECUTION_ENABLED",): {"type": "bool"},
    ("BYBIT_EXECUTION_ENABLED",): {"type": "bool"},
    ("FX_FACTOR_SCANNER_ENABLED",): {"type": "bool"},
    ("FX_FACTOR_EXECUTION_ENABLED",): {"type": "bool"},
    ("FX_FACTOR_ALLOW_LIVE_EXECUTION",): {"type": "bool"},
    ("FX_FACTOR_REQUIRE_VALIDATION_FOR_EXECUTION",): {"type": "bool"},
    ("FX_FACTOR_STRICT_DATA_DEFAULT",): {"type": "bool"},
    ("FX_FACTOR_SCAN_INCLUDE_BLOCKED_DEFAULT",): {"type": "bool"},
    ("FX_FACTOR_SCAN_REFRESH_DECISIONS_DEFAULT",): {"type": "bool"},
    ("BYBIT_LEVERAGE",): {"type": "int", "min": 1, "max": 1},
    ("RISK_PCT",): {"type": "number", "min": 0.0, "max": 0.05},
    ("MAX_RISK_PER_TRADE",): {"type": "number", "min": 0.0, "max": 0.05},
    ("MAX_PORTFOLIO_HEAT",): {"type": "number", "min": 0.0, "max": 1.0},
    ("MAX_OPEN_POSITIONS",): {"type": "int", "min": 0, "max": 100},
    ("MAX_CORRELATED_POSITIONS",): {"type": "int", "min": 0, "max": 20},
    ("DAILY_LOSS_LIMIT",): {"type": "number", "min": 0.0, "max": 0.20},
    ("DRAWDOWN_REDUCE_THRESHOLD",): {"type": "number", "min": 0.0, "max": 1.0},
    ("DRAWDOWN_STOP_THRESHOLD",): {"type": "number", "min": 0.0, "max": 1.0},
}


def _critical_safety_config_errors(
    cfg: dict,
    *,
    env: dict | None = None,
) -> list[str]:
    """Validate critical trading-safety config keys without modeling the full config."""
    errors: list[str] = []
    if not isinstance(cfg, dict):
        return ["CONFIG must be a dict"]

    for path, rule in _CRITICAL_SAFETY_SCHEMA.items():
        label = _path_label(path)
        value = _get_config_path(cfg, path)
        if value is _MISSING:
            errors.append(f"{label} is required")
            continue
        typ = rule["type"]
        if typ == "dict":
            if not isinstance(value, dict):
                errors.append(f"{label} must be a dict, got {type(value).__name__}")
            continue
        if typ == "bool":
            if not isinstance(value, bool):
                errors.append(f"{label} must be bool, got {type(value).__name__}")
                continue
            if "must_be" in rule and value is not rule["must_be"]:
                errors.append(f"{label} must be {rule['must_be']!r}")
            continue
        if typ == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"{label} must be int, got {type(value).__name__}")
                continue
            if "min" in rule and value < rule["min"]:
                errors.append(f"{label}={value} is below minimum {rule['min']}")
            if "max" in rule and value > rule["max"]:
                errors.append(f"{label}={value} exceeds maximum {rule['max']}")
            continue
        if typ == "number":
            if not _is_number(value):
                errors.append(f"{label} must be number, got {type(value).__name__}")
                continue
            if "min" in rule and value < rule["min"]:
                errors.append(f"{label}={value} is below minimum {rule['min']}")
            if "max" in rule and value > rule["max"]:
                errors.append(f"{label}={value} exceeds maximum {rule['max']}")
            continue
        if typ == "enum":
            if not isinstance(value, str):
                errors.append(f"{label} must be string enum, got {type(value).__name__}")
                continue
            normalized = value.strip().lower()
            if normalized not in rule["allowed"]:
                allowed = ", ".join(sorted(rule["allowed"]))
                errors.append(f"{label}={value!r} must be one of: {allowed}")

    reduce_threshold = _get_config_path(cfg, ("DRAWDOWN_REDUCE_THRESHOLD",))
    stop_threshold = _get_config_path(cfg, ("DRAWDOWN_STOP_THRESHOLD",))
    if _is_number(reduce_threshold) and _is_number(stop_threshold):
        if stop_threshold <= reduce_threshold:
            errors.append(
                "DRAWDOWN_STOP_THRESHOLD must be greater than DRAWDOWN_REDUCE_THRESHOLD"
            )

    if cfg.get("DRAWDOWN_STOP_ENABLED") is False:
        errors.append(
            "DRAWDOWN_STOP_ENABLED=false disables account drawdown protection and is not allowed"
        )

    env_map = env if env is not None else os.environ
    real_orders_allowed = bool(cfg.get("REAL_ORDERS_ALLOWED", False))
    paper_soak = cfg.get("PAPER_SOAK") if isinstance(cfg.get("PAPER_SOAK"), dict) else {}
    nested_real_orders_allowed = bool(paper_soak.get("REAL_ORDERS_ALLOWED", False))
    executor_mode = str(cfg.get("EXECUTOR_MODE", "paper") or "paper").strip().lower()
    paper_mode_disabled = paper_soak.get("ENABLED") is False
    unsafe_live_mode = (
        real_orders_allowed
        or nested_real_orders_allowed
        or executor_mode == "live"
        or paper_mode_disabled
    )
    if unsafe_live_mode:
        token = str(env_map.get(_REAL_ORDER_CONFIRM_ENV, "") or "").strip()
        if token != _REAL_ORDER_CONFIRM_TOKEN:
            errors.append(
                f"Unsafe live/real-order mode requires {_REAL_ORDER_CONFIRM_ENV}="
                f"{_REAL_ORDER_CONFIRM_TOKEN!r}"
            )

    return errors


def _validate_critical_safety_config(cfg: dict, *, env: dict | None = None) -> None:
    errors = _critical_safety_config_errors(cfg, env=env)
    if errors:
        for e in errors:
            log.critical("CONFIG_SAFETY_FATAL: %s", e)
        raise ConfigValidationError(
            f"System refused to start due to {len(errors)} critical safety config error(s)."
        )


def _fatal_config_validation(cfg: dict) -> None:
    """Fatal assertions — system refuses to start if any fail.

    Checks:
      1. Threshold consistency: volatile >= stable
      2. Bound non-contradiction: addon bound == research lab MAX_ABS
      3. Weight normalization: trend weights sum to 1.0 per asset class
      4. BT_MIN prohibited: BACKTEST_USE_BT_MIN_THRESHOLDS must not exist
      5. Floor sanity: conviction floor in [0.10, 0.30]
      6. Definition guards: max_possible defined for Engine B
    """
    errors: list[str] = []
    try:
        _validate_critical_safety_config(cfg)
    except ConfigValidationError as exc:
        errors.append(str(exc))

    # 1. Threshold consistency (scoring.py tiers — hardcoded to avoid circular import)
    _TIER_VOLATILE = 2.0
    _TIER_STABLE = 1.5
    if _TIER_VOLATILE < _TIER_STABLE:
        errors.append(f"TIER_VOLATILE ({_TIER_VOLATILE}) must be >= TIER_STABLE ({_TIER_STABLE})")

    from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS

    _group_thresholds = cfg.get("ENGINE_A_SCORE_GROUP_THRESHOLDS") or {}
    if not isinstance(_group_thresholds, dict):
        errors.append("ENGINE_A_SCORE_GROUP_THRESHOLDS must be a dict")
    else:
        for group in sorted(ENGINE_A_KNOWN_SCORE_GROUPS):
            if group not in _group_thresholds:
                errors.append(
                    f"ENGINE_A_SCORE_GROUP_THRESHOLDS missing score_group {group!r} "
                    "(required — do not rely on legacy 3-tier fallback)"
                )
        for key in _group_thresholds:
            if key == "default":
                continue
            if key not in ENGINE_A_KNOWN_SCORE_GROUPS:
                log.warning(
                    "[CFG] ENGINE_A_SCORE_GROUP_THRESHOLDS has unknown score_group key %r",
                    key,
                )

    _group_conf = cfg.get("ENGINE_A_SCORE_GROUP_MIN_CONFIDENCE") or {}
    if not isinstance(_group_conf, dict):
        errors.append("ENGINE_A_SCORE_GROUP_MIN_CONFIDENCE must be a dict")
    else:
        for group in sorted(ENGINE_A_KNOWN_SCORE_GROUPS):
            if group not in _group_conf:
                errors.append(
                    f"ENGINE_A_SCORE_GROUP_MIN_CONFIDENCE missing score_group {group!r}"
                )
        for key, raw in _group_conf.items():
            if key == "default":
                continue
            if key not in ENGINE_A_KNOWN_SCORE_GROUPS:
                log.warning(
                    "[CFG] ENGINE_A_SCORE_GROUP_MIN_CONFIDENCE has unknown score_group key %r",
                    key,
                )
            try:
                val = float(raw)
            except (TypeError, ValueError):
                errors.append(f"ENGINE_A_SCORE_GROUP_MIN_CONFIDENCE[{key!r}] must be numeric")
                continue
            if not (0.0 <= val <= 1.0):
                errors.append(
                    f"ENGINE_A_SCORE_GROUP_MIN_CONFIDENCE[{key!r}] must be in [0, 1], got {val}"
                )

    _adx_trend = cfg.get("ADX_TREND_MIN_CLASS") or {}
    _adx_hard = cfg.get("FACTOR_ADX_HARD_FAIL_CLASS") or {}
    if isinstance(_adx_trend, dict) and isinstance(_adx_hard, dict):
        for group in sorted(ENGINE_A_KNOWN_SCORE_GROUPS):
            if group == "unknown":
                continue
            if group not in _adx_trend or group not in _adx_hard:
                log.warning(
                    "[CFG] score_group %r missing explicit ADX_TREND_MIN_CLASS and/or "
                    "FACTOR_ADX_HARD_FAIL_CLASS — inherits asset_type via _resolve_class_keyed",
                    group,
                )

    # 2. Bound non-contradiction
    _addon_confirm = float(cfg.get("FACTOR_ADDON_CONFIRM_MAX", cfg.get("FACTOR_ADDON_CONFIRM", 0.20)))
    _research_max = float((cfg.get("ENGINE_A_RESEARCH_LAB_FACTORS") or {}).get("MAX_ABS", 0.20))
    if abs(_addon_confirm - _research_max) > 1e-6:
        errors.append(
            f"Addon bound high ({_addon_confirm}) must equal research MAX_ABS ({_research_max})"
        )

    # 3. Weight normalization — trend weights per asset class must sum to 1.0
    _trend_weights = cfg.get("INDICATOR_WEIGHTS", {}).get("trend", {})
    for asset_class, weights in _trend_weights.items():
        if isinstance(weights, dict):
            total = sum(float(v) for v in weights.values() if isinstance(v, (int, float)))
            if abs(total - 1.0) > 1e-6:
                errors.append(
                    f"Trend weights for {asset_class} sum to {total:.4f}, expected 1.0"
                )

    # 4. BT_MIN prohibited — Stage 4.2
    _factor_weights = cfg.get("ENGINE_A_FACTOR_WEIGHTS_BY_CLASS", {}) or {}
    for class_key, weights in _factor_weights.items():
        if not isinstance(weights, dict):
            errors.append(f"Engine A factor weights for {class_key} must be a dict")
            continue
        total = 0.0
        missing = []
        for key in ("momentum", "addon", "base"):
            if key not in weights:
                missing.append(key)
                continue
            try:
                value = float(weights[key])
            except (TypeError, ValueError):
                errors.append(f"Engine A factor weight {class_key}.{key} must be numeric")
                continue
            if not math.isfinite(value) or value < 0:
                errors.append(f"Engine A factor weight {class_key}.{key} must be finite and non-negative")
            total += value
        if missing:
            errors.append(
                f"Engine A factor weights for {class_key} missing keys: {', '.join(missing)}"
            )
        elif abs(total - 1.0) > 1e-6:
            errors.append(
                f"Engine A factor weights for {class_key} sum to {total:.4f}, expected 1.0"
            )

    _pair_profiles = cfg.get("PAIR_PROFILES", {}) or {}
    for profile_name, profile in _pair_profiles.items():
        if not isinstance(profile, dict):
            continue
        for inactive_key in ("weight_overrides", "bt_min"):
            if inactive_key in profile:
                errors.append(
                    f"PAIR_PROFILES[{profile_name!r}].{inactive_key} is inactive in Engine A v2"
                )

    if "BACKTEST_USE_BT_MIN_THRESHOLDS" in cfg:
        errors.append("BACKTEST_USE_BT_MIN_THRESHOLDS must be deleted — dual thresholds prohibited")
    if "BT_MIN_GROUP" in cfg:
        errors.append("BT_MIN_GROUP must be deleted - use Engine A live threshold resolver")

    # 5. Floor sanity
    _floor = float(cfg.get("FACTOR_CONVICTION_FLOOR", 0.20))
    if not (0.10 <= _floor <= 0.30):
        errors.append(f"Conviction floor {_floor} must be in [0.10, 0.30]")

    _asset_class_keys = {"crypto", "forex", "commodity", "stock", "index", "default"}
    _class_floors = cfg.get("ENGINE_A_CONVICTION_FLOOR_BY_CLASS") or {}
    if not isinstance(_class_floors, dict):
        errors.append("ENGINE_A_CONVICTION_FLOOR_BY_CLASS must be a dict")
    else:
        for key, value in _class_floors.items():
            if key not in _asset_class_keys and key not in ENGINE_A_KNOWN_SCORE_GROUPS:
                log.warning("[CFG] ENGINE_A_CONVICTION_FLOOR_BY_CLASS has unknown class key %r", key)
            try:
                floor_value = float(value)
            except (TypeError, ValueError):
                errors.append(f"Engine A conviction floor {key} must be numeric")
                continue
            if not math.isfinite(floor_value) or not (0.10 <= floor_value <= 0.70):
                errors.append(f"Engine A conviction floor {key}={floor_value} must be in [0.10, 0.70]")

    _adx_sources = cfg.get("ENGINE_A_ADX_SOURCE_BY_CLASS") or {}
    if not isinstance(_adx_sources, dict):
        errors.append("ENGINE_A_ADX_SOURCE_BY_CLASS must be a dict")
    else:
        for key, mode in _adx_sources.items():
            if key not in _asset_class_keys and key not in ENGINE_A_KNOWN_SCORE_GROUPS:
                log.warning("[CFG] ENGINE_A_ADX_SOURCE_BY_CLASS has unknown class key %r", key)
            mode_s = str(mode or "").strip().lower()
            if mode_s not in {"d1_first", "h4_first", "max"}:
                errors.append(
                    f"Engine A ADX source mode {key}={mode!r} must be one of d1_first, h4_first, max"
                )

    _di_profiles = cfg.get("ENGINE_A_DI_ALIGNMENT_MULT_BY_CLASS") or {}
    if not isinstance(_di_profiles, dict):
        errors.append("ENGINE_A_DI_ALIGNMENT_MULT_BY_CLASS must be a dict")
    else:
        for key, profile in _di_profiles.items():
            if key not in _asset_class_keys and key not in ENGINE_A_KNOWN_SCORE_GROUPS:
                log.warning("[CFG] ENGINE_A_DI_ALIGNMENT_MULT_BY_CLASS has unknown class key %r", key)
            if not isinstance(profile, dict):
                errors.append(f"Engine A DI alignment profile {key} must be a dict")
                continue
            for state in ("missing", "balanced", "opposed"):
                if state not in profile:
                    continue
                try:
                    mult_value = float(profile[state])
                except (TypeError, ValueError):
                    errors.append(f"Engine A DI alignment multiplier {key}.{state} must be numeric")
                    continue
                if not math.isfinite(mult_value) or not (0.10 <= mult_value <= 1.0):
                    errors.append(
                        f"Engine A DI alignment multiplier {key}.{state}={mult_value} must be in [0.10, 1.0]"
                    )

    _trusted_vp = cfg.get("ENGINE_B_PROFILE_TRUSTED_ASSET_TYPES")
    if _trusted_vp is None:
        pass
    elif not isinstance(_trusted_vp, (list, tuple)) or not _trusted_vp:
        errors.append("ENGINE_B_PROFILE_TRUSTED_ASSET_TYPES must be a non-empty list")
    else:
        for entry in _trusted_vp:
            if not str(entry or "").strip():
                errors.append("ENGINE_B_PROFILE_TRUSTED_ASSET_TYPES entries must be non-empty strings")

    # 6. Definition guards — Engine B max_possible must be defined
    _b_max = cfg.get("ENGINE_B_MAX_POSSIBLE")
    if _b_max is None:
        # Fallback: compute from existing config
        _profile_on = bool(cfg.get("ENGINE_B_PROFILE_SCORING_ENABLED", False))
        _aggtrade_cvd_on = bool(
            cfg.get("ENGINE_B_CRYPTO_AGGTRADE_ENABLED", True)
            and cfg.get("ENGINE_B_CRYPTO_AGGTRADE_CVD_SCORE_ENABLED", True)
        )
        try:
            _aggtrade_bonus = float(
                (cfg.get("NAKED_ENGINE") or {}).get("aggtrade_cvd_bonus", 1.0)
            )
        except (TypeError, ValueError):
            _aggtrade_bonus = 1.0
        _b_max = 6 + 3 + (1.0 if _profile_on else 0.0) + (
            max(0.0, _aggtrade_bonus) if _aggtrade_cvd_on else 0.0
        )
    if _b_max is None or float(_b_max) <= 0:
        errors.append("Engine B max_possible must be defined and positive")

    if errors:
        for e in errors:
            log.critical("CONFIG_VALIDATION_FATAL: %s", e)
        raise ConfigValidationError(
            f"System refused to start due to {len(errors)} config error(s). See logs above."
        )

    log.info("[BOOT] Fatal config validation passed (%d checks)", 6)


validate_config(CONFIG)
_fatal_config_validation(CONFIG)

AISafetyConstants.startup_safety_check()


def scan_candle_limits() -> dict[str, int]:
    """Bar counts for Engine A (`analyze_pair`), Engine B, Engine C B-leg, and naked scans.

    Single source of truth: ``D1_CANDLES``, ``H4_CANDLES``, ``H1_CANDLES`` in CONFIG / config.yaml.
    ``fetch_candles`` (athena) routes by pair ``source`` to Binance (crypto), EODHD (forex/stocks/
    commodities/indices/ETFs), Polygon, or yfinance — same limits apply to every asset class.
    Live ``analyze_pair`` may include the forming bar in indicator inputs (F8); callers that need
    confirmed-only bars must split via ``split_market_state`` themselves.
    """
    return {
        "D1": int(CONFIG["D1_CANDLES"]),
        "H4": int(CONFIG["H4_CANDLES"]),
        "H1": int(CONFIG["H1_CANDLES"]),
    }


def get_optimal_workers(configured_max: int | None = None, conservative: bool = True) -> int:
    """Calculate optimal worker count based on CPU and memory (conservative for work laptops).

    Args:
        configured_max: Upper bound from config (SCAN_MAX_WORKERS or BACKTEST_MAX_WORKERS)
        conservative: If True, uses 60% of CPUs; if False, uses 100%

    Returns:
        Sensible worker count that leaves headroom for other applications
    """
    try:
        import psutil
        cpu_count = psutil.cpu_count(logical=True) or 4
        mem_gb = psutil.virtual_memory().total / (1024**3)
    except Exception:
        # psutil not available - fallback to config values
        cpu_count = os.cpu_count() or 4
        mem_gb = 16  # Assume 16GB if unknown

    # CPU-based: 60% for conservative (work laptop), 80% for aggressive
    cpu_factor = 0.6 if conservative else 0.8
    cpu_based = max(1, int(cpu_count * cpu_factor))

    # Memory-based: ~2GB per worker for safety (conservative for 16GB total)
    mem_based = max(1, int(mem_gb / 2.5))

    # Take the more restrictive limit
    optimal = min(cpu_based, mem_based)

    # Apply configured max if provided
    if configured_max is not None:
        optimal = min(optimal, max(1, configured_max))

    return max(1, optimal)


def _json_safe(value):
    """Recursively convert NaN/inf float values to None so Flask emits valid JSON."""
    import math as _math

    try:
        import numpy as _np
    except Exception:
        _np = None

    if isinstance(value, float):
        return value if _math.isfinite(value) else None
    if _np is not None and isinstance(value, _np.generic):
        return _json_safe(value.item())
    if _np is not None and isinstance(value, _np.ndarray):
        return [_json_safe(v) for v in value.tolist()]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    return value
