"""athena_research/ai_research_loop.py — Phase 7 agentic research + parameter discovery.

Two advisory, config-gated, default-off, fail-closed capabilities:

1. `run_agentic_research_loop(seed_question, run_backtest, max_steps)` — a
   bounded planner-executor loop: the LLM proposes a research plan, the
   caller-supplied `run_backtest(plan)` executes it (read-only backtest),
   the LLM analyzes the result and suggests the next experiment. Bounded by
   `AI_RESEARCH_MAX_STEPS` (default 4). No auto-apply — findings are returned
   for human review.

2. `propose_parameter_sweep(context)` + `ParameterApprovalQueue` — AI
   proposes parameter sweep candidates; a human-approval queue holds them
   until a human explicitly approves. Approved candidates are returned to
   the caller for manual application. This module NEVER touches the live
   `config.yaml` — it only returns proposed values.

Safety:
- Advisory-only. Never executes trades, never mutates config, never auto-
  applies findings.
- Bounded iterations; fail-closed on any LLM error.
- Parameter proposals are validated against a read-only allowlist and
  bounded ranges; anything touching locked safety fields is rejected.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable

from config import (
    AITemperatureConfig,
    CONFIG,
    create_ai_client,
    get_ai_api_key,
    get_ai_model,
    get_ai_timeout_sec,
    resolve_ai_review_runtime,
)

log = logging.getLogger("athena.ai_research_loop")


def _cfg_bool(key: str, default: bool = False) -> bool:
    try:
        return bool(CONFIG.get(key, default))
    except Exception:
        return default


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(CONFIG.get(key, default))
    except Exception:
        return default


def _client_and_model():
    try:
        runtime = resolve_ai_review_runtime(CONFIG)
        provider = str(runtime.get("provider") or runtime.get("selectedProvider") or "grok")
        api_key = (get_ai_api_key(CONFIG) or str(runtime.get("api_key") or "") or "").strip()
        if not api_key:
            return None, None, provider
        model = str(get_ai_model(CONFIG, preferred_key="RESEARCH_MODEL", provider=provider) or "").strip()
        if not model:
            return None, None, provider
        client = create_ai_client(CONFIG, api_key=api_key, provider=provider)
        return client, model, provider
    except Exception:
        return None, None, "grok"


def _llm_json(system: str, user: str, *, max_tokens: int = 500) -> dict[str, Any] | None:
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
        log.warning("[AI_RESEARCH] LLM call failed (fail-closed): %s", exc)
        return None


# ---------------------------------------------------------------------------
# 1. Agentic research loop
# ---------------------------------------------------------------------------


def run_agentic_research_loop(
    seed_question: str,
    run_backtest: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    max_steps: int | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bounded agentic research loop. No auto-apply.

    `run_backtest(plan_dict) -> result_dict` is supplied by the caller; this
    module never runs backtests itself. The LLM proposes a plan, the caller
    runs it, the LLM analyzes and suggests the next plan, up to max_steps.

    Returns {schema_version, steps:[...], final_synthesis, do_not_auto_apply}.
    """
    if not _cfg_bool("AI_RESEARCH_LOOP_ENABLED", False):
        return {
            "schema_version": "agentic_research_loop.v1",
            "enabled": False,
            "steps": [],
            "final_synthesis": None,
            "do_not_auto_apply": True,
        }
    bound = int(max_steps or _cfg_int("AI_RESEARCH_MAX_STEPS", 4))
    bound = max(1, min(bound, 8))
    sys_prompt = (
        "You are an advisory research planner for a paper-trading research lab. "
        "Propose a small, concrete research plan as strict JSON: "
        "{hypothesis, params:{...}, focus_metric}. Then, given a backtest "
        "result, analyze it and either propose the next plan or set "
        "done=true with a final_synthesis. Never claim authority to change "
        "live config or execute trades. Advisory-only."
    )
    steps: list[dict[str, Any]] = []
    ctx = context if isinstance(context, dict) else {}
    current_plan: dict[str, Any] | None = None
    for i in range(bound):
        if i == 0:
            user = f"Seed question:\n{seed_question}\nContext:\n{json.dumps(ctx, default=str)[:3000]}\nPropose first plan."
        else:
            last = steps[-1]
            user = (
                f"Previous plan:\n{json.dumps(last.get('plan') or {}, default=str)[:1500]}\n"
                f"Result:\n{json.dumps(last.get('result') or {}, default=str)[:3000]}\n"
                "Analyze and either propose the next plan or return done=true with final_synthesis."
            )
        plan = _llm_json(sys_prompt, user, max_tokens=400)
        if not isinstance(plan, dict):
            steps.append({"step": i, "plan": None, "result": None, "error": "plan_llm_failed"})
            break
        if plan.get("done"):
            steps.append({"step": i, "plan": plan, "result": None, "done": True})
            break
        try:
            result = run_backtest(plan)
        except Exception as exc:
            steps.append({"step": i, "plan": plan, "result": None, "error": f"backtest_exception:{type(exc).__name__}"})
            break
        if not isinstance(result, dict):
            result = {"raw": str(result)[:1000]}
        steps.append({"step": i, "plan": plan, "result": result})
        current_plan = plan
    # Final synthesis
    final = None
    if steps:
        synth_user = (
            f"Seed question:\n{seed_question}\nSteps:\n{json.dumps(steps, default=str)[:6000]}\n"
            "Return a final synthesis: {key_findings:[...], recommended_next_experiments:[...], confidence:0..1}."
        )
        final = _llm_json(sys_prompt, synth_user, max_tokens=500)
    return {
        "schema_version": "agentic_research_loop.v1",
        "enabled": True,
        "steps": steps,
        "final_synthesis": final,
        "do_not_auto_apply": True,
    }


