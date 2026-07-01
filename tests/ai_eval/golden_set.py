"""Golden-set prompt regression harness (Phase 3 eval loop).

This module defines a small, hand-curated set of golden cases for each AI
surface (marcus, engine_b, vision, scalp, engine_c, debate, news, lee,
strategist, research). Each case specifies the inputs to feed the prompt
builder and a set of structural/decision constraints the LLM response must
satisfy. The harness evaluates a response dict against the case and reports
pass/fail with concrete failure reasons — but it does NOT call the LLM
itself. The CLI runner (tools/run_prompt_eval.py) supplies the LLM call
function and feeds responses into `evaluate_response`.

Safety:
- No auto-apply. The harness only reports pass/fail; it never mutates
  prompts, config, or scoring.
- Fail-closed: an unreachable/missing case is a failure, not a skip.
- Pure data + pure functions; no network, no DB, no side effects.
"""

from __future__ import annotations

from typing import Any, Callable

# ---------------------------------------------------------------------------
# Golden cases
# ---------------------------------------------------------------------------
#
# Each case is a dict with:
#   id          — stable identifier
#   surface     — AI surface name (marcus, engine_b, vision, ...)
#   inputs      — context dict passed to the prompt builder / LLM
#   expect      — constraints:
#       required_fields    — top-level keys that must be present
#       decision_in        — allowed values for the "decision" field
#       confidence_range   — (lo, hi) bounds for "confidence" if present
#       max_response_chars — soft upper bound on response length
#       must_contain_keys  — nested keys that must exist (dotted paths)
#       forbidden_keys     — top-level keys that must NOT be present
# ---------------------------------------------------------------------------

GOLDEN_CASES: list[dict[str, Any]] = [
    {
        "id": "marcus_basic_long_setup",
        "surface": "marcus",
        "inputs": {
            "symbol": "BTCUSDT",
            "asset_type": "crypto",
            "trend": "bullish",
            "structure": "higher-high/higher-low",
            "rr": 2.5,
        },
        "expect": {
            "required_fields": ["decision", "confidence"],
            "decision_in": ["long", "short", "neutral", "no_trade"],
            "confidence_range": (0.0, 1.0),
            "max_response_chars": 6000,
        },
    },
    {
        "id": "marcus_basic_short_setup",
        "surface": "marcus",
        "inputs": {
            "symbol": "ETHUSDT",
            "asset_type": "crypto",
            "trend": "bearish",
            "structure": "lower-high/lower-low",
            "rr": 1.8,
        },
        "expect": {
            "required_fields": ["decision", "confidence"],
            "decision_in": ["long", "short", "neutral", "no_trade"],
            "confidence_range": (0.0, 1.0),
            "max_response_chars": 6000,
        },
    },
    {
        "id": "engine_b_naked_structure",
        "surface": "engine_b",
        "inputs": {
            "symbol": "BTCUSDT",
            "structure": "sweep+liquidity_grab",
            "rr": 2.0,
        },
        "expect": {
            "required_fields": ["decision"],
            "decision_in": ["long", "short", "neutral", "no_trade"],
            "max_response_chars": 6000,
        },
    },
    {
        "id": "vision_chart_review_basic",
        "surface": "vision",
        "inputs": {
            "symbol": "BTCUSDT",
            "timeframe": "4h",
        },
        "expect": {
            "required_fields": ["decision"],
            "decision_in": ["long", "short", "neutral", "no_trade"],
            "max_response_chars": 8000,
            "forbidden_keys": ["auto_upgrade_trade"],
        },
    },
    {
        "id": "scalp_review_d_basic",
        "surface": "scalp_d",
        "inputs": {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
        },
        "expect": {
            "required_fields": ["decision"],
            "decision_in": ["long", "short", "neutral", "no_trade"],
            "max_response_chars": 6000,
        },
    },
    {
        "id": "engine_c_weight_verdict",
        "surface": "engine_c",
        "inputs": {
            "symbol": "BTCUSDT",
            "agreement": "both_agree",
        },
        "expect": {
            "required_fields": ["decision"],
            "decision_in": ["long", "short", "neutral", "no_trade"],
            "max_response_chars": 4000,
            "forbidden_keys": ["weight_override"],  # AI must not claim to override weights
        },
    },
    {
        "id": "debate_bull_basic",
        "surface": "debate_bull",
        "inputs": {
            "symbol": "BTCUSDT",
            "stance": "bull",
        },
        "expect": {
            "required_fields": ["stance"],
            "max_response_chars": 4000,
        },
    },
    {
        "id": "debate_judge_basic",
        "surface": "debate_judge",
        "inputs": {
            "symbol": "BTCUSDT",
        },
        "expect": {
            "required_fields": ["verdict"],
            "max_response_chars": 4000,
        },
    },
    {
        "id": "news_sentiment_basic",
        "surface": "news",
        "inputs": {
            "headline": "Fed holds rates steady",
            "symbol": "BTCUSDT",
        },
        "expect": {
            "required_fields": ["sentiment"],
            "max_response_chars": 3000,
        },
    },
    {
        "id": "lee_confirmation_basic",
        "surface": "lee",
        "inputs": {
            "symbol": "BTCUSDT",
        },
        "expect": {
            "required_fields": ["confirmation"],
            "max_response_chars": 4000,
        },
    },
]


