"""Verify the entry-efficiency guard: gate OFF (=baseline) vs gate ON, per family.
Measurement only. Usage: python tmp/verify_efficiency_gate_20260618.py
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

import config
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
COSTS = dict(spread_bps=2.0, commission_bps=1.0, slippage_bps=1.0, swap_bps_per_day=0.0)
KEEP = {"D1": 400, "H4": 700, "H1": 2800}


def _load(sym, tf):
    hits = glob.glob(str(CANDLES / f"{sym}__{tf}__*.json"))
    if not hits:
        return []
    rows = [r for r in json.load(open(hits[0])) if isinstance(r, dict) and r.get("time")]
    rows.sort(key=lambda r: r["time"])
    return rows[-KEEP[tf]:]


def _sqn(xs):
    if len(xs) < 2:
        return 0.0
    sd = stdev(xs)
    return round(fmean(xs) / sd * sqrt(len(xs)), 2) if sd > 0 else 0.0


def run() -> dict[str, list[float]]:
    reg = demo_unvalidated_registry()
    fam: dict[str, list[float]] = {}
    for sym, (display, ptype) in PAIRS.items():
        c = {tf: _load(sym, tf) for tf in ("D1", "H4", "H1")}
        if min(len(c[tf]) for tf in ("D1", "H4", "H1")) < 80:
            continue
        pair = {"display": display, "symbol": display, "type": ptype}
        f = route_specialist(pair).family
        r = run_v3_backtest(pair, c, horizon="swing", registry=reg, **COSTS)
        fam.setdefault(f, []).extend(float(t["resultR"]) for t in (r.get("trades") or []))
    return fam


def main() -> int:
    # Sweep max_efficiency for commodity+index to see whether the gate bites.
    for max_eff in (None, 0.001, 0.1, 0.15, 0.2, 0.3):
        if max_eff is None:
            config.CONFIG["ENGINE_A_V3_ENTRY_EFFICIENCY_GATE"] = {"ENABLED": False}
            tag = "OFF"
        else:
            config.CONFIG["ENGINE_A_V3_ENTRY_EFFICIENCY_GATE"] = {
                "ENABLED": True, "max_efficiency": max_eff, "window": 20,
                "families": ["commodity", "index"],
            }
            tag = f"max={max_eff}"
        fam = run()
        for f in ("commodity", "index"):
            xs = fam.get(f, [])
            print("%-10s %-9s n=%-3d expR=%+.3f sqn=%+.2f" % (
                f, tag, len(xs), fmean(xs) if xs else 0, _sqn(xs)), flush=True)
        print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
