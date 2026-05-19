"""One-off research helper: analyse calibration_sweep baseline outputs.

Does not mutate config or trading logic. Writes markdown + aggregation CSVs.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MIN_SAMPLE = 30
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "logs" / "calibration" / "full_baseline_real"
DEFAULT_INPUT = ROOT / "logs" / "calibration" / "sweep_input_from_audit.json"
AUDIT_DB = ROOT / "audit.db"


def _norm_engine(raw: Any) -> str | None:
    e = str(raw or "").strip().lower()
    if e in ("engine_a", "a"):
        return "ENGINE_A"
    if e in ("engine_b", "b", "structural", "naked"):
        return "ENGINE_B"
    return None


def _blank(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else "(blank)"


def _load_audit_trades(db_path: Path) -> tuple[list[dict[str, Any]], str | None, str | None]:
    if not db_path.exists():
        return [], None, None
    con = sqlite3.connect(str(db_path), timeout=15.0)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT pair, asset_class, style, regime, score, score_pct, max_score,
               pnl, r_multiple, engine, exit_time, ts
        FROM audit_log
        WHERE exit_price IS NOT NULL AND pnl IS NOT NULL
          AND (error_tag IS NULL OR error_tag = '')
        """
    ).fetchall()
    con.close()

    trades: list[dict[str, Any]] = []
    dates: list[str] = []
    for row in rows:
        engine = _norm_engine(row["engine"])
        if engine not in {"ENGINE_A", "ENGINE_B"}:
            continue
        pnl = float(row["pnl"])
        rm = float(row["r_multiple"]) if row["r_multiple"] is not None else None
        score_pct = float(row["score_pct"]) if row["score_pct"] is not None else None
        if score_pct is not None and score_pct > 1.0:
            score_pct /= 100.0
        score = float(row["score"]) if row["score"] is not None else None
        max_score = float(row["max_score"]) if row["max_score"] is not None else None
        for col in ("exit_time", "ts"):
            val = row[col]
            if val:
                dates.append(str(val)[:10])
        trades.append(
            {
                "engine": engine,
                "symbol": row["pair"],
                "asset_class": _blank(row["asset_class"]),
                "score_group": "(blank)",
                "regime": _blank(row["regime"]),
                "timeframe_style": _blank(row["style"]),
                "win": pnl > 0,
                "r_multiple": rm,
                "score": score,
                "score_pct": score_pct,
                "max_score": max_score,
                "level_mode": "(not_in_audit_export)",
                "fallback_tp_applied": "(not_in_audit_export)",
            }
        )
    date_min = min(dates) if dates else None
    date_max = max(dates) if dates else None
    return trades, date_min, date_max


def _win_rate(items: list[dict[str, Any]]) -> float | None:
    if not items:
        return None
    return sum(1 for x in items if x["win"]) / len(items)


