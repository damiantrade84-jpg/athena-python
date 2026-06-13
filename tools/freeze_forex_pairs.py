"""Freeze forex pairs using backtest_runner cache only (no athena.py import)."""
from __future__ import annotations

import os
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
LIMITS = {"D1": 750, "H4": 4400, "H1": 17600}
MIN_BARS = {"D1": 230, "H4": 500, "H1": 500}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default="2026-05-30")
    parser.add_argument("--pairs", default=",".join(PAIRS))
    args = parser.parse_args()

    os.environ["ATHENA_FREEZE_BUILDING"] = "1"
    os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")
    os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")
    os.environ.pop("BACKTEST_DATA_AS_OF", None)
    os.chdir(REPO)

    import backtest_runner as br
    from frozen_data import write_frozen_candles

    displays = [p.strip() for p in args.pairs.split(",") if p.strip()]
    for display in displays:
        pair = PAIRS.get(display)
        if not pair:
            print(f"[skip] unknown pair {display}")
            continue
        for tf in ("D1", "H4", "H1"):
            print(f"[FREEZE] {display} {tf} ...", flush=True)
            candles = br._bt_cached_fetch(
                pair,
                tf,
                LIMITS[tf],
                lambda lim, _tf=tf, _pair=pair: br._rt().fetch_candles(_pair, _tf, lim),
                provider="mt5",
                min_bars=MIN_BARS[tf],
            )
            rec = write_frozen_candles(args.as_of, pair, tf, "mt5", candles)
            print(f"  rows={rec['row_count']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