def get_cases(surface: str | None = None) -> list[dict[str, Any]]:
    """Return golden cases, optionally filtered by surface."""
    if surface is None:
        return list(GOLDEN_CASES)
    return [c for c in GOLDEN_CASES if c.get("surface") == surface]


def list_surfaces() -> list[str]:
    """Return the distinct surface names covered by the golden set."""
    seen: list[str] = []
    for c in GOLDEN_CASES:
        s = c.get("surface")
        if s and s not in seen:
            seen.append(s)
    return seen


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _get_path(d: dict[str, Any], dotted: str) -> Any:
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def evaluate_response(case: dict[str, Any], response: dict[str, Any] | None) -> dict[str, Any]:
    """Evaluate a single LLM response dict against a golden case.

    Returns {case_id, surface, pass, failures: [...]}. Never raises.
    """
    case_id = case.get("id", "<unknown>")
    surface = case.get("surface", "<unknown>")
    expect = case.get("expect", {}) or {}
    failures: list[str] = []
    if not isinstance(response, dict):
        return {
            "case_id": case_id,
            "surface": surface,
            "pass": False,
            "failures": ["response_not_dict"],
        }

    # required_fields
    for f in expect.get("required_fields", []) or []:
        if f not in response:
            failures.append(f"missing_field:{f}")

    # forbidden_keys
    for f in expect.get("forbidden_keys", []) or []:
        if f in response:
            failures.append(f"forbidden_field_present:{f}")

    # decision_in
    decision_in = expect.get("decision_in")
    if decision_in is not None:
        dec = response.get("decision")
        if dec is not None and dec not in decision_in:
            failures.append(f"decision_out_of_range:{dec}")

    # confidence_range
    cr = expect.get("confidence_range")
    if cr is not None and "confidence" in response:
        try:
            conf = float(response.get("confidence"))
            lo, hi = float(cr[0]), float(cr[1])
            if not (lo <= conf <= hi):
                failures.append(f"confidence_out_of_range:{conf}")
        except Exception:
            failures.append("confidence_not_numeric")

    # max_response_chars (rough: measure JSON length)
    max_chars = expect.get("max_response_chars")
    if max_chars is not None:
        try:
            import json as _json

            n = len(_json.dumps(response))
            if n > int(max_chars):
                failures.append(f"response_too_long:{n}>{max_chars}")
        except Exception:
            failures.append("response_length_unmeasurable")

    # must_contain_keys (dotted)
    for path in expect.get("must_contain_keys", []) or []:
        if _get_path(response, path) is None:
            failures.append(f"missing_nested:{path}")

    return {
        "case_id": case_id,
        "surface": surface,
        "pass": len(failures) == 0,
        "failures": failures,
    }


def run_golden_set(
    llm_call: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None],
    surface: str | None = None,
) -> dict[str, Any]:
    """Run the golden set against an LLM call function.

    `llm_call(case, inputs) -> response_dict` is supplied by the caller (the
    CLI tool). The harness never calls the LLM directly.

    Returns a summary report:
      {schema_version, total, passed, failed, pass_rate, results: [...],
       do_not_auto_apply: true}

    Never raises; per-case exceptions are caught and recorded as failures.
    """
    cases = get_cases(surface)
    results: list[dict[str, Any]] = []
    for case in cases:
        try:
            resp = llm_call(case, case.get("inputs", {}))
        except Exception as exc:
            results.append(
                {
                    "case_id": case.get("id"),
                    "surface": case.get("surface"),
                    "pass": False,
                    "failures": [f"llm_call_exception:{type(exc).__name__}"],
                }
            )
            continue
        results.append(evaluate_response(case, resp))
    total = len(results)
    passed = sum(1 for r in results if r.get("pass"))
    failed = total - passed
    pass_rate = (passed / total) if total else 0.0
    return {
        "schema_version": "golden_set_eval.v1",
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(pass_rate, 4),
        "results": results,
        "do_not_auto_apply": True,
    }
