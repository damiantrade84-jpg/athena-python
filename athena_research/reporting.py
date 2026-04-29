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

OUTPUT_COLUMNS = [
    "run_id", "symbol", "asset_class", "timeframe", "zone", "family", "strategy_name",
    "params_str", "direction", "session", "status",
    "trade_count", "win_rate", "profit_factor", "avg_return", "expectancy",
    "max_drawdown", "sharpe", "sqn", "exposure_pct", "avg_duration_bars",
    "gross_return", "net_return", "is_return", "oos_return",
    "robustness_score", "param_sensitivity", "skip_reason", "data_source",
    "engine", "engine_component", "candidate_action", "source_indicator",
    "market_group", "pair_group", "timeframe_zone", "session_bucket",
    "structure_context", "baseline_delta_pf", "baseline_delta_oos",
    "sample_ok", "recommendation",
]


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


def _safe_float(v) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else float("nan")
    except Exception:
        return float("nan")


# ─── CSV writers ──────────────────────────────────────────────────────────────

def write_csvs(df: pd.DataFrame, run_dir: Path) -> list[Path]:
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

    if "market_group" in df.columns:
        _save(_group_context_breakdown(df), "group_breakdown.csv")

    if "structure_context" in df.columns:
        _save(_structure_context_breakdown(df), "structure_context_breakdown.csv")

    if "recommendation" in df.columns:
        _save(_automated_next_tests(df), "automated_next_tests.csv")
        _save(_recommendation_table(df), "add_remove_retest_recommendations.csv")

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
    weak = df[df["recommendation"].isin(["REMOVE_OR_DEMOTE", "REJECT", "RETEST", "WATCHLIST_ONLY"])].copy()
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

    lines: list[str] = []
    a = lines.append

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    a(f"# Athena Research Lab — Report")
    a(f"**Run ID:** `{run_id}`  **Generated:** {ts}  ")
    a(f"**Mode:** {run_meta.get('mode', 'research')}  **Symbols:** {run_meta.get('symbol_count', '?')}  "
      f"**Families:** {run_meta.get('families', '?')}")
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
    a("## Engine D Findings")
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
    data = {"run_id": run_id, **run_meta,
            "generated_at": datetime.now(timezone.utc).isoformat()}
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
        status_path.write_text(json.dumps(status_data, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass

    if errors:
        log.warning("[reporting] %d file(s) failed for %s: %s", len(errors), run_id, errors)
    else:
        log.info("[reporting] All reports written to %s", run_dir)
    return run_dir
