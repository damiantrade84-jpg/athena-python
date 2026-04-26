"""
Athena Research Lab — AI Analyst Prompt Builder
Constructs the prompt sent to the AI analyst from aggregated CSV data.
No live imports. No config writes.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import pandas as pd


# ─── Safety preamble (injected into every prompt) ────────────────────────────

_SAFETY_PREAMBLE = """
You are a quantitative research analyst reviewing backtest discovery results for Athena Pro v4,
a multi-engine algorithmic trading system.

IMPORTANT SAFETY RULES — YOU MUST FOLLOW THESE:
1. Never recommend direct live execution from backtest discovery alone.
2. Never suggest copying BT_MIN / backtest thresholds into live engine gates.
3. Always label each finding with exactly one of: STRONG_CANDIDATE | WEAK_CANDIDATE | REJECT | NEEDS_MORE_DATA | TELEMETRY_BUG
4. Penalise tiny samples (< 30 trades).
5. Penalise strategies that only work on one symbol.
6. Penalise strategies that are gross-profitable but net-negative after fees.
7. Penalise strategies that work IS but fail OOS.
8. Prefer robust clusters (work across multiple symbols/timeframes) over one-off winners.
9. Separate Engine A, Engine B, and Engine D recommendations.
10. Mention explicitly if a result is not trustworthy due to missing data or telemetry.
""".strip()

_PROJECT_CONTEXT = """
## Project Context

Athena Pro v4 has four engines:

- **Engine A** — 3-factor quantitative scoring: EMA trend coherence (D1/H4/H1), RSI+MACD momentum quality, ADX gate.
  Unified 0–3.0 scale. Current live forex floor: MIN_CONFLUENCE_CLASS.forex = 2.1.
  **Do NOT recommend changing live thresholds from backtest findings.**

- **Engine B** — Naked price-action (BOS/CHoCH, FVG, OB, swing sequence, location, trigger, room/RR).
  Checklist-based pass/fail. Strict by design.

- **Engine C** — Consensus layer blending Engine A + Engine B + AI Vision.

- **Engine D** — Fabio Valentini VP+OrderFlow scalping (POC/VAH/VAL, Absorption, CVD, VWAP, AAA sequence).
  Crypto-focused. Grade A/B/C/D.

## Backtest Discovery Context

- BT_MIN / discovery thresholds are intentionally LOWER than live thresholds.
- Backtest results may appear worse because marginal trades are included.
- IS = In-Sample (first 70% of data), OOS = Out-of-Sample (last 30%).
- Robustness score: 0–1 combining sample adequacy, IS/OOS consistency, fee survival.
""".strip()

_PURPOSE = """
## Purpose of This Analysis

Identify which indicators, strategy families, filters, sessions, and symbols actually produce edge —
and which do not — so we can make informed decisions about Engine A, Engine B, and Engine D.