# ---------------------------------------------------------------------------
# 2. Parameter discovery with human-approval queue
# ---------------------------------------------------------------------------


# Read-only allowlist of parameters the AI may propose sweeps for. Anything
# touching locked safety fields is rejected outright.
PARAM_ALLOWLIST = frozenset(
    {
        "engine_a_ema_periods",
        "engine_a_rsi_period",
        "engine_b_struct_confirmed_min_bars",
        "engine_d_scalp_lookback",
        "news_sentiment_decay_hours",
        "confluence_min_score",
    }
)

LOCKED_PARAM_PREFIXES = ("risk_", "execution_", "guardian_", "kill_switch", "broker_", "sl_", "tp_", "rr_", "freshness_")


def _validate_param_proposal(name: str, value: Any) -> bool:
    """Reject any param not in the allowlist or touching locked safety fields."""
    n = str(name or "").strip().lower()
    if not n:
        return False
    if any(n.startswith(p) for p in LOCKED_PARAM_PREFIXES):
        return False
    if n not in PARAM_ALLOWLIST:
        return False
    if isinstance(value, (int, float)):
        try:
            f = float(value)
            if not (-1e9 < f < 1e9):
                return False
        except Exception:
            return False
    return True


def propose_parameter_sweep(context: dict[str, Any] | None) -> dict[str, Any]:
    """Ask the LLM to propose a parameter sweep. No auto-apply.

    Returns {schema_version, proposals:[{param, suggested_value, rationale,
    approved:bool}], rejected:[...], advisory_only, do_not_auto_apply}.
    Approved is always false here; humans set it via the approval queue.
    """
    if not _cfg_bool("AI_PARAM_DISCOVERY_ENABLED", False):
        return {
            "schema_version": "parameter_sweep_proposal.v1",
            "enabled": False,
            "proposals": [],
            "rejected": [],
            "advisory_only": True,
            "do_not_auto_apply": True,
        }
    sys_prompt = (
        "You are an advisory parameter-discovery agent for a paper-trading research lab. "
        "Propose 1-3 parameter sweep candidates as strict JSON: "
        "{proposals:[{param, suggested_value, rationale}]}. Only propose parameters from "
        f"this allowlist: {sorted(PARAM_ALLOWLIST)}. Never propose changes to risk, "
        "execution, guardian, SL/TP, RR, or freshness fields. Advisory-only."
    )
    user = f"Context:\n{json.dumps(context or {}, default=str)[:3000]}\nPropose candidates."
    raw = _llm_json(sys_prompt, user, max_tokens=400)
    proposals: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if isinstance(raw, dict) and isinstance(raw.get("proposals"), list):
        for p in raw["proposals"]:
            if not isinstance(p, dict):
                continue
            name = str(p.get("param") or "")
            val = p.get("suggested_value")
            if _validate_param_proposal(name, val):
                proposals.append(
                    {
                        "param": name,
                        "suggested_value": val,
                        "rationale": str(p.get("rationale") or "")[:500],
                        "approved": False,
                    }
                )
            else:
                rejected.append({"param": name, "suggested_value": val, "reason": "not_in_allowlist_or_locked"})
    return {
        "schema_version": "parameter_sweep_proposal.v1",
        "enabled": True,
        "proposals": proposals,
        "rejected": rejected,
        "advisory_only": True,
        "do_not_auto_apply": True,
    }


class ParameterApprovalQueue:
    """In-memory human-approval queue for parameter sweep proposals.

    Thread-safe. Proposals start unapproved. A human calls `approve(item_id)`
    to mark one approved. `approved_items()` returns only approved items for
    the caller to apply manually. This queue NEVER touches live config.yaml.
    """

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._counter = 0

    def enqueue(self, proposal: dict[str, Any]) -> str:
        with self._lock:
            self._counter += 1
            item_id = f"pq_{self._counter}"
            self._items[item_id] = {
                "id": item_id,
                "param": proposal.get("param"),
                "suggested_value": proposal.get("suggested_value"),
                "rationale": proposal.get("rationale"),
                "approved": False,
            }
            return item_id

    def approve(self, item_id: str) -> bool:
        with self._lock:
            item = self._items.get(item_id)
            if item is None:
                return False
            item["approved"] = True
            return True

    def reject(self, item_id: str) -> bool:
        with self._lock:
            if item_id in self._items:
                self._items[item_id]["approved"] = False
                self._items[item_id]["rejected"] = True
                return True
            return False

    def approved_items(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._items.values() if v.get("approved")]

    def pending_items(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._items.values() if not v.get("approved") and not v.get("rejected")]

    def all_items(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._items.values()]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
