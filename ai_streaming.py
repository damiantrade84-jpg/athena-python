"""ai_streaming.py — SSE streaming helpers for advisory AI surfaces.

Phase 0 of the Athena AI Edge Program. Config-gated by
`AI_STREAMING_ENABLED` (default False). When disabled, callers should fall
back to the existing non-streaming endpoints.

Safety:
- Streaming is advisory-only. The same unsafe-execution-language checks that
  apply to non-streaming responses apply here. Streamed tokens are validated
  by `ai_agent_safety.validate_ai_chat_response` on the assembled text.
- Streaming failures never raise — the generator simply ends. The route
  handler emits a final `error` event and a `done` event so the client can
  reconcile.
- No execution, no order approval, no gate override, no score mutation.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from config import (
    AITemperatureConfig,
    CONFIG,
    create_ai_client,
    get_ai_max_retries,
    get_ai_model,
    get_ai_timeout_sec,
    resolve_ai_review_runtime,
)

log = logging.getLogger("athena.ai_streaming")


def is_streaming_enabled() -> bool:
    """Return True only if streaming is explicitly enabled in CONFIG."""
    try:
        return bool(CONFIG.get("AI_STREAMING_ENABLED", False))
    except Exception:
        return False


def sse_format(event: str, data: Any) -> str:
    """Format a single Server-Sent Events message.

    `data` is JSON-serialized when not already a string. The result is a
    complete SSE block terminated by a blank line, ready to flush to the
    client.
    """
    if isinstance(data, str):
        payload = data
    else:
        try:
            payload = json.dumps(data, default=str, ensure_ascii=False)
        except Exception:
            payload = str(data)
    return f"event: {event}\ndata: {payload}\n\n"


def sse_done() -> str:
    """Terminal SSE marker so the client knows the stream has ended."""
    return sse_format("done", {"ok": True})


def stream_chat_completion(
    *,
    messages: list[dict[str, str]],
    system_prompt: str,
    model: str | None = None,
    max_tokens: int = 600,
    temperature: float | None = None,
    api_key: str | None = None,
    provider: str | None = None,
    preferred_key: str = "MARCUS_MODEL",
    temperature_profile: str = "marcus",
) -> Iterator[str]:
    """Yield incremental text tokens from a streaming chat completion.

    Falls back silently (yields nothing) on any error. The route handler is
    responsible for emitting a final error/done event. Never raises.

    Uses the OpenAI-compatible `chat.completions.create(stream=True)` interface
    which is supported by the OpenAI, Anthropic (compat), and xAI providers via
    `create_ai_client`.
    """
    try:
        runtime = resolve_ai_review_runtime(CONFIG)
        active_provider = str(
            provider or runtime.get("provider") or runtime.get("selectedProvider") or "grok"
        )
        key = (api_key or str(runtime.get("api_key") or "") or "").strip()
        if not key:
            log.debug("[AI_STREAMING] no api key configured; yielding nothing")
            return
        resolved_model = str(
            model or get_ai_model(CONFIG, preferred_key=preferred_key, provider=active_provider)
        ).strip()
        if not resolved_model:
            log.debug("[AI_STREAMING] no model resolved; yielding nothing")
            return
        client = create_ai_client(
            CONFIG,
            api_key=key,
            max_retries=get_ai_max_retries(CONFIG, preferred_key="AI_SDK_MAX_RETRIES"),
            provider=active_provider,
        )
        full_messages = [{"role": "system", "content": system_prompt}] + list(messages)
        temp = float(
            temperature if temperature is not None else AITemperatureConfig.get_temperature(temperature_profile)
        )
        stream = client.chat.completions.create(
            model=resolved_model,
            max_tokens=int(max_tokens),
            temperature=temp,
            timeout=get_ai_timeout_sec(CONFIG),
            messages=full_messages,
            stream=True,
        )
        for chunk in stream:
            try:
                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None)
                token = getattr(delta, "content", None) if delta is not None else None
            except (AttributeError, IndexError, TypeError):
                token = None
            if token:
                yield token
    except Exception as exc:
        log.warning("[AI_STREAMING] streaming chat completion failed: %s", exc)
        return


def stream_sse_tokens(
    *,
    messages: list[dict[str, str]],
    system_prompt: str,
    model: str | None = None,
    max_tokens: int = 600,
    temperature: float | None = None,
    api_key: str | None = None,
    provider: str | None = None,
    preferred_key: str = "MARCUS_MODEL",
    temperature_profile: str = "marcus",
    meta: dict[str, Any] | None = None,
) -> Iterator[str]:
    """Stream tokens as SSE-formatted strings.

    Emits:
      - `start` event with metadata
      - `token` events with incremental text fragments
      - `error` event if the generator ends without yielding (no key/model)
      - `done` event at the end

    Never raises. Safe to wire directly to a Flask Response with mimetype
    text/event-stream.
    """
    yield sse_format("start", meta or {})
    any_token = False
    for token in stream_chat_completion(
        messages=messages,
        system_prompt=system_prompt,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        api_key=api_key,
        provider=provider,
        preferred_key=preferred_key,
        temperature_profile=temperature_profile,
    ):
        any_token = True
        yield sse_format("token", {"text": token})
    if not any_token:
        yield sse_format("error", {"error": "streaming_unavailable", "fallback": "non_streaming"})
    yield sse_done()
