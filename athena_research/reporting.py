"""
Athena Research Lab — Reporting
Writes CSV files and a structured Markdown report from StrategyMetrics results.
No live imports. No config writes.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from athena_research.metrics import StrategyMetrics

log = logging.getLogger(__name__)

MIN_IMPLEMENTATION_TRADES = 30
RUN_META_SCHEMA_VERSION = 2

OUTPUT_COLUMNS = [
    "run_id", "symbol", "asset_class", "timeframe", "zone", "family", "strategy_name",
    "params_str", "direction", "session", "status",
    "trade_count", "win_rate", "profit_factor", "avg_return", "expectancy",
    "max_drawdown", "sharpe", "sqn", "exposure_pct", "avg_duration_bars",
    "gross_return", "net_return", "is_return", "oos_return",
    "robustness_score", "param_sensitivity", "skip_reason", "data_source",
    "data_hash", "fee_per_side", "slippage",
    "entry_signal_count", "short_entry_signal_count", "exit_signal_count",
    "short_exit_signal_count", "simulation_backend", "simulation_warning",
    "engine", "engine_component", "candidate_action", "source_indicator",
    "market_group", "pair_group", "timeframe_zone", "session_bucket",
    "structure_context", "baseline_delta_pf", "baseline_delta_oos",
    "sample_ok", "recommendation",
    "backtest_exit_mode", "exit_reason_breakdown", "same_bar_policy", "atr_length",
    "implementation_verdict", "implementation_scope", "implementation_blockers",
    "engine_fidelity", "fidelity_note", "trust_tier", "trust_summary",
]


def _enrich_with_trust(df: pd.DataFrame, *, validation_run_count: int = 0) -> pd.DataFrame:
    """Apply implementation readiness, recommendations, and trust metadata."""
    from athena_research.trust_metadata import apply_trust_metadata

    work = _apply_implementation_readiness(_normalise_action_recommendations(df))
    return apply_trust_metadata(work, validation_run_count=validation_run_count)


def _prepare_report_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Readiness + recommendations only (trust deferred to API for large runs)."""
    return _apply_implementation_readiness(_normalise_action_recommendations(df))


def sample_operator_source_df(
    summary_path: Path,
    *,
    ranked_path: Path | None = None,
    limit: int = 2000,
) -> pd.DataFrame:
    """Load a capped slice for operator summary (avoid reading/enriching 20k+ rows)."""
    import pandas as pd

    if ranked_path is not None and ranked_path.exists():
        try:
            ranked = pd.read_csv(ranked_path)
            if not ranked.empty:
                return ranked.head(limit)
        except Exception:
            pass

    if not summary_path.exists():
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    try:
        df = pd.read_csv(summary_path)
    except Exception:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    if df.empty:
        return df

    work = df.copy()
    if "status" in work.columns:
        order = {"STRONG_CANDIDATE": 0, "WEAK_CANDIDATE": 1, "NEEDS_MORE_DATA": 2, "REJECT": 3}
        work["_status_order"] = work["status"].map(order).fillna(9)
        sort_cols = ["_status_order"]
        if "robustness_score" in work.columns:
            work["_rob"] = pd.to_numeric(work["robustness_score"], errors="coerce").fillna(-1)
            sort_cols.append("_rob")
        work = work.sort_values(sort_cols, ascending=[True] + [False] * (len(sort_cols) - 1))
        work = work.drop(columns=[c for c in work.columns if c.startswith("_")], errors="ignore")
    return work.head(limit)


# ─── Run folder helpers ───────────────────────────────────────────────────────

def make_run_dir(base_dir: str | Path, run_id: str) -> Path:
    d = Path(base_dir) / run_id
    d.mkdir(parents=True, exist_ok=True)
    # Update "latest" symlink / directory pointer
    latest = Path(base_dir) / "latest"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.symlink_to(d, target_is_directory=True)
    except Exception:
        # Windows may not support symlinks without elevated perms — write a pointer file instead
        try:
            (Path(base_dir) / "latest.txt").write_text(str(d))
        except Exception:
            pass
    return d


# ─── DataFrame helpers ────────────────────────────────────────────────────────

def metrics_to_df(results: list[StrategyMetrics]) -> pd.DataFrame:
    if not results:
        # Return empty DataFrame with correct schema so CSV writers never crash
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    rows = [r.to_dict() for r in results]
    df = pd.DataFrame(rows)
    # Ensure all expected columns exist (handles schema additions gracefully)
    for col in OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = float("nan")
    return df[OUTPUT_COLUMNS]


def write_research_summary_csv(df: pd.DataFrame, run_dir: Path) -> Path:
    """Persist research_summary.csv as early as possible (survives later report failures)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "research_summary.csv"
    out = df.copy()
    if not out.empty:
        out.to_csv(path, index=False, float_format="%.6f")
    else:
        pd.DataFrame(columns=OUTPUT_COLUMNS).to_csv(path, index=False)
    log.info("[reporting] wrote research_summary.csv (%d rows)", len(out))
    return path


def _safe_float(v) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else float("nan")
    except Exception:
        return float("nan")


def _safe_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None or pd.isna(v):
        return False
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y"}
    return bool(v)


def _safe_str(v) -> str:
    if v is None or pd.isna(v):
        return ""
    return str(v).strip()


def _apply_implementation_readiness(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add a separate implementation verdict to research rows.

    This is intentionally report-only. It does not alter candidate scoring,
    live gates, risk logic, or engine thresholds.
    """
    if df.empty:
        out = df.copy()
        for col in ("implementation_verdict", "implementation_scope", "implementation_blockers"):
            if col not in out.columns:
                out[col] = ""
        return out

    out = df.copy()
    verdicts: list[str] = []
    scopes: list[str] = []
    blockers_list: list[str] = []

    for _, row in out.iterrows():
        blockers: list[str] = []
        status = _safe_str(row.get("status", "")).upper()
        data_source = _safe_str(row.get("data_source", "")).lower()
        backend = _safe_str(row.get("simulation_backend", "")).lower()
        warning = _safe_str(row.get("simulation_warning", ""))
        trade_count = _safe_float(row.get("trade_count"))
        net_return = _safe_float(row.get("net_return"))
        oos_return = _safe_float(row.get("oos_return"))
        profit_factor = _safe_float(row.get("profit_factor"))
        robustness = _safe_float(row.get("robustness_score"))
        sample_ok = _safe_bool(row.get("sample_ok"))

        if status != "STRONG_CANDIDATE":
            blockers.append(f"status_not_strong:{status or 'missing'}")
        if not sample_ok:
            blockers.append("sample_not_ok")
        if warning:
            blockers.append(f"simulation_warning:{warning}")
        if backend == "pandas_fallback":
            blockers.append("simulator_fallback_used")
        if data_source in {"", "synthetic_test", "data_unavailable"}:
            blockers.append(f"data_source_not_live_research:{data_source or 'missing'}")
        if not math.isfinite(trade_count) or trade_count <= 0:
            blockers.append("no_completed_trades")
        elif trade_count < MIN_IMPLEMENTATION_TRADES:
            blockers.append(f"trade_count_below_{MIN_IMPLEMENTATION_TRADES}")
        if not math.isfinite(net_return) or net_return <= 0:
            blockers.append("net_return_not_positive")
        if not math.isfinite(oos_return) or oos_return <= 0:
            blockers.append("oos_return_not_positive")
        if not math.isfinite(profit_factor) or profit_factor <= 1.0:
            blockers.append("profit_factor_not_above_one")
        if not math.isfinite(robustness) or robustness < 0.60:
            blockers.append("robustness_below_strong_floor")

        if blockers:
            verdicts.append("NOT_IMPLEMENTABLE")
            scopes.append("RESEARCH_ONLY")
        else:
            verdicts.append("IMPLEMENTATION_READY")
            scopes.append("PAPER_TOOL_ONLY")
        blockers_list.append(";".join(blockers))

    out["implementation_verdict"] = verdicts
    out["implementation_scope"] = scopes
    out["implementation_blockers"] = blockers_list
    return out


