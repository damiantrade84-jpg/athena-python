#!/usr/bin/env python3
"""Inspect trade bucket DB for Engine D aggTrade verification."""

from __future__ import annotations

import argparse
import os
import re
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


def _parse_crypto_symbols_athena(repo_root: Path) -> list[str]:
    """Symbols from CRYPTO_PAIRS in athena.py without importing the monolith."""
    path = repo_root / "athena.py"
    text = path.read_text(encoding="utf-8")
    start = text.index("CRYPTO_PAIRS = [")
    end = text.index("\nALL_PAIRS = (", start)
    block = text[start:end]
    found = re.findall(r'"symbol":\s*"([^"]+)"', block)
    return sorted({s.replace("/", "").upper() for s in found if s.strip()})


def _load_config_trade_bucket(repo_root: Path) -> tuple[object, int, int]:
    """Return (MICROSTRUCTURE_FEEDS_ENABLED, TRADE_BUCKET_MAX_AGE_SEC, TRADE_BUCKET_MIN_LEVELS)."""
    cfg_path = repo_root / "config.yaml"
    micro: object = "n/a"
    max_age = 300
    min_levels = 8
    if cfg_path.is_file():
        try:
            import yaml

            with cfg_path.open(encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh)
            micro = cfg.get("MICROSTRUCTURE_FEEDS_ENABLED")
            se = cfg.get("SCALP_ENGINE") or {}
            if se.get("TRADE_BUCKET_MAX_AGE_SEC") is not None:
                max_age = int(se["TRADE_BUCKET_MAX_AGE_SEC"])
            if se.get("TRADE_BUCKET_MIN_LEVELS") is not None:
                min_levels = int(se["TRADE_BUCKET_MIN_LEVELS"])
        except Exception as exc:
            print("config.yaml read failed:", exc)
    return micro, max_age, min_levels


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    micro_d, yaml_max_age, yaml_min_levels = _load_config_trade_bucket(repo_root)

    ap = argparse.ArgumentParser(description="Check Binance trade bucket rows for a symbol.")
    ap.add_argument(
        "--all-crypto",
        action="store_true",
        help="Report freshness for every symbol in athena.py CRYPTO_PAIRS (parsed, no app import).",
    )
    ap.add_argument(
        "symbol",
        nargs="?",
        default="DOT/USDT",
        help="Display pair e.g. DOT/USDT (default; ignored with --all-crypto)",
    )
    ap.add_argument(
        "--max-age",
        type=int,
        default=None,
        help=f"Freshness window seconds (default from config TRADE_BUCKET_MAX_AGE_SEC or {yaml_max_age})",
    )
    args = ap.parse_args()
    max_age = int(args.max_age if args.max_age is not None else yaml_max_age)

    db = _resolve_db_path()
    print("DB path:", db)
    print("DB exists:", db.exists())

    min_levels = yaml_min_levels
    print("MICROSTRUCTURE_FEEDS_ENABLED:", micro_d)
    print("SCALP_ENGINE TRADE_BUCKET_MAX_AGE_SEC:", max_age)
    print("SCALP_ENGINE TRADE_BUCKET_MIN_LEVELS:", min_levels)
    print()

    now = time.time()
    session_id = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")

    if not db.exists():
        print("No database file - aggTrades are not being persisted at this path, or app never ran.")
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
        if g_age > float(max_age):
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
    print()

    fresh_cutoff = now - int(max_age)

    if args.all_crypto:
        try:
            symbols = _parse_crypto_symbols_athena(repo_root)
        except ValueError as exc:
            print("Could not parse CRYPTO_PAIRS from athena.py:", exc, file=sys.stderr)
            con.close()
            return 2
        print(
            f"Aggtrade bucket freshness | UTC session {session_id} | "
            f"fresh rows = last_ts >= now-{max_age}s for this session"
        )
        hdr = f"{'symbol':12} {'fresh_rows':>10} {'max_age_s':>10} {'pass_min':>8}  notes"
        print(hdr)
        print("-" * len(hdr))
        any_fail = False
        for sym_key in symbols:
            cur.execute(
                """
                SELECT COUNT(*) FROM trade_buckets
                WHERE exchange = ? AND symbol = ? AND session_id = ? AND last_ts >= ?
                """,
                ("binance", sym_key, session_id, fresh_cutoff),
            )
            fresh = cur.fetchone()[0]
            cur.execute(
                "SELECT MAX(last_ts) FROM trade_buckets WHERE exchange = ? AND symbol = ?",
                ("binance", sym_key),
            )
            mx_any = cur.fetchone()[0]
            age_s = (now - float(mx_any)) if mx_any else None
            passes = mx_any is not None and fresh >= min_levels
            if mx_any is None:
                notes = "no rows ever"
                any_fail = True
            elif fresh < min_levels:
                notes = "insufficient fresh buckets"
                any_fail = True
            elif age_s is not None and age_s > max_age:
                notes = "stale max_ts vs gate"
                any_fail = True
            else:
                notes = "ok"
            age_str = f"{age_s:.1f}" if age_s is not None else "-"
            print(f"{sym_key:12} {fresh:10} {age_str:>10} {str(passes):>8}  {notes}")
        con.close()
        print()
        print(f"Total CRYPTO_PAIRS symbols checked: {len(symbols)}")
        if not sample:
            print(
                "Action: Ensure athena.py is running with MICROSTRUCTURE_FEEDS_ENABLED true "
                "and the pair enabled in crypto pairs selection for micro feeds."
            )
        elif any_fail:
            print(
                "Some symbols failed freshness/min-levels. If Athena is running, subs may be demand-scoped - "
                "only symbols selected for scalp/micro WS get buckets."
            )
        return 0

    sym_key = str(args.symbol or "").replace("/", "").upper()
    if not sym_key:
        print("empty symbol", file=sys.stderr)
        con.close()
        return 2

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
    cur.execute(
        """
        SELECT COUNT(*) FROM trade_buckets
        WHERE exchange = ? AND symbol = ? AND session_id = ? AND last_ts >= ?
        """,
        ("binance", sym_key, session_id, fresh_cutoff),
    )
    fresh = cur.fetchone()[0]
    print(f"  rows with last_ts >= now-{max_age}s (fresh gate): {fresh}")
    need = min_levels
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
