"""tools/run_prompt_eval.py — CLI runner for the golden-set prompt eval harness.

Phase 3 of the Athena AI Edge Program. Runs the golden set in
tests/ai_eval/golden_set.py against a configured AI provider, writes a JSON
report to disk, and prints a summary. Fail-closed and never auto-applies
findings.

Usage:
    python tools/run_prompt_eval.py --provider grok --out reports/prompt_eval.json
    python tools/run_prompt_eval.py --surface marcus --out reports/prompt_eval_marcus.json

Safety:
- No auto-apply. The report is for human review only.
- Config-gated by AI_PROMPT_EVAL_ENABLED (default false). When disabled, the
  runner exits with a message and writes nothing.
- If no API key is configured, the runner records per-case
  `llm_call_exception` failures (fail-closed) and still writes the report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make the repo root importable when run as a script
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.ai_eval.golden_set import run_golden_set  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_config() -> dict[str, Any]:
    try:
        from config import CONFIG

        return CONFIG
    except Exception:
        return {}


def _is_eval_enabled(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("AI_PROMPT_EVAL_ENABLED", False))


def _get_api_key(cfg: dict[str, Any]) -> str:
    try:
        from config import get_ai_api_key

        return (get_ai_api_key(cfg) or "").strip()
    except Exception:
        return ""


def _get_model(cfg: dict[str, Any], provider: str) -> str:
    try:
        from config import get_ai_model

        return str(get_ai_model(cfg, preferred_key="MARCUS_MODEL", provider=provider) or "").strip()
    except Exception:
        return ""


def _make_llm_call(cfg: dict[str, Any], provider: str):
    """Return a callable(case, inputs) -> response_dict that calls the LLM.

    Fail-closed: if no api key / no client, returns a callable that raises
    so the harness records an `llm_call_exception` failure per case.
    """
    api_key = _get_api_key(cfg)
    model = _get_model(cfg, provider)
    if not api_key or not model:
        def _bad(_case, _inputs):
            raise RuntimeError("no_api_key_or_model_configured")
        return _bad

    try:
        from config import (
            AITemperatureConfig,
            create_ai_client,
            get_ai_max_retries,
            get_ai_timeout_sec,
        )

        client = create_ai_client(
            cfg,
            api_key=api_key,
            max_retries=get_ai_max_retries(cfg, preferred_key="AI_SDK_MAX_RETRIES"),
            provider=provider,
        )
    except Exception as exc:
        err = f"client_init_failed:{type(exc).__name__}"

        def _bad(_case, _inputs):
            raise RuntimeError(err)
        return _bad

    def _call(case: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any] | None:
        surface = case.get("surface", "marcus")
        prompt = (
            f"Surface: {surface}\nInputs: {json.dumps(inputs)}\n"
            "Return a JSON object with at least a 'decision' field "
            "(one of long/short/neutral/no_trade) and a 'confidence' field (0..1)."
        )
        try:
            completion = client.chat.completions.create(
                model=model,
                max_tokens=400,
                temperature=float(AITemperatureConfig.get_temperature("marcus")),
                timeout=get_ai_timeout_sec(cfg),
                messages=[
                    {"role": "system", "content": "You are an advisory trading AI. Return strict JSON."},
                    {"role": "user", "content": prompt},
                ],
            )
            text = (completion.choices[0].message.content or "").strip()
            # Strip code fences if present
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:]
                text = text.strip()
            return json.loads(text)
        except Exception:
            return None

    return _call


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the golden-set prompt eval harness.")
    parser.add_argument("--provider", default="grok", help="AI provider (grok/openai/anthropic)")
    parser.add_argument("--surface", default=None, help="Optional surface filter (e.g. marcus)")
    parser.add_argument("--out", default="reports/prompt_eval.json", help="Output JSON report path")
    parser.add_argument("--force", action="store_true", help="Run even if AI_PROMPT_EVAL_ENABLED is false")
    args = parser.parse_args(argv)

    cfg = _load_config()
    if not args.force and not _is_eval_enabled(cfg):
        print("[prompt_eval] AI_PROMPT_EVAL_ENABLED is false; nothing to do. Use --force to override.")
        return 0

    llm_call = _make_llm_call(cfg, args.provider)
    report = run_golden_set(llm_call, surface=args.surface)
    report["provider"] = args.provider
    report["generated_at"] = _now_iso()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(
        f"[prompt_eval] provider={args.provider} total={report['total']} "
        f"passed={report['passed']} failed={report['failed']} "
        f"pass_rate={report['pass_rate']} -> {out_path}"
    )
    if report["failed"]:
        for r in report["results"]:
            if not r.get("pass"):
                print(f"  FAIL {r.get('case_id')}: {r.get('failures')}")
    print("[prompt_eval] Advisory only — no auto-apply.")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