def _normalise_action_recommendations(df: pd.DataFrame) -> pd.DataFrame:
    """Recompute display recommendations from row fields for new and older CSVs."""
    if df.empty:
        return df.copy()
    out = df.copy()
    try:
        from athena_research.research_context import recommendation_from_fields

        recs: list[str] = []
        for _, row in out.iterrows():
            recs.append(recommendation_from_fields(
                status=str(row.get("status", "")),
                action=str(row.get("candidate_action", "")),
                strategy_name=str(row.get("strategy_name", "")),
                family=str(row.get("family", "")),
                delta_pf=_safe_float(row.get("baseline_delta_pf")),
                delta_oos=_safe_float(row.get("baseline_delta_oos")),
            ))
        out["recommendation"] = recs
    except Exception:
        if "recommendation" not in out.columns:
            out["recommendation"] = ""
    return out


def build_operator_decision_summary(
    df: pd.DataFrame,
    *,
    validation_run_count: int = 0,
) -> dict:
    """Build the operator-facing action summary from deterministic result rows."""
    from athena_research.trust_metadata import build_trust_context

    if df.empty:
        return {
            "headline": "No usable research rows found.",
            "decision": "NEEDS_MORE_DATA",
            "source": "DETERMINISTIC_RESULTS",
            "source_of_truth": "research_summary.csv",
            "use_now": [],
            "research_candidates": [],
            "blocked_candidates": [],
            "candidate_groups": [],
            "keep": [],
            "remove_or_demote": [],
            "retest": [],
            "warnings": ["No Research Lab rows were available."],
            "next_step": "Run a new Research Lab discovery.",
            "implementation_ready_count": 0,
            "ready_use_add_count": 0,
            "total_candidate_count": 0,
            "use_now_trusted": [],
            "screen_only": [],
            "reject_trusted": [],
            "trust_context": build_trust_context(df, validation_run_count=validation_run_count),
        }

    work = _enrich_with_trust(df, validation_run_count=validation_run_count)

    def _num(col: str) -> pd.Series:
        return pd.to_numeric(work.get(col, pd.Series(float("nan"), index=work.index)), errors="coerce")

    work["_status_order"] = work.get("status", "").map({
        "STRONG_CANDIDATE": 0,
        "WEAK_CANDIDATE": 1,
        "NEEDS_MORE_DATA": 2,
        "REJECT": 3,
    }).fillna(9)
    work["_sort_oos"] = _num("oos_return").fillna(-999)
    work["_sort_robust"] = _num("robustness_score").fillna(-999)
    work["_sort_pf"] = _num("profit_factor").fillna(-999)
    work = work.sort_values(
        ["_status_order", "_sort_oos", "_sort_robust", "_sort_pf"],
        ascending=[True, False, False, False],
    )

    def _records(frame: pd.DataFrame, limit: int = 100) -> list[dict]:
        cols = [
            "recommendation", "engine", "engine_component", "strategy_name", "source_indicator",
            "family", "symbol", "timeframe", "direction", "status", "trade_count",
            "win_rate", "profit_factor", "oos_return", "robustness_score",
            "implementation_verdict", "implementation_blockers",
            "engine_fidelity", "fidelity_note", "trust_tier", "trust_summary",
        ]
        available = [c for c in cols if c in frame.columns]
        return json.loads(frame[available].head(limit).fillna("").to_json(orient="records"))

    def _groups(frame: pd.DataFrame, limit: int = 50) -> list[dict]:
        if frame.empty:
            return []
        group_cols = [
            c for c in [
                "recommendation", "engine", "engine_component", "strategy_name",
                "family", "market_group", "pair_group", "timeframe",
            ]
            if c in frame.columns
        ]
        if not group_cols:
            return []
        grouped = frame.groupby(group_cols, dropna=False).agg(
            configs=("status", "count"),
            symbols=("symbol", lambda x: ", ".join(sorted({str(v) for v in x if str(v)}))),
            avg_trades=("trade_count", "mean"),
            avg_pf=("profit_factor", "mean"),
            avg_oos=("oos_return", "mean"),
            avg_robustness=("robustness_score", "mean"),
        ).reset_index()
        grouped = grouped.sort_values(["configs", "avg_oos", "avg_robustness"], ascending=False)
        return json.loads(grouped.head(limit).fillna("").to_json(orient="records"))

    add_rows = work[work["recommendation"] == "ADD"]
    keep_rows = work[work["recommendation"] == "KEEP"]
    use_rows = pd.concat([add_rows, keep_rows], ignore_index=False).sort_values(
        ["_status_order", "_sort_oos", "_sort_robust", "_sort_pf"],
        ascending=[True, False, False, False],
    )
    remove_rows = work[work["recommendation"].isin(["REMOVE_OR_DEMOTE", "REJECT"])]
    retest_rows = work[work["recommendation"].isin(["RETEST", "WATCHLIST_ONLY"])]
    ready_use_rows = use_rows[use_rows["implementation_verdict"] == "IMPLEMENTATION_READY"]
    blocked_use_rows = use_rows[use_rows["implementation_verdict"] != "IMPLEMENTATION_READY"]

    warnings: list[str] = []
    warning_col = work.get("simulation_warning", pd.Series("", index=work.index)).fillna("").astype(str)
    backend_col = work.get("simulation_backend", pd.Series("", index=work.index)).fillna("").astype(str)
    if int((warning_col != "").sum()) > 0:
        warnings.append(f"{int((warning_col != '').sum())} rows have simulator warnings.")
    if int((backend_col == "pandas_fallback").sum()) > 0:
        warnings.append(f"{int((backend_col == 'pandas_fallback').sum())} rows used pandas fallback.")

    ready_col = work.get("implementation_verdict", pd.Series("", index=work.index)).fillna("").astype(str)
    ready_count = int((ready_col == "IMPLEMENTATION_READY").sum())
    ready_use_count = int(len(ready_use_rows))
    if ready_use_count == 0 and not use_rows.empty:
        warnings.append("Use/add candidates exist, but none passed implementation readiness yet.")

    if not ready_use_rows.empty:
        top = ready_use_rows.iloc[0]
        decision = "USE_ADD_CANDIDATE"
        headline = (
            f"{len(ready_use_rows)} ready use/add result rows. Top: "
            f"{top.get('strategy_name', 'candidate')} for {top.get('engine', 'research')} "
            f"on {top.get('timeframe', 'TF')} / {top.get('symbol', 'symbol')}."
        )
        next_step = "Use the implementation-ready rows from this result table as the paper-tool action list."
    elif not use_rows.empty:
        decision = "CANDIDATES_BLOCKED"
        headline = "Candidate rows exist, but none passed implementation readiness."
        next_step = "Use the listed blockers to decide what data or telemetry must be fixed before implementation."
    elif not keep_rows.empty:
        decision = "KEEP_CURRENT"
        headline = "Keep existing setup; no better add candidate beat it."
        next_step = "Keep the current component and do not add new logic from this run."
    elif not remove_rows.empty:
        decision = "REMOVE_OR_DEMOTE"
        headline = "Remove or demote failing components from this run."
        next_step = "Do not implement rejected components."
    else:
        decision = "NEEDS_MORE_DATA"
        headline = "No clear keep/add/remove action from this run."
        next_step = "Run a broader validation only if this area is still important."

    trust_col = work.get("trust_tier", pd.Series("", index=work.index)).fillna("").astype(str)
    use_now_trusted = work[trust_col == "USE_NOW"]
    screen_only_rows = work[trust_col == "SCREEN_ONLY"]
    reject_trusted = work[trust_col == "REJECT"]
    retest_trusted = work[trust_col == "RETEST"]

    if not use_now_trusted.empty and ready_use_count == 0:
        warnings.append(
            f"{len(use_now_trusted)} live-aligned row(s) passed trust but failed implementation readiness."
        )
    if decision == "USE_ADD_CANDIDATE" and use_now_trusted.empty and not use_rows.empty:
        warnings.append(
            "Use/add candidates exist but none are USE_NOW trusted (proxy or discovery-only)."
        )

    return {
        "headline": headline,
        "decision": decision,
        "source": "DETERMINISTIC_RESULTS",
        "source_of_truth": "research_summary.csv",
        "use_now": _records(ready_use_rows),
        "research_candidates": _records(use_rows),
        "blocked_candidates": _records(blocked_use_rows),
        "candidate_groups": _groups(ready_use_rows),
        "keep": _records(keep_rows),
        "remove_or_demote": _records(remove_rows),
        "retest": _records(retest_rows),
        "warnings": warnings,
        "next_step": next_step,
        "implementation_ready_count": ready_count,
        "ready_use_add_count": ready_use_count,
        "total_candidate_count": int(len(use_rows)),
        "use_now_trusted": _records(use_now_trusted),
        "screen_only": _records(screen_only_rows),
        "reject_trusted": _records(reject_trusted),
        "retest_trusted": _records(retest_trusted),
        "trust_context": build_trust_context(work, validation_run_count=validation_run_count),
    }


