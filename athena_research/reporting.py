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
    "run_id", "symbol", "asset_class", "timeframe", "family", "strategy_name",
    "params_str", "direction", "session", "status",
    "trade_count", "win_rate", "profit_factor", "avg_return", "expectancy",
    "max_drawdown", "sharpe", "sqn", "exposure_pct", "avg_duration_bars",
    "gross_return", "net_return", "is_return", "oos_return",
    "robustness_score", "param_sensitivity", "skip_reason",
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
    rows = [r.to_dict() for r in results]
    df = pd.DataFrame(rows)
    # Ensure all expected columns exist
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

    # Indicator attribution (aggregated by strategy_name)
    _save(_indicator_attribution(df), "indicator_attribution.csv")

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
    ).reset_index()
    agg["pass_rate"] = (agg["strong"] + agg["weak"]) / agg["total_configs"].replace(0, 1)
    agg["verdict"] = agg.apply(
        lambda r: "HELPS" if r["pass_rate"] >= 0.3 and _safe_float(r["avg_net_return"]) > 0
        else ("HURTS" if _safe_float(r["avg_net_return"]) < 0 else "NEUTRAL"), axis=1
    )
    return agg.sort_values("avg_robustness", ascending=False)


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
        a(_df_to_md(helps))
        a("\n**Harmful indicators/strategies:**")
        a(_df_to_md(hurts))
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
        a(f"Focus on: `{best['family']}` / `{best['strategy_name']}` on `{best['symbol']}` `{best['timeframe']}`.")
        a(f"Robustness: {best['robustness_score']:.2f}, Net return: {best['net_return']:.3f}, "
          f"Trade count: {int(best['trade_count'])}")
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

    df = metrics_to_df(results)
    write_csvs(df, run_dir)
    write_markdown_report(df, run_dir, run_id, run_meta)
    if run_meta:
        write_run_meta(run_dir, run_id, run_meta)

    log.info("[reporting] All reports written to %s", run_dir)
    return run_dir
