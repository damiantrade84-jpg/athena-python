"""Research Agent — safe, read-only analysis of completed research/backtest results.

Safety contract:
  - NEVER imports live execution modules.
  - NEVER modifies live thresholds or config.yaml.
  - NEVER executes live trades.
  - NEVER auto-applies recommendations.
  - All outputs are advisory-only research artifacts.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ai_research_contracts import (
    ResearchComparison,
    ResearchHypothesis,
    ResearchPlan,
    ResearchRecommendation,
    ResearchResultSummary,
)

log = logging.getLogger(__name__)

_RESEARCH_LAB_DIR = Path("logs/research_lab")
_BACKTEST_MATRIX_DIR = Path("logs/backtest_matrix")
_STRATEGY_LAB_DIR = Path("logs/strategy_lab")
_RESEARCH_AGENT_OUTPUT = Path("logs/research_agent")

SAMPLE_WEAK = 30
SAMPLE_MODERATE = 100
SAMPLE_STRONG = 500

_MAX_PLAN_JOBS = 20
_MAX_PLAN_SYMBOLS = 20
_MAX_PLAN_TIMEFRAMES = 5
_SUPPORTED_STRATEGY_FAMILIES = {
    "trend_following", "mean_reversion", "breakout", "momentum",
    "volatility", "stat_arb", "ml", "engine_b_proxy", "engine_d_proxy",
}
_SUPPORTED_ENGINES = {"engine_a", "engine_b", "engine_c", "engine_d"}
_SUPPORTED_TIMEFRAMES = {"M1", "M5", "M15", "M30", "H1", "H2", "H4", "D1", "W1"}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_csv_safe(path: Path) -> list[dict[str, Any]]:
    """Read CSV and return list of dicts. Returns empty on any failure."""
    if not path.exists():
        return []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception as exc:
        log.warning("[ResearchAgent] Failed to read %s: %s", path, exc)
        return []


def _read_json_safe(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, (dict, list)) else None
    except Exception as exc:
        log.warning("[ResearchAgent] Failed to read %s: %s", path, exc)
        return None


def _find_latest_run_dir(base: Path) -> Optional[Path]:
    """Find the most recently modified run directory in base."""
    if not base.exists():
        return None
    try:
        dirs = [d for d in base.iterdir() if d.is_dir() and d.name.startswith("run_")]
        if not dirs:
            return None
        return max(dirs, key=lambda d: d.stat().st_mtime)
    except Exception:
        return None


def _list_run_dirs(base: Path, limit: int = 10) -> list[Path]:
    if not base.exists():
        return []
    try:
        dirs = sorted(
            [d for d in base.iterdir() if d.is_dir() and d.name.startswith("run_")],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        return dirs[:limit]
    except Exception:
        return []


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _sample_quality_label(n: int) -> str:
    if n <= 0:
        return "unknown"
    if n < SAMPLE_WEAK:
        return "insufficient"
    if n < SAMPLE_MODERATE:
        return "weak"
    if n < SAMPLE_STRONG:
        return "moderate"
    return "strong"


def _collect_field(rows: list[dict[str, Any]], key: str) -> list[str]:
    vals: set[str] = set()
    for row in rows:
        v = str(row.get(key, "")).strip()
        if v:
            vals.add(v)
    return sorted(vals)


def _cluster_rows(
    rows: list[dict[str, Any]], by: list[str], metric: str = "profit_factor",
) -> list[dict[str, Any]]:
    """Group rows by keys and compute aggregate metrics."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key_parts = []
        for k in by:
            v = str(row.get(k, "unknown")).strip()
            key_parts.append(f"{k}={v}")
        key = "|".join(key_parts)
        groups.setdefault(key, []).append(row)

    clusters: list[dict[str, Any]] = []
    for key, grp in groups.items():
        trades = sum(_safe_int(r.get("trade_count", r.get("trades", 0))) for r in grp)
        pf_values = [_safe_float(r.get(metric, 0)) for r in grp if _safe_float(r.get(metric, 0)) > 0]
        wr_values = [_safe_float(r.get("win_rate", r.get("winRate", 0))) for r in grp if r.get("win_rate") is not None]
        avg_pf = sum(pf_values) / len(pf_values) if pf_values else 0.0
        avg_wr = sum(wr_values) / len(wr_values) if wr_values else 0.0
        clusters.append({
            "key": key,
            "count": len(grp),
            "total_trades": trades,
            "avg_profit_factor": round(avg_pf, 3),
            "avg_win_rate": round(avg_wr, 3),
            "sample_quality": _sample_quality_label(trades),
            "rows": grp[:5],
        })
    clusters.sort(key=lambda c: c["avg_profit_factor"], reverse=True)
    return clusters


