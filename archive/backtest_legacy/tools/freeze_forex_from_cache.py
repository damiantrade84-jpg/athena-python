"""Export forex pairs from backtest candle cache into frozen manifest (no athena.py)."""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

PAIRS = {
    "AUD/USD": {"display": "AUD/USD", "symbol": "AUDUSD", "source": "mt5", "type": "forex"},
    "NZD/USD": {"display": "NZD/USD", "symbol": "NZDUSD", "source": "mt5", "type": "forex"},
    "USD/CAD": {"display": "USD/CAD", "symbol": "USDCAD", "source": "mt5", "type": "forex"},
    "USD/CHF": {"display": "USD/CHF", "symbol": "USDCHF", "source": "mt5", "type": "forex"},
    "EUR/GBP": {"display": "EUR/GBP", "symbol": "EURGBP", "source": "mt5", "type": "forex"},
    "USD/MXN": {"display": "USD/MXN", "symbol": "USDMXN", "source": "mt5", "type": "forex"},
}
MIN_BARS = {"D1": 230, "H4": 500, "H1": 500}
LIMITS = {"D1": 750, "H4": 4400, "H1": 17600}


def _cache_key(pair: dict, provider: str) -> str:
    import backtest_candle_cache as cache

    return cache._cache_key(pair, provider)


def _read_cache(pair: dict, tf: str, provider: str = "mt5") -> list[dict]:
    from runtime_paths import resolve_backtest_candle_cache_db_path

    db = resolve_backtest_candle_cache_db_path()
    if not db.exists():
        return []
    key = _cache_key(pair, provider)
    with sqlite3.connect(str(db), timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT bar_time, open, high, low, close, volume
            FROM backtest_candles
            WHERE cache_key=? AND timeframe=? AND provider=?
            ORDER BY bar_time ASC
            LIMIT ?
            """,
            (key, tf, provider, LIMITS[tf]),
        ).fetchall()
    return [
        {
            "time": r["bar_time"],
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "vol": float(r["volume"] or 0),
        }
        for r in rows
    ]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default="2026-05-30")
    parser.add_argument("--pairs", default=",".join(PAIRS))
    args = parser.parse_args()

    os.environ["ATHENA_FREEZE_BUILDING"] = "1"
    os.chdir(REPO)

    from frozen_data import write_frozen_candles

    displays = [p.strip() for p in args.pairs.split(",") if p.strip()]
    for display in displays:
        pair = PAIRS.get(display)
        if not pair:
            print(f"[skip] unknown pair {display}")
            continue
        for tf in ("D1", "H4", "H1"):
            candles = _read_cache(pair, tf)
            need = MIN_BARS[tf]
            if len(candles) < need:
                print(f"[miss] {display} {tf}: cache rows={len(candles)} need={need}")
                continue
            rec = write_frozen_candles(args.as_of, pair, tf, "mt5", candles[-LIMITS[tf] :])
            print(f"[ok] {display} {tf} rows={rec['row_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
