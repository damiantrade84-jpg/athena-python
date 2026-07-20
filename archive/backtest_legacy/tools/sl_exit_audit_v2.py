#!/usr/bin/env python3
"""Entry-TF backtest audit (Engine B) — canonical pair dicts, XAU/USD GC=F fix."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")
os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

from scoring import get_pair_score_group
from tools._athena_pair_loader import find_pair_by_display, representative_audit_pairs
from tools.h4_overlay_runtime import bootstrap_backtest_runtime, entry_tf_override

AUDIT_DB = Path(os.environ.get("ATHENA_AUDIT_DB", Path.home() / "AppData" / "Local" / "Athena" / "audit.db"))


def _session_bucket(ts_str: str | None) -> str:
    if not ts_str:
        return "unknown"
    try:
        dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hour = dt.astimezone(timezone.utc).hour
    except (TypeError, ValueError):
        return "unknown"
    if 0 <= hour < 7:
        return "asia"
    if 7 <= hour < 12:
        return "london_open"
    if 12 <= hour < 17:
        return "london_ny"
    if 17 <= hour < 22:
        return "ny_session"
    return "ny_close_rollover"


def _summarize_bt(result: dict, trades: list, entry_tf: str, label: str) -> str:
    if result.get("error"):
        return f"  {label} entry_tf={entry_tf} error={result['error']}"
    total = int(result.get("totalTrades") or 0)
    sl = sum(1 for t in trades if t.get("outcome") == "SL")
    tp = sum(1 for t in trades if t.get("outcome") in ("TP1", "TP2"))
    timeout = sum(1 for t in trades if t.get("outcome") == "TIMEOUT")
    be = sum(1 for t in trades if t.get("outcome") == "BE")
    wr = float(result.get("winRate") or 0) / 100.0 if total else 0.0
    avg_r = float(result.get("expectancy") or 0.0)
    sqn = float(result.get("sqn") or 0.0)
    sl_pct = (100.0 * sl / total) if total else 0.0
    sl_hours: list[float] = []
    sl_sessions: dict[str, int] = {}
    for t in trades:
        if t.get("outcome") != "SL":
            continue
        entry_ts = t.get("entryTime") or t.get("entry_time")
        exit_ts = t.get("exitTime") or t.get("exit_time")
        sl_sessions[_session_bucket(exit_ts or entry_ts)] = sl_sessions.get(_session_bucket(exit_ts or entry_ts), 0) + 1
        if entry_ts and exit_ts:
            try:
                e0 = datetime.fromisoformat(str(entry_ts).replace("Z", "+00:00"))
                e1 = datetime.fromisoformat(str(exit_ts).replace("Z", "+00:00"))
                sl_hours.append((e1 - e0).total_seconds() / 3600.0)
            except (TypeError, ValueError):
                pass
    sl_avg = sum(sl_hours) / len(sl_hours) if sl_hours else None
    return (
        f"  {label}|entry_tf={entry_tf}|trades={total}|wr={wr:.3f}|avg_r={avg_r}|sqn={sqn:.2f}|"
        f"SL={sl}({sl_pct:.1f}%)|TP={tp}|TIMEOUT={timeout}|BE={be}|"
        f"sl_avg_hours={sl_avg}|sl_sessions={sl_sessions}"
    )


@contextmanager
def _entry_tf_override(entry_tf: str):
    with entry_tf_override(entry_tf):
        yield


def phase1_wiring(pairs: list[dict]) -> None:
    from engine_a_v3.quant_scorer import _resolve_v3_tf_weights
    from market_structure import resolve_engine_b_tfs

    print("=== PHASE 1 WIRING ===")
    print("display|asset_type|score_group|style|v3_entry_tf|eb_trigger_tf|agree")
    for pair in pairs:
        sg = get_pair_score_group(pair)
        asset = pair.get("type", "")
        for style in ("intraday", "swing"):
            eb = resolve_engine_b_tfs(asset, style)
            v3 = _resolve_v3_tf_weights(sg, style)
            v3_entry = "H1" if style == "intraday" else "H4"
            eb_trigger = eb.get("trigger", "?")
            agree = "AGREE" if v3_entry == eb_trigger else "MISMATCH"
            print(
                f"{pair['display']}|{asset}|{sg}|{style}|{v3_entry}|{eb_trigger}|{agree}"
            )


def phase2_baseline(pairs: list[dict], lookback_days: int = 90) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    print(f"\n=== PHASE 2 BASELINE (lookback={lookback_days}d, cutoff={cutoff.isoformat()}) ===")
    if not AUDIT_DB.exists():
        print(f"audit.db missing: {AUDIT_DB}")
        return
    with sqlite3.connect(AUDIT_DB, timeout=15.0) as con:
        for pair in pairs:
            display = pair["display"]
            sql = (
                "SELECT outcome, exit_reason, r_multiple, entry_time, exit_time "
                "FROM audit_log WHERE pair=? AND exit_time IS NOT NULL AND exit_time >= ?"
            )
            rows = con.execute(sql, (display, cutoff.isoformat())).fetchall()
            print(f"\n--- {display} total_closed={len(rows)} ---")
            print(f"  SQL row_count={len(rows)}")
            if not rows:
                continue
            wins = sum(1 for r in rows if (r[2] or 0) > 0)
            avg_r = sum((r[2] or 0) for r in rows) / len(rows)
            sl = sum(1 for r in rows if r[0] == "SL" or r[1] == "SL_HIT")
            tp = sum(1 for r in rows if r[0] in ("TP1", "TP2") or r[1] == "TP_HIT")
            timeout = sum(1 for r in rows if r[0] == "TIMEOUT" or r[1] == "TIMED_CLOSE")
            manual = sum(1 for r in rows if str(r[1] or "").startswith("MANUAL"))
            print(f"  win_rate={wins/len(rows):.3f} avg_r={avg_r:.6f}")
            print(f"  exits SL={sl} TP={tp} TIMEOUT={timeout} MANUAL={manual}")


def phase3_backtest(pairs: list[dict]) -> None:
    import backtest_runner

    bootstrap_backtest_runtime()
    print("\n=== PHASE 3 CONTROLLED TF BACKTEST (Engine B intraday via backtest_pair_naked) ===")
    print("Harness: backtest_runner.backtest_pair_naked")
    xau = next((p for p in pairs if p["display"] == "XAU/USD"), None)
    if xau:
        print(f"XAU/USD symbol check: {xau['symbol']}")

    for pair in pairs:
        print(f"\n--- BT {pair['display']} (symbol={pair.get('symbol')}) ---")
        for label, entry_tf in (("baseline_H1", "H1"), ("variant_B_slower_H4", "H4")):
            try:
                with _entry_tf_override(entry_tf):
                    result = backtest_runner.backtest_pair_naked(pair, style="intraday")
                trades = result.get("trades") or []
                print(_summarize_bt(result, trades, entry_tf, label))
            except Exception as exc:
                print(f"  {label} entry_tf={entry_tf} ERROR={exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="SL-exit / entry-TF audit v2")
    parser.add_argument("--phase", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--pair", default="", help="Comma-separated display names (default: all representative)")
    parser.add_argument("--lookback-days", type=int, default=90)
    args = parser.parse_args()

    if args.pair.strip():
        pairs = []
        for name in [p.strip() for p in args.pair.split(",") if p.strip()]:
            found = find_pair_by_display(name)
            if found is None:
                print(f"Unknown pair: {name}")
                return 1
            pairs.append(found)
    else:
        pairs = representative_audit_pairs()

    if args.phase == 1:
        phase1_wiring(pairs)
    elif args.phase == 2:
        phase2_baseline(pairs, lookback_days=args.lookback_days)
    else:
        phase3_backtest(pairs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
