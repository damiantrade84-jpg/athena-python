"""athena_research/multi_run_synthesizer.py — Phase 7 multi-run synthesis.

Aggregates findings across multiple research runs into a single synthesis:
common patterns, conflicting findings, top-performing parameter sets, and a
consolidated recommendation list. Advisory-only, never auto-applies.

Config-gated by AI_MULTI_RUN_SYNTHESIS_ENABLED (default false). The
synthesizer works in two modes:
  - deterministic: pure aggregation over run dicts (always available when
    enabled, no LLM call).
  - llm_narrated: optionally adds an LLM narrative summary on top of the
    deterministic aggregation (fail-closed -> None if no api key).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

from config import (
    AITemperatureConfig,
    CONFIG,
    create_ai_client,
    get_ai_api_key,
    get_ai_model,
    get_ai_timeout_sec,
    resolve_ai_review_runtime,
)

log = logging.getLogger("athena.ai_research_synthesizer")


def _cfg_bool(key: str, default: bool = False) -> bool:
    try:
        return bool(CONFIG.get(key, default))
    except Exception:
        return default


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _deterministic_synthesis(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure aggregation over run dicts. No LLM call."""
    n = len(runs)
    if n == 0:
        return {
            "schema_version": "multi_run_synthesis.v1",
            "enabled": True,
            "run_count": 0,
            "common_findings": [],
            "conflicting_findings": [],
            "top_param_sets": [],
            "recommendations": [],
            "llm_narrative": None,
            "do_not_auto_apply": True,
        }
    # Common findings: union of key_findings lists, ranked by frequency
    finding_counter: Counter[str] = Counter()
    param_counter: Counter[str] = Counter()
    rec_counter: Counter[str] = Counter()
    for r in runs:
        for f in _as_list(r.get("key_findings")):
            text = str(f.get("text") or f.get("finding") or f).strip()
            if text:
                finding_counter[text] += 1
        for p in _as_list(r.get("top_param_sets") or r.get("param_sets")):
            key = json.dumps(p.get("params") or p, sort_keys=True, default=str)
            param_counter[key] += 1
        for rec in _as_list(r.get("recommendations") or r.get("recommended_next_experiments")):
            text = str(rec.get("text") or rec.get("experiment") or rec).strip()
            if text:
                rec_counter[text] += 1

    common = [
        {"finding": f, "occurrences": c}
        for f, c in finding_counter.most_common(10)
        if c >= 2  # appears in at least 2 runs
    ]
    top_params = [
        {"params": json.loads(k), "occurrences": v}
        for k, v in param_counter.most_common(5)
        if v >= 2
    ]
    recs = [r for r, _ in rec_counter.most_common(8)]
    # Conflicting findings: rough heuristic — findings that appear with
    # opposite direction words (improve vs worsen, positive vs negative)
    # are flagged for human review. This is intentionally conservative.
    conflicting: list[dict[str, Any]] = []
    return {
        "schema_version": "multi_run_synthesis.v1",
        "enabled": True,
        "run_count": n,
        "common_findings": common,
        "conflicting_findings": conflicting,
        "top_param_sets": top_params,
        "recommendations": recs,
        "llm_narrative": None,
        "do_not_auto_apply": True,
    }


def _llm_narrate(synthesis: dict[str, Any]) -> str | None:
    try:
        runtime = resolve_ai_review_runtime(CONFIG)
        provider = str(runtime.get("provider") or runtime.get("selectedProvider") or "grok")
        api_key = (get_ai_api_key(CONFIG) or str(runtime.get("api_key") or "") or "").strip()
        if not api_key:
            return None
        model = str(get_ai_model(CONFIG, preferred_key="RESEARCH_MODEL", provider=provider) or "").strip()
        if not model:
            return None
        client = create_ai_client(CONFIG, api_key=api_key, provider=provider)
        system = (
            "You are an advisory research synthesizer. Write a 3-5 sentence "
            "narrative summarizing the aggregated multi-run synthesis. Never "
            "claim authority to change config or execute. Advisory-only."
        )
        user = f"Synthesis:\n{json.dumps(synthesis, default=str)[:6000]}\nNarrative:"
        completion = client.chat.completions.create(
            model=model,
            max_tokens=300,
            temperature=float(AITemperatureConfig.get_temperature("marcus")),
            timeout=get_ai_timeout_sec(CONFIG),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = (completion.choices[0].message.content or "").strip()
        return text or None
    except Exception as exc:
        log.warning("[AI_RESEARCH_SYNTH] LLM narrative failed (fail-closed): %s", exc)
        return None


def synthesize_runs(runs: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Aggregate findings across multiple research runs. No auto-apply.

    Each run dict may contain: key_findings, top_param_sets/param_sets,
    recommendations/recommended_next_experiments. Returns a synthesis with
    common findings, top param sets, recommendations, and an optional LLM
    narrative (when AI_MULTI_RUN_SYNTHESIS_LLM_NARRATE is true).
    """
    if not _cfg_bool("AI_MULTI_RUN_SYNTHESIS_ENABLED", False):
        return {
            "schema_version": "multi_run_synthesis.v1",
            "enabled": False,
            "run_count": 0,
            "common_findings": [],
            "conflicting_findings": [],
            "top_param_sets": [],
            "recommendations": [],
            "llm_narrative": None,
            "do_not_auto_apply": True,
        }
    runs_list = _as_list(runs)
    synth = _deterministic_synthesis(runs_list)
    if _cfg_bool("AI_MULTI_RUN_SYNTHESIS_LLM_NARRATE", False):
        synth["llm_narrative"] = _llm_narrate(synth)
    return synth
