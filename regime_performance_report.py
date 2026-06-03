"""Regime-bucketed closed-trade performance report.

Diagnostic-only. This module reads ``learning_log`` and aggregates closed-trade
outcomes. It must not change scan, backtest, paper, live, risk, scoring, or
execution decisions.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MIN_TRADES_PER_CELL = 30


def _default_audit_db_path() -> Path:
    """Resolve the default repo-root audit DB path without importing athena.py."""
    return Path(__file__).resolve().parent / "audit.db"


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        if not math.isfinite(out):
            return None
        return out
    except (TypeError, ValueError):
        return None


def _valid_r_multiple(value: Any) -> float | None:
    out = _safe_float(value)
    if out is None or abs(out) > 50:
        return None
    return out


def _normalize_engine(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "unknown"
    return raw.replace("-", "_").replace(" ", "_")


def _normalize_direction(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if raw in {"BUY", "LONG"}:
        return "LONG"
    if raw in {"SELL", "SHORT"}:
        return "SHORT"
    return raw or "UNKNOWN"


def _normalize_regime(value: Any) -> str:
    raw = str(value or "").strip().upper()
    return raw or "UNKNOWN"


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.OperationalError:
        return set()


def _pick_timestamp_column(columns: set[str]) -> str | None:
    for candidate in ("ts", "trade_ts", "timestamp", "created_at", "closed_at"):
        if candidate in columns:
            return candidate
    return None


def _resolve_score_group(
    pair: str | None,
    asset_type: str | None,
    score_group_resolver: Any | None = None,
) -> str:
    """Best-effort score group for rows that do not persist one.

    ``learning_log`` stores pair and asset_type, but older rows may not carry a
    score_group. Use an already-supplied resolver when the caller can provide
    one safely, and otherwise degrade to asset_type/unknown. This report-only
    path must not import athena.py, scoring.py, or mutate scoring decisions.
    """
    if score_group_resolver is not None:
        try:
            resolved = score_group_resolver({"display": pair or "", "type": asset_type or ""})
            normalized = str(resolved or "").strip().lower()
            if normalized:
                return normalized
        except Exception:
            pass

    return str(asset_type or "unknown").strip().lower() or "unknown"


def _readonly_connect(path: Path) -> sqlite3.Connection | None:
    """Open SQLite in explicit read-only mode without creating a missing DB."""
    try:
        return sqlite3.connect(
            f"file:{path.resolve()}?mode=ro",
            uri=True,
            timeout=15.0,
        )
    except sqlite3.OperationalError:
        return None


def load_closed_trades(
    db_path: str | Path | None = None,
    *,
    days: int | None = None,
    engine: str | None = None,
) -> list[dict[str, Any]]:
    """Load closed ``learning_log`` rows in read-only mode.

    Returns an empty list when the DB/table is absent, the required
    ``r_multiple`` column is absent, or the read-only connection fails.

    ``days`` filters by a known timestamp column when one exists. If no known
    timestamp column exists, the report skips the date filter instead of
    returning a false empty result.
    """
    path = Path(db_path) if db_path is not None else _default_audit_db_path()
    if not path.exists():
        return []

    con = _readonly_connect(path)
    if con is None:
        return []

    try:
        con.row_factory = sqlite3.Row
        columns = _table_columns(con, "learning_log")
        if not columns or "r_multiple" not in columns:
            return []

        where = [
            "r_multiple IS NOT NULL",
            "abs(CAST(r_multiple AS REAL)) <= 50",
        ]
        params: list[Any] = []

        if engine and "engine" in columns:
            where.append("lower(replace(replace(engine, '-', '_'), ' ', '_')) = ?")
            params.append(_normalize_engine(engine))

        ts_col = _pick_timestamp_column(columns)
        if days is not None and days > 0 and ts_col:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            where.append(f"{ts_col} > ?")
            params.append(cutoff)

        order_col = ts_col if ts_col else "rowid"
        sql = (
            f"SELECT * FROM learning_log "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY {order_col} DESC"
        )

        try:
            cur = con.execute(sql, params)
        except sqlite3.OperationalError:
            return []

        return [{key: row[key] for key in row.keys()} for row in cur.fetchall()]
    finally:
        con.close()


def _cell_key(
    row: dict[str, Any],
    score_group_resolver: Any | None = None,
) -> tuple[str, str, str, str]:
    explicit_score_group = row.get("score_group")
    normalized_score_group = str(explicit_score_group or "").strip().lower()
    if normalized_score_group:
        score_group = normalized_score_group
    else:
        score_group = _resolve_score_group(
            row.get("pair"),
            row.get("asset_type"),
            score_group_resolver,
        )

    regime = _normalize_regime(row.get("regime"))
    direction = _normalize_direction(row.get("direction"))
    engine = _normalize_engine(row.get("engine"))
    return (score_group, regime, direction, engine)


def _is_win(row: dict[str, Any]) -> bool:
    win = row.get("win")
    if win is not None:
        try:
            win_int = int(win)
            if win_int in (0, 1):
                return win_int == 1
        except (TypeError, ValueError):
            pass

    r_multiple = _valid_r_multiple(row.get("r_multiple"))
    return r_multiple is not None and r_multiple > 0.0


def build_regime_performance(
    rows: list[dict[str, Any]],
    *,
    min_trades: int = MIN_TRADES_PER_CELL,
    score_group_resolver: Any | None = None,
) -> dict[str, Any]:
    """Aggregate closed trades into per-cell regime performance.

    Pure aggregation: no DB access and no scoring/live-path imports. If the
    caller supplies ``score_group_resolver``, it is used only when a row lacks
    ``score_group``; resolver failures fall back to asset_type/unknown.
    """
    try:
        effective_min_trades = max(int(min_trades), MIN_TRADES_PER_CELL)
    except (TypeError, ValueError):
        effective_min_trades = MIN_TRADES_PER_CELL

    cells: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        if _valid_r_multiple(row.get("r_multiple")) is None:
            continue
        cells[_cell_key(row, score_group_resolver)].append(row)

    out_cells: list[dict[str, Any]] = []

    for (score_group, regime, direction, engine_name), cell_rows in sorted(cells.items()):
        r_values = [
            r_value
            for r_value in (_valid_r_multiple(row.get("r_multiple")) for row in cell_rows)
            if r_value is not None
        ]
        n_closed = len(r_values)
        wins = sum(1 for row in cell_rows if _is_win(row))

        score_pcts = [
            score_pct
            for score_pct in (_safe_float(row.get("score_pct")) for row in cell_rows)
            if score_pct is not None
        ]

        grade_split: Counter[str] = Counter()
        for row in cell_rows:
            grade = str(row.get("ai_grade") or "unknown").strip().upper()
            grade_split[grade or "UNKNOWN"] += 1

        out_cells.append(
            {
                "score_group": score_group,
                "regime": regime,
                "direction": direction,
                "engine": engine_name,
                "n_closed": n_closed,
                "win_rate": round(wins / n_closed, 4) if n_closed else None,
                "avg_r": round(statistics.fmean(r_values), 4) if r_values else None,
                "median_r": round(statistics.median(r_values), 4) if r_values else None,
                "total_r": round(sum(r_values), 4) if r_values else None,
                "avg_score_pct": (
                    round(statistics.fmean(score_pcts), 4) if score_pcts else None
                ),
                "ai_grade_split": dict(grade_split),
                "mae": None,
                "mfe": None,
                "excursion_status": "not_recorded",
                "sufficient": n_closed >= effective_min_trades,
            }
        )

    sufficient_cells = [cell for cell in out_cells if cell["sufficient"]]

    return {
        "total_closed_trades": sum(cell["n_closed"] for cell in out_cells),
        "total_cells": len(out_cells),
        "sufficient_cells": len(sufficient_cells),
        "min_trades_per_cell": effective_min_trades,
        "excursion_metrics_available": False,
        "excursion_note": (
            "MAE/MFE not recorded in learning_log; add excursion capture at "
            "trade-close before these can be reported."
        ),
        "calibration_gate_note": (
            "Never set a sub-1.0 RANGING multiplier from a cell flagged "
            "sufficient=false."
        ),
        "cells": out_cells,
    }


def _fmt_number(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_ratio_pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value) * 100:.1f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_score_pct(value: Any) -> str:
    """Format score_pct whether stored as a 0-1 ratio or 0-100 percent."""
    if value is None:
        return "-"
    try:
        out = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(out) <= 1.0:
        out *= 100.0
    return f"{out:.1f}"


def _fmt_grade_split(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "-"
    parts = []
    for grade, count in sorted(value.items()):
        parts.append(f"{grade}:{count}")
    return ",".join(parts)


def build_regime_performance_report(summary: dict[str, Any]) -> str:
    """Render a markdown report from ``build_regime_performance`` output."""
    lines = [
        "# Regime-Bucketed Closed-Trade Performance (READ-ONLY)",
        "",
        f"Total closed trades: {summary['total_closed_trades']}",
        f"Total cells: {summary['total_cells']}",
        (
            f"Cells with >= {summary['min_trades_per_cell']} trades "
            f"(calibration-eligible): {summary['sufficient_cells']}"
        ),
        f"Excursion (MAE/MFE) available: {summary['excursion_metrics_available']}",
        "",
    ]

    if summary["sufficient_cells"] == 0:
        lines.extend(
            [
                (
                    "> No cell has enough closed trades to calibrate a Phase 2 "
                    "regime multiplier. Keep "
                    "ENGINE_A_VOLATILITY_REGIME_MULTIPLIERS at 1.00 "
                    "(report-only). Execution must continue respecting AI review."
                ),
                "",
            ]
        )

    lines.extend(
        [
            "| score_group | regime | dir | engine | n | win% | avg_R | med_R | tot_R | avg_score% | ai_grades | eligible |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|:--:|",
        ]
    )

    for cell in summary["cells"]:
        lines.append(
            f"| {cell['score_group']} "
            f"| {cell['regime']} "
            f"| {cell['direction']} "
            f"| {cell['engine']} "
            f"| {cell['n_closed']} "
            f"| {_fmt_ratio_pct(cell['win_rate'])} "
            f"| {_fmt_number(cell['avg_r'])} "
            f"| {_fmt_number(cell['median_r'])} "
            f"| {_fmt_number(cell['total_r'])} "
            f"| {_fmt_score_pct(cell['avg_score_pct'])} "
            f"| {_fmt_grade_split(cell['ai_grade_split'])} "
            f"| {'YES' if cell['sufficient'] else 'no'} |"
        )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- MAE/MFE are unavailable because learning_log does not persist excursion columns.",
            "- ai_grade_split is based only on closed trades present in learning_log.",
            "- Rejected AI setups that never executed are not represented here.",
            "- Do not calibrate Phase 2 multipliers from cells marked eligible=no.",
            "- Never set a sub-1.0 RANGING multiplier from a cell flagged sufficient=false.",
        ]
    )

    return "\n".join(lines)


def run(
    db_path: str | Path | None = None,
    *,
    days: int | None = None,
    engine: str | None = "engine_a",
    min_trades: int = MIN_TRADES_PER_CELL,
    as_markdown: bool = False,
) -> str:
    """Convenience entry point. Defaults to Engine A closed trades only."""
    rows = load_closed_trades(db_path, days=days, engine=engine)
    summary = build_regime_performance(rows, min_trades=min_trades)

    if as_markdown:
        return build_regime_performance_report(summary)

    return json.dumps(summary, indent=2, sort_keys=True, default=str)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regime-bucketed closed-trade report (read-only)"
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to audit.db. Default: <repo>/audit.db",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Restrict to last N days if a timestamp column exists.",
    )
    parser.add_argument(
        "--engine",
        default="engine_a",
        help="Engine filter. Default: engine_a. Use an empty string for all engines.",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=MIN_TRADES_PER_CELL,
        help="Minimum trades per cell before calibration eligibility.",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Render markdown instead of JSON.",
    )

    args = parser.parse_args()
    print(
        run(
            args.db,
            days=args.days,
            engine=(args.engine or None),
            min_trades=args.min_trades,
            as_markdown=args.markdown,
        )
    )


if __name__ == "__main__":  # pragma: no cover - manual diagnostic entry point
    main()
