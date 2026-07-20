"""Report aggregation and output for TF/entry path audit (Parts 7, 10)."""

from __future__ import annotations

import csv
from collections import defaultdict
from math import sqrt
from pathlib import Path
from statistics import fmean, median, stdev
from typing import Any


def _safe_median(values: list[float]) -> float | None:
    return round(median(values), 4) if values else None


def _sqn(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    sd = stdev(values)
    if sd <= 0:
        return 0.0
    return round(fmean(values) / sd * sqrt(len(values)), 4)


def _profit_factor(values: list[float]) -> float | None:
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v <= 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None
    return round(sum(wins) / gl, 4)


def aggregate_realism_metrics(trades: list[dict]) -> dict[str, Any]:
    """Part 7 static SL/TP realism report for a cohort."""
    if not trades:
        return {"trade_count": 0}

    rs = [float(t.get("final_r") or 0) for t in trades]
    mfes = [float(t.get("path_mfe_r") or t.get("max_favorable_excursion_r") or 0) for t in trades]
    maes = [abs(float(t.get("path_mae_r") or t.get("max_adverse_excursion_r") or 0)) for t in trades]
    n = len(trades)
    wins = [r for r in rs if r > 0]

    sl_hits = sum(1 for t in trades if str(t.get("outcome", "")).upper() in ("SL", "BE", "TP1_THEN_SL"))
    tp_hits = sum(1 for t in trades if str(t.get("outcome", "")).upper() in ("TP1", "TP2", "TP1_TP2"))
    pos_mfe_neg_r = sum(1 for t, r, m in zip(trades, rs, mfes) if m > 0 and r < 0)
    tp_beyond_mfe = sum(
        1
        for t in trades
        if t.get("path_entry_to_target_distance") and t.get("path_mfe_r") is not None
        and float(t["path_entry_to_target_distance"]) > 0
        and float(t.get("path_mfe_r") or 0) * float(t.get("path_entry_to_stop_distance") or 1)
        < float(t["path_entry_to_target_distance"])
    )

    early = sum(1 for t in trades if t.get("entry_quality_class") == "EARLY_ADVERSE")
    clean = sum(1 for t in trades if t.get("entry_quality_class") == "CLEAN_ENTRY")
    ambiguous = sum(1 for t in trades if t.get("intrabar_exit_class") == "BOTH_TOUCHED_SAME_CANDLE")

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "trade_count": n,
        "win_rate": round(len(wins) / n * 100, 2),
        "avg_r": round(fmean(rs), 4),
        "median_r": _safe_median(rs),
        "sqn": _sqn(rs),
        "profit_factor": _profit_factor(rs),
        "max_drawdown_r": round(max_dd, 4),
        "sl_hit_rate": round(sl_hits / n * 100, 2),
        "tp_hit_rate": round(tp_hits / n * 100, 2),
        "be_touch_rate": round(sum(1 for t in trades if t.get("path_touched_break_even")) / n * 100, 2),
        "plus_0_25r_touch_rate": round(sum(1 for t in trades if t.get("path_touched_plus_0_25r")) / n * 100, 2),
        "plus_0_5r_touch_rate": round(sum(1 for t in trades if t.get("path_touched_plus_0_5r")) / n * 100, 2),
        "plus_1_0r_touch_rate": round(sum(1 for t in trades if t.get("path_touched_plus_1_0r")) / n * 100, 2),
        "avg_mfe": round(fmean(mfes), 4),
        "median_mfe": _safe_median(mfes),
        "avg_mae": round(fmean(maes), 4),
        "median_mae": _safe_median(maes),
        "pct_pos_mfe_neg_final_r": round(pos_mfe_neg_r / n * 100, 2),
        "pct_tp_exceeded_mfe": round(tp_beyond_mfe / n * 100, 2),
        "pct_early_adverse": round(early / n * 100, 2),
        "pct_clean_entry": round(clean / n * 100, 2),
        "pct_ambiguous_intrabar": round(ambiguous / n * 100, 2),
    }


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = fieldnames or sorted({k for row in rows for k in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_summary_markdown(
    *,
    engine_a_results: list[dict],
    engine_b_results: list[dict],
    validation_rows: list[dict],
    vectorbt_rows: list[dict],
    parameter_intent_rows: list[dict],
) -> str:
    """Part 10 final verdict markdown."""
    lines = [
        "# TF / Entry Path Audit Summary",
        "",
        "Research diagnostic only. No production changes recommended unless results are",
        "positive, stable, cost-adjusted, low ambiguity, and validated by Athena harness.",
        "",
        "## Direct Answers",
        "",
    ]
    lines.extend(_verdict_answers(engine_a_results, engine_b_results, validation_rows, vectorbt_rows))
    lines.extend([
        "",
        "## Parameter Intent Map (excerpt)",
        "",
        "| Engine | Parameter | Intent |",
        "|--------|-----------|--------|",
    ])
    for row in parameter_intent_rows[:12]:
        lines.append(f"| {row['engine']} | {row['parameter']} | {row['intent']} |")
    lines.extend([
        "",
        "## Validation Summary",
        "",
        "| Engine | Symbol | Signal TF | Entry TF | Profile | Trades | Avg R | SQN | Verdict |",
        "|--------|--------|-----------|----------|---------|--------|-------|-----|---------|",
    ])
    for row in validation_rows:
        lines.append(
            f"| {row.get('engine','')} | {row.get('symbol','')} | {row.get('signal_tf','')} | "
            f"{row.get('entry_tf','')} | {row.get('indicator_profile','')} | "
            f"{row.get('trade_count',0)} | {row.get('avg_r',0)} | {row.get('sqn',0)} | "
            f"{row.get('verdict','')} |"
        )
    if vectorbt_rows:
        lines.extend([
            "",
            "## VectorBT Provisional (NOT final)",
            "",
            f"{len(vectorbt_rows)} provisional sweep rows — must be replayed through Athena harness.",
        ])
    return "\n".join(lines) + "\n"


def _verdict_answers(
    engine_a: list[dict],
    engine_b: list[dict],
    validation: list[dict],
    vectorbt: list[dict],
) -> list[str]:
    def _best(rows: list[dict], engine: str) -> dict | None:
        candidates = [r for r in rows if r.get("engine") == engine and r.get("trade_count", 0) >= 10]
        if not candidates:
            return None
        return max(candidates, key=lambda r: (r.get("avg_r", -999), r.get("sqn", -999)))

    a_best = _best(validation, "A")
    b_best = _best(validation, "B")
    b_h4_h1 = next((r for r in validation if r.get("engine") == "B" and r.get("signal_tf") == "H4" and r.get("entry_tf") == "H1"), None)
    b_h1_h1 = next((r for r in validation if r.get("engine") == "B" and r.get("signal_tf") == "H1" and r.get("entry_tf") == "H1"), None)
    a_h4_h1 = next((r for r in validation if r.get("engine") == "A" and r.get("signal_tf") == "H4" and r.get("entry_tf") == "H1"), None)
    a_h4_h4 = next((r for r in validation if r.get("engine") == "A" and r.get("signal_tf") == "H4" and r.get("entry_tf") == "H4"), None)
    a_h1_h1 = next((r for r in validation if r.get("engine") == "A" and r.get("signal_tf") == "H1" and r.get("entry_tf") == "H1"), None)

    early_b = fmean([r.get("pct_early_adverse", 0) for r in engine_b if r.get("trade_count", 0) > 0] or [0])
    early_a = fmean([r.get("pct_early_adverse", 0) for r in engine_a if r.get("trade_count", 0) > 0] or [0])
    tp_far_b = fmean([r.get("pct_tp_exceeded_mfe", 0) for r in engine_b if r.get("trade_count", 0) > 0] or [0])

    answers = [
        f"1. **Bad direction vs bad entry?** Engine B early-adverse rate avg {early_b:.1f}%; "
        f"Engine A {early_a:.1f}%. High early-adverse suggests entry timing dominates over direction.",
        f"2. **Does H4 improve bias?** "
        + (
            f"H4 signal avg R={b_h4_h1.get('avg_r')} vs H1 signal={b_h1_h1.get('avg_r') if b_h1_h1 else 'n/a'} (Engine B)."
            if b_h4_h1
            else "Insufficient H4 vs H1 data."
        ),
        f"3. **Does H1/M15 improve execution after H4 bias?** "
        + (
            f"H4→H1 entry avg R={b_h4_h1.get('avg_r')} (Engine B)."
            if b_h4_h1
            else "H4→H1 combo not validated."
        ),
        f"4. **Static TPs too far?** Engine B avg TP-beyond-MFE rate {tp_far_b:.1f}%.",
        "5. **Wide SL vs poor entry?** High early-adverse + positive MFE-but-negative-R rates "
        "indicate SL being hit after poor entry path, not necessarily SL width alone.",
        f"6. **Engine A TF experiment?** "
        + (
            f"H4→H1 avg R={a_h4_h1.get('avg_r')} vs H1→H1={a_h1_h1.get('avg_r') if a_h1_h1 else 'n/a'}."
            if a_h4_h1 and a_h1_h1
            else (
                f"H1→H1 avg R={a_h1_h1.get('avg_r')} vs H4→H4={a_h4_h4.get('avg_r') if a_h4_h4 else 'n/a'}."
                if a_h1_h1 or a_h4_h4
                else "Engine A separated-TF data pending."
            )
        ),
        f"7. **Engine B TF experiment?** Best validated cell: "
        + (f"{b_best.get('label')} avg R={b_best.get('avg_r')} verdict={b_best.get('verdict')}" if b_best else "none"),
        "8. **Symbols for deeper retuning:** See by-symbol CSV; focus symbols with WATCHLIST verdict and high early-adverse %.",
        "9. **Symbols to block:** Symbols with FAIL verdict, negative avg R, and SQN < 1 across all cells.",
        f"10. **VectorBT provisional:** {len(vectorbt)} rows in vectorbt CSV (if generated).",
        f"11. **Athena harness validated:** {sum(1 for r in validation if r.get('validated_by_athena'))} cohorts.",
    ]
    return answers


def group_metrics(
    trades: list[dict],
    key_fn,
) -> list[dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        buckets[key_fn(t)].append(t)
    rows = []
    for key, bucket in sorted(buckets.items()):
        metrics = aggregate_realism_metrics(bucket)
        metrics["group_key"] = key
        rows.append(metrics)
    return rows
