"""Engine A v3 edge validation on the RESCALED scorer + uniform 2.0 threshold.

For each frozen pair, run the self-contained run_v3_backtest() (which drives the
live evaluate_engine_a_v3 -> rescaled score_pair + baseline 2.0 profile), forcing
demo-unvalidated qualification so every threshold-passing signal trades. Pool
trades per score_group and report n / winRate / expectancyR / SQN / profitFactor,
plus REVERSE SQN (negated results) — reverse SQN >> forward SQN = anti-prediction
(direction is wrong), per the standing forex/crypto negative-edge finding.

Measurement only: no config mutation, no orders, no promotion writes.
Usage:  python tmp/diag_v3_edge_rescaled_20260618.py
"""
from __future__ import annotations

import glob
import json
import os
import sys
from math import sqrt
from pathlib import Path
from statistics import fmean, stdev

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
os.chdir(REPO)

from engine_a_v3.backtest import run_v3_backtest
from engine_a_v3.promotion import demo_unvalidated_registry
from engine_a_v3.routing import route_specialist

CANDLES = REPO / "data" / "frozen" / "2026-05-30" / "candles"

PAIRS = {
    "EUR_USD": ("EUR/USD", "forex"), "GBP_USD": ("GBP/USD", "forex"),
    "USD_CHF": ("USD/CHF", "forex"), "USD_JPY": ("USD/JPY", "forex"),
    "GBP_JPY": ("GBP/JPY", "forex"),
    "BTC_USDT": ("BTC/USDT", "crypto"), "ETH_USDT": ("ETH/USDT", "crypto"),
    "SOL_USDT": ("SOL/USDT", "crypto"), "DOGE_USDT": ("DOGE/USDT", "crypto"),
    "XRP_USDT": ("XRP/USDT", "crypto"),
    "XAU_USD": ("XAU/USD", "commodity"), "XAG_USD": ("XAG/USD", "commodity"),
    "WTI_Oil": ("WTI OIL", "commodity"),
    "DAX_40": ("DAX 40", "index"), "Dow_Jones": ("DOW JONES", "index"),
    "NASDAQ-100": ("NASDAQ-100", "index"), "S_P_500": ("S&P 500", "index"),
    "AAPL": ("AAPL", "stock"), "MSFT": ("MSFT", "stock"),
    "NVDA": ("NVDA", "stock"), "TSLA": ("TSLA", "stock"),
}

# Modest uniform cost model (bps). Edge read; reverse-SQN is cost-insensitive.
COSTS = dict(spread_bps=2.0, commission_bps=1.0, slippage_bps=1.0, swap_bps_per_day=0.0)
# Truncation to bound run_v3_backtest's per-bar prefix rebuild (O(n^2)).
KEEP = {"D1": 400, "H4": 700, "H1": 2800}


def _load(sym: str, tf: str) -> list[dict]:
    hits = glob.glob(str(CANDLES / f"{sym}__{tf}__*.json"))
    if not hits:
        return []
    rows = [r for r in json.load(open(hits[0])) if isinstance(r, dict) and r.get("time")]
    rows.sort(key=lambda r: r["time"])
    return rows[-KEEP[tf]:]


def _stats(results: list[float]) -> dict:
    n = len(results)
    if n == 0:
        return {"n": 0}
    wins = [r for r in results if r > 0]
    losses = [r for r in results if r <= 0]
    exp = fmean(results)
    sd = stdev(results) if n > 1 else 0.0
    rev = [-r for r in results]
    rexp = fmean(rev)
    rsd = stdev(rev) if n > 1 else 0.0
    return {
        "n": n,
        "winRate": round(len(wins) / n * 100, 1),
        "expectancyR": round(exp, 4),
        "sqn": round(exp / sd * sqrt(n), 3) if sd > 0 else 0.0,
        "profitFactor": round(sum(wins) / abs(sum(losses)), 3) if losses and sum(losses) != 0 else None,
        "totalR": round(sum(results), 2),
        "reverse_sqn": round(rexp / rsd * sqrt(n), 3) if rsd > 0 else 0.0,
    }


def main() -> int:
    registry = demo_unvalidated_registry()
    by_group: dict[str, list[float]] = {}
    group_family: dict[str, str] = {}
    for sym, (display, ptype) in PAIRS.items():
        candles = {tf: _load(sym, tf) for tf in ("D1", "H4", "H1")}
        if min(len(candles[tf]) for tf in ("D1", "H4", "H1")) < 80:
            print(f"[skip] {display}: insufficient", flush=True)
            continue
        pair = {"display": display, "symbol": display, "type": ptype}
        route = route_specialist(pair)
        group_family[route.score_group] = route.family
        try:
            r = run_v3_backtest(pair, candles, horizon="swing", registry=registry, **COSTS)
        except Exception as exc:
            print(f"[err] {display}: {exc!r}", flush=True)
            continue
        rr = [float(t["resultR"]) for t in (r.get("trades") or []) if "resultR" in t]
        by_group.setdefault(route.score_group, []).extend(rr)
        print(f"[ok] {display:12s} {route.score_group:20s} trades={len(rr):3d} sqn={r.get('sqn')}", flush=True)

    out = {"data_as_of": "2026-05-30", "scorer": "rescaled_aligned_quality", "threshold": 2.0,
           "costs_bps": COSTS, "groups": {}}
    print("\n%-20s %4s %6s %8s %6s %6s %9s" % (
        "group", "n", "WR%", "expR", "sqn", "PF", "rev_sqn"))
    for group in sorted(by_group):
        s = _stats(by_group[group])
        s["family"] = group_family.get(group)
        out["groups"][group] = s
        if s["n"] == 0:
            print("%-20s %4d  (no trades)" % (group, 0)); continue
        print("%-20s %4d %6.1f %8.4f %6.2f %6s %9.2f" % (
            group, s["n"], s["winRate"], s["expectancyR"], s["sqn"],
            str(s["profitFactor"]), s["reverse_sqn"]))

    # Family-level pooling for a higher-level read.
    by_family: dict[str, list[float]] = {}
    for group, rr in by_group.items():
        by_family.setdefault(group_family.get(group, "?"), []).extend(rr)
    print("\n--- pooled by family ---")
    print("%-12s %4s %6s %8s %6s %9s" % ("family", "n", "WR%", "expR", "sqn", "rev_sqn"))
    out["families"] = {}
    for fam in sorted(by_family):
        s = _stats(by_family[fam])
        out["families"][fam] = s
        if s["n"]:
            print("%-12s %4d %6.1f %8.4f %6.2f %9.2f" % (
                fam, s["n"], s["winRate"], s["expectancyR"], s["sqn"], s["reverse_sqn"]))

    path = REPO / "tmp" / "diag_v3_edge_rescaled_20260618.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