Provide a decision memo with specific, actionable recommendations.
""".strip()


# ─── Data summariser helpers ──────────────────────────────────────────────────

def _safe_head(df: pd.DataFrame, n: int = 15) -> str:
    if df.empty:
        return "*No data*"
    df = df.head(n).copy()
    for col in df.select_dtypes("float").columns:
        df[col] = df[col].map(lambda x: f"{x:.4f}" if (isinstance(x, float) and math.isfinite(x)) else str(x))
    return df.to_string(index=False)


def _read_csv_safe(path: Path) -> Optional[pd.DataFrame]:
    try:
        return pd.read_csv(path)
    except Exception:
        return None


def _summarise_csv(path: Path, label: str, head: int = 15) -> str:
    df = _read_csv_safe(path)
    if df is None or df.empty:
        return f"### {label}\n*File not found or empty*\n"
    lines = [f"### {label}", f"Rows: {len(df)}", "```"]
    lines.append(_safe_head(df, head))
    lines.append("```")
    return "\n".join(lines)


# ─── Public builder ────────────────────────────────────────────────────────────

def build_prompt(run_dir: Path) -> str:
    """
    Reads all aggregate CSV files from *run_dir* and builds the AI analyst prompt.
    Returns the full prompt string.
    """
    sections = [_SAFETY_PREAMBLE, "", _PROJECT_CONTEXT, "", _PURPOSE, ""]

    sections.append("## Research Results\n")

    # Research summary (top rows only)
    sections.append(_summarise_csv(run_dir / "research_summary.csv", "All Results Summary", head=20))
    sections.append("")

    # Ranked strategies
    sections.append(_summarise_csv(run_dir / "ranked_strategies.csv", "Top Ranked Strategies", head=20))
    sections.append("")

    # By asset group
    sections.append(_summarise_csv(run_dir / "by_asset_group.csv", "By Asset Group"))
    sections.append("")

    # By symbol
    sections.append(_summarise_csv(run_dir / "by_symbol.csv", "By Symbol", head=15))
    sections.append("")

    # By timeframe
    sections.append(_summarise_csv(run_dir / "by_timeframe.csv", "By Timeframe"))
    sections.append("")

    # By session
    sections.append(_summarise_csv(run_dir / "by_session.csv", "By Session"))
    sections.append("")

    # By direction
    sections.append(_summarise_csv(run_dir / "by_direction.csv", "By Direction"))
    sections.append("")

    # Indicator attribution
    sections.append(_summarise_csv(run_dir / "indicator_attribution.csv", "Indicator Attribution", head=20))
    sections.append("")

    # Rejected configs summary
    rej_df = _read_csv_safe(run_dir / "rejected_or_failed_configs.csv")
    if rej_df is not None and not rej_df.empty:
        rej_fam = rej_df.groupby("family").size().reset_index(name="count") if "family" in rej_df.columns else rej_df
        sections.append("### Rejected / Failed Configs (by family)")
        sections.append("```")
        sections.append(rej_fam.to_string(index=False))
        sections.append("```\n")

    # Markdown report (trimmed)
    report_path = run_dir / "research_report.md"
    if report_path.exists():
        txt = report_path.read_text(encoding="utf-8")
        sections.append("### research_report.md (excerpt)\n```")
        sections.append(txt[:3000])
        if len(txt) > 3000:
            sections.append("... [truncated]")
        sections.append("```\n")

    # ── Required output specification ────────────────────────────────────────
    sections.append("""
## Required Output

Provide a complete decision memo answering ALL of the following questions.
Use the label tags defined in the safety rules.

1. What strategy family worked best? (label each STRONG_CANDIDATE / WEAK_CANDIDATE / REJECT)
2. What indicator helped most?
3. What indicator hurt most?
4. Which asset group worked best?
5. Which symbol worked best?
6. Which timeframe worked best?
7. Which session worked best?
8. Did LONG or SHORT work better overall?
9. Which setups collapsed after fees? (list by family/strategy)
10. Which setups had too little sample size? (list by family/strategy)
11. What should Engine A keep / remove / tune? (be specific — factor names, threshold direction)
12. What should Engine B keep / remove / tune? (be specific — checklist gates, zone logic)
13. What should Engine D keep / remove / tune? (be specific — VP params, grade thresholds)
14. What is the next smallest useful test to run?
15. What should NOT be tested further right now?

After the memo, output a JSON block labelled ```json with this structure:
{
  "overall_verdict": "...",
  "top_candidates": [{"strategy": "...", "symbol": "...", "tf": "...", "label": "STRONG_CANDIDATE"}],
  "rejected_setups": [{"strategy": "...", "reason": "..."}],
  "engine_a": {"keep": [], "remove_or_demote": [], "tune": [], "next_tests": []},
  "engine_b": {"keep": [], "remove_or_demote": [], "tune": [], "next_tests": []},
  "engine_d": {"keep": [], "remove_or_demote": [], "tune": [], "next_tests": []},
  "data_quality_warnings": [],
  "telemetry_warnings": [],
  "next_tiny_test": {"symbols": [], "timeframes": [], "strategy_families": [], "reason": "..."},
  "do_not_do_next": []
}
""".strip())

    return "\n".join(sections)
