"""Engine B evidence: live learning_log SQN + frozen backtest sweep (read-only live DB).

Rerunnable diagnostic. Does not mutate config or learning_log.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import math
import os
import socket
import statistics
import sys
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from regime_performance_report import load_closed_trades, build_regime_performance, MIN_TRADES_PER_CELL

DATE_TAG = "20260622"
OUT_JSON = REPO / "tmp" / f"evidence_engine_b_{DATE_TAG}.json"
BACKTEST_OUT = REPO / "tmp" / f"evidence_engine_b_backtest_{DATE_TAG}.json"
DATA_AS_OF = "2026-05-30"
AUDIT_DB = REPO / "audit.db"

# AB6 / AB7-aligned universe (frozen snapshot 2026-05-30)
PAIR_UNIVERSE = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "GBP/JPY",
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "XAU/USD",
    "XAG/USD",
    "WTI Oil",
    "NASDAQ-100",
    "S&P 500",
    "Dow Jones",
    "DAX 40",
    "AAPL",
    "TSLA",
    "NVDA",
    "MSFT",
]

ENGINE_A_BACKTEST = REPO / "tmp" / "diag_majors_summary.json"
AB6_OFF = REPO / "tmp" / "ab6_off.json"


def _sqn(r_values: list[float]) -> float | None:
    if len(r_values) < 2:
        return None
    stdev = statistics.pstdev(r_values)
    if stdev == 0:
        return None
    return statistics.mean(r_values) / stdev * math.sqrt(len(r_values))


def _win_pct(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    wins = sum(1 for r in rows if (r.get("win") == 1 or float(r.get("r_multiple") or 0) > 0))
    return round(100.0 * wins / len(rows), 1)


def _avg_r(r_values: list[float]) -> float | None:
    if not r_values:
        return None
    return round(statistics.mean(r_values), 3)


def _valid_r(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        if not math.isfinite(out) or abs(out) > 50:
            return None
        return out
    except (TypeError, ValueError):
        return None


def _live_precheck(db_path: Path) -> dict[str, Any]:
    import sqlite3

    if not db_path.exists():
        return {"db_exists": False}

    con = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=15.0)
    try:
        total = con.execute(
            """
            SELECT COUNT(*) FROM learning_log
            WHERE lower(replace(replace(engine, '-', '_'), ' ', '_')) = 'engine_b'
            """
        ).fetchone()[0]
        closed = con.execute(
            """
            SELECT COUNT(*) FROM learning_log
            WHERE lower(replace(replace(engine, '-', '_'), ' ', '_')) = 'engine_b'
              AND r_multiple IS NOT NULL
              AND abs(CAST(r_multiple AS REAL)) <= 50
            """
        ).fetchone()[0]
        null_pair = con.execute(
            """
            SELECT COUNT(*) FROM learning_log
            WHERE lower(replace(replace(engine, '-', '_'), ' ', '_')) = 'engine_b'
              AND (pair IS NULL OR trim(pair) = '')
            """
        ).fetchone()[0]
        closed_null_pair = con.execute(
            """
            SELECT COUNT(*) FROM learning_log
            WHERE lower(replace(replace(engine, '-', '_'), ' ', '_')) = 'engine_b'
              AND r_multiple IS NOT NULL
              AND abs(CAST(r_multiple AS REAL)) <= 50
              AND (pair IS NULL OR trim(pair) = '')
            """
        ).fetchone()[0]
        ts_row = con.execute(
            """
            SELECT MIN(ts), MAX(ts) FROM learning_log
            WHERE lower(replace(replace(engine, '-', '_'), ' ', '_')) = 'engine_b'
              AND r_multiple IS NOT NULL
            """
        ).fetchone()
        return {
            "db_exists": True,
            "db_path": str(db_path),
            "total_rows": int(total),
            "closed_rows": int(closed),
            "null_or_blank_pair_rows": int(null_pair),
            "closed_null_or_blank_pair_rows": int(closed_null_pair),
            "closed_date_min": ts_row[0],
            "closed_date_max": ts_row[1],
        }
    finally:
        con.close()


def _aggregate_live(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_pair: dict[str, list[float]] = defaultdict(list)
    all_r: list[float] = []
    for row in rows:
        pair = str(row.get("pair") or "").strip()
        r_val = _valid_r(row.get("r_multiple"))
        if r_val is None:
            continue
        all_r.append(r_val)
        if pair:
            by_pair[pair].append(r_val)

    pair_stats = {}
    for pair, r_vals in sorted(by_pair.items()):
        pair_stats[pair] = {
            "n": len(r_vals),
            "sqn": round(_sqn(r_vals), 2) if _sqn(r_vals) is not None else None,
            "win_pct": _win_pct([{"r_multiple": v} for v in r_vals]),
            "avg_r": _avg_r(r_vals),
            "clears_n30": len(r_vals) >= MIN_TRADES_PER_CELL,
            "clears_sqn_gt_2": (_sqn(r_vals) or -999) > 2.0 if len(r_vals) >= 2 else False,
        }

    overall_sqn = _sqn(all_r)
    return {
        "closed_n": len(all_r),
        "overall": {
            "n": len(all_r),
            "sqn": round(overall_sqn, 2) if overall_sqn is not None else None,
            "win_pct": _win_pct(rows),
            "avg_r": _avg_r(all_r),
            "clears_n30": len(all_r) >= MIN_TRADES_PER_CELL,
            "clears_sqn_gt_2": (overall_sqn or -999) > 2.0 if overall_sqn is not None else False,
        },
        "by_pair": pair_stats,
        "regime_report": build_regime_performance(rows),
    }


def _block_network() -> None:
    def _blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("NETWORK_BLOCKED_EVIDENCE_B")

    socket.create_connection = _blocked
    socket.socket = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("NETWORK_BLOCKED_EVIDENCE_B")
    )
    try:
        import requests

        requests.get = _blocked
        requests.post = _blocked
        requests.sessions.Session.request = _blocked
    except Exception:
        pass


def _stub_optional_deps() -> None:
    # athena_ase/registry/artifacts.py imports sklearn solely for sklearn.__version__
    # (no functional use). sklearn is absent in this env and is irrelevant to the
    # Engine B backtest, so stub it to let athena.py import. No behavioral effect.
    import types
    if "sklearn" not in sys.modules:
        try:
            import sklearn  # noqa: F401
        except Exception:
            stub = types.ModuleType("sklearn")
            stub.__version__ = "absent-stub"
            sys.modules["sklearn"] = stub


def _load_athena() -> Any:
    _stub_optional_deps()
    spec = importlib.util.spec_from_file_location("athena_monolith_evidence_b", REPO / "athena.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load athena.py")
    athena = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = athena
    spec.loader.exec_module(athena)
    return athena


def _run_engine_b_backtest(*, run_backtest: bool) -> list[dict[str, Any]]:
    if not run_backtest:
        if BACKTEST_OUT.exists():
            return json.loads(BACKTEST_OUT.read_text(encoding="utf-8"))
        return []

    os.environ["BACKTEST_DATA_AS_OF"] = DATA_AS_OF
    os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")
    os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")

    athena = _load_athena()
    from athena_runtime import rt

    rt().AUDIT_DB = str(REPO / "tmp" / f"evidence_engine_b_bt_audit_{DATE_TAG}.db")
    _block_network()

    pair_map = {p.get("display"): p for p in athena.ALL_PAIRS}
    rows: list[dict[str, Any]] = []
    for display in PAIR_UNIVERSE:
        pair = pair_map.get(display)
        if not pair:
            rows.append({"pair": display, "error": "pair_not_found"})
            print(f"[ERR] {display}: pair_not_found", flush=True)
            continue
        print(f"BT {display} ...", flush=True)
        try:
            result = athena.backtest_pair_naked(copy.deepcopy(pair), style="auto")
            funnel = result.get("funnel") if isinstance(result.get("funnel"), dict) else {}
            row = {
                "pair": result.get("pair") or display,
                "type": result.get("type") or pair.get("type"),
                "totalTrades": result.get("totalTrades"),
                "winRate": result.get("winRate"),
                "sqn": result.get("sqn"),
                "expectancy": result.get("expectancy"),
                "profitFactor": result.get("profitFactor"),
                "funnelTaken": funnel.get("taken"),
                "funnelSetups": funnel.get("total_setups"),
                "error": result.get("error"),
                "clears_n30": (result.get("totalTrades") or 0) >= MIN_TRADES_PER_CELL,
                "clears_sqn_gt_2": (result.get("sqn") or -999) > 2.0
                if result.get("sqn") is not None
                else False,
            }
        except Exception as exc:
            row = {"pair": display, "error": repr(exc)}
            traceback.print_exc()
        rows.append(row)
        print(json.dumps(row, sort_keys=True), flush=True)

    BACKTEST_OUT.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {BACKTEST_OUT}", flush=True)
    return rows


def _load_engine_a_comparators() -> dict[str, Any]:
    out: dict[str, Any] = {"majors_summary": None, "ab6_off_by_pair": {}}
    if ENGINE_A_BACKTEST.exists():
        out["majors_summary"] = json.loads(ENGINE_A_BACKTEST.read_text(encoding="utf-8"))
    if AB6_OFF.exists():
        for row in json.loads(AB6_OFF.read_text(encoding="utf-8")):
            pair = row.get("pair")
            if pair:
                out["ab6_off_by_pair"][pair] = row
    return out


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--live-only", action="store_true")
    parser.add_argument("--backtest-only", action="store_true")
    parser.add_argument("--skip-backtest", action="store_true", help="Use cached backtest JSON if present")
    args = parser.parse_args()

    os.chdir(REPO)
    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_standard": {"min_n": MIN_TRADES_PER_CELL, "min_sqn": 2.0},
        "backtest_data_as_of": DATA_AS_OF,
        "pair_universe": PAIR_UNIVERSE,
        "engine_a_comparators": _load_engine_a_comparators(),
    }

    if not args.backtest_only:
        precheck = _live_precheck(AUDIT_DB)
        payload["live_precheck"] = precheck
        rows = load_closed_trades(AUDIT_DB, engine="engine_b")
        payload["live"] = _aggregate_live(rows)
        payload["live_verdict"] = (
            "INSUFFICIENT_EVIDENCE"
            if precheck.get("closed_rows", 0) < MIN_TRADES_PER_CELL
            else "SUFFICIENT_N"
        )

    if not args.live_only:
        run_bt = not args.skip_backtest or not BACKTEST_OUT.exists()
        payload["engine_b_backtest"] = _run_engine_b_backtest(run_backtest=run_bt)
        payload["engine_b_backtest_source"] = str(BACKTEST_OUT)

    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(f"wrote {OUT_JSON}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