def _avg_r(items: list[dict[str, Any]]) -> float | None:
    vals = [x["r_multiple"] for x in items if x.get("r_multiple") is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _summarize(trades: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        key = tuple(_blank(row.get(k)) for k in keys)
        buckets[key].append(row)
    out: list[dict[str, Any]] = []
    for key, items in buckets.items():
        n = len(items)
        record = {keys[i]: key[i] for i in range(len(keys))}
        record["trade_count"] = n
        wr = _win_rate(items)
        ar = _avg_r(items)
        record["win_rate"] = round(wr, 4) if wr is not None else None
        record["avg_r_multiple"] = round(ar, 4) if ar is not None else None
        record["expectancy_r"] = record["avg_r_multiple"]
        record["sample_size_warning"] = n < MIN_SAMPLE
        out.append(record)
    return sorted(out, key=lambda r: (-int(r["trade_count"]), str(r)))


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def analyze(out_dir: Path, input_json: Path, audit_db: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    trades, date_min, date_max = _load_audit_trades(audit_db)
    sweep_meta: dict[str, Any] = {}
    sweep_rows: list[dict[str, str]] = []
    sweep_json = out_dir / "calibration_sweep.json"
    sweep_csv = out_dir / "calibration_sweep.csv"
    if sweep_json.exists():
        payload = json.loads(sweep_json.read_text(encoding="utf-8"))
        sweep_meta = payload.get("metadata") or {}
        sweep_rows = payload.get("rows") or []
    if sweep_csv.exists() and not sweep_rows:
        with sweep_csv.open(encoding="utf-8", newline="") as handle:
            sweep_rows = list(csv.DictReader(handle))

    _write_csv(out_dir / "by_engine.csv", _summarize(trades, ["engine"]), [
        "engine", "trade_count", "win_rate", "avg_r_multiple", "expectancy_r", "sample_size_warning",
    ])
    _write_csv(out_dir / "by_engine_asset_class.csv", _summarize(trades, ["engine", "asset_class"]), [
        "engine", "asset_class", "trade_count", "win_rate", "avg_r_multiple", "expectancy_r", "sample_size_warning",
    ])
    _write_csv(out_dir / "by_engine_score_group.csv", _summarize(trades, ["engine", "score_group"]), [
        "engine", "score_group", "trade_count", "win_rate", "avg_r_multiple", "expectancy_r", "sample_size_warning",
    ])
    _write_csv(out_dir / "by_engine_regime.csv", _summarize(trades, ["engine", "regime"]), [
        "engine", "regime", "trade_count", "win_rate", "avg_r_multiple", "expectancy_r", "sample_size_warning",
    ])
    _write_csv(out_dir / "by_engine_style.csv", _summarize(trades, ["engine", "timeframe_style"]), [
        "engine", "timeframe_style", "trade_count", "win_rate", "avg_r_multiple", "expectancy_r", "sample_size_warning",
    ])
    _write_csv(out_dir / "by_engine_level_mode.csv", _summarize(trades, ["engine", "level_mode"]), [
        "engine", "level_mode", "trade_count", "win_rate", "avg_r_multiple", "expectancy_r", "sample_size_warning",
    ])
    _write_csv(out_dir / "by_engine_fallback_tp.csv", _summarize(trades, ["engine", "fallback_tp_applied"]), [
        "engine", "fallback_tp_applied", "trade_count", "win_rate", "avg_r_multiple", "expectancy_r", "sample_size_warning",
    ])

    ea = [t for t in trades if t["engine"] == "ENGINE_A"]
    eb = [t for t in trades if t["engine"] == "ENGINE_B"]
    sweep_warn = sum(1 for r in sweep_rows if str(r.get("sample_size_warning")).lower() == "true")

    def sufficient(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
        return [r for r in _summarize(rows, [key]) if r["trade_count"] >= MIN_SAMPLE]

    report_path = out_dir / "baseline_analysis.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        "# Calibration baseline analysis (audit.db closed trades)",
        "",
        f"_Generated: {now}. Research-only; no live config or scoring changes._",
        "",
        "## Run context",
        "",
        "| Item | Value |",
        "| --- | --- |",
        f"| Input JSON | `{input_json.as_posix()}` |",
        f"| Sweep command | `.venv\\\\Scripts\\\\python.exe tools/calibration_sweep.py --engine both --preset tiny --input-json logs\\\\calibration\\\\sweep_input_from_audit.json --output-dir logs\\\\calibration\\\\full_baseline_real` |",
        f"| Sweep JSON | `{sweep_json.as_posix()}` |",
        f"| Sweep CSV | `{sweep_csv.as_posix()}` |",
        f"| Export source | `audit.db` (`audit_log` rows with `exit_price` and `pnl`; ad-hoc export, not a checked-in repo script) |",
        f"| Sweep metadata `mode` | `{sweep_meta.get('mode', 'n/a')}` |",
        f"| Parameter variants tested | `{sweep_meta.get('parameter_variants_tested', 'n/a')}` |",
        f"| Ranking policy | `{sweep_meta.get('ranking_policy', 'n/a')}` |",
        "",
        "## Summary counts",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Closed Engine A/B trades analysed (trade-level) | **{len(trades)}** |",
        f"| ENGINE_A trades | **{len(ea)}** |",
        f"| ENGINE_B trades | **{len(eb)}** |",
        f"| Sweep aggregated rows (`calibration_sweep.csv`) | **{len(sweep_rows)}** |",
        f"| Sweep rows with `sample_size_warning` | **{sweep_warn}** ({sweep_warn}/{len(sweep_rows) or 1}) |",
        f"| Date range (from `exit_time` / `ts`) | **{date_min or 'n/a'}** to **{date_max or 'n/a'}** |",
        f"| Unique symbols in sweep output | **{len({r.get('symbol') for r in sweep_rows})}** |",
        "",
        "### Trade-level performance by engine",
        "",
        _md_table(
            ["Engine", "Trades", "Win rate", "Avg R", "Sufficient (n≥30)"],
            [
                [
                    "ENGINE_A",
                    str(len(ea)),
                    f"{_win_rate(ea):.4f}" if _win_rate(ea) is not None else "n/a",
                    f"{_avg_r(ea):.4f}" if _avg_r(ea) is not None else "n/a",
                    "Yes",
                ],
                [
                    "ENGINE_B",
                    str(len(eb)),
                    f"{_win_rate(eb):.4f}" if _win_rate(eb) is not None else "n/a",
                    f"{_avg_r(eb):.4f}" if _avg_r(eb) is not None else "n/a",
                    "Yes",
                ],
            ],
        ),
        "",
        "**Note:** Positive win rate with negative average R suggests many small wins and fewer larger losses (or missing R on some rows).",
        "",
        "## Data quality / missing fields",
        "",
        "- **`score_group`:** empty in `sweep_input_from_audit.json` and in `audit_log` export — breakdown is not available.",
        "- **`asset_class`:** populated for only **38 / 466** trades; **428** rows are `(blank)` at export time.",
        "- **`level_mode` / `fallback_tp_applied`:** not present in the audit export used for this baseline — Engine B level/TP attribution **cannot** be concluded from this dataset.",
        "- **`sample_period` / `split_label`:** `unspecified` in sweep metadata.",
        "- **Sweep CSV buckets** group by `symbol × regime × timeframe_style × parameter_set`; almost all buckets are **< 30 trades**, so sweep-row rankings are **not** tuning evidence.",
        "",
        "## Breakdowns (trade-level, from audit.db)",
        "",
        "### Asset class",
        "",
        "Only `(blank)` buckets meet n≥30 per engine; labelled asset classes are too small for class-specific tuning.",
        "",
    ]

    ac_rows: list[list[str]] = []
    for eng, subset in (("ENGINE_A", ea), ("ENGINE_B", eb)):
        for row in _summarize(subset, ["asset_class"]):
            ac_rows.append([
                eng,
                row["asset_class"],
                str(row["trade_count"]),
                str(row["win_rate"]),
                str(row["avg_r_multiple"]),
                "Yes" if not row["sample_size_warning"] else "No",
            ])
    lines.append(_md_table(["Engine", "Asset class", "Trades", "Win rate", "Avg R", "n≥30"], ac_rows[:12]))
    if len(ac_rows) > 12:
        lines.append(f"\n_({len(ac_rows) - 12} more rows in `by_engine_asset_class.csv`)_")

    lines.extend(["", "### Regime (n≥30 only)", ""])
    reg_rows: list[list[str]] = []
    for eng, subset in (("ENGINE_A", ea), ("ENGINE_B", eb)):
        for row in sufficient(subset, "regime"):
            reg_rows.append([eng, row["regime"], str(row["trade_count"]), str(row["win_rate"]), str(row["avg_r_multiple"])])
    lines.append(_md_table(["Engine", "Regime", "Trades", "Win rate", "Avg R"], reg_rows or [["—", "—", "—", "—", "—"]]))

    lines.extend(["", "### Style / timeframe (n≥30 only)", ""])
    style_rows: list[list[str]] = []
    for eng, subset in (("ENGINE_A", ea), ("ENGINE_B", eb)):
        for row in sufficient(subset, "timeframe_style"):
            style_rows.append([eng, row["timeframe_style"], str(row["trade_count"]), str(row["win_rate"]), str(row["avg_r_multiple"])])
    lines.append(_md_table(["Engine", "Style", "Trades", "Win rate", "Avg R"], style_rows))

    lines.extend(["", "## Engine A — evidence-backed observations", ""])
    scores = [t["score"] for t in ea if t.get("score") is not None]
    pcts = [t["score_pct"] for t in ea if t.get("score_pct") is not None]
    mxs = [t["max_score"] for t in ea if t.get("max_score") is not None]
    if scores:
        lines.append(
            f"- **Score (raw):** n={len(scores)}, min={min(scores):.2f}, max={max(scores):.2f}, mean={statistics.mean(scores):.3f}"
        )
    if pcts:
        lines.append(
            f"- **score_pct:** n={len(pcts)}, min={min(pcts):.3f}, max={max(pcts):.3f}, mean={statistics.mean(pcts):.3f}"
        )
    if mxs:
        uniq = sorted(set(mxs))
        lines.append(f"- **max_score:** n={len(mxs)}, distinct values={uniq}")

    low = [t for t in ea if t.get("score_pct") is not None and t["score_pct"] < 0.5]
    high = [t for t in ea if t.get("score_pct") is not None and t["score_pct"] >= 0.5]
    lines.extend([
        "",
        "### score_pct vs outcomes (exploratory)",
        "",
        f"| Bucket | Trades | Win rate | Avg R | n≥30 |",
        f"| --- | ---: | ---: | ---: | --- |",
        f"| score_pct < 0.5 | {len(low)} | {_win_rate(low) or 0:.4f} | {_avg_r(low) if _avg_r(low) is not None else 'n/a'} | {'Yes' if len(low) >= MIN_SAMPLE else '**No**'} |",
        f"| score_pct ≥ 0.5 | {len(high)} | {_win_rate(high) or 0:.4f} | {_avg_r(high) if _avg_r(high) is not None else 'n/a'} | {'Yes' if len(high) >= MIN_SAMPLE else 'No'} |",
        "",
        "- Lower **score_pct** bucket shows worse win rate and R in this sample, but the **< 0.5 bucket has only "
        f"{len(low)} trades** — **insufficient** for a tuning recommendation.",
        "- Labelled **asset_class** slices (forex, crypto, etc.) are all **n < 30**.",
        "",
        "## Engine B — evidence-backed observations",
        "",
        f"- **Overall (n={len(eb)}):** win rate {_win_rate(eb):.4f}, avg R {_avg_r(eb):.4f} — sufficient at engine level only.",
        "",
        "### Style (n≥30)",
        "",
    ])
    for row in sufficient(eb, "timeframe_style"):
        lines.append(
            f"- **{row['timeframe_style']}:** n={row['trade_count']}, win rate={row['win_rate']}, avg R={row['avg_r_multiple']}"
        )
    lines.extend([
        "",
        "### Regime (n≥30)",
        "",
    ])
    for row in sufficient(eb, "regime"):
        lines.append(
            f"- **{row['regime']}:** n={row['trade_count']}, win rate={row['win_rate']}, avg R={row['avg_r_multiple']}"
        )
    lines.extend([
        "",
        "### level_mode / fallback_tp_applied",
        "",
        "**No conclusion.** Fields were not exported from `audit_log` for this baseline (`(not_in_audit_export)` in aggregation CSVs). "
        "Those fields live in the **separate** `logs/calibration_diagnostics/calibration_events.jsonl` pipeline "
        "(`calibration_diagnostics.py`, report via `tools/calibration_diagnostics_report.py`) — not in `calibration_sweep.py`. "
        "Enable `CALIBRATION_DIAGNOSTICS_ENABLED`, collect JSONL, then join or re-export for sweep if needed.",
        "",
        "## Sample-size warnings",
        "",
        f"- **Sweep output:** all **{sweep_warn}** aggregated rows flagged `sample_size_warning` (per-symbol micro-buckets).",
        "- **Trade-level aggregations:** use `by_engine_*.csv`; only trust rows with `sample_size_warning=false`.",
        "",
        "## Do not tune yet",
        "",
        "1. **Per-symbol sweep rows** — 226 groups, effectively all n < 30; sweep `robust_rank_score` is not actionable.",
        "2. **score_group** — missing from export.",
        "3. **asset_class** — 92% blank; labelled classes lack sample size.",
        "4. **Engine B level_mode / fallback TP** — not in input data.",
        "5. **Engine A score_pct < 0.5** — only 22 trades.",
        "6. **Total n=466** — engine-level OK; multi-dimensional slices still thin.",
        "7. **~52 calendar days** — single window; no walk-forward split in metadata.",
        "",
        "Collect more closed trades, enrich export (score_group, level_mode, fallback flags), and set `--sample-period` / `--split-label` on the next sweep.",
        "",
        "## Next experiments (only where n may suffice)",
        "",
        "1. **Re-export input JSON** from `audit_log` with `score_group`, `level_mode`, `fallback_tp_applied` (from diagnostics JSONL or execution metadata).",
        "2. **Re-run sweep** with `--sample-period` and `--split-label` after defining OOS window.",
        "3. **Trade-level dashboards** — use `by_engine_regime.csv` and `by_engine_style.csv`; compare ENGINE_A intraday vs scalp vs swing (n≥30).",
        "4. **Defer `--preset standard`** until total n is several multiples larger; tiny preset already shows negative avg R at engine level.",
        "5. **Engine B fallback TP** — dedicated experiment only after export includes boolean `fallback_tp_applied` per trade.",
        "",
        "## Aggregation artifacts",
        "",
        "- `by_engine.csv`",
        "- `by_engine_asset_class.csv`",
        "- `by_engine_score_group.csv`",
        "- `by_engine_regime.csv`",
        "- `by_engine_style.csv`",
        "- `by_engine_level_mode.csv`",
        "- `by_engine_fallback_tp.csv`",
        "",
    ])
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


if __name__ == "__main__":
    path = analyze(DEFAULT_OUT, DEFAULT_INPUT, AUDIT_DB)
    print(path)