def _best_worst_clusters(rows: list[dict[str, Any]], by: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    clusters = _cluster_rows(rows, by)
    top = [c for c in clusters if c["avg_profit_factor"] >= 1.3 and c["total_trades"] >= SAMPLE_WEAK]
    weak = [c for c in clusters if c["avg_profit_factor"] < 1.0 or c["total_trades"] < SAMPLE_WEAK]
    return top[:10], weak[:10]


# ─── Core API ─────────────────────────────────────────────────────────────────

def load_latest_research_results(limit: int = 5) -> list[dict[str, Any]]:
    """Read existing research outputs from known directories.

    Returns stable empty result with warning if no files found.
    """
    results: list[dict[str, Any]] = []

    # Research Lab runs
    for run_dir in _list_run_dirs(_RESEARCH_LAB_DIR, limit):
        ranked_path = run_dir / "ranked_strategies.csv"
        summary_path = run_dir / "research_summary.csv"
        meta_path = run_dir / "run_meta.json"

        run_data: dict[str, Any] = {
            "run_id": run_dir.name,
            "source": "research_lab",
            "path": str(run_dir),
            "created_at": _now_iso(),
        }

        meta = _read_json_safe(meta_path)
        if isinstance(meta, dict):
            run_data.update({
                k: meta[k] for k in (
                    "symbols", "timeframes", "families", "mode", "direction",
                    "market_group", "trading_style", "research_depth",
                ) if k in meta
            })

        ranked = _read_csv_safe(ranked_path)
        if ranked:
            run_data["ranked_count"] = len(ranked)
            run_data["symbols"] = _collect_field(ranked, "symbol") or run_data.get("symbols", [])
            run_data["timeframes"] = _collect_field(ranked, "timeframe") or run_data.get("timeframes", [])
            run_data["families"] = _collect_field(ranked, "family") or run_data.get("families", [])
            run_data["engines"] = _collect_field(ranked, "engine")

        summary = _read_csv_safe(summary_path)
        if summary:
            run_data["summary_rows"] = len(summary)

        run_data["has_ranked"] = bool(ranked)
        run_data["has_summary"] = bool(summary)
        results.append(run_data)

    # Backtest matrix runs
    for run_dir in _list_run_dirs(_BACKTEST_MATRIX_DIR, limit):
        results_path = run_dir / "results.json"
        meta_path = run_dir / "run_meta.json"
        run_data: dict[str, Any] = {
            "run_id": run_dir.name,
            "source": "backtest_matrix",
            "path": str(run_dir),
            "created_at": _now_iso(),
        }
        meta = _read_json_safe(meta_path)
        if isinstance(meta, dict):
            run_data.update(meta)
        results_data = _read_json_safe(results_path)
        if isinstance(results_data, dict):
            run_data["result_count"] = len(results_data)
        run_data["has_results"] = results_data is not None
        results.append(run_data)

    if not results:
        return [{"warning": "No research results found. Run a research lab or backtest first.",
                 "missing_fields": ["research_lab_outputs", "backtest_matrix_outputs"]}]

    return results


def summarize_research_results(run_path_or_id: str | None = None) -> dict[str, Any]:
    """Compute a ResearchResultSummary-compatible dict from research outputs."""
    run_dir: Path | None = None

    if run_path_or_id:
        candidate = Path(run_path_or_id)
        if candidate.exists():
            run_dir = candidate
        else:
            for base in [_RESEARCH_LAB_DIR, _BACKTEST_MATRIX_DIR]:
                p = base / run_path_or_id
                if p.exists() and p.is_dir():
                    run_dir = p
                    break
    else:
        run_dir = _find_latest_run_dir(_RESEARCH_LAB_DIR)

    if run_dir is None or not run_dir.exists():
        return ResearchResultSummary(
            run_id=run_path_or_id or "latest",
            source="not_found",
            warnings=["No research run directory found."],
            missing_fields=["run_dir"],
        ).model_dump()

    ranked = _read_csv_safe(run_dir / "ranked_strategies.csv")
    summary = _read_csv_safe(run_dir / "research_summary.csv")
    meta = _read_json_safe(run_dir / "run_meta.json")

    rows = ranked or summary or []
    total_trades = sum(_safe_int(r.get("trade_count", r.get("trades", 0))) for r in rows)
    sample_quality = _sample_quality_label(total_trades)

    symbols = _collect_field(rows, "symbol")
    timeframes = _collect_field(rows, "timeframe")
    families = _collect_field(rows, "family")
    engines = _collect_field(rows, "engine")

    top_by_engine = _best_worst_clusters(rows, ["engine"])
    top_by_family = _best_worst_clusters(rows, ["family"])
    top_by_symbol = _best_worst_clusters(rows, ["symbol"])
    top_by_timeframe = _best_worst_clusters(rows, ["timeframe"])

    top_clusters = (top_by_engine[0] + top_by_family[0] + top_by_symbol[0] + top_by_timeframe[0])[:15]
    weak_clusters = (top_by_engine[1] + top_by_family[1] + top_by_symbol[1] + top_by_timeframe[1])[:15]

    timeframe_findings: dict[str, Any] = {}
    for tf in timeframes:
        tf_rows = [r for r in rows if str(r.get("timeframe", "")).strip() == tf]
        if tf_rows:
            trades = sum(_safe_int(r.get("trade_count", 0)) for r in tf_rows)
            pf_vals = [_safe_float(r.get("profit_factor", 0)) for r in tf_rows if _safe_float(r.get("profit_factor", 0)) > 0]
            avg_pf = sum(pf_vals) / len(pf_vals) if pf_vals else 0
            timeframe_findings[tf] = {
                "total_trades": trades,
                "avg_profit_factor": round(avg_pf, 3),
                "sample_quality": _sample_quality_label(trades),
            }

    direction_findings: dict[str, Any] = {}
    for direction in ("LONG", "SHORT", "long", "short", "both"):
        d_rows = [r for r in rows if str(r.get("direction", "")).strip().upper() == direction.upper()]
        if d_rows:
            trades = sum(_safe_int(r.get("trade_count", 0)) for r in d_rows)
            pf_vals = [_safe_float(r.get("profit_factor", 0)) for r in d_rows if _safe_float(r.get("profit_factor", 0)) > 0]
            avg_pf = sum(pf_vals) / len(pf_vals) if pf_vals else 0
            direction_findings[direction.upper()] = {
                "total_trades": trades,
                "avg_profit_factor": round(avg_pf, 3),
                "sample_quality": _sample_quality_label(trades),
            }

    session_findings: dict[str, Any] = {}
    for session in ("London", "New_York", "Asia", "all"):
        s_rows = [r for r in rows if str(r.get("session", "")).strip() == session]
        if s_rows:
            trades = sum(_safe_int(r.get("trade_count", 0)) for r in s_rows)
            pf_vals = [_safe_float(r.get("profit_factor", 0)) for r in s_rows if _safe_float(r.get("profit_factor", 0)) > 0]
            avg_pf = sum(pf_vals) / len(pf_vals) if pf_vals else 0
            session_findings[session] = {
                "total_trades": trades,
                "avg_profit_factor": round(avg_pf, 3),
                "sample_quality": _sample_quality_label(trades),
            }

    regime_findings: dict[str, Any] = {}
    for regime in ("TRENDING", "RANGING", "HIGH_VOL", "LOW_VOL"):
        r_rows = [r for r in rows if str(r.get("regime", "")).strip() == regime]
        if r_rows:
            trades = sum(_safe_int(r.get("trade_count", 0)) for r in r_rows)
            pf_vals = [_safe_float(r.get("profit_factor", 0)) for r in r_rows if _safe_float(r.get("profit_factor", 0)) > 0]
            avg_pf = sum(pf_vals) / len(pf_vals) if pf_vals else 0
            regime_findings[regime] = {
                "total_trades": trades,
                "avg_profit_factor": round(avg_pf, 3),
                "sample_quality": _sample_quality_label(trades),
            }

    warnings: list[str] = []
    if total_trades < SAMPLE_WEAK:
        warnings.append(f"Sample too small ({total_trades} trades). Avoid strong conclusions.")
    if total_trades < SAMPLE_MODERATE:
        warnings.append("Sample < 100 trades. Recommendations are preliminary.")
    if not rows:
        warnings.append("No ranked strategy data found for this run.")

    fee_notes: list[str] = []
    fee_cols = [r.get("fee", r.get("fees", r.get("slippage", None))) for r in rows[:5] if r.get("fee") or r.get("fees") or r.get("slippage")]
    if fee_cols:
        fee_notes.append("Fee/slippage data present in raw results.")

    summary_obj = ResearchResultSummary(
        run_id=run_dir.name,
        source="research_lab",
        created_at=_now_iso(),
        mode=str(meta.get("mode", "unknown")) if isinstance(meta, dict) else "unknown",
        engines=engines,
        strategy_families=families,
        symbols=symbols,
        timeframes=timeframes,
        asset_groups=_collect_field(rows, "asset_class") or _collect_field(rows, "market_group"),
        total_trades=total_trades,
        sample_quality=sample_quality,
        top_clusters=top_clusters,
        weak_clusters=weak_clusters,
        regime_findings=regime_findings,
        timeframe_findings=timeframe_findings,
        session_findings=session_findings,
        direction_findings=direction_findings,
        fee_slippage_notes=fee_notes,
        warnings=warnings,
    )
    return summary_obj.model_dump()


def propose_next_research_plan(
    summary: dict[str, Any],
    user_goal: str | None = None,
) -> dict[str, Any]:
    """Propose the next research plan based on current summary.

    The plan answers:
    - What is the next best thing to test?
    - Why test this next?
    - Which engine/strategy/timeframes/symbols should run?
    - What metrics decide pass/fail?
    """
    if not isinstance(summary, dict):
        summary = {}

    total_trades = int(summary.get("total_trades", 0))
    sample_quality = str(summary.get("sample_quality", "unknown"))
    top_clusters = summary.get("top_clusters", [])
    weak_clusters = summary.get("weak_clusters", [])

    hypotheses: list[ResearchHypothesis] = []
    jobs: list[dict[str, Any]] = []

    user_goal_lower = (user_goal or "").lower()

    # Determine target scope
    target_engines = summary.get("engines", ["engine_d", "engine_a"])
    target_timeframes = summary.get("timeframes", ["H1", "H4"])
    target_symbols = summary.get("symbols", ["BTCUSDT", "ETHUSDT", "EUR/USD", "XAU/USD"])

    if "crypto" in user_goal_lower:
        target_symbols = [s for s in target_symbols if "USD" in s and "/" not in s]
        if not target_symbols:
            target_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        if "scalp" in user_goal_lower:
            target_timeframes = ["M15", "M30", "H1"]
        elif "swing" in user_goal_lower:
            target_timeframes = ["H4", "D1"]

    if "forex" in user_goal_lower:
        target_symbols = [s for s in target_symbols if "/" in s]
        if not target_symbols:
            target_symbols = ["EUR/USD", "GBP/USD"]
        target_timeframes = ["H1", "H4"]

    # Generate hypothesis for weak clusters
    for wc in weak_clusters[:3]:
        wc_key = wc.get("key", "unknown")
        wc_trades = int(wc.get("total_trades", 0))
        wc_pf = float(wc.get("avg_profit_factor", 0))

        if wc_trades < SAMPLE_WEAK:
            hypothesis_id = f"h_{uuid.uuid4().hex[:8]}"
            hypotheses.append(ResearchHypothesis(
                hypothesis_id=hypothesis_id,
                title=f"Increase sample for weak cluster: {wc_key}",
                hypothesis=f"Cluster {wc_key} has only {wc_trades} trades (PF={wc_pf}). "
                          f"More data may confirm or reject reliability.",
                reason=f"Insufficient sample ({wc_trades} trades) to draw conclusions.",
                target_engine=summary.get("engines", ["unknown"])[0] if summary.get("engines") else None,
                priority=3,
            ))
            jobs.append({
                "job_id": f"job_{uuid.uuid4().hex[:8]}",
                "title": f"Expand sample for {wc_key}",
                "mode": "small",
                "engines": summary.get("engines", ["engine_d"]),
                "symbols": target_symbols[:5],
                "timeframes": target_timeframes[:3],
                "acceptance_criteria": {"min_trade_count": SAMPLE_WEAK, "min_profit_factor": 1.2},
                "priority": 3,
            })

    # Generate hypothesis for best performing areas to validate
    for tc in top_clusters[:2]:
        tc_key = tc.get("key", "unknown")
        tc_trades = int(tc.get("total_trades", 0))
        tc_pf = float(tc.get("avg_profit_factor", 0))

        if tc_trades >= SAMPLE_WEAK and tc_pf >= 1.3:
            hypothesis_id = f"h_{uuid.uuid4().hex[:8]}"
            hypotheses.append(ResearchHypothesis(
                hypothesis_id=hypothesis_id,
                title=f"Validate strong cluster: {tc_key}",
                hypothesis=f"Cluster {tc_key} shows PF={tc_pf} over {tc_trades} trades. "
                          f"Need out-of-sample validation before any consideration.",
                reason=f"Strong in-sample results (PF={tc_pf}, {tc_trades} trades) need OOS validation.",
                target_engine=summary.get("engines", ["unknown"])[0] if summary.get("engines") else None,
                priority=5,
            ))
            jobs.append({
                "job_id": f"job_{uuid.uuid4().hex[:8]}",
                "title": f"OOS validation for {tc_key}",
                "mode": "small",
                "engines": summary.get("engines", ["engine_d"]),
                "symbols": target_symbols[:3],
                "timeframes": target_timeframes[:2],
                "acceptance_criteria": {"min_trade_count": 30, "min_profit_factor": 1.2, "min_oos_return": 0.0},
                "priority": 5,
            })

    # Default hypothesis if nothing specific found
    if not hypotheses:
        hypothesis_id = f"h_{uuid.uuid4().hex[:8]}"
        hypotheses.append(ResearchHypothesis(
            hypothesis_id=hypothesis_id,
            title="Initial research discovery",
            hypothesis="Run initial research across target symbols/timeframes to establish baseline metrics.",
            reason="No prior research data available to analyze.",
            target_engine="engine_d",
            priority=5,
        ))
        jobs.append({
            "job_id": f"job_{uuid.uuid4().hex[:8]}",
            "title": "Baseline discovery run",
            "mode": "tiny",
            "engines": ["engine_d"],
            "symbols": target_symbols[:5],
            "timeframes": target_timeframes[:2],
            "acceptance_criteria": {"min_trade_count": 20, "min_profit_factor": 1.0},
            "priority": 5,
        })

    # Apply guardrails
    jobs = jobs[:_MAX_PLAN_JOBS]
    for job in jobs:
        job["symbols"] = job.get("symbols", target_symbols)[:_MAX_PLAN_SYMBOLS]
        job["timeframes"] = job.get("timeframes", target_timeframes)[:_MAX_PLAN_TIMEFRAMES]

    plan = ResearchPlan(
        plan_id=f"plan_{uuid.uuid4().hex[:12]}",
        created_at=_now_iso(),
        title=f"Research Plan: {user_goal or 'General discovery'}",
        objective=user_goal or "Discover what works best across engines, timeframes, and asset groups.",
        hypotheses=hypotheses,
        jobs=jobs,
        estimated_scope="small",
        guardrails={
            "max_jobs": _MAX_PLAN_JOBS,
            "max_symbols_per_job": _MAX_PLAN_SYMBOLS,
            "max_timeframes_per_job": _MAX_PLAN_TIMEFRAMES,
            "supported_strategy_families": sorted(_SUPPORTED_STRATEGY_FAMILIES),
            "supported_engines": sorted(_SUPPORTED_ENGINES),
            "supported_timeframes": sorted(_SUPPORTED_TIMEFRAMES),
        },
        requires_user_approval=True,
        can_modify_live_config=False,
        can_execute_live_trades=False,
    )
    return plan.model_dump()


def validate_research_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate a research plan for safety and consistency.

    Returns dict with valid bool and list of issues.
    """
    if not isinstance(plan, dict):
        return {"valid": False, "issues": ["Plan is not a valid dict."], "plan_id": None}

    plan_id = plan.get("plan_id", "unknown")
    issues: list[str] = []
    warnings: list[str] = []

    # Safety checks
    if plan.get("can_execute_live_trades", False):
        issues.append("Plan must not enable live trade execution.")
    if plan.get("can_modify_live_config", False):
        issues.append("Plan must not modify live config.")
    if not plan.get("requires_user_approval", False):
        issues.append("Plan must require user approval.")

    # Job checks
    jobs = plan.get("jobs", [])
    if not isinstance(jobs, list):
        issues.append("jobs must be a list.")
        jobs = []

    if len(jobs) > _MAX_PLAN_JOBS:
        issues.append(f"Too many jobs: {len(jobs)} > max {_MAX_PLAN_JOBS}.")
    if len(jobs) == 0 and not issues:
        warnings.append("Plan has no jobs.")

    for i, job in enumerate(jobs):
        if not isinstance(job, dict):
            issues.append(f"Job {i} is not a dict.")
            continue
        symbols = job.get("symbols", [])
        timeframes = job.get("timeframes", [])
        engines = job.get("engines", [])

        if not symbols:
            issues.append(f"Job {i} has no symbols.")
        elif len(symbols) > _MAX_PLAN_SYMBOLS:
            issues.append(f"Job {i} has {len(symbols)} symbols > max {_MAX_PLAN_SYMBOLS}.")

        if not timeframes:
            issues.append(f"Job {i} has no timeframes.")
        elif len(timeframes) > _MAX_PLAN_TIMEFRAMES:
            issues.append(f"Job {i} has {len(timeframes)} timeframes > max {_MAX_PLAN_TIMEFRAMES}.")

        for tf in timeframes:
            if tf not in _SUPPORTED_TIMEFRAMES:
                warnings.append(f"Job {i} timeframe '{tf}' may not be supported.")

        for eng in engines:
            if eng not in _SUPPORTED_ENGINES:
                warnings.append(f"Job {i} engine '{eng}' may not be supported.")

        # Check acceptance criteria
        criteria = job.get("acceptance_criteria", {})
        min_trades = criteria.get("min_trade_count", 0)
        if isinstance(min_trades, (int, float)) and min_trades < 10:
            warnings.append(f"Job {i} min_trade_count {min_trades} very low (<10).")

    # Hypotheses checks
    hypotheses = plan.get("hypotheses", [])
    if not isinstance(hypotheses, list):
        issues.append("hypotheses must be a list.")

    # Estimate runtime warning
    total_jobs = len(jobs)
    if total_jobs >= 10:
        warnings.append(f"Large plan ({total_jobs} jobs) may take significant time to execute.")

    valid = len(issues) == 0
    return {
        "valid": valid,
        "plan_id": plan_id,
        "issues": issues,
        "warnings": warnings,
        "requires_user_approval": True,
        "can_modify_live_config": False,
        "can_execute_live_trades": False,
    }


def run_approved_research_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Execute an approved research plan IF a safe existing runner exists.

    Safety: requires explicit approved=true. Never runs live trading.
    """
    if not isinstance(plan, dict):
        return {"status": "error", "reason": "Plan is not a valid dict."}

    approved = bool(plan.get("approved", False))
    if not approved:
        return {"status": "rejected", "reason": "Plan not approved. Set approved=true to execute."}

    # Check plan_id
    plan_id = plan.get("plan_id", f"plan_{uuid.uuid4().hex[:12]}")

    # Try to use existing research lab runner
    try:
        from athena_research.run_manager import run_research, _DEFAULT_CONFIG
    except ImportError:
        return {
            "status": "not_implemented",
            "reason": "No safe existing research runner found (athena_research.run_manager unavailable).",
            "plan_id": plan_id,
        }

    jobs = plan.get("jobs", [])
    if not jobs:
        return {"status": "completed", "plan_id": plan_id, "child_run_ids": [], "message": "No jobs in plan."}

    child_run_ids: list[str] = []
    import threading
    from athena_research.research_agent_store import persist_plan_run

    output_dir = _RESEARCH_AGENT_OUTPUT / plan_id
    output_dir.mkdir(parents=True, exist_ok=True)

    for job in jobs:
        job_run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
        child_run_ids.append(job_run_id)
        mode = job.get("mode", "tiny")
        direction = job.get("directions", ["both"])[0] if job.get("directions") else "both"
        symbols = job.get("symbols")
        timeframes = job.get("timeframes")
        families = job.get("families")
        strategies = job.get("strategies")

        def _worker(cid: str, m: str, d: str, syms, tfs, fams, strats):
            try:
                run_research(
                    mode=m,
                    config_path=_DEFAULT_CONFIG,
                    output_dir=_DEFAULT_CONFIG.parent if hasattr(_DEFAULT_CONFIG, "parent") else Path("logs/research_lab"),
                    run_id=cid,
                    direction=d,
                    run_ai_review=False,
                    symbols=syms,
                    timeframes=tfs,
                    families=fams,
                    strategies=strats,
                )
            except Exception as exc:
                log.error("[ResearchAgent] Job %s failed: %s", cid, exc)

        t = threading.Thread(target=_worker, args=(job_run_id, mode, direction, symbols, timeframes, families, strategies), daemon=True)
        t.start()

    # Persist the plan run
    try:
        persist_plan_run(
            plan_id=plan_id,
            user_goal=plan.get("objective", ""),
            plan_json=plan,
            approved=True,
            run_status="started",
            child_run_ids=child_run_ids,
        )
    except Exception as exc:
        log.warning("[ResearchAgent] Failed to persist plan run: %s", exc)

    return {
        "status": "started",
        "plan_id": plan_id,
        "child_run_ids": child_run_ids,
        "message": f"Started {len(jobs)} research job(s). Check /api/research-agent/runs/<run_id> for results.",
    }


def compare_research_runs(baseline: str, candidate: str) -> dict[str, Any]:
    """Compare two research runs and return a ResearchComparison-compatible dict."""
    baseline_dir = None
    candidate_dir = None

    for base in [_RESEARCH_LAB_DIR, _BACKTEST_MATRIX_DIR]:
        b = base / baseline
        c = base / candidate
        if b.exists() and b.is_dir() and baseline_dir is None:
            baseline_dir = b
        if c.exists() and c.is_dir() and candidate_dir is None:
            candidate_dir = c

    if baseline_dir is None or candidate_dir is None:
        missing = []
        if baseline_dir is None:
            missing.append(f"baseline={baseline}")
        if candidate_dir is None:
            missing.append(f"candidate={candidate}")
        return ResearchComparison(
            baseline_run_id=baseline,
            candidate_run_id=candidate,
            sample_warning="Could not find one or both run directories.",
            missing_fields=missing,
        ).model_dump()

    b_rows = _read_csv_safe(baseline_dir / "ranked_strategies.csv") or _read_csv_safe(baseline_dir / "research_summary.csv")
    c_rows = _read_csv_safe(candidate_dir / "ranked_strategies.csv") or _read_csv_safe(candidate_dir / "research_summary.csv")

    if not b_rows or not c_rows:
        return ResearchComparison(
            baseline_run_id=baseline,
            candidate_run_id=candidate,
            sample_warning="One or both runs have no ranked strategy data.",
            missing_fields=["ranked_strategies.csv"] if not b_rows else [] + ["ranked_strategies.csv"] if not c_rows else [],
        ).model_dump()

    def _agg(rows):
        trades = sum(_safe_int(r.get("trade_count", r.get("trades", 0))) for r in rows)
        pf_vals = [_safe_float(r.get("profit_factor", 0)) for r in rows if _safe_float(r.get("profit_factor", 0)) > 0]
        wr_vals = [_safe_float(r.get("win_rate", 0)) for r in rows if r.get("win_rate") is not None]
        dd_vals = [_safe_float(r.get("max_drawdown", 0)) for r in rows if r.get("max_drawdown") is not None]
        expectancy_vals = [_safe_float(r.get("expectancy", 0)) for r in rows if r.get("expectancy") is not None]
        return {
            "total_trades": trades,
            "avg_profit_factor": round(sum(pf_vals) / len(pf_vals), 3) if pf_vals else 0,
            "avg_win_rate": round(sum(wr_vals) / len(wr_vals), 3) if wr_vals else 0,
            "avg_max_drawdown": round(sum(dd_vals) / len(dd_vals), 3) if dd_vals else 0,
            "avg_expectancy": round(sum(expectancy_vals) / len(expectancy_vals), 3) if expectancy_vals else 0,
            "sample_size": len(rows),
        }

    b_agg = _agg(b_rows)
    c_agg = _agg(c_rows)

    what_improved: list[str] = []
    what_worsened: list[str] = []

    pf_diff = c_agg["avg_profit_factor"] - b_agg["avg_profit_factor"]
    wr_diff = c_agg["avg_win_rate"] - b_agg["avg_win_rate"]
    dd_diff = c_agg["avg_max_drawdown"] - b_agg["avg_max_drawdown"]
    exp_diff = c_agg["avg_expectancy"] - b_agg["avg_expectancy"]

    if pf_diff > 0.05:
        what_improved.append(f"Profit factor: {b_agg['avg_profit_factor']} -> {c_agg['avg_profit_factor']}")
    elif pf_diff < -0.05:
        what_worsened.append(f"Profit factor: {b_agg['avg_profit_factor']} -> {c_agg['avg_profit_factor']}")

    if wr_diff > 0.02:
        what_improved.append(f"Win rate: {b_agg['avg_win_rate']} -> {c_agg['avg_win_rate']}")
    elif wr_diff < -0.02:
        what_worsened.append(f"Win rate: {b_agg['avg_win_rate']} -> {c_agg['avg_win_rate']}")

    if dd_diff > 0.01:
        what_worsened.append(f"Max drawdown: {b_agg['avg_max_drawdown']} -> {c_agg['avg_max_drawdown']}")
    elif dd_diff < -0.01:
        what_improved.append(f"Max drawdown: {b_agg['avg_max_drawdown']} -> {c_agg['avg_max_drawdown']}")

    if exp_diff > 0.05:
        what_improved.append(f"Expectancy: {b_agg['avg_expectancy']} -> {c_agg['avg_expectancy']}")
    elif exp_diff < -0.05:
        what_worsened.append(f"Expectancy: {b_agg['avg_expectancy']} -> {c_agg['avg_expectancy']}")

    total_trades_both = min(b_agg["total_trades"], c_agg["total_trades"])
    statistically_reliable = total_trades_both >= SAMPLE_MODERATE

    sample_warning = None
    if total_trades_both < SAMPLE_WEAK:
        sample_warning = f"Sample too small ({total_trades_both} trades) for reliable comparison."
    elif total_trades_both < SAMPLE_MODERATE:
        sample_warning = f"Sample moderate ({total_trades_both} trades). Comparison is preliminary."

    if not what_improved and not what_worsened:
        if sample_warning:
            recommendation = "Need more data before drawing conclusions."
        else:
            recommendation = "No material difference detected between runs."

    comparison = ResearchComparison(
        baseline_run_id=baseline,
        candidate_run_id=candidate,
        what_improved=what_improved,
        what_worsened=what_worsened,
        statistically_reliable=statistically_reliable,
        sample_warning=sample_warning,
        recommendation=recommendation if not what_improved and not what_worsened else "",
    )
    return comparison.model_dump()


def produce_research_recommendations(
    summary_or_comparison: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate advisory recommendations from a summary or comparison.

    All recommendations set do_not_auto_apply=True.
    """
    if not isinstance(summary_or_comparison, dict):
        return [ResearchRecommendation(
            recommendation_id=f"rec_{uuid.uuid4().hex[:8]}",
            recommendation_type="DO_NOT_CHANGE",
            target="unknown",
            evidence="No valid input provided.",
            do_not_auto_apply=True,
        ).model_dump()]

    recommendations: list[ResearchRecommendation] = []
    total_trades = int(summary_or_comparison.get("total_trades", 0))
    sample_quality = str(summary_or_comparison.get("sample_quality", "unknown"))
    top_clusters = summary_or_comparison.get("top_clusters", [])
    weak_clusters = summary_or_comparison.get("weak_clusters", [])

    # Insufficient sample → NEED_MORE_DATA
    if total_trades < SAMPLE_WEAK or sample_quality in ("unknown", "insufficient"):
        recommendations.append(ResearchRecommendation(
            recommendation_id=f"rec_{uuid.uuid4().hex[:8]}",
            recommendation_type="NEED_MORE_DATA",
            target="overall",
            evidence=f"Total trades: {total_trades}. Sample too small for reliable conclusions.",
            confidence="low",
            sample_size=total_trades,
            reliability=sample_quality,
            implementation_notes="Run more research with broader symbol/timeframe coverage.",
            do_not_auto_apply=True,
        ))

    # Weak clusters → DO_NOT_CHANGE or DISABLE_WEAK_SETUP
    for wc in weak_clusters:
        wc_key = wc.get("key", "unknown")
        wc_trades = int(wc.get("total_trades", 0))
        wc_pf = float(wc.get("avg_profit_factor", 0))

        if wc_trades >= SAMPLE_WEAK and wc_pf < 1.0:
            recommendations.append(ResearchRecommendation(
                recommendation_id=f"rec_{uuid.uuid4().hex[:8]}",
                recommendation_type="DO_NOT_CHANGE",
                target=wc_key,
                evidence=f"Cluster PF={wc_pf} over {wc_trades} trades indicates no edge.",
                confidence="low" if wc_trades < SAMPLE_MODERATE else "medium",
                sample_size=wc_trades,
                reliability=_sample_quality_label(wc_trades),
                implementation_notes="Re-test with more data before considering disabling.",
                do_not_auto_apply=True,
            ))

    # Strong clusters → validate
    for tc in top_clusters[:3]:
        tc_key = tc.get("key", "unknown")
        tc_trades = int(tc.get("total_trades", 0))
        tc_pf = float(tc.get("avg_profit_factor", 0))

        if tc_trades >= SAMPLE_MODERATE and tc_pf >= 1.3:
            recommendations.append(ResearchRecommendation(
                recommendation_id=f"rec_{uuid.uuid4().hex[:8]}",
                recommendation_type="PROMOTE_TO_VALIDATION",
                target=tc_key,
                evidence=f"Strong cluster: PF={tc_pf} over {tc_trades} trades. Needs OOS validation.",
                confidence="medium" if tc_trades >= SAMPLE_STRONG else "low",
                sample_size=tc_trades,
                reliability=_sample_quality_label(tc_trades),
                implementation_notes="Run OOS validation before any implementation consideration.",
                do_not_auto_apply=True,
            ))

    # Default recommendation
    if not recommendations:
        recommendations.append(ResearchRecommendation(
            recommendation_id=f"rec_{uuid.uuid4().hex[:8]}",
            recommendation_type="DO_NOT_CHANGE",
            target="general",
            evidence="No strong signals or weak areas detected in current data.",
            sample_size=total_trades,
            reliability=sample_quality,
            do_not_auto_apply=True,
        ))

    return [r.model_dump() for r in recommendations]
