"""ai_reasoning_depth.py — Phase 5 reasoning-depth primitives.

Consolidates four advisory, config-gated, default-off, fail-closed
capabilities:

1. `run_multi_role_debate(signal, roles)` — adds `risk_officer` and
   `macro_strategist` advisory roles alongside the existing Bull/Bear/Judge
   debate. The judge-style aggregation here is advisory-only and never
   overrides the core debate's grade or `allowed` flag. Honors
   `FORCE_DEBATE_DOWNGRADE_ONLY`: extra roles can only reinforce a downgrade,
   never an upgrade.

2. `tree_of_thought(signal, n_branches)` — generates multiple independent
   reasoning branches for high-conviction setups and picks the most-cited
   verdict. Config-gated by `AI_TOT_ENABLED`; conductor-routed when score >=
   `AI_TOT_MIN_SCORE`.

3. `self_critique(verdict, context)` — a self-critique pass that reviews a
   prior verdict and surfaces weaknesses. Returns a `self_critique` dict
   (advisory-only, never mutates the input verdict).

4. `cross_engine_consistency(a, b, c, d)` — checks A/B/C/D agreement and
   flags contradictions (direction mismatches, allow/block disagreements).
   Returns a consistency report; does NOT call the contradiction detector's
   mutation path.

Safety:
- Advisory-only. Never approves, blocks, executes, or mutates scoring.
- All functions default-off via config; fail-closed (return None / empty on
  any error).
- Never raises.
- Honors FORCE_DEBATE_DOWNGRADE_ONLY for the multi-role aggregation.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
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

try:
    from ai_safety_constants import AISafetyConstants
except Exception:
    AISafetyConstants = None  # type: ignore[assignment]

log = logging.getLogger("athena.ai_reasoning_depth")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _cfg_bool(key: str, default: bool = False) -> bool:
    try:
        return bool(CONFIG.get(key, default))
    except Exception:
        return default


def _cfg_float(key: str, default: float) -> float:
    try:
        return float(CONFIG.get(key, default))
    except Exception:
        return default


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(CONFIG.get(key, default))
    except Exception:
        return default


def _downgrade_only() -> bool:
    if AISafetyConstants is None:
        return True
    try:
        return bool(getattr(AISafetyConstants, "FORCE_DEBATE_DOWNGRADE_ONLY", True))
    except Exception:
        return True


def _client_and_model(provider_hint: str = "grok"):
    """Return (client, model, provider) or (None, None, provider) if unavailable."""
    try:
        runtime = resolve_ai_review_runtime(CONFIG)
        provider = str(runtime.get("provider") or runtime.get("selectedProvider") or provider_hint)
        api_key = (get_ai_api_key(CONFIG) or str(runtime.get("api_key") or "") or "").strip()
        if not api_key:
            return None, None, provider
        model = str(get_ai_model(CONFIG, preferred_key="MARCUS_MODEL", provider=provider) or "").strip()
        if not model:
            return None, None, provider
        client = create_ai_client(CONFIG, api_key=api_key, provider=provider)
        return client, model, provider
    except Exception:
        return None, None, provider_hint


def _llm_json(system: str, user: str, *, max_tokens: int = 400) -> dict[str, Any] | None:
    """Call the LLM and parse a JSON object. Fail-closed -> None. Never raises."""
    try:
        client, model, _ = _client_and_model()
        if client is None or not model:
            return None
        completion = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
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
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception as exc:
        log.warning("[AI_REASONING] LLM call failed (fail-closed): %s", exc)
        return None


# ---------------------------------------------------------------------------
# 1. Multi-role debate
# ---------------------------------------------------------------------------


DEFAULT_EXTRA_ROLES = ("risk_officer", "macro_strategist")


def _role_system_prompt(role: str) -> str:
    if role == "risk_officer":
        return (
            "You are a risk officer reviewing a trade setup. Identify the top 2-3 "
            "risks (stop distance, R:R, correlation, event risk, drawdown). Return "
            "strict JSON: {stance, key_risks:[...], downgrade_recommended:bool, reasoning}. "
            "Advisory-only; never claim authority to approve or execute."
        )
    if role == "macro_strategist":
        return (
            "You are a macro strategist reviewing a trade setup. Identify macro "
            "headwinds/tailwinds (regime, calendar, rates, DXY). Return strict JSON: "
            "{stance, macro_factors:[...], downgrade_recommended:bool, reasoning}. "
            "Advisory-only; never claim authority to approve or execute."
        )
    return "You are an advisory debate role. Return strict JSON with stance and reasoning."


def _run_one_role(role: str, signal: dict[str, Any]) -> dict[str, Any] | None:
    sys_prompt = _role_system_prompt(role)
    ctx = {
        "signal_id": signal.get("trace_id") or signal.get("signal_id"),
        "symbol": signal.get("display") or signal.get("pair"),
        "direction": signal.get("direction"),
        "score": signal.get("confluenceScore"),
        "regime": signal.get("trendState"),
        "asset_type": signal.get("type"),
    }
    user = f"Setup context:\n{json.dumps(ctx, default=str)[:4000]}\nReturn JSON."
    return _llm_json(sys_prompt, user, max_tokens=300)


def run_multi_role_debate(
    signal: dict[str, Any],
    roles: list[str] | None = None,
    *,
    core_debate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run extra advisory debate roles in parallel.

    Returns:
      {schema_version, roles: {role_name: {...}|None}, aggregate, advisory_only,
       do_not_auto_apply}

    When `core_debate` is supplied, the aggregate honors
    FORCE_DEBATE_DOWNGRADE_ONLY: any role recommending a downgrade can only
    reinforce a downgrade of the core debate's allowed flag, never upgrade it.
    """
    if not _cfg_bool("AI_MULTI_ROLE_DEBATE_ENABLED", False):
        return {
            "schema_version": "multi_role_debate.v1",
            "enabled": False,
            "roles": {},
            "aggregate": None,
            "advisory_only": True,
            "do_not_auto_apply": True,
        }
    chosen = list(roles or CONFIG.get("DEBATE_ROLES") or DEFAULT_EXTRA_ROLES)
    chosen = [r for r in chosen if isinstance(r, str) and r]
    roles_out: dict[str, Any] = {}
    if chosen:
        try:
            with ThreadPoolExecutor(max_workers=min(4, len(chosen))) as pool:
                futures = {pool.submit(_run_one_role, r, signal): r for r in chosen}
                for fut in futures:
                    role = futures[fut]
                    try:
                        roles_out[role] = fut.result()
                    except Exception as exc:
                        log.warning("[AI_REASONING] role %s failed: %s", role, exc)
                        roles_out[role] = None
        except Exception as exc:
            log.warning("[AI_REASONING] multi-role pool failed: %s", exc)

    # Aggregate: count downgrade recommendations
    downgrade_votes = 0
    valid = 0
    for r in chosen:
        out = roles_out.get(r)
        if isinstance(out, dict):
            valid += 1
            if bool(out.get("downgrade_recommended")):
                downgrade_votes += 1

    aggregate: dict[str, Any] = {
        "roles_run": len(chosen),
        "roles_valid": valid,
        "downgrade_votes": downgrade_votes,
        "suggest_downgrade": downgrade_votes > 0 and valid > 0,
    }
    # Honor downgrade-only: if core debate says allowed=True and any role
    # suggests downgrade, we surface a downgrade recommendation but we do NOT
    # flip the core debate's allowed flag (we never mutate it). We only report
    # an advisory `recommended_core_allowed` that is allowed ANDed with NOT
    # suggest_downgrade — purely advisory.
    if isinstance(core_debate, dict):
        core_allowed = bool(core_debate.get("allowed"))
        advisory_allowed = core_allowed and not aggregate["suggest_downgrade"]
        if _downgrade_only() and core_allowed and aggregate["suggest_downgrade"]:
            advisory_allowed = False  # downgrade-only
        aggregate["core_allowed"] = core_allowed
        aggregate["advisory_recommended_allowed"] = advisory_allowed
        aggregate["downgrade_only_enforced"] = _downgrade_only()

    return {
        "schema_version": "multi_role_debate.v1",
        "enabled": True,
        "roles": roles_out,
        "aggregate": aggregate,
        "advisory_only": True,
        "do_not_auto_apply": True,
    }