# ─── CSV writers ──────────────────────────────────────────────────────────────

def write_csvs(df: pd.DataFrame, run_dir: Path) -> list[Path]:
    # Trust columns are added on API read for top rows — full-frame enrich is too slow at 20k+ rows.
    df = _prepare_report_frame(df)
    written = []

    def _save(frame: pd.DataFrame, name: str):
        p = run_dir / name
        frame.to_csv(p, index=False, float_format="%.6f")
        written.append(p)
        log.info("[reporting] wrote %s (%d rows)", name, len(frame))

    # Full summary (all results including rejects)
    _save(df, "research_summary.csv")

    # Ranked — valid results only, sorted by robustness × net_return
    ranked = df[df["status"].isin(["STRONG_CANDIDATE", "WEAK_CANDIDATE"])].copy()
    if len(ranked) > 0:
        ranked["rank_score"] = (
            ranked["robustness_score"].fillna(0) * 0.5 +
            ranked["net_return"].apply(_safe_float).clip(-1, 5) * 0.3 +
            ranked["sqn"].apply(_safe_float).clip(-5, 5) / 10 * 0.2
        )
        ranked = ranked.sort_values("rank_score", ascending=False)
    _save(ranked, "ranked_strategies.csv")

    # By asset group
    if "asset_class" in df.columns:
        _save(_group_agg(df, "asset_class"), "by_asset_group.csv")

    # By symbol
    if "symbol" in df.columns:
        _save(_group_agg(df, "symbol"), "by_symbol.csv")

    # By timeframe
    if "timeframe" in df.columns:
        _save(_group_agg(df, "timeframe"), "by_timeframe.csv")

    # By session
    if "session" in df.columns:
        _save(_group_agg(df, "session"), "by_session.csv")
    if "session_bucket" in df.columns and df["session_bucket"].notna().any() and (df["session_bucket"] != "").any():
        _save(_group_agg(df[df["session_bucket"] != ""], "session_bucket"), "by_session_bucket.csv")

    # By direction
    if "direction" in df.columns:
        _save(_group_agg(df, "direction"), "by_direction.csv")

    # By zone (scalp/intra/swing)
    if "zone" in df.columns and df["zone"].notna().any() and (df["zone"] != "").any():
        _save(_group_agg(df[df["zone"] != ""], "zone"), "by_zone.csv")
        _save(_zone_breakdown(df[df["zone"] != ""]), "zone_breakdown_scalp_intra_swing.csv")

    if "engine" in df.columns:
        _save(_engine_component_audit(df, "ENGINE_A"), "engine_a_component_audit.csv")
        _save(_engine_component_audit(df, "ENGINE_B"), "engine_b_component_audit.csv")

    if {"engine", "asset_class", "timeframe", "family", "backtest_exit_mode"}.issubset(df.columns):
        _save(
            _audit_agg(df, ["engine", "asset_class", "timeframe", "family", "backtest_exit_mode"]),
            "by_engine_asset_timeframe_family_exit_mode.csv",
        )
    if "backtest_exit_mode" in df.columns:
        _save(_group_agg(df, "backtest_exit_mode"), "by_backtest_exit_mode.csv")

    if "market_group" in df.columns:
        _save(_group_context_breakdown(df), "group_breakdown.csv")

    if "structure_context" in df.columns:
        _save(_structure_context_breakdown(df), "structure_context_breakdown.csv")

    if "recommendation" in df.columns:
        _save(_automated_next_tests(df), "automated_next_tests.csv")
        _save(_recommendation_table(df), "add_remove_retest_recommendations.csv")

    if "implementation_verdict" in df.columns:
        ready = df[df["implementation_verdict"] == "IMPLEMENTATION_READY"].copy()
        _save(ready, "implementation_ready_candidates.csv")

    # Per-pair recommendation (top result per symbol)
    if not ranked.empty:
        per_pair = ranked.groupby("symbol").first().reset_index()
        _save(per_pair, "per_pair_recommendation.csv")

    # Indicator attribution (aggregated by strategy_name)
    _save(_indicator_attribution(df), "indicator_attribution.csv")
    _save(_candidate_indicator_attribution(df), "candidate_indicator_attribution.csv")

    # Rejected / failed
    rejected = df[~df["status"].isin(["STRONG_CANDIDATE", "WEAK_CANDIDATE"])]
    _save(rejected, "rejected_or_failed_configs.csv")

    return written


