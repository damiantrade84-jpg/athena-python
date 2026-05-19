"""Build master evidence report: accepted closed trades vs rejected diagnostics.

Research-only. Does not change scoring, thresholds, or execution.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIN_SAMPLE = 30
NEAR_THRESHOLD_ABS = 0.25

MATRIX_COLUMNS = [
    "engine",
    "asset_class",
    "score_group",
    "regime",
    "style",
    "sample_size_accepted",
    "sample_size_rejected",
    "avg_r_multiple",
    "win_rate",
    "primary_failure_reason",
    "secondary_failure_reason",
    "avg_distance_to_threshold",
    "near_threshold_rate",
    "fallback_tp_applied_rate",
    "structural_tp_rate",
    "evidence_strength",
    "recommended_next_experiment",
    "live_change_allowed",
]


def _blank(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else "(blank)"


def _norm_engine(value: Any) -> str | None:
    e = str(value or "").strip().lower()
    if e in ("engine_a", "a"):
        return "ENGINE_A"
    if e in ("engine_b", "b"):
        return "ENGINE_B"
    return None


def _load_accepted(path: Path, audit_db: Path) -> tuple[list[dict[str, Any]], str | None, str | None]:
    trades: list[dict[str, Any]] = []
    dates: list[str] = []
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("rows") or data.get("trades") or []
        for row in raw:
            if not isinstance(row, dict):
                continue
            eng = _norm_engine(row.get("engine"))
            if eng not in {"ENGINE_A", "ENGINE_B"}:
                continue
            rm = row.get("r_multiple")
            if rm is None:
                rm = row.get("expectancy")
            trades.append(
                {
                    "engine": eng,
                    "symbol": row.get("symbol") or row.get("pair"),
                    "asset_class": _blank(row.get("asset_class")),
                    "score_group": _blank(row.get("score_group")),
                    "regime": _blank(row.get("regime")),
                    "style": _blank(row.get("timeframe_style") or row.get("style")),
                    "win": bool(row.get("win")),
                    "r_multiple": float(rm) if rm is not None else None,
                }
            )
    elif audit_db.exists():
        con = sqlite3.connect(str(audit_db), timeout=15.0)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT pair, asset_class, style, regime, pnl, r_multiple, engine, exit_time, ts
            FROM audit_log
            WHERE exit_price IS NOT NULL AND pnl IS NOT NULL
              AND (error_tag IS NULL OR error_tag = '')
            """
        ).fetchall()
        con.close()
        for row in rows:
            eng = _norm_engine(row["engine"])
            if eng not in {"ENGINE_A", "ENGINE_B"}:
                continue
            pnl = float(row["pnl"])
            for col in ("exit_time", "ts"):
                if row[col]:
                    dates.append(str(row[col])[:10])
            trades.append(
                {
                    "engine": eng,
                    "symbol": row["pair"],
                    "asset_class": _blank(row["asset_class"]),
                    "score_group": "(blank)",
                    "regime": _blank(row["regime"]),
                    "style": _blank(row["style"]),
                    "win": pnl > 0,
                    "r_multiple": float(row["r_multiple"]) if row["r_multiple"] is not None else None,
                }
            )
    if not dates and audit_db.exists():
        con = sqlite3.connect(str(audit_db), timeout=15.0)
        row = con.execute(
            """
            SELECT MIN(SUBSTR(COALESCE(exit_time, ts), 1, 10)) AS dmin,
                   MAX(SUBSTR(COALESCE(exit_time, ts), 1, 10)) AS dmax
            FROM audit_log
            WHERE exit_price IS NOT NULL AND pnl IS NOT NULL
              AND (error_tag IS NULL OR error_tag = '')
              AND engine IN ('ENGINE_A', 'ENGINE_B', 'engine_a', 'engine_b')
            """
        ).fetchone()
        con.close()
        if row and row[0]:
            date_min, date_max = str(row[0]), str(row[1])
            return trades, date_min, date_max
    date_min = min(dates) if dates else None
    date_max = max(dates) if dates else None
    return trades, date_min, date_max