# ---------------------------------------------------------------------------
# 2. Tree-of-thought
# ---------------------------------------------------------------------------


def tree_of_thought(signal: dict[str, Any], n_branches: int | None = None) -> dict[str, Any] | None:
    """Generate N independent reasoning branches and pick the most-cited verdict.

    Config-gated by AI_TOT_ENABLED (default false). Conductor-routed when
    score >= AI_TOT_MIN_SCORE. Fail-closed -> None.
    """
    if not _cfg_bool("AI_TOT_ENABLED", False):
        return None
    try:
        score = float(signal.get("confluenceScore") or 0.0)
    except Exception:
        score = 0.0
    if score < _cfg_float("AI_TOT_MIN_SCORE", 7.5):
        return None
    n = int(n_branches or _cfg_int("AI_TOT_BRANCHES", 3))
    n = max(2, min(n, 5))

    sys_prompt = (
        "You are an advisory reasoning branch for a trade setup. Independently "
        "reason about the setup and return strict JSON: "
        "{verdict:'long'|'short'|'neutral'|'no_trade', confidence:0..1, key_reason}. "
        "Advisory-only; never claim authority to approve or execute."
    )
    branches: list[dict[str, Any] | None] = []
    try:
        with ThreadPoolExecutor(max_workers=n) as pool:
            futs = [pool.submit(_llm_json, sys_prompt, json.dumps(signal, default=str)[:4000], 250) for _ in range(n)]
            branches = [f.result() for f in futs]
    except Exception as exc:
        log.warning("[AI_REASONING] ToT pool failed: %s", exc)
        return None

    valid = [b for b in branches if isinstance(b, dict) and b.get("verdict")]
    if not valid:
        return None
    # Most-cited verdict
    counts: dict[str, int] = {}
    for b in valid:
        v = str(b.get("verdict")).lower()
        counts[v] = counts.get(v, 0) + 1
    best = max(counts, key=counts.get)
    avg_conf = sum(float(b.get("confidence") or 0.0) for b in valid) / len(valid)
    return {
        "schema_version": "tree_of_thought.v1",
        "branches_run": n,
        "branches_valid": len(valid),
        "verdict_distribution": counts,
        "consensus_verdict": best,
        "avg_confidence": round(avg_conf, 4),
        "advisory_only": True,
        "do_not_auto_apply": True,
    }


