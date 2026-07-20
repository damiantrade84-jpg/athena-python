"""Read-only probe: crypto funding/OI + XAU macro context coverage in backtest.

Loads the historical derivative series the backtest uses, then walks the
candle history bar-by-bar and checks how often each context helper returns
actual data vs None. If coverage is <50%, factor scoring is running on
degraded inputs for most of the walk-forward window.

Usage:
    python probe_bt_context.py [--pair BTC/USDT] [--bars 500]

Never writes to disk.
"""

from __future__ import annotations

import argparse
import sys
import importlib.util
from pathlib import Path
import pandas as pd


def load_athena():
    spec = importlib.util.spec_from_file_location("athena_main", "athena.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["athena_main"] = m
    spec.loader.exec_module(m)
    return m


def probe_crypto(pair: dict, sample_bars: int) -> None:
    from backtest_runner import _bt_crypto_funding_oi_for_bar
    from athena_runtime import rt

    d1 = rt().fetch_candles(pair, "D1", 750)
    h4 = rt().fetch_candles(pair, "H4", 4400)
    h1 = rt().fetch_candles(pair, "H1", 17600)
    if not d1:
        print(f"[{pair['display']}] no D1 data — skip")
        return

    from data_feeds import prepare_crypto_backtest_derivative_series
    funding_rows, oi_rows = prepare_crypto_backtest_derivative_series(
        pair, d1, h4, h1
    )
    print(f"\n=== {pair['display']} (crypto) ===")
    print(f"  Series loaded: funding_rows={len(funding_rows or [])}, oi_rows={len(oi_rows or [])}")

    if not funding_rows and not oi_rows:
        print("  → BOTH series empty. All backtest bars will get None/None.")
        return

    bars = d1[-sample_bars:] if len(d1) > sample_bars else d1
    n = len(bars)
    funding_hit = 0
    oi_hit = 0
    prev_ts = None
    for b in bars:
        entry_ts = pd.Timestamp(b["time"])
        fr, oi = _bt_crypto_funding_oi_for_bar(
            pair.get("type", "crypto"),
            funding_rows,
            oi_rows,
            entry_ts,
            prev_ts,
        )
        if fr is not None:
            funding_hit += 1
        if oi is not None:
            oi_hit += 1
        prev_ts = entry_ts

    print(f"  Bars probed: {n}")
    print(f"  Funding hits: {funding_hit}/{n} = {funding_hit/n:.1%}")
    print(f"  OI hits:      {oi_hit}/{n} = {oi_hit/n:.1%}")
    if funding_hit / n < 0.5 or oi_hit / n < 0.5:
        print("  VERDICT: coverage < 50% — factor scoring degraded.")
    else:
        print("  VERDICT: coverage OK.")


def probe_xau(pair: dict, sample_bars: int, athena_mod) -> None:
    from backtest_runner import _bt_gold_macro_context
    from athena_runtime import rt

    asset_h4 = rt().fetch_candles(pair, "H4", 4400)
    if not asset_h4:
        print(f"[{pair['display']}] no H4 data — skip")
        return

    # DXY proxy fetch — mirrors backtest_runner's setup
    dxy_pair = None
    for p in athena_mod.ALL_PAIRS:
        if p.get("display") == "DXY" or p.get("display") == "US Dollar Index":
            dxy_pair = p
            break
    if dxy_pair is None:
        print("  DXY pair not found in ALL_PAIRS — skipping XAU probe.")
        return
    dxy_h4 = rt().fetch_candles(dxy_pair, "H4", 4400)
    if not dxy_h4:
        print(f"  DXY H4 empty — macro context will be None for all bars.")
        return
    dxy_times = [pd.Timestamp(b["time"]) for b in dxy_h4]

    print(f"\n=== {pair['display']} (XAU macro) ===")
    print(f"  DXY H4 bars loaded: {len(dxy_h4)}")

    bars = asset_h4[-sample_bars:] if len(asset_h4) > sample_bars else asset_h4
    n = len(bars)
    hit = 0
    for i, b in enumerate(bars):
        cutoff = pd.Timestamp(b["time"])
        window = bars[: i + 1]
        ctx = _bt_gold_macro_context(pair, cutoff, window, dxy_h4, dxy_times)
        if ctx is not None:
            hit += 1

    print(f"  Bars probed: {n}")
    print(f"  Macro-ctx hits: {hit}/{n} = {hit/n:.1%}")
    if hit / n < 0.5:
        print("  VERDICT: coverage < 50% — XAU macro context degraded.")
    else:
        print("  VERDICT: coverage OK.")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pair", default="BTC/USDT", help="Display name to probe (default BTC/USDT)")
    p.add_argument("--bars", type=int, default=500, help="Sample last N bars (default 500)")
    p.add_argument("--xau", action="store_true", help="Also probe XAU/USD macro context")
    args = p.parse_args(argv)

    m = load_athena()
    pair = next((pr for pr in m.ALL_PAIRS if pr.get("display") == args.pair), None)
    if not pair:
        print(f"Pair {args.pair} not found in ALL_PAIRS", file=sys.stderr)
        return 2

    ptype = pair.get("type", "")
    if ptype == "crypto":
        probe_crypto(pair, args.bars)
    elif pair.get("display") == "XAU/USD" or args.xau:
        probe_xau(pair, args.bars, m)
    else:
        print(f"{args.pair}: type={ptype} has no BT-specific context helpers (forex/commodity/stock/index don't use these).")

    if args.xau and pair.get("display") != "XAU/USD":
        xau = next((pr for pr in m.ALL_PAIRS if pr.get("display") == "XAU/USD"), None)
        if xau:
            probe_xau(xau, args.bars, m)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
