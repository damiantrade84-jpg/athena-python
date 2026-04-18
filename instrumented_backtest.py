"""Run Engine A backtests across a representative pair set and dump per-trade
factor breakdowns to a JSONL sidecar for offline analysis.

Does NOT write to audit.db. Does NOT change scoring. Requires the additive
`factors` / `factor_weights` fields in backtest_runner.py trades.append sites.

Usage:
    python instrumented_backtest.py [--out factor_dump.jsonl] [--style intraday]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def load_pairs():
    import importlib.util
    spec = importlib.util.spec_from_file_location("athena_mod", Path("athena.py").resolve())
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ALL_PAIRS


PAIR_UNIVERSE = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT",
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF",
    "XAU/USD", "XAG/USD",
    "SPX500", "NAS100",
]


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="factor_dump.jsonl")
    p.add_argument("--style", default="intraday", choices=["intraday", "swing", "scalp"])
    p.add_argument("--pairs", nargs="*", default=None)
    args = p.parse_args(argv)

    from backtest_runner import backtest_pair

    all_pairs = load_pairs()
    by_display = {p["display"]: p for p in all_pairs}
    wanted = args.pairs or PAIR_UNIVERSE

    out_path = Path(args.out)
    total_trades = 0
    summary = []

    with out_path.open("w", encoding="utf-8") as fout:
        for disp in wanted:
            pair = by_display.get(disp)
            if not pair:
                print(f"[skip] {disp} not in ALL_PAIRS")
                continue
            t0 = time.time()
            try:
                res = backtest_pair(pair, style=args.style, validation_mode="standard")
            except Exception as e:
                print(f"[err] {disp}: {e}")
                continue
            if "error" in res:
                print(f"[err] {disp}: {res['error']}")
                continue
            trades = res.get("trades") or []
            n_with_factors = 0
            for t in trades:
                factors = t.get("factors") or {}
                if not factors:
                    continue
                n_with_factors += 1
                fout.write(json.dumps({
                    "pair": disp,
                    "asset_class": pair.get("type"),
                    "style": args.style,
                    "date": t.get("date"),
                    "direction": t.get("direction"),
                    "score": t.get("score"),
                    "resultR": t.get("resultR"),
                    "outcome": t.get("outcome"),
                    "regime": t.get("regime"),
                    "factors": factors,
                    "factor_weights": t.get("factor_weights") or {},
                }) + "\n")
            dt = time.time() - t0
            wr = res.get("win_rate") or res.get("winRate")
            summary.append((disp, len(trades), n_with_factors, wr, dt))
            print(f"[ok]  {disp:12s} trades={len(trades):4d} factored={n_with_factors:4d} WR={wr} dt={dt:.1f}s")
            total_trades += n_with_factors

    print("\n=== SUMMARY ===")
    for disp, n, nf, wr, dt in summary:
        print(f"  {disp:12s} trades={n:4d} factored={nf:4d} WR={wr}")
    print(f"\nTotal factor-populated trades written: {total_trades}")
    print(f"Output: {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