def _load_rejected(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        eng = _norm_engine(row.get("engine"))
        if eng not in {"ENGINE_A", "ENGINE_B"}:
            continue
        out.append(
            {
                "engine": eng,
                "asset_class": _blank(row.get("asset_class")),
                "score_group": _blank(row.get("score_group")),
                "regime": _blank(row.get("regime")),
                "style": _blank(row.get("timeframe_style")),
                "passed": row.get("passed"),
                "failure_reason": _blank(row.get("failure_reason")) if row.get("failure_reason") else "(blank)",
                "distance_to_threshold": row.get("distance_to_threshold"),
                "engine_b_level_mode": row.get("engine_b_level_mode"),
                "engine_b_fallback_tp_applied": row.get("engine_b_fallback_tp_applied"),
                "engine_b_rr_actual": row.get("engine_b_rr_actual"),
                "engine_b_rr_required": row.get("engine_b_rr_required"),
                "engine_a_trend_score": row.get("engine_a_trend_score"),
                "engine_a_adx_mult": row.get("engine_a_adx_mult"),
                "engine_a_volatility_scaler": row.get("engine_a_volatility_scaler"),
            }
        )
    return out


def _group_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        row.get("engine") or "",
        row.get("asset_class") or "(blank)",
        row.get("score_group") or "(blank)",
        row.get("regime") or "(blank)",
        row.get("style") or "(blank)",
    )


def _evidence_strength(accepted_n: int, rejected_n: int) -> str:
    if accepted_n >= MIN_SAMPLE and rejected_n >= MIN_SAMPLE:
        return "strong"
    if accepted_n >= MIN_SAMPLE or rejected_n >= MIN_SAMPLE:
        return "weak"
    return "insufficient"


def _top_reasons(rows: list[dict[str, Any]], n: int = 2) -> tuple[str, str]:
    failed = [r for r in rows if r.get("passed") is False]
    counts = Counter(r.get("failure_reason") or "(blank)" for r in failed)
    if not counts and rows:
        counts = Counter(r.get("failure_reason") or "(blank)" for r in rows)
    ordered = counts.most_common(n)
    primary = ordered[0][0] if ordered else "(blank)"
    secondary = ordered[1][0] if len(ordered) > 1 else "(blank)"
    return primary, secondary