# ---------------------------------------------------------------------------
# 3. Self-critique
# ---------------------------------------------------------------------------


def self_critique(verdict: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Self-critique pass over a prior verdict. Fail-closed -> None.

    Returns {schema_version, weaknesses:[...], stands:bool, reasoning,
    advisory_only}. Never mutates the input verdict.
    """
    if not _cfg_bool("AI_SELF_CRITIQUE_ENABLED", False):
        return None
    if not isinstance(verdict, dict):
        return None
    sys_prompt = (
        "You are a self-critique pass reviewing a prior AI verdict. Identify "
        "2-3 weaknesses, missing evidence, or unstated assumptions. Return strict "
        "JSON: {weaknesses:[...], stands:bool, reasoning}. Advisory-only."
    )
    user = f"Prior verdict:\n{json.dumps(verdict, default=str)[:4000]}\nContext:\n{json.dumps(context or {}, default=str)[:2000]}\nCritique:"
    out = _llm_json(sys_prompt, user, max_tokens=300)
    if out is None:
        return None
    return {
        "schema_version": "self_critique.v1",
        "weaknesses": out.get("weaknesses") or [],
        "stands": bool(out.get("stands", True)),
        "reasoning": out.get("reasoning") or "",
        "advisory_only": True,
        "do_not_auto_apply": True,
    }


# ---------------------------------------------------------------------------
# 4. Cross-engine consistency
# ---------------------------------------------------------------------------


def _engine_direction(engine: dict[str, Any] | None) -> str | None:
    if not isinstance(engine, dict):
        return None
    for key in ("direction", "ai_decision", "decision"):
        v = engine.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return None


def _engine_allowed(engine: dict[str, Any] | None) -> bool | None:
    if not isinstance(engine, dict):
        return None
    for key in ("allowed", "entryAllowedNow", "entry_allowed_now", "trade"):
        v = engine.get(key)
        if isinstance(v, bool):
            return v
    return None


def cross_engine_consistency(
    engine_a: dict[str, Any] | None,
    engine_b: dict[str, Any] | None,
    engine_c: dict[str, Any] | None = None,
    engine_d: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check A/B/C/D agreement. Advisory-only, never raises.

    Returns {schema_version, directions, alloweds, direction_agreement:bool,
    allow_agreement:bool, contradictions:[...], advisory_only}.
    """
    engines = {"A": engine_a, "B": engine_b, "C": engine_c, "D": engine_d}
    directions = {k: _engine_direction(v) for k, v in engines.items() if v is not None}
    alloweds = {k: _engine_allowed(v) for k, v in engines.items() if v is not None}
    contradictions: list[str] = []

    present_dirs = [v for v in directions.values() if v is not None]
    if present_dirs:
        # Long vs short is a contradiction; neutral is compatible with either.
        longs = sum(1 for d in present_dirs if d == "long")
        shorts = sum(1 for d in present_dirs if d == "short")
        if longs > 0 and shorts > 0:
            contradictions.append("direction_long_short_conflict")
    direction_agreement = not contradictions

    present_allows = [v for v in alloweds.values() if v is not None]
    allow_agreement = True
    if len(present_allows) >= 2 and any(present_allows) and not all(present_allows):
        allow_agreement = False
        contradictions.append("allow_block_conflict")

    return {
        "schema_version": "cross_engine_consistency.v1",
        "directions": directions,
        "alloweds": alloweds,
        "direction_agreement": direction_agreement,
        "allow_agreement": allow_agreement,
        "contradictions": contradictions,
        "advisory_only": True,
        "do_not_auto_apply": True,
    }
