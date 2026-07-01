"""Weekly AI Improvement Report — summarizes AI performance over the last 7 days.

Safety:
  - Report only. No live changes.
  - Telegram integration disabled by default.
  - All recommendations set do_not_auto_apply=True.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from ai_evaluation import generate_ai_evaluation_report
from ai_evaluation_contracts import AIEvaluationReport

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_config_bool(key: str, default: bool = False) -> bool:
    try:
        from config import CONFIG
        return bool(CONFIG.get(key, default))
    except Exception:
        return default


def generate_weekly_ai_report() -> dict[str, Any]:
    """Generate a weekly AI performance report.

    Output includes:
      - surface scores
      - what AI got right/wrong
      - false blocks / harmful allows
      - vision/strategist/debate/chat findings
      - recommendations (all do_not_auto_apply=True)
    """
    report = generate_ai_evaluation_report(lookback_days=7)
    surfaces = report.get("surface_metrics", {})
    vision = report.get("vision", {})
    strategist = report.get("strategist", {})
    debate = report.get("debate", {})

    headline = _build_headline(report)
    summary = report.get("overall_summary", "")

    right: list[str] = report.get("best_ai_behaviors", [])
    wrong: list[str] = report.get("worst_ai_behaviors", [])
    recs: list[str] = report.get("recommendations", [])

    false_block_list: list[str] = []
    harmful_allow_list: list[str] = []
    vision_findings: list[str] = []
    strategist_findings: list[str] = []
    debate_findings: list[str] = []
    chat_findings: list[str] = []

    for surface_name, metrics in surfaces.items():
        m = metrics if isinstance(metrics, dict) else {}
        fb = m.get("false_block_count", 0)
        ha = m.get("harmful_allow_count", 0)
        if fb > 0:
            false_block_list.append(f"{surface_name}: {fb} false blocks")
        if ha > 0:
            harmful_allow_list.append(f"{surface_name}: {ha} harmful allows")

    vision_warning = vision.get("warning")
    if vision_warning:
        vision_findings.append(f"Warning: {vision_warning}")
    else:
        vision_findings.append(f"Vision processed {vision.get('sample_count', 0)} samples.")
        cwr = vision.get("confirms_win_rate")
        if cwr is not None:
            vision_findings.append(f"Confirms win rate: {round(cwr * 100, 1)}%")
        svr = vision.get("stale_vision_rate", 0)
        if svr > 0.2:
            vision_findings.append(f"High stale vision rate: {round(svr * 100, 1)}%")

    strat_warning = strategist.get("warning")
    if strat_warning:
        strategist_findings.append(f"Warning: {strat_warning}")
    else:
        strategist_findings.append(f"Strategist reviewed {strategist.get('sample_count', 0)} samples.")
        cwr = strategist.get("concur_win_rate")
        if cwr is not None:
            strategist_findings.append(f"Concur win rate: {round(cwr * 100, 1)}%")
        fa = strategist.get("false_objections", 0)
        if fa > 0:
            strategist_findings.append(f"False objections: {fa}")

    debate_warning = debate.get("warning")
    if debate_warning:
        debate_findings.append(f"Warning: {debate_warning}")
    else:
        debate_findings.append(f"Debate gate processed {debate.get('sample_count', 0)} samples.")
        debate_findings.append(f"Useful blocks: {debate.get('useful_blocks', 0)}, False blocks: {debate.get('false_blocks', 0)}")
        pf = debate.get("parse_failures", 0)
        if pf > 0:
            debate_findings.append(f"Parse failures: {pf}")

    chat_metrics = surfaces.get("CHAT", {})
    if isinstance(chat_metrics, dict):
        cc = chat_metrics.get("sample_count", 0)
        if cc > 0:
            chat_findings.append(f"AI Chat used in {cc} conversations.")

    output: dict[str, Any] = {
        "schema_version": "weekly_ai_report.v1",
        "generated_at": _now_iso(),
        "report_id": f"weekly_{uuid.uuid4().hex[:12]}",
        "lookback_days": 7,
        "headline": headline,
        "summary": summary,
        "surface_scores": {k: {
            "sample_count": v.get("sample_count", 0) if isinstance(v, dict) else 0,
            "win_rate_when_positive": v.get("win_rate_when_positive") if isinstance(v, dict) else None,
            "avg_r_when_positive": v.get("avg_r_when_positive") if isinstance(v, dict) else None,
            "useful_block_count": v.get("useful_block_count", 0) if isinstance(v, dict) else 0,
            "false_block_count": v.get("false_block_count", 0) if isinstance(v, dict) else 0,
        } for k, v in surfaces.items()},
        "what_ai_got_right": right,
        "what_ai_got_wrong": wrong,
        "false_blocks": false_block_list,
        "harmful_allows": harmful_allow_list,
        "vision_findings": vision_findings,
        "strategist_findings": strategist_findings,
        "debate_findings": debate_findings,
        "chat_findings": chat_findings,
        "prompt_quality": _build_prompt_quality_section(),
        "recommendations": recs,
        "do_not_auto_apply": True,
    }
    return output


def _build_prompt_quality_section() -> dict[str, Any]:
    """Phase 3 — advisory prompt-quality summary. Never auto-applies.

    Pulls the most recent golden-set eval report (if present on disk) and a
    drift snapshot (if drift detection is enabled). All findings are
    advisory-only; this function never mutates prompts or config.
    """
    section: dict[str, Any] = {
        "enabled": False,
        "golden_set": None,
        "drift": None,
        "do_not_auto_apply": True,
    }
    try:
        if _get_config_bool("AI_PROMPT_EVAL_ENABLED", False):
            section["enabled"] = True
            # Best-effort: read the latest eval report if it exists
            try:
                import json as _json
                import os as _os

                path = _os.path.join("reports", "prompt_eval.json")
                if _os.path.isfile(path):
                    with open(path, "r", encoding="utf-8") as fh:
                        data = _json.load(fh)
                    section["golden_set"] = {
                        "total": data.get("total"),
                        "passed": data.get("passed"),
                        "failed": data.get("failed"),
                        "pass_rate": data.get("pass_rate"),
                        "provider": data.get("provider"),
                        "generated_at": data.get("generated_at"),
                    }
            except Exception:
                section["golden_set"] = None
        if _get_config_bool("AI_DRIFT_DETECTION_ENABLED", False):
            section["enabled"] = True
            section["drift"] = "drift_detection_enabled_run_detector_for_snapshot"
    except Exception:
        pass
    return section


def _build_headline(report: dict[str, Any]) -> str:
    total = report.get("total_samples", 0)
    valid = report.get("valid_outcomes", 0)
    surface_count = len(report.get("surface_metrics", {}))
    if total < 5:
        return "Insufficient AI activity this week to generate a meaningful report."
    surfaces_active = ", ".join(report.get("surfaces", []))
    return f"Weekly AI Report: {total} samples across {surface_count} surfaces ({surfaces_active}). {valid} outcomes linked."


def send_weekly_ai_report_to_telegram(report: dict[str, Any]) -> bool:
    """Send the weekly AI report to Telegram. Disabled by default."""
    enabled = _get_config_bool("AI_WEEKLY_REPORT_TELEGRAM_ENABLED", False)
    if not enabled:
        log.info("[WeeklyReport] Telegram disabled by default. Set AI_WEEKLY_REPORT_TELEGRAM_ENABLED=true to enable.")
        return False
    try:
        from telegram_notify import send_telegram_message
        lines = [f"AI Weekly Report ({report.get('generated_at', '')[:10]})"]
        lines.append(f"Headline: {report.get('headline', '')}")
        lines.append(f"Samples: {report.get('total_samples', 0)}")
        recs = report.get("recommendations", [])
        if recs:
            lines.append("Recommendations:")
            lines.extend(f"  - {r}" for r in recs[:5])
        lines.append("(Advisory only - no auto-apply)")
        send_telegram_message("\n".join(lines))
        return True
    except Exception as exc:
        log.warning("[WeeklyReport] Telegram send failed: %s", exc)
        return False
