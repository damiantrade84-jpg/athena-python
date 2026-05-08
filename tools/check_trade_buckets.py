#!/usr/bin/env python3
"""Inspect trade bucket DB for Engine D aggTrade verification."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def _resolve_db_path() -> Path:
    env_path = os.environ.get("TRADE_BUCKET_DB_PATH", "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            p = Path(local_app_data) / "Athena" / "trade_buckets.db"
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
    return Path(__file__).resolve().parents[1] / "trade_buckets.db"


def main() -> int:
    ap = argparse.ArgumentParser(description="Check Binance trade bucket rows for a symbol.")
    ap.add_argument(
        "symbol",
        nargs="?",
        default="DOT/USDT",
        help="Display pair e.g. DOT/USDT (default)",
    )
    ap.add_argument("--max-age", type=int, default=300, help="Freshness window seconds (default 300)")
    args = ap.parse_args()
    sym_key = str(args.symbol or "").replace("/", "").upper()
    if not sym_key:
        print("empty symbol", file=sys.stderr)
        return 2

    db = _resolve_db_path()
    print("DB path:", db)
    print("DB exists:", db.exists())

    repo_root = Path(__file__).resolve().parents[1]
    cfg_path = repo_root / "config.yaml"
    micro = scalp_max_age = min_levels = "n/a"
    if cfg_path.is_file():
        try:
            import yaml

            with cfg_path.open(encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh)
            micro = cfg.get("MICROSTRUCTURE_FEEDS_ENABLED")
            se = cfg.get("SCALP_ENGINE") or {}
            scalp_max_age = se.get("TRADE_BUCKET_MAX_AGE_SEC")
            min_levels = se.get("TRADE_BUCKET_MIN_LEVELS")
        except Exception as exc:
            print("config.yaml read failed:", exc)
    print("MICROSTRUCTURE_FEEDS_ENABLED:", micro)
    print("SCALP_ENGINE TRADE_BUCKET_MAX_AGE_SEC:", scalp_max_age)
    print("SCALP_ENGINE TRADE_BUCKET_MIN_LEVELS:", min_levels)
    print()

    now = time.time()
    session_id = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")

    if not db.exists():
        print("No database file — aggTrades are not being persisted at this path, or app never ran.")
        return 1

    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute("SELECT MAX(last_ts) FROM trade_buckets WHERE exchange = 'binance'")
    glob_mx = cur.fetchone()[0]
    if glob_mx:
        g_age = now - float(glob_mx)
        print(
            f"Global newest last_ts (all Binance rows): {glob_mx} (age_sec={g_age:.1f}; {g_age/86400:.2f} days)"
        )
        if g_age > float(args.max_age):
            print(
                "  Note: nothing in the DB is within TRADE_BUCKET_MAX_AGE_SEC - live Engine D will fall back to "
                "candle VP/CVD and strict crypto will block."
            )
        print()
    cur.execute(
        "SELECT DISTINCT symbol FROM trade_buckets WHERE exchange = 'binance' ORDER BY symbol LIMIT 40"
    )
    sample = [r[0] for r in cur.fetchall()]
    print("Sample symbols with any rows:", ", ".join(sample) if sample else "(none)")
    cur.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT price_bucket),
               MIN(last_ts), MAX(last_ts), SUM(total_volume)
        FROM trade_buckets
        WHERE exchange = ? AND symbol = ? AND session_id = ?
        """,
        ("binance", sym_key, session_id),
    )
    cnt, buckets, mn_ts, mx_ts, vol = cur.fetchone()
    print(f"{sym_key} session {session_id}: rows={cnt} distinct_price_buckets={buckets}")
    if mx_ts:
        age = now - float(mx_ts)
        print(f"  last_ts range: min={mn_ts} max={mx_ts} newest_age_sec={age:.1f}")
        print(f"  sum(total_volume): {vol}")
    fresh_cutoff = now - int(args.max_age)
    cur.execute(
        """
        SELECT COUNT(*) FROM trade_buckets
        WHERE exchange = ? AND symbol = ? AND session_id = ? AND last_ts >= ?
        """,
        ("binance", sym_key, session_id, fresh_cutoff),
    )
    fresh = cur.fetchone()[0]
    print(f"  rows with last_ts >= now-{args.max_age}s (fresh gate): {fresh}")
    try:
        need = int(min_levels) if min_levels != "n/a" else 8
    except (TypeError, ValueError):
        need = 8
    meets = fresh >= need
    print(f"  passes TRADE_BUCKET_MIN_LEVELS ({need}) using fresh bucket rows:", meets)

    cur.execute(
        "SELECT MAX(last_ts) FROM trade_buckets WHERE exchange = ? AND symbol = ?",
        ("binance", sym_key),
    )
    mx_any = cur.fetchone()[0]
    if mx_any:
        print(f"  global max last_ts (any session): {mx_any} age_sec={now - float(mx_any):.1f}")
    else:
        print(f"  no rows ever for exchange=binance symbol={sym_key}")
    con.close()

    print()
    if not sample:
        print(
            "Action: Ensure athena.py is running with MICROSTRUCTURE_FEEDS_ENABLED true "
            "and the pair enabled in crypto pairs selection for micro feeds."
        )
    elif mx_any is None or fresh < need:
        print(
            "Action: If Athena is running but fresh count is low, wait for liquidity, "
            "or widen TRADE_BUCKET_MAX_AGE_SEC / lower TRADE_BUCKET_MIN_LEVELS in config.yaml (tradeoffs)."
        )
    else:
        print("Trade bucket path looks populated for freshness/min-levels heuristic.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
