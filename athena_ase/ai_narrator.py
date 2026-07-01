"""athena_ase/ai_narrator.py — advisory LLM narrator for ASE signals.

Phase 4 of the Athena AI Edge Program. Config-gated by
`AI_ASE_NARRATOR_ENABLED` (default false). Produces a short advisory
commentary for an ASE signal. It does NOT touch ASE ML inference, signal
generation, scoring, promotion, or execution. It is purely additive
commentary for human review.

Safety:
- Advisory-only. Never approves, blocks, or executes.
- Never mutates the ASE signal dict; returns a separate commentary dict.
- Config-gated, default-off, fail-closed (returns None on any error or when
  disabled).
- ASE stays standalone: this module imports nothing from Engine A/B/C/D and
  does not import ASE inference code.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("athena.ase.ai_narrator")


def is_narrator_enabled() -> bool:
    try:
        from config import CONFIG

        return bool(CONFIG.get("AI_ASE_NARRATOR_ENABLED", False))
    except Exception:
        return False


def narrate_ase_signal(signal: dict[str, Any]) -> dict[str, Any] | None:
    """Produce advisory LLM commentary for an ASE signal.

    Returns a dict:
      {schema_version, signal_ref, commentary, model_used, provider,
       advisory_only, do_not_auto_apply}

    Returns None when disabled, missing API key, or on any error. Never
    raises. Never mutates the input signal.
    """
    if not is_narrator_enabled():
        return None
    if not isinstance(signal, dict):
        return None
    try:
        from config import (
            AITemperatureConfig,
            CONFIG,
            create_ai_client,
            get_ai_api_key,
            get_ai_model,
            get_ai_timeout_sec,
            resolve_ai_review_runtime,
        )

        runtime = resolve_ai_review_runtime(CONFIG)
        active_provider = str(runtime.get("provider") or runtime.get("selectedProvider") or "grok")
        api_key = (get_ai_api_key(CONFIG) or str(runtime.get("api_key") or "") or "").strip()
        if not api_key:
            return None
        model = str(get_ai_model(CONFIG, preferred_key="ASE_NARRATOR_MODEL", provider=active_provider) or "").strip()
        if not model:
            return None
        client = create_ai_client(
            CONFIG,
            api_key=api_key,
            provider=active_provider,
        )
        signal_ref = str(signal.get("signal_id") or signal.get("id") or signal.get("trace_id") or "")
        # Trim signal context to keep the prompt bounded
        ctx = {
            "signal_id": signal_ref,
            "instrument": signal.get("instrument") or signal.get("symbol"),
            "horizon": signal.get("horizon"),
            "direction": signal.get("direction"),
            "score": signal.get("score"),
            "layer1": signal.get("layer1"),
            "meta_model": signal.get("meta_model"),
        }
        system = (
            "You are an advisory narrator for the Adaptive Specialist Engine (ASE). "
            "Produce a 2-4 sentence commentary that explains what the signal is "
            "indicating in plain language. Never claim authority to approve, block, "
            "or execute. Never cite thresholds, weights, or promotion gates. "
            "Advisory-only; do not auto-apply."
        )
        user = f"ASE signal context:\n{json.dumps(ctx, default=str)[:4000]}\nCommentary:"
        completion = client.chat.completions.create(
            model=model,
            max_tokens=200,
            temperature=float(AITemperatureConfig.get_temperature("marcus")),
            timeout=get_ai_timeout_sec(CONFIG),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = (completion.choices[0].message.content or "").strip()
        if not text:
            return None
        return {
            "schema_version": "ase_narrator.v1",
            "signal_ref": signal_ref,
            "commentary": text,
            "model_used": model,
            "provider": active_provider,
            "advisory_only": True,
            "do_not_auto_apply": True,
        }
    except Exception as exc:
        log.warning("[ASE_NARRATOR] narration failed (fail-closed -> None): %s", exc)
        return None