def _group_agg(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    valid = df[df["status"].isin(["STRONG_CANDIDATE", "WEAK_CANDIDATE"])].copy()
    if valid.empty:
        return pd.DataFrame()

    numeric_cols = ["win_rate", "profit_factor", "expectancy", "sharpe", "sqn",
                    "net_return", "oos_return", "robustness_score", "trade_count"]
    available = [c for c in numeric_cols if c in valid.columns]

    agg = valid.groupby(group_col)[available].agg(["mean", "count"]).reset_index()
    agg.columns = [f"{a}_{b}" if b else a for a, b in agg.columns]
    return agg


def _indicator_attribution(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    agg = df.groupby("strategy_name").agg(
        total_configs=("status", "count"),
        strong=("status", lambda x: (x == "STRONG_CANDIDATE").sum()),
        weak=("status", lambda x: (x == "WEAK_CANDIDATE").sum()),
        reject=("status", lambda x: (x == "REJECT").sum()),
        avg_net_return=("net_return", "mean"),
        avg_win_rate=("win_rate", "mean"),
        avg_robustness=("robustness_score", "mean"),
        avg_sqn=("sqn", "mean"),
        avg_oos_return=("oos_return", "mean"),
        total_trades=("trade_count", "sum"),
    ).reset_index()
    agg["pass_rate"] = (agg["strong"] + agg["weak"]) / agg["total_configs"].replace(0, 1)
    
    def _calc_verdict(r):
        oos = _safe_float(r["avg_oos_return"])
        rob = _safe_float(r["avg_robustness"])
        trades = _safe_float(r["total_trades"])
        # Positive OOS, acceptable robustness (>= 0.3), and minimum trades
        if oos > 0 and rob >= 0.3 and trades >= 10:
            return "HELPS"
        elif oos < 0:
            return "HURTS"
        return "NEUTRAL"

    agg["verdict"] = agg.apply(_calc_verdict, axis=1)
    return agg.sort_values("avg_robustness", ascending=False)


def _candidate_indicator_attribution(df: pd.DataFrame) -> pd.DataFrame:
    group_cols = [
        c for c in ["source_indicator", "engine", "engine_component", "strategy_name", "market_group", "pair_group", "timeframe_zone"]
        if c in df.columns
    ]
    return _audit_agg(df, group_cols)


def _audit_agg(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    cols = [c for c in group_cols if c in df.columns]
    if not cols:
        return pd.DataFrame()
    numeric = {
        "configs": ("status", "count"),
        "strong": ("status", lambda x: (x == "STRONG_CANDIDATE").sum()),
        "weak": ("status", lambda x: (x == "WEAK_CANDIDATE").sum()),
        "reject": ("status", lambda x: (x == "REJECT").sum()),
        "needs_more": ("status", lambda x: (x == "NEEDS_MORE_DATA").sum()),
        "avg_pf": ("profit_factor", "mean"),
        "avg_wr": ("win_rate", "mean"),
        "avg_oos": ("oos_return", "mean"),
        "avg_delta_pf": ("baseline_delta_pf", "mean"),
        "avg_delta_oos": ("baseline_delta_oos", "mean"),
        "avg_robustness": ("robustness_score", "mean"),
        "total_trades": ("trade_count", "sum"),
    }
    available = {k: v for k, v in numeric.items() if v[0] in df.columns}
    if not available:
        return pd.DataFrame()
    out = df.groupby(cols, dropna=False).agg(**available).reset_index()
    out["pass_rate"] = (out["strong"] + out["weak"]) / out["configs"].replace(0, 1)
    return out.sort_values(["strong", "avg_delta_oos", "avg_oos"], ascending=False)


def _engine_component_audit(df: pd.DataFrame, engine: str) -> pd.DataFrame:
    edf = df[df["engine"] == engine].copy() if "engine" in df.columns else pd.DataFrame()
    return _audit_agg(edf, ["engine", "engine_component", "strategy_name", "market_group", "pair_group", "timeframe_zone"])


def _zone_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    return _audit_agg(df, ["timeframe_zone", "engine", "market_group", "pair_group"])


def _group_context_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    return _audit_agg(df, ["market_group", "pair_group", "timeframe_zone", "engine", "engine_component"])


def _structure_context_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    return _audit_agg(df, ["structure_context", "engine", "market_group", "pair_group", "timeframe_zone"])


def _recommendation_table(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "recommendation", "engine", "engine_component", "strategy_name", "source_indicator",
        "family", "market_group", "pair_group", "symbol", "timeframe_zone", "timeframe", "structure_context", "direction",
        "status", "trade_count", "win_rate", "profit_factor", "oos_return",
        "baseline_delta_pf", "baseline_delta_oos", "robustness_score",
        "implementation_verdict", "implementation_scope", "implementation_blockers",
    ]
    available = [c for c in cols if c in df.columns]
    if not available:
        return pd.DataFrame()
    ranked = df.copy()
    ranked["_rec_order"] = ranked["recommendation"].map({
        "ADD": 0,
        "KEEP": 1,
        "RETEST": 2,
        "WATCHLIST_ONLY": 3,
        "REMOVE_OR_DEMOTE": 4,
        "REJECT": 5,
    }).fillna(9)
    ranked = ranked.sort_values(["_rec_order", "baseline_delta_oos", "robustness_score"], ascending=[True, False, False])
    return ranked[available].head(200)


def _automated_next_tests(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "recommendation" not in df.columns:
        return pd.DataFrame()
    status_col = df.get("status", pd.Series("", index=df.index)).fillna("").astype(str).str.upper()
    rec_col = df["recommendation"].fillna("").astype(str).str.upper()
    weak = df[
        rec_col.isin(["REMOVE_OR_DEMOTE", "REJECT"])
        | status_col.isin(["REJECT", "NEEDS_MORE_DATA"])
    ].copy()
    if weak.empty:
        return pd.DataFrame()
    rows = []
    alt_map = {
        "ema_coherence": ["aroon_trend", "supertrend_follow", "chandelier_trend"],
        "rsi_macd_momentum": ["stochastic_cross", "rsi_divergence"],
        "entry_pullback": ["fib_retracement", "structure_filters"],
        "session_breakout": ["realized_vol_breakout", "bb_squeeze_breakout", "ob_bos"],
        "breakout_filter": ["realized_vol_breakout", "structure_filters"],
        "mean_reversion_candidate": ["stochastic_divergence", "rsi_divergence", "obv_divergence"],
        "structure_break": ["structure_filters", "micro_breakout"],
        "entry_trigger": ["cvd_momentum", "vwap_reclaim", "micro_breakout"],
        "location_quality": ["vwap_deviation", "fib_retracement"],
    }
    group_cols = ["engine", "engine_component", "market_group", "pair_group", "timeframe_zone"]
    for keys, grp in weak.groupby([c for c in group_cols if c in weak.columns], dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        ctx = dict(zip([c for c in group_cols if c in weak.columns], keys))
        component = str(ctx.get("engine_component", ""))
        rows.append({
            **ctx,
            "failed_configs": len(grp),
            "failed_strategies": ",".join(sorted(set(str(x) for x in grp.get("strategy_name", [])))[:5]),
            "suggested_strategies": ",".join(alt_map.get(component, ["trend_momentum", "engine_b_proxy", "volatility"])),
            "reason": "Automated retest because current component failed or remained conditional in this group/zone.",
        })
    return pd.DataFrame(rows)



# ─── Markdown report ─────────────────────────────────────────────────────────

def write_markdown_report(df: pd.DataFrame, run_dir: Path, run_id: str, run_meta: dict) -> Path:
    """Generate research_report.md answering the required research questions."""
    report_path = run_dir / "research_report.md"
    df = _enrich_with_trust(df)

    lines: list[str] = []
    a = lines.append

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    a(f"# Athena Research Lab — Report")
    a(f"**Run ID:** `{run_id}`  **Generated:** {ts}  ")
    a(f"**Mode:** {run_meta.get('mode', 'research')}  **Symbols:** {run_meta.get('symbol_count', '?')}  "
      f"**Families:** {run_meta.get('families', '?')}")
    a(f"**Backtest exit mode:** `{run_meta.get('backtest_exit_mode', 'triple_barrier')}`")
    a("")
    a("> **IMPORTANT:** These are backtest discovery findings at intentionally lower thresholds.")
    a("> Do NOT copy these thresholds into live engine gates.")
    a("> Label: **STRONG_CANDIDATE** / **WEAK_CANDIDATE** / **REJECT** / **NEEDS_MORE_DATA**")
    a("")

    valid = df[df["status"].isin(["STRONG_CANDIDATE", "WEAK_CANDIDATE"])]
    strong = df[df["status"] == "STRONG_CANDIDATE"]
    n_total = len(df)
    n_valid = len(valid)
    n_strong = len(strong)

    a("## Executive Summary")
    a(f"- Total strategy/param/symbol/TF combinations tested: **{n_total}**")
    a(f"- Valid (pass robustness): **{n_valid}** ({n_valid/max(n_total,1)*100:.1f}%)")
    a(f"- Strong candidates: **{n_strong}**")
    if valid.empty:
        a("- **No valid candidates found.** More data or different parameters needed.")
    a("")

    # ── Q1: Best strategy family ─────────────────────────────────────────────
    a("## Research Run Self-Audit")
    if df.empty:
        a("No rows available for self-audit.")
    else:
        warning_col = df.get("simulation_warning", pd.Series("", index=df.index)).fillna("").astype(str)
        backend_col = df.get("simulation_backend", pd.Series("", index=df.index)).fillna("").astype(str)
        entry_col = pd.to_numeric(df.get("entry_signal_count", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
        short_entry_col = pd.to_numeric(df.get("short_entry_signal_count", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
        warning_rows = warning_col[warning_col != ""]
        fallback_rows = backend_col[backend_col == "pandas_fallback"]
        total_signals = int(entry_col.sum() + short_entry_col.sum())
        a(f"- Rows with simulator warnings: **{len(warning_rows)}**")
        a(f"- Rows using pandas fallback: **{len(fallback_rows)}**")
        a(f"- Total entry signals observed: **{total_signals}**")
        if len(warning_rows) > 0:
            warn_agg = warning_rows.value_counts().rename_axis("simulation_warning").reset_index(name="count")
            a(_df_to_md(warn_agg))
        else:
            a("- Simulator warnings: none")
        if "backtest_exit_mode" in df.columns:
            exit_mode_counts = df["backtest_exit_mode"].fillna("").astype(str).value_counts().rename_axis("backtest_exit_mode").reset_index(name="rows")
            a("")
            a("### Exit Baseline")
            a(_df_to_md(exit_mode_counts))
        if "exit_reason_breakdown" in df.columns:
            reason_counts = df["exit_reason_breakdown"].fillna("").astype(str)
            reason_counts = reason_counts[reason_counts != ""].value_counts().rename_axis("exit_reason_breakdown").reset_index(name="rows")
            if not reason_counts.empty:
                a(_df_to_md(reason_counts.head(20)))
    a("")

    a("## Implementation Readiness")
    if df.empty:
        a("No rows available for implementation readiness.")
    else:
        readiness = df.get("implementation_verdict", pd.Series("", index=df.index)).fillna("").astype(str)
        counts = readiness.value_counts().rename_axis("implementation_verdict").reset_index(name="count")
        a(_df_to_md(counts))
        ready_rows = df[df["implementation_verdict"] == "IMPLEMENTATION_READY"].copy()
        if ready_rows.empty:
            a("")
            a("**Implementation verdict:** `NOT_IMPLEMENTABLE`")
            a("No candidate passed all implementation-readiness checks for this run.")
        else:
            cols = [
                "symbol", "timeframe", "strategy_name", "status", "trade_count",
                "profit_factor", "oos_return", "robustness_score", "implementation_scope",
            ]
            a("")
            a("**Implementation verdict:** `IMPLEMENTATION_READY` candidates exist for paper-tool integration.")
            a(_df_to_md(ready_rows[[c for c in cols if c in ready_rows.columns]].head(15)))
        blocked = df[df["implementation_verdict"] != "IMPLEMENTATION_READY"].copy()
        if not blocked.empty and "implementation_blockers" in blocked.columns:
            blocker_counts = (
                blocked["implementation_blockers"]
                .fillna("")
                .astype(str)
                .str.split(";")
                .explode()
            )
            blocker_counts = blocker_counts[blocker_counts != ""].value_counts().head(12)
            if not blocker_counts.empty:
                a("")
                a("Top implementation blockers:")
                a(_df_to_md(blocker_counts.rename_axis("blocker").reset_index(name="count")))
    a("")

    a("## Which Strategy Family Works Best?")
    if not valid.empty:
        fam_agg = valid.groupby("family").agg(
            count=("status", "count"),
            avg_net_return=("net_return", "mean"),
            avg_wr=("win_rate", "mean"),
            avg_pf=("profit_factor", "mean"),
            avg_sqn=("sqn", "mean"),
        ).sort_values("avg_net_return", ascending=False)
        a(_df_to_md(fam_agg.reset_index()))
        best_fam = fam_agg.index[0] if len(fam_agg) > 0 else "N/A"
        a(f"\n**Best family:** `{best_fam}` (avg net return {fam_agg.iloc[0]['avg_net_return']:.3f})")
    else:
        a("Insufficient valid results.")
    a("")

    # ── Q2: Best symbol ──────────────────────────────────────────────────────
    a("## Which Symbol Works Best?")
    if not valid.empty:
        sym_agg = valid.groupby("symbol").agg(
            count=("status", "count"),
            avg_net_return=("net_return", "mean"),
            avg_wr=("win_rate", "mean"),
        ).sort_values("avg_net_return", ascending=False).head(10)
        a(_df_to_md(sym_agg.reset_index()))
    else:
        a("No valid results.")
    a("")

    # ── Q3: Best timeframe ───────────────────────────────────────────────────
    a("## Which Timeframe Works Best?")
    if not valid.empty:
        tf_agg = valid.groupby("timeframe").agg(
            count=("status", "count"),
            avg_net_return=("net_return", "mean"),
            avg_wr=("win_rate", "mean"),
        ).sort_values("avg_net_return", ascending=False)
        a(_df_to_md(tf_agg.reset_index()))
    else:
        a("No valid results.")
    a("")

    # ── Zone breakdown ──────────────────────────────────────────────────────
    a("## Performance by Zone (Scalp / Intra / Swing)")
    if not valid.empty and "zone" in valid.columns and (valid["zone"] != "").any():
        zone_df = valid[valid["zone"] != ""]
        zone_agg = zone_df.groupby("zone").agg(
            count=("status", "count"),
            avg_net_return=("net_return", "mean"),
            avg_wr=("win_rate", "mean"),
            avg_robustness=("robustness_score", "mean"),
        ).sort_values("avg_net_return", ascending=False)
        a(_df_to_md(zone_agg.reset_index()))
    else:
        a("No zone data available for this run.")
    a("")

    # ── Engine A/B audit sections ───────────────────────────────────────────
    a("## Current Engine A Baseline")
    ea_audit = _engine_component_audit(df, "ENGINE_A")
    if not ea_audit.empty:
        a(_df_to_md(ea_audit.head(15)))
    else:
        a("No Engine A research rows were available for this run.")
    a("")

    a("## Current Engine B Baseline")
    eb_audit = _engine_component_audit(df, "ENGINE_B")
    if not eb_audit.empty:
        a(_df_to_md(eb_audit.head(15)))
    else:
        a("No Engine B research rows were available for this run.")
    a("")

    a("## Scalp Results")
    scalp_df = df[df.get("timeframe_zone", df.get("zone", "")) == "scalp"] if not df.empty else pd.DataFrame()
    a(_df_to_md(_audit_agg(scalp_df, ["engine", "engine_component", "strategy_name", "market_group", "pair_group"]).head(12)) if not scalp_df.empty else "No scalp rows.")
    a("")

    a("## Intraday Results")
    intra_df = df[df.get("timeframe_zone", df.get("zone", "")) == "intra"] if not df.empty else pd.DataFrame()
    a(_df_to_md(_audit_agg(intra_df, ["engine", "engine_component", "strategy_name", "market_group", "pair_group"]).head(12)) if not intra_df.empty else "No intraday rows.")
    a("")

    a("## Swing Results")
    swing_df = df[df.get("timeframe_zone", df.get("zone", "")) == "swing"] if not df.empty else pd.DataFrame()
    a(_df_to_md(_audit_agg(swing_df, ["engine", "engine_component", "strategy_name", "market_group", "pair_group"]).head(12)) if not swing_df.empty else "No swing rows.")
    a("")

    a("## What Works By Group")
    recs = _recommendation_table(df)
    works = recs[recs["recommendation"].isin(["ADD", "KEEP"])] if not recs.empty and "recommendation" in recs.columns else pd.DataFrame()
    a(_df_to_md(works.head(15)) if not works.empty else "No add/keep recommendations found.")
    a("")

    a("## What Fails By Group")
    fails = recs[recs["recommendation"].isin(["REMOVE_OR_DEMOTE", "REJECT"])] if not recs.empty and "recommendation" in recs.columns else pd.DataFrame()
    a(_df_to_md(fails.head(15)) if not fails.empty else "No clear failures found.")
    a("")

    a("## What Only Works With Structure")
    conditional = df[df["recommendation"].isin(["WATCHLIST_ONLY", "RETEST"])] if "recommendation" in df.columns else pd.DataFrame()
    if not conditional.empty:
        a(_df_to_md(conditional[[
            c for c in ["engine", "engine_component", "strategy_name", "market_group", "pair_group",
                        "timeframe_zone", "structure_context", "recommendation", "profit_factor",
                        "oos_return", "baseline_delta_oos"] if c in conditional.columns
        ]].head(15)))
    else:
        a("No structure-only candidates found.")
    a("")

    a("## Automated Alternative Tests")
    next_tests = _automated_next_tests(df)
    a(_df_to_md(next_tests.head(20)) if not next_tests.empty else "No automated follow-up tests required from this run.")
    a("")

    a("## Add / Keep / Remove / Retest")
    a(_df_to_md(recs.head(25)) if not recs.empty else "No recommendations available.")
    a("")

    # ── Q4: Best direction ───────────────────────────────────────────────────
    a("## Which Direction Works Better?")
    if not valid.empty and "direction" in valid.columns:
        dir_agg = valid.groupby("direction").agg(
            count=("status", "count"),
            avg_net_return=("net_return", "mean"),
            avg_wr=("win_rate", "mean"),
        ).sort_values("avg_net_return", ascending=False)
        a(_df_to_md(dir_agg.reset_index()))
    else:
        a("No valid results.")
    a("")

    # ── Q5: Indicator attribution ────────────────────────────────────────────
    a("## Which Indicators Help / Hurt?")
    attr = _indicator_attribution(df)
    if not attr.empty:
        helps = attr[attr["verdict"] == "HELPS"][["strategy_name", "pass_rate", "avg_net_return", "avg_sqn"]].head(10)
        hurts = attr[attr["verdict"] == "HURTS"][["strategy_name", "pass_rate", "avg_net_return"]].head(5)
        a("**Helpful indicators/strategies:**")
        a(_df_to_md(helps) if not helps.empty else "No clear indicator edge.")
        a("\n**Globally weak across tested configs:**")
        a(_df_to_md(hurts) if not hurts.empty else "No clear globally weak indicators.")
    elif not valid.empty:
        a("**Helpful indicators/strategies:**")
        helps = valid.groupby("strategy_name").agg(pass_rate=("status", "count"), avg_net_return=("net_return", "mean")).reset_index().head(10)
        a(_df_to_md(helps))
        a("\n**Globally weak across tested configs:**")
        a("No data.")
    else:
        a("Insufficient data.")
    a("")

    # ── Q6: Fee sensitivity ──────────────────────────────────────────────────
    a("## Which Setups Collapse After Fees?")
    if not df.empty:
        fee_collapse = df[
            (df["gross_return"].apply(_safe_float) > 0) &
            (df["net_return"].apply(_safe_float) <= 0)
        ]
        if len(fee_collapse) > 0:
            a(f"**{len(fee_collapse)} strategy configs are gross-profitable but net-negative after fees.**")
            fc_agg = fee_collapse.groupby("family").size().reset_index(name="count")
            a(_df_to_md(fc_agg))
        else:
            a("No gross-profitable-but-fee-killed setups found.")
    a("")

    # ── Q7: Insufficient sample ──────────────────────────────────────────────
    a("## Which Setups Had Too Little Sample?")
    low_n = df[df["status"] == "NEEDS_MORE_DATA"]
    a(f"{len(low_n)} configs had insufficient trades.  ")
    if len(low_n) > 0:
        lo_fam = low_n.groupby("family").size().reset_index(name="count").sort_values("count", ascending=False)
        a(_df_to_md(lo_fam))
    a("")

    # ── New Autopilot sections ────────────────────────────────────────────────
    a("## Recommended Research Queue")
    a("Please use the Autopilot console on the dashboard to generate comprehensive research vectors.")
    a("")

    a("## Confirmed / Weakened / Rejected After Validation")
    a("Validation tracks are executed sequentially.")
    a("")

    a("## Conditional Edge Candidates")
    conditional_edges = df[(df["net_return"].apply(_safe_float) > 0) & (df["status"] == "REJECT")]
    if not conditional_edges.empty:
        a("The following configurations show high conditional edge potential despite global weakness:")
        ce_agg = conditional_edges[["strategy_name", "symbol", "timeframe", "net_return"]].head(5)
        a(_df_to_md(ce_agg))
    else:
        a("No conditional edges detected.")
    a("")

    # ── Q8–Q10: Engine recommendations ──────────────────────────────────────
    a("## Engine A Findings")
    a(_engine_recommendation(valid, "trend_momentum") + "\n" + _engine_recommendation(valid, "pullback"))
    a("")
    a("## Engine B Findings")
    a(_engine_recommendation(valid, "engine_b_proxy"))
    a("")
    a("## Engine D Proxy Findings (NOT real Engine D)")
    a(_engine_recommendation(valid, "engine_d_proxy"))
    a("")

    # ── Q11: Do not test further ─────────────────────────────────────────────
    a("## What Should NOT Be Tested Further Right Now?")
    reject_fam = df[df["status"] == "REJECT"].groupby("family").size().sort_values(ascending=False)
    for fam, cnt in reject_fam.items():
        a(f"- `{fam}`: {cnt} configs rejected — insufficient edge at current parameters")
    if reject_fam.empty:
        a("- No definitive rejections yet — more data needed.")
    a("")

    # ── Q12: Next tiny test ──────────────────────────────────────────────────
    a("## Recommended Next Tiny Test")
    if n_strong > 0:
        best = strong.sort_values("robustness_score", ascending=False).iloc[0]
        a(f"Run `{best.get('strategy_name', '')}` on `{best.get('symbol', '')}` `{best.get('timeframe', '')}`.")
    else:
        a("No strong candidates yet.  Run more symbols and timeframes before tuning.")
    a("")

    a("---")
    a(f"*Generated by Athena Research Lab v1.0 — {ts}*")
    a("*Backtest discovery findings only.  Not a live execution recommendation.*")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("[reporting] wrote research_report.md")
    return report_path


def _df_to_md(df: pd.DataFrame) -> str:
    if df.empty:
        return "*No data*"
    # Truncate floats
    df = df.copy()
    for col in df.select_dtypes(include="float").columns:
        df[col] = df[col].map(lambda x: f"{x:.4f}" if pd.notna(x) else "")
    header = " | ".join(str(c) for c in df.columns)
    sep = " | ".join(["---"] * len(df.columns))
    rows = [" | ".join(str(v) for v in row) for row in df.values]
    return "\n".join([header, sep] + rows)


def _engine_recommendation(valid: pd.DataFrame, family: str) -> str:
    fam_df = valid[valid["family"] == family] if not valid.empty else pd.DataFrame()
    if fam_df.empty:
        return f"*No valid `{family}` results — insufficient data or all rejected.*"
    best = fam_df.sort_values("robustness_score", ascending=False).iloc[0]
    lines = [
        f"Best `{family}` config: `{best['strategy_name']}` params=`{best['params_str']}`",
        f"  - Symbol: `{best['symbol']}` TF: `{best['timeframe']}` Direction: `{best['direction']}`",
        f"  - Win rate: {_safe_float(best['win_rate']):.1%}  PF: {_safe_float(best['profit_factor']):.2f}  "
        f"Robustness: {_safe_float(best['robustness_score']):.2f}  Status: `{best['status']}`",
        f"  - **Action:** Validate on additional symbols/windows before considering Engine changes.",
    ]
    return "\n".join(lines)


# ─── Run metadata writer ─────────────────────────────────────────────────────

def write_run_meta(run_dir: Path, run_id: str, run_meta: dict) -> None:
    meta_path = run_dir / "run_meta.json"
    data = {
        "schema_version": RUN_META_SCHEMA_VERSION,
        "run_id": run_id,
        **run_meta,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ─── Full report pipeline ─────────────────────────────────────────────────────

def generate_all_reports(
    results: list[StrategyMetrics],
    base_dir: str | Path,
    run_id: str,
    run_meta: Optional[dict] = None,
) -> Path:
    """Write all CSVs + markdown report and return run_dir."""
    run_dir = make_run_dir(base_dir, run_id)
    run_meta = run_meta or {}

    try:
        df = metrics_to_df(results)
    except Exception as e:
        log.error("[reporting] metrics_to_df failed: %s", e, exc_info=True)
        df = pd.DataFrame(columns=OUTPUT_COLUMNS)

    errors: list[str] = []

    # Checkpoint summary before heavier enrichment / markdown (large runs).
    try:
        write_research_summary_csv(df, run_dir)
    except Exception as e:
        log.error("[reporting] write_research_summary_csv failed: %s", e, exc_info=True)
        errors.append(f"write_research_summary_csv: {e}")

    try:
        write_csvs(df, run_dir)
    except Exception as e:
        log.error("[reporting] write_csvs failed: %s", e)
        errors.append(f"write_csvs: {e}")

    try:
        write_markdown_report(df, run_dir, run_id, run_meta)
    except Exception as e:
        log.error("[reporting] write_markdown_report failed: %s", e)
        errors.append(f"write_markdown_report: {e}")

    if run_meta:
        try:
            write_run_meta(run_dir, run_id, run_meta)
        except Exception as e:
            log.error("[reporting] write_run_meta failed: %s", e)
            errors.append(f"write_run_meta: {e}")

    # Sentinel file: written last so routes can confirm all reports exist
    status_path = run_dir / "status.json"
    status_data = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results_count": len(results),
        "files_ok": len(errors) == 0,
        "errors": errors,
    }
    try:
        df = _prepare_report_frame(df)
        warnings = df.get("simulation_warning", pd.Series("", index=df.index)).fillna("").astype(str)
        backends = df.get("simulation_backend", pd.Series("", index=df.index)).fillna("").astype(str)
        readiness = df.get("implementation_verdict", pd.Series("", index=df.index)).fillna("").astype(str)
        status_data["self_audit"] = {
            "simulation_warning_rows": int((warnings != "").sum()),
            "pandas_fallback_rows": int((backends == "pandas_fallback").sum()),
        }
        status_data["implementation_readiness"] = {
            "ready_rows": int((readiness == "IMPLEMENTATION_READY").sum()),
            "blocked_rows": int((readiness != "IMPLEMENTATION_READY").sum()),
            "run_verdict": (
                "IMPLEMENTATION_READY"
                if int((readiness == "IMPLEMENTATION_READY").sum()) > 0
                else "NOT_IMPLEMENTABLE"
            ),
            "scope": "PAPER_TOOL_ONLY",
        }
    except Exception:
        status_data["self_audit"] = {
            "simulation_warning_rows": 0,
            "pandas_fallback_rows": 0,
        }
        status_data["implementation_readiness"] = {
            "ready_rows": 0,
            "blocked_rows": 0,
            "run_verdict": "NOT_IMPLEMENTABLE",
            "scope": "RESEARCH_ONLY",
        }
    try:
        status_path.write_text(json.dumps(status_data, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass

    if errors:
        log.warning("[reporting] %d file(s) failed for %s: %s", len(errors), run_id, errors)
    else:
        log.info("[reporting] All reports written to %s", run_dir)
    return run_dir
