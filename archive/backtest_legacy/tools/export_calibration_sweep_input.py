"""Export closed Engine A/B audit trades for calibration sweeps.

Research-only. Reads ``audit.db`` and writes JSON rows with score fields so
``tools/calibration_sweep.py`` can apply candidate threshold filters.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_AUDIT_DB = ROOT / "audit.db"
DEFAULT_OUTPUT = ROOT / "logs" / "calibration" / "sweep_input_from_audit.json"


def _norm_engine(raw: Any) -> str | None:
    text = str(raw or "").strip().lower()
    if text in {"engine_a", "a"}:
        return "ENGINE_A"
    if text in {"engine_b", "b", "structural", "naked"}:
        return "ENGINE_B"
    return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        out = float(value)
        if out != out:
            return None
        return out
    except (TypeError, ValueError):
        return None


def _blank(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else ""


def _json_or_none(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def load_closed_trades(audit_db: Path) -> tuple[list[dict[str, Any]], str | None, str | None]:
    if not audit_db.exists():
        raise FileNotFoundError(f"audit db not found: {audit_db}")
    con = sqlite3.connect(str(audit_db), timeout=15.0)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT pair, asset_class, style, regime, score, score_pct, max_score,
               direction, entry_price, exit_price, pnl, r_multiple, engine,
               exit_time, ts, exit_reason, holding_period_hours, sl, tp, tp2,
               tp_partial, risk, risk_amount, risk_pct, grade, edge_prob,
               factors_json, votes_json, warnings_json, fee_cost,
               signal_price_ref, slippage_bps, trend, trend_state, adx_pct,
               btc_bias, session_name, ticket
        FROM audit_log
        WHERE exit_price IS NOT NULL AND pnl IS NOT NULL
          AND (error_tag IS NULL OR error_tag = '')
        """
    ).fetchall()
    con.close()

    out: list[dict[str, Any]] = []
    dates: list[str] = []
    for row in rows:
        engine = _norm_engine(row["engine"])
        if engine not in {"ENGINE_A", "ENGINE_B"}:
            continue
        pnl = _safe_float(row["pnl"])
        r_multiple = _safe_float(row["r_multiple"])
        score_pct = _safe_float(row["score_pct"])
        if score_pct is not None and score_pct > 1.0:
            score_pct = round(score_pct / 100.0, 6)
        factors = _json_or_none(row["factors_json"])
        votes = _json_or_none(row["votes_json"])
        warnings = _json_or_none(row["warnings_json"])
        pnl = _safe_float(row["pnl"])
        r_multiple = _safe_float(row["r_multiple"])
        for col in ("exit_time", "ts"):
            val = row[col]
            if val:
                dates.append(str(val)[:10])
        out.append(
            {
                "engine": engine,
                "symbol": row["pair"],
                "pair": row["pair"],
                "asset_class": _blank(row["asset_class"]) or None,
                "score_group": None,
                "regime": _blank(row["regime"]),
                "timeframe_style": _blank(row["style"]) or "intraday",
                "timeframe": _blank(row["style"]) or "intraday",
                "style": _blank(row["style"]) or None,
                "score": _safe_float(row["score"]),
                "score_pct": score_pct,
                "max_score": _safe_float(row["max_score"]),
                "direction": _blank(row["direction"]) or None,
                "entry": _safe_float(row["entry_price"]),
                "entry_price": _safe_float(row["entry_price"]),
                "exit": _safe_float(row["exit_price"]),
                "exit_price": _safe_float(row["exit_price"]),
                "exit_time": row["exit_time"],
                "opened_at": row["ts"],
                "exit_reason": row["exit_reason"],
                "pnl": pnl,
                "r_multiple": r_multiple,
                "expectancy": r_multiple,
                "win": bool(pnl is not None and pnl > 0),
                "loss": bool(pnl is not None and pnl <= 0),
                "passed": bool(pnl is not None and pnl > 0),
                "sl": _safe_float(row["sl"]),
                "tp": _safe_float(row["tp"]),
                "tp2": _safe_float(row["tp2"]),
                "tp_partial": _safe_float(row["tp_partial"]),
                "risk": _safe_float(row["risk"]),
                "risk_amount": _safe_float(row["risk_amount"]),
                "risk_pct": _safe_float(row["risk_pct"]),
                "grade": row["grade"],
                "edge_prob": _safe_float(row["edge_prob"]),
                "ticket": row["ticket"],
                "holding_period_hours": _safe_float(row["holding_period_hours"]),
                "fee_cost": _safe_float(row["fee_cost"]),
                "signal_price_ref": _safe_float(row["signal_price_ref"]),
                "slippage_bps": _safe_float(row["slippage_bps"]),
                "trend": row["trend"],
                "trend_state": row["trend_state"],
                "adx_pct": _safe_float(row["adx_pct"]),
                "btc_bias": row["btc_bias"],
                "session_name": row["session_name"],
                "votes": votes,
                "warnings": warnings,
                "factors_json": factors,
                "engine_a_factor_data": factors if engine == "ENGINE_A" else None,
                "engine_b_factor_data": factors if engine == "ENGINE_B" else None,
                "engine_b_style": _blank(row["style"]) if engine == "ENGINE_B" else None,
                "engine_b_level_mode": None,
                "engine_b_level_cohort": None,
                "engine_b_rr_actual": r_multiple if engine == "ENGINE_B" else None,
                "engine_b_rr_required": None,
                "engine_b_rr_passed": None,
                "engine_b_fallback_tp_applied": None,
                "level_mode": None,
                "fallback_tp_applied": None,
                "rr_actual": r_multiple,
            }
        )
    return out, (min(dates) if dates else None), (max(dates) if dates else None)


def export_sweep_input(*, audit_db: Path, output: Path) -> dict[str, Any]:
    rows, date_min, date_max = load_closed_trades(audit_db)
    payload = {
        "metadata": {
            "research_only": True,
            "source": str(audit_db),
            "closed_trade_count": len(rows),
            "engine_a_count": sum(1 for row in rows if row.get("engine") == "ENGINE_A"),
            "engine_b_count": sum(1 for row in rows if row.get("engine") == "ENGINE_B"),
            "date_min": date_min,
            "date_max": date_max,
            "contains_score_fields": True,
            "live_config_mutation": False,
            "live_execution_behavior_changed": False,
        },
        "rows": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload["metadata"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-db", default=str(DEFAULT_AUDIT_DB))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    metadata = export_sweep_input(audit_db=Path(args.audit_db), output=Path(args.output))
    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
