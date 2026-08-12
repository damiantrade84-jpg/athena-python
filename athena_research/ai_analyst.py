"""
Athena Research Lab — AI Research Analyst
Reads aggregate CSV outputs and produces an AI-authored research review.

Safety contract:
  - Does NOT import or call live execution modules.
  - Does NOT modify live thresholds or config.yaml.
  - Does NOT auto-apply any recommendation.
  - All outputs are report files only.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from athena_research.prompt_builder import build_prompt

log = logging.getLogger(__name__)


# ─── AI provider (pluggable) ──────────────────────────────────────────────────

def _get_ai_client(provider: str = "auto") -> Optional[object]:
    """
    Return an OpenAI-compatible client using the same env var / config pattern
    as the rest of Athena.  Never hardcodes API keys.
    """
    # Try to reuse project config helpers (non-execution modules only)
    try:
        from config import CONFIG, create_ai_client, get_ai_api_key
        api_key = get_ai_api_key(CONFIG)
        if api_key:
            return create_ai_client(CONFIG, api_key=api_key)
    except ImportError:
        pass

    # Fallback: read XAI_API_KEY directly from env
    import os
    api_key = os.environ.get("XAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if api_key:
        try:
            from config import create_ai_client
            base_url = os.environ.get("AI_BASE_URL", "https://api.x.ai/v1")
            return create_ai_client({"AI_BASE_URL": base_url}, api_key=api_key)
        except ImportError:
            pass

    return None


def _get_model_name(cfg_model: Optional[str] = None) -> str:
    try:
        from config import get_ai_model, CONFIG
        return cfg_model or get_ai_model(CONFIG)
    except ImportError:
        pass
    import os
    return cfg_model or os.environ.get("XAI_MODEL", os.environ.get("AI_MODEL", "grok-4.6"))


# ─── Deterministic fallback review ───────────────────────────────────────────

def _log_research_ai_review(
    *,
    run_id: str,
    model: str,
    prompt_text: str,
    action_plan: dict | None,
    parse_success: bool,
    schema_valid: bool,
) -> None:
    try:
        from ai_review_logger import (
            AI_STATE_CAUTION,
            AI_STATE_REVIEW_INCOMPLETE,
            REVIEW_TYPE_RESEARCH_AI,
            log_ai_review,
        )
        from config import CONFIG, get_ai_provider_label

        log_ai_review(
            symbol=run_id,
            asset_type="research",
            review_type=REVIEW_TYPE_RESEARCH_AI,
            model=model,
            provider=get_ai_provider_label(CONFIG),
            prompt_version="research_ai_analyst_v1",
            input_packet=prompt_text,
            has_chart_image=False,
            candle_freshness_status="not_applicable",
            engine_a_state=(action_plan or {}).get("engine_a") if isinstance(action_plan, dict) else None,
            engine_b_state=(action_plan or {}).get("engine_b") if isinstance(action_plan, dict) else None,
            engine_c_state=None,
            engine_d_state=(action_plan or {}).get("engine_d") if isinstance(action_plan, dict) else None,
            risk_state=None,
            ai_review_state=AI_STATE_CAUTION if parse_success else AI_STATE_REVIEW_INCOMPLETE,
            ai_confidence=None,
            contradictions_count=0,
            missing_information_count=0 if parse_success else 1,
            parse_success=parse_success,
            schema_valid=schema_valid,
            execution_allowed_before_ai=True,
            execution_allowed_after_ai=True,
            final_action="report_only",
        )
    except Exception as _log_exc:
        log.debug("[ai_analyst] audit log failed: %s", _log_exc)


def _deterministic_review(run_dir: Path) -> tuple[str, dict]:
    """Rule-based review from metrics when no AI provider is configured."""
    lines = ["# Athena Research Lab — AI Review (Deterministic Fallback)\n",
             "> No AI provider configured. Using rule-based analysis.\n"]

    def _read(name: str) -> pd.DataFrame:
        p = run_dir / name
        try:
            return pd.read_csv(p) if p.exists() else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    summary = _read("research_summary.csv")
    ranked = _read("ranked_strategies.csv")
    attr = _read("indicator_attribution.csv")
    by_ac = _read("by_asset_group.csv")
    by_tf = _read("by_timeframe.csv")
    by_dir = _read("by_direction.csv")

    # Helper
    def _best_group(df: pd.DataFrame, group_col: str, metric: str = "avg_net_return_mean") -> str:
        if df.empty or group_col not in df.columns or metric not in df.columns:
            return "N/A"
        row = df.sort_values(metric, ascending=False).iloc[0]
        return str(row[group_col])

    total = len(summary)
    strong = (summary["status"] == "STRONG_CANDIDATE").sum() if "status" in summary.columns else 0
    weak = (summary["status"] == "WEAK_CANDIDATE").sum() if "status" in summary.columns else 0

    lines += [
        f"## Summary",
        f"- Total configs tested: {total}",
        f"- Strong candidates: {strong}",
        f"- Weak candidates: {weak}",
        f"- Pass rate: {(strong+weak)/max(total,1)*100:.1f}%",
        "",
    ]

    # Top strategies
    if not ranked.empty:
        top_strats = ranked.head(5)[["strategy_name", "symbol", "timeframe", "net_return", "robustness_score", "status"]]
        lines.append("## Top 5 Ranked Strategies")
        lines.append("```")
        lines.append(top_strats.to_string(index=False))
        lines.append("```\n")

    # Indicator attribution
    if not attr.empty:
        helps = attr[attr.get("verdict", pd.Series()) == "HELPS"]["strategy_name"].tolist() if "verdict" in attr.columns else []
        hurts = attr[attr.get("verdict", pd.Series()) == "HURTS"]["strategy_name"].tolist() if "verdict" in attr.columns else []
        lines.append(f"## Indicators")
        lines.append(f"- **Helpful:** {', '.join(helps) or 'none identified'}")
        lines.append(f"- **Harmful:** {', '.join(hurts) or 'none identified'}")
        lines.append("")

    lines += [
        "## Recommendations",
        "- **Engine A:** No changes recommended from this run alone. More OOS validation needed.",
        "- **Engine B:** Proxy signals show structural patterns — validate against real Engine B audit logs.",
        "- **Engine D:** VWAP and EMA scalp proxies show early promise — run on more crypto symbols.",
        "",
        "## Next Tiny Test Recommendation",
        "Run trend_momentum and pullback families on crypto (BTC/USDT, ETH/USDT) at H4 with 500+ bars.",
        "",
        "> *Deterministic review.  Configure XAI_API_KEY for AI-powered analysis.*",
    ]

    # Build action plan JSON
    top_candidates = []
    if not ranked.empty:
        for _, row in ranked.head(5).iterrows():
            top_candidates.append({
                "strategy": str(row.get("strategy_name", "")),
                "symbol": str(row.get("symbol", "")),
                "tf": str(row.get("timeframe", "")),
                "label": str(row.get("status", "NEEDS_MORE_DATA")),
            })

    action_plan = {
        "overall_verdict": f"{strong} strong candidates out of {total} tested. More validation needed.",
        "top_candidates": top_candidates,
        "rejected_setups": [],
        "engine_a": {"keep": ["ema_trend", "rsi_momentum"], "remove_or_demote": [], "tune": [], "next_tests": ["pullback_ema on H4"]},
        "engine_b": {"keep": ["structure_filters"], "remove_or_demote": [], "tune": [], "next_tests": ["ob_bos on more symbols"]},
        "engine_d": {"keep": ["vwap_reclaim"], "remove_or_demote": [], "tune": [], "next_tests": ["ema_scalp_pullback on crypto H1"]},
        "data_quality_warnings": ["Deterministic review — AI provider not configured"],
        "telemetry_warnings": [],
        "next_tiny_test": {
            "symbols": ["BTC/USDT", "ETH/USDT"],
            "timeframes": ["H4"],
            "strategy_families": ["trend_momentum", "pullback"],
            "reason": "Most liquid assets with real volume data for reliable VectorBT simulation",
        },
        "do_not_do_next": ["mean_reversion on forex without volume confirmation"],
    }

    return "\n".join(lines), action_plan


# ─── AI-powered review ────────────────────────────────────────────────────────

def _ai_review(run_dir: Path, client, model: str, max_tokens: int = 4000, temperature: float = 0.2) -> tuple[str, dict]:
    prompt = build_prompt(run_dir)
    log.info("[ai_analyst] Sending prompt to AI (%d chars)", len(prompt))

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        raw = response.choices[0].message.content or ""
    except Exception as e:
        log.error("[ai_analyst] AI call failed: %s", e)
        raise

    # Extract JSON block from response — robust nested-brace aware parsing
    action_plan = {}
    json_match = re.search(r"```json\s*(\{.*\})\s*```", raw, re.DOTALL)
    if not json_match:
        # Fallback: find any top-level JSON object in the response
        json_match = re.search(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})", raw, re.DOTALL)
    if not json_match:
        # Last resort: find outermost braces
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            json_match = type("M", (), {"group": lambda self, n=1: raw[start:end+1]})()

    if json_match:
        raw_json = json_match.group(1)
        # Try parsing directly
        try:
            action_plan = json.loads(raw_json)
        except json.JSONDecodeError:
            # Try fixing common LLM issues: trailing commas, single quotes
            cleaned = re.sub(r",\s*([}\]])", r"\1", raw_json)  # trailing commas
            cleaned = cleaned.replace("'", '"')  # single quotes
            try:
                action_plan = json.loads(cleaned)
            except json.JSONDecodeError as e:
                log.warning("[ai_analyst] JSON parse failed after cleanup: %s", e)
                action_plan = {"parse_error": str(e), "raw_snippet": raw_json[:500]}
    else:
        log.warning("[ai_analyst] No JSON block found in AI response")
        action_plan = {"warning": "No JSON block found", "raw_length": len(raw)}

    return raw, action_plan


# ─── Engine recommendations writer ───────────────────────────────────────────

def _build_engine_recommendations(action_plan: dict) -> dict:
    """Extract engine-specific recommendations from action plan."""
    return {
        "engine_a": action_plan.get("engine_a", {}),
        "engine_b": action_plan.get("engine_b", {}),
        "engine_d": action_plan.get("engine_d", {}),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "safety_note": "These are backtest discovery findings only. Do NOT apply to live thresholds without further validation.",
    }


# ─── Main public function ─────────────────────────────────────────────────────

def run_ai_analysis(
    run_dir: Path | str,
    provider: str = "auto",
    model: Optional[str] = None,
    max_tokens: int = 4000,
    temperature: float = 0.2,
    timeout: int = 90,
) -> dict:
    """
    Run AI analysis on a research lab run directory.

    Returns dict with file paths written.
    Saves:
      ai_research_review.md
      ai_action_plan.json
      ai_engine_recommendations.json
      ai_prompt_used.txt
      ai_model_response_raw.txt
    """
    run_dir = Path(run_dir)
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    log.info("[ai_analyst] Starting AI analysis for run: %s", run_dir.name)

    # Try AI provider
    client = _get_ai_client(provider) if provider != "none" else None
    model_name = _get_model_name(model)

    used_ai = False
    raw_response = ""
    prompt_text = ""

    if client is not None:
        try:
            prompt_text = build_prompt(run_dir)
            raw_response, action_plan = _ai_review(run_dir, client, model_name, max_tokens, temperature)
            review_md = _format_review_md(raw_response, run_dir.name, model_name, ai_powered=True)
            used_ai = True
            _log_research_ai_review(
                run_id=run_dir.name,
                model=model_name,
                prompt_text=prompt_text,
                action_plan=action_plan,
                parse_success=not bool(action_plan.get("parse_error") or action_plan.get("warning")),
                schema_valid=not bool(action_plan.get("parse_error") or action_plan.get("warning")),
            )
        except Exception as e:
            log.warning("[ai_analyst] AI failed (%s) — using deterministic fallback", e)
            _log_research_ai_review(
                run_id=run_dir.name,
                model=model_name,
                prompt_text=prompt_text,
                action_plan=None,
                parse_success=False,
                schema_valid=False,
            )
            review_md, action_plan = _deterministic_review(run_dir)
            raw_response = f"AI FAILED: {e}"
    else:
        log.info("[ai_analyst] No AI provider configured — using deterministic fallback")
        review_md, action_plan = _deterministic_review(run_dir)

    engine_recs = _build_engine_recommendations(action_plan)

    # Write output files
    written = {}
    ts = datetime.now(timezone.utc).isoformat()

    def _write(name: str, content: str, is_json: bool = False):
        p = run_dir / name
        if is_json:
            p.write_text(json.dumps(content, indent=2, default=str), encoding="utf-8")
        else:
            p.write_text(content, encoding="utf-8")
        written[name] = str(p)
        log.info("[ai_analyst] wrote %s", name)

    _write("ai_research_review.md", review_md)
    _write("ai_action_plan.json", action_plan, is_json=True)
    _write("ai_engine_recommendations.json", engine_recs, is_json=True)
    if prompt_text:
        _write("ai_prompt_used.txt", prompt_text)
    _write("ai_model_response_raw.txt", raw_response)

    return {
        "run_id": run_dir.name,
        "ai_powered": used_ai,
        "model": model_name if used_ai else "deterministic",
        "files_written": written,
        "overall_verdict": action_plan.get("overall_verdict", ""),
        "top_candidates": action_plan.get("top_candidates", []),
    }


def _format_review_md(raw: str, run_id: str, model: str, ai_powered: bool) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = (
        f"# Athena Research Lab — AI Review\n"
        f"**Run ID:** `{run_id}`  **Model:** `{model}`  **Generated:** {ts}\n\n"
        f"> {'AI-powered analysis.' if ai_powered else 'Deterministic fallback analysis.'}\n"
        f"> Backtest discovery only — NOT a live execution recommendation.\n\n"
        f"---\n\n"
    )
    return header + raw


# ─── Convenience: load existing review ───────────────────────────────────────

def load_ai_review(run_dir: Path | str) -> dict:
    """Load previously saved AI review files from run_dir."""
    run_dir = Path(run_dir)
    result = {}

    review_path = run_dir / "ai_research_review.md"
    if review_path.exists():
        result["review_md"] = review_path.read_text(encoding="utf-8")

    action_path = run_dir / "ai_action_plan.json"
    if action_path.exists():
        try:
            result["action_plan"] = json.loads(action_path.read_text(encoding="utf-8"))
        except Exception:
            result["action_plan"] = {}

    engine_path = run_dir / "ai_engine_recommendations.json"
    if engine_path.exists():
        try:
            result["engine_recommendations"] = json.loads(engine_path.read_text(encoding="utf-8"))
        except Exception:
            result["engine_recommendations"] = {}

    return result