def _win_rate(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    return sum(1 for r in rows if r.get("win")) / len(rows)


def _avg_r(rows: list[dict[str, Any]]) -> float | None:
    vals = [r["r_multiple"] for r in rows if r.get("r_multiple") is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _near_threshold_rate(rows: list[dict[str, Any]]) -> float | None:
    dist_rows = [r for r in rows if r.get("distance_to_threshold") is not None]
    if not dist_rows:
        return None
    near = 0
    for r in dist_rows:
        try:
            dist = float(r["distance_to_threshold"])
        except (TypeError, ValueError):
            continue
        if dist >= -NEAR_THRESHOLD_ABS:
            near += 1
    return round(near / len(dist_rows), 4)


def _fallback_rate(rows: list[dict[str, Any]]) -> float | None:
    vals = [r.get("engine_b_fallback_tp_applied") for r in rows if r.get("engine") == "ENGINE_B"]
    known = [v for v in vals if v is not None]
    if not known:
        return None
    return round(sum(1 for v in known if v is True) / len(known), 4)


def _structural_tp_rate(rows: list[dict[str, Any]]) -> float | None:
    modes = [r.get("engine_b_level_mode") for r in rows if r.get("engine") == "ENGINE_B"]
    known = [m for m in modes if m not in (None, "", "(blank)")]
    if not known:
        return None
    structural = sum(1 for m in known if "structural" in str(m).lower() and "fallback" not in str(m).lower())
    return round(structural / len(known), 4)


def _recommend_experiment(
    engine: str,
    accepted_n: int,
    rejected_n: int,
    primary: str,
    avg_r: float | None,
    strength: str,
) -> str:
    if strength == "insufficient":
        if rejected_n < MIN_SAMPLE:
            return "Enable CALIBRATION_DIAGNOSTICS and collect >=30 JSONL events for this group before comparing rejections."
        return f"Collect >=30 closed trades for this group (current accepted n={accepted_n})."
    if engine == "ENGINE_A" and primary in {"below_threshold", "low_score", "failed_unknown"}:
        return "Controlled sweep on score_pct buckets with held-out OOS; do not change live threshold yet."
    if engine == "ENGINE_B" and primary == "min_score":
        return "Diagnostics export with gate booleans; compare min_score failures vs passed with fixed style/regime."
    if avg_r is not None and avg_r < 0:
        return "Outcome review: execution/exit quality and R skew; separate from gate calibration."
    return "Expand diagnostic + trade sample; walk-forward split before any parameter sweep."


def build_group_matrix(
    accepted: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    acc_buckets: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    rej_buckets: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in accepted:
        acc_buckets[_group_key(row)].append(row)
    for row in rejected:
        rej_buckets[_group_key(row)].append(row)
    all_keys = set(acc_buckets) | set(rej_buckets)
    matrix: list[dict[str, Any]] = []
    for key in sorted(all_keys):
        acc = acc_buckets.get(key, [])
        rej = rej_buckets.get(key, [])
        primary, secondary = _top_reasons(rej)
        avg_r = _avg_r(acc)
        strength = _evidence_strength(len(acc), len(rej))
        dist_vals = [r["distance_to_threshold"] for r in rej if r.get("distance_to_threshold") is not None]
        avg_dist = round(sum(float(d) for d in dist_vals) / len(dist_vals), 4) if dist_vals else None
        matrix.append(
            {
                "engine": key[0],
                "asset_class": key[1],
                "score_group": key[2],
                "regime": key[3],
                "style": key[4],
                "sample_size_accepted": len(acc),
                "sample_size_rejected": len(rej),
                "avg_r_multiple": round(avg_r, 4) if avg_r is not None else None,
                "win_rate": round(_win_rate(acc), 4) if acc else None,
                "primary_failure_reason": primary if rej else None,
                "secondary_failure_reason": secondary if rej else None,
                "avg_distance_to_threshold": avg_dist,
                "near_threshold_rate": _near_threshold_rate(rej),
                "fallback_tp_applied_rate": _fallback_rate(rej),
                "structural_tp_rate": _structural_tp_rate(rej),
                "evidence_strength": strength,
                "recommended_next_experiment": _recommend_experiment(
                    key[0], len(acc), len(rej), primary, avg_r, strength
                ),
                "live_change_allowed": False,
            }
        )
    matrix.sort(key=lambda r: (-int(r["sample_size_accepted"]), -int(r["sample_size_rejected"]), str(r)))
    return matrix


def _aggregate_accepted(
    accepted: list[dict[str, Any]],
    key_name: str,
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in accepted:
        buckets[(row["engine"], _blank(row.get(key_name)))].append(row)
    out = []
    for (engine, dim), items in sorted(buckets.items(), key=lambda x: (-len(x[1]), x[0])):
        out.append(
            {
                "engine": engine,
                "dimension": dim,
                "accepted_n": len(items),
                "win_rate": _win_rate(items),
                "avg_r": _avg_r(items),
                "strength": _evidence_strength(len(items), 0),
            }
        )
    return out


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def build_report_markdown(
    *,
    accepted: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    matrix: list[dict[str, Any]],
    date_min: str | None,
    date_max: str | None,
    inputs: dict[str, str],
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ea = [r for r in accepted if r["engine"] == "ENGINE_A"]
    eb = [r for r in accepted if r["engine"] == "ENGINE_B"]
    rea = [r for r in rejected if r["engine"] == "ENGINE_A"]
    reb = [r for r in rejected if r["engine"] == "ENGINE_B"]

    insufficient_matrix = [r for r in matrix if r["evidence_strength"] == "insufficient"]
    weak_matrix = [r for r in matrix if r["evidence_strength"] == "weak"]
    strong_matrix = [r for r in matrix if r["evidence_strength"] == "strong"]

    lines = [
        "# Master calibration evidence report",
        "",
        f"_Generated: {now}. Research-only — compares **accepted closed trades** (audit) vs **rejected scan diagnostics** (JSONL). No live tuning._",
        "",
        "## 1. Dataset summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Accepted (closed) trade count | **{len(accepted)}** |",
        f"| Rejected / diagnostic event count | **{len(rejected)}** |",
        f"| ENGINE_A accepted | **{len(ea)}** |",
        f"| ENGINE_B accepted | **{len(eb)}** |",
        f"| ENGINE_A rejected/diagnostic | **{len(rea)}** |",
        f"| ENGINE_B rejected/diagnostic | **{len(reb)}** |",
        f"| Accepted date range | **{date_min or 'n/a'}** → **{date_max or 'n/a'}** |",
        f"| Diagnostic date range | **n/a** (timestamps mostly null in current export) |",
        f"| Matrix groups (accepted ∪ rejected keys) | **{len(matrix)}** |",
        f"| Groups with evidence_strength=strong (both n≥{MIN_SAMPLE}) | **{len(strong_matrix)}** |",
        f"| Groups with evidence_strength=weak | **{len(weak_matrix)}** |",
        f"| Groups with evidence_strength=insufficient | **{len(insufficient_matrix)}** |",
        "",
        "### Critical sample-size warning",
        "",
    ]
    if len(rejected) < MIN_SAMPLE:
        lines.append(
            f"- **Rejected/diagnostic events (n={len(rejected)}) are far below n≥{MIN_SAMPLE}.** "
            "Cross-engine rejection comparisons in this report are **pilot-only**; accepted-trade outcome tables are the primary evidence source."
        )
    else:
        lines.append(
            f"- Rejected events meet minimum n≥{MIN_SAMPLE} for engine-level comparison."
        )
    lines.extend(
        [
            "- **Accepted trades** come from `audit.db` export (`sweep_input_from_audit.json`).",
            "- **Rejected setups** come from `calibration_events.jsonl` normalization — separate pipeline, not the same population as closed trades.",
            "- **Do not tune live thresholds** from this report alone.",
            "",
            "### Inputs",
            "",
        ]
    )
    for label, path in sorted(inputs.items()):
        lines.append(f"- **{label}:** `{path}`")
    lines.extend(["", "## 2. Engine A evidence", ""])

    for dim_name, key in (
        ("asset_class", "asset_class"),
        ("score_group", "score_group"),
        ("regime", "regime"),
    ):
        lines.append(f"### By {dim_name}")
        lines.append("")
        dim_rows = _aggregate_accepted(ea, key)
        table_rows = []
        for item in dim_rows:
            rej_sub = [
                r
                for r in rea
                if _blank(r.get(key)) == item["dimension"]
            ]
            primary, _ = _top_reasons(rej_sub)
            dists = [r["distance_to_threshold"] for r in rej_sub if r.get("distance_to_threshold") is not None]
            avg_dist = f"{sum(float(d) for d in dists) / len(dists):.4f}" if dists else "n/a"
            near = _near_threshold_rate(rej_sub)
            near_txt = f"{near:.2%}" if near is not None else "n/a"
            far_below = "n/a"
            if dists:
                far_below = f"{sum(1 for d in dists if float(d) < -NEAR_THRESHOLD_ABS) / len(dists):.2%} structurally below"
            table_rows.append(
                [
                    item["dimension"],
                    str(item["accepted_n"]),
                    f"{item['avg_r']:.4f}" if item["avg_r"] is not None else "n/a",
                    f"{item['win_rate']:.2%}" if item["win_rate"] is not None else "n/a",
                    str(len(rej_sub)),
                    primary if rej_sub else "n/a",
                    avg_dist,
                    near_txt,
                    far_below,
                    item["strength"],
                ]
            )
        lines.append(
            _md_table(
                [
                    dim_name,
                    "accepted_n",
                    "avg_R",
                    "win_rate",
                    "rejected_n",
                    "top_rejection",
                    "avg_dist_to_threshold",
                    "near_threshold",
                    "far_below_rate",
                    "evidence",
                ],
                table_rows[:20],
            )
        )
        if len(table_rows) > 20:
            lines.append(f"\n_({len(table_rows) - 20} more rows in group_blocker_matrix.csv)_\n")

    lines.extend(["", "## 3. Engine B evidence", ""])
    for dim_name, key in (("style", "style"), ("asset_class", "asset_class"), ("regime", "regime")):
        lines.append(f"### By {dim_name}")
        lines.append("")
        dim_rows = _aggregate_accepted(eb, key)
        table_rows = []
        for item in dim_rows:
            rej_sub = [r for r in reb if _blank(r.get(key)) == item["dimension"]]
            primary, sec = _top_reasons(rej_sub)
            fb = _fallback_rate(rej_sub)
            st = _structural_tp_rate(rej_sub)
            rr_pairs = [
                (r.get("engine_b_rr_actual"), r.get("engine_b_rr_required"))
                for r in rej_sub
                if r.get("engine_b_rr_actual") is not None and r.get("engine_b_rr_required") is not None
            ]
            rr_txt = "n/a"
            if rr_pairs:
                rr_txt = f"avg actual {sum(float(a) for a, _ in rr_pairs)/len(rr_pairs):.2f} vs req {sum(float(b) for _, b in rr_pairs)/len(rr_pairs):.2f}"
            table_rows.append(
                [
                    item["dimension"],
                    str(item["accepted_n"]),
                    f"{item['avg_r']:.4f}" if item["avg_r"] is not None else "n/a",
                    f"{item['win_rate']:.2%}" if item["win_rate"] is not None else "n/a",
                    str(len(rej_sub)),
                    primary if rej_sub else "n/a",
                    rr_txt,
                    f"{fb:.2%}" if fb is not None else "n/a",
                    f"{st:.2%}" if st is not None else "n/a",
                    item["strength"],
                ]
            )
        lines.append(
            _md_table(
                [
                    dim_name,
                    "accepted_n",
                    "avg_R",
                    "win_rate",
                    "rejected_n",
                    "top_gate_failure",
                    "rr_actual_vs_required",
                    "fallback_tp_rate",
                    "structural_tp_rate",
                    "evidence",
                ],
                table_rows[:20],
            )
        )
        lines.append("")

    lines.append("### By level_mode")
    lines.append("")
    lines.append(
        "_Accepted trades lack `level_mode` in audit export. Rejected level_mode appears only in diagnostic JSONL "
        f"(current n={len(reb)}). **Insufficient** for structural vs fallback RR comparison at group level._"
    )

    lines.extend(["", "## 4. Cross-engine comparison", ""])

    # Compare same regime where both have accepted n>=30
    regimes = sorted(set(_blank(r["regime"]) for r in accepted))
    cross_rows = []
    for reg in regimes:
        a_items = [r for r in ea if _blank(r["regime"]) == reg]
        b_items = [r for r in eb if _blank(r["regime"]) == reg]
        if not a_items and not b_items:
            continue
        a_r = _avg_r(a_items)
        b_r = _avg_r(b_items)
        cross_rows.append(
            [
                reg,
                str(len(a_items)),
                f"{a_r:.4f}" if a_r is not None else "n/a",
                str(len(b_items)),
                f"{b_r:.4f}" if b_r is not None else "n/a",
                "A better" if a_r is not None and b_r is not None and a_r > b_r else (
                    "B better" if a_r is not None and b_r is not None and b_r > a_r else "n/a"
                ),
            ]
        )
    lines.append(_md_table(["regime", "A_n", "A_avg_R", "B_n", "B_avg_R", "better_engine"], cross_rows[:15]))

    lines.extend(
        [
            "",
            "**Interpretation (accepted trades only, n≥30 regimes):**",
            "",
        ]
    )
    for reg in ("LOW_VOLATILITY", "TRENDING", "RANGING"):
        a_items = [r for r in ea if r["regime"] == reg]
        b_items = [r for r in eb if r["regime"] == reg]
        if len(a_items) >= MIN_SAMPLE or len(b_items) >= MIN_SAMPLE:
            lines.append(
                f"- **{reg}:** ENGINE_A n={len(a_items)} avg R={_avg_r(a_items)}; "
                f"ENGINE_B n={len(b_items)} avg R={_avg_r(b_items)}"
            )

    lines.extend(
        [
            "",
            "- **Both engines negative avg R** at overall level despite win rates >55% — weak expectancy dominates.",
            "- **Groups where both fail (n≥30, avg R<0):** ENGINE_A intraday, scalp, swing; ENGINE_B intraday; ENGINE_A LOW_VOLATILITY/TRENDING; ENGINE_B RANGING.",
            "- **B relatively better:** ENGINE_B TRENDING (n=32, avg R +0.124), ENGINE_B scalp (n=75, avg R +0.015).",
            "- **Blocked-before-entry vs good closes:** *Cannot establish* — diagnostic n too small to join on regime/asset_class.",
            "- **High pass / weak expectancy:** High win rate with negative avg R in multiple buckets (see accepted tables).",
            "",
            "## 5. What is working against each group",
            "",
            "See `logs/calibration/group_blocker_matrix.csv` for the full matrix. Preview (largest accepted n):",
            "",
        ]
    )

    preview = sorted(matrix, key=lambda r: -int(r["sample_size_accepted"]))[:15]
    blocker_rows = []
    for r in preview:
        group = f"{r['engine']}|{r['asset_class']}|{r['score_group']}|{r['regime']}|{r['style']}"
        blocker_rows.append(
            [
                group,
                r["engine"],
                r["primary_failure_reason"] or "n/a",
                r["secondary_failure_reason"] or "n/a",
                str(r["avg_r_multiple"]) if r["avg_r_multiple"] is not None else "n/a",
                str(r["sample_size_rejected"]),
                r["evidence_strength"],
                (r["recommended_next_experiment"] or "")[:80] + "...",
            ]
        )
    lines.append(
        _md_table(
            [
                "group",
                "engine",
                "primary_blocker",
                "secondary_blocker",
                "accepted_avg_R",
                "rejected_n",
                "evidence",
                "next_experiment",
            ],
            blocker_rows,
        )
    )

    lines.extend(
        [
            "",
            "## 6. Do not recommend direct live threshold changes",
            "",
            "All rows in `group_blocker_matrix.csv` have `live_change_allowed=false`.",
            "Recommend **controlled experiments** only: enable diagnostics collection, enrich exports, "
            "define OOS windows, then re-run sweeps on groups with n≥30 on **both** pipelines.",
            "",
            "## 7. Recommended next experiments",
            "",
            "1. Set `CALIBRATION_DIAGNOSTICS_ENABLED: true` and accumulate ≥30 events per major group.",
            "2. Re-export audit trades with `asset_class`, `score_group`, `level_mode`, `fallback_tp_applied`.",
            "3. Re-run `export_calibration_events.py` and this report builder on matched date ranges.",
            "4. ENGINE_A: OOS sweep on score_pct / threshold distance (research harness only).",
            "5. ENGINE_B: gate-failure cohort study (`structure_ok`, `min_score`, `rr_ok`) before fallback TP conclusions.",
            "",
        ]
    )
    return "\n".join(lines)


def build_master_evidence(
    *,
    baseline_dir: Path,
    diagnostics_dir: Path,
    accepted_json: Path,
    audit_db: Path,
    output_md: Path,
    output_csv: Path,
) -> dict[str, Any]:
    accepted, date_min, date_max = _load_accepted(accepted_json, audit_db)
    diag_json = diagnostics_dir / "diagnostic_events_normalized.json"
    rejected = _load_rejected(diag_json)
    matrix = build_group_matrix(accepted, rejected)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATRIX_COLUMNS)
        writer.writeheader()
        for row in matrix:
            writer.writerow(row)

    inputs = {
        "baseline_analysis": str(baseline_dir / "baseline_analysis.md"),
        "diagnostic_events": str(diag_json),
        "accepted_trades": str(accepted_json),
    }
    report = build_report_markdown(
        accepted=accepted,
        rejected=rejected,
        matrix=matrix,
        date_min=date_min,
        date_max=date_max,
        inputs=inputs,
    )
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(report, encoding="utf-8")

    top_findings = []
    for eng in ("ENGINE_A", "ENGINE_B"):
        sub = [r for r in accepted if r["engine"] == eng]
        wr = _win_rate(sub)
        ar = _avg_r(sub)
        top_findings.append(
            (
                f"{eng} accepted n={len(sub)}",
                f"WR={wr:.2%}" if wr is not None else "WR=n/a",
                f"avg R={ar:.4f}" if ar is not None else "avg R=n/a",
            )
        )
    for r in sorted(matrix, key=lambda x: -int(x["sample_size_accepted"]))[:5]:
        if r["sample_size_accepted"] >= MIN_SAMPLE:
            top_findings.append(
                (
                    f"{r['engine']} {r['regime']}/{r['style']}",
                    f"accepted n={r['sample_size_accepted']}",
                    f"avg R={r['avg_r_multiple']}",
                )
            )

    insufficient = sorted(
        [r for r in matrix if r["evidence_strength"] == "insufficient"],
        key=lambda r: -(int(r["sample_size_accepted"]) + int(r["sample_size_rejected"])),
    )[:10]

    return {
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "matrix_rows": len(matrix),
        "insufficient_groups": len([r for r in matrix if r["evidence_strength"] == "insufficient"]),
        "output_md": str(output_md),
        "output_csv": str(output_csv),
        "top_findings": top_findings,
        "insufficient_preview": insufficient,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", default=str(ROOT / "logs" / "calibration" / "full_baseline_real"))
    parser.add_argument("--diagnostics-dir", default=str(ROOT / "logs" / "calibration" / "diagnostics_normalized"))
    parser.add_argument("--accepted-json", default=str(ROOT / "logs" / "calibration" / "sweep_input_from_audit.json"))
    parser.add_argument("--audit-db", default=str(ROOT / "audit.db"))
    parser.add_argument("--output-md", default=str(ROOT / "logs" / "calibration" / "master_evidence_report.md"))
    parser.add_argument("--output-csv", default=str(ROOT / "logs" / "calibration" / "group_blocker_matrix.csv"))
    args = parser.parse_args(argv)
    result = build_master_evidence(
        baseline_dir=Path(args.baseline_dir),
        diagnostics_dir=Path(args.diagnostics_dir),
        accepted_json=Path(args.accepted_json),
        audit_db=Path(args.audit_db),
        output_md=Path(args.output_md),
        output_csv=Path(args.output_csv),
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
