"""Engine A v3 entry-direction diagnosis (rescaled scorer, 2026-06-18).

forex/crypto showed negative directional edge in validation. This stratifies the
SAME trades (run_v3_backtest already tags each with regime, efficiencyAtEntry, and
a REAL SL/TP-flipped reverseResultR) to decide WHICH fix, if any, is supported:

  * regime gate  -> forward edge positive ONLY in 'trending', negative elsewhere
  * direction flip -> reverse edge >> forward edge across regimes (deployable flip)
  * efficient / unfixable -> both forward AND reverse weak everywhere

Measurement only. Usage: python tmp/diag_v3_direction_20260618.py
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
COSTS = dict(spread_bps=2.0, commission_bps=1.0, slippage_bps=1.0, swap_bps_per_day=0.0)
KEEP = {"D1": 400, "H4": 700, "H1": 2800}
EFF_BINS = [(0.0, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.6), (0.6, 1.01)]


def _load(sym: str, tf: str) -> list[dict]:
    hits = glob.glob(str(CANDLES / f"{sym}__{tf}__*.json"))
    if not hits:
        return []
    rows = [r for r in json.load(open(hits[0])) if isinstance(r, dict) and r.get("time")]
    rows.sort(key=lambda r: r["time"])
    return rows[-KEEP[tf]:]


def _sqn(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    sd = stdev(xs)
    return round(fmean(xs) / sd * sqrt(len(xs)), 2) if sd > 0 else 0.0


def _line(xs: list[float]) -> str:
    if not xs:
        return "   n=0"
    wr = 100.0 * sum(1 for x in xs if x > 0) / len(xs)
    return "n=%-3d WR=%4.0f%% expR=%+.3f sqn=%+.2f" % (len(xs), wr, fmean(xs), _sqn(xs))


def main() -> int:
    registry = demo_unvalidated_registry()
    fam_trades: dict[str, list[dict]] = {}
    for sym, (display, ptype) in PAIRS.items():
        candles = {tf: _load(sym, tf) for tf in ("D1", "H4", "H1")}
        if min(len(candles[tf]) for tf in ("D1", "H4", "H1")) < 80:
            continue
        pair = {"display": display, "symbol": display, "type": ptype}
        fam = route_specialist(pair).family
        try:
            r = run_v3_backtest(pair, candles, horizon="swing", registry=registry, **COSTS)
        except Exception as exc:
            print(f"[err] {display}: {exc!r}", flush=True)
            continue
        fam_trades.setdefault(fam, []).extend(r.get("trades") or [])
        print(f"[ok] {display:12s} {fam:10s} trades={len(r.get('trades') or [])}", flush=True)

    out: dict = {"scorer": "rescaled", "threshold": 2.0, "families": {}}
    for fam in sorted(fam_trades):
        trades = fam_trades[fam]
        fwd = [float(t["resultR"]) for t in trades if "resultR" in t]
        rev = [float(t["reverseResultR"]) for t in trades if t.get("reverseResultR") is not None]
        print(f"\n================ {fam.upper()}  (n={len(trades)}) ================")
        print(f"  FORWARD : {_line(fwd)}")
        print(f"  REVERSE : {_line(rev)}")
        print("  -- by regime (forward / reverse) --")
        reg: dict[str, dict] = {}
        for label in ("trending", "neutral", "ranging", "unknown"):
            f = [float(t["resultR"]) for t in trades if str(t.get("regime")) == label]
            v = [float(t["reverseResultR"]) for t in trades
                 if str(t.get("regime")) == label and t.get("reverseResultR") is not None]
            if f:
                print(f"    {label:9s} FWD {_line(f)}")
                print(f"    {' '*9} REV {_line(v)}")
                reg[label] = {"fwd_expR": round(fmean(f), 4), "fwd_sqn": _sqn(f),
                              "rev_expR": round(fmean(v), 4) if v else None, "n": len(f)}
        print("  -- by efficiencyAtEntry (forward) --")
        eff: dict[str, dict] = {}
        for lo, hi in EFF_BINS:
            f = [float(t["resultR"]) for t in trades
                 if lo <= float(t.get("efficiencyAtEntry") or 0) < hi]
            if f:
                print(f"    eff[{lo:.1f}-{hi:.1f}) {_line(f)}")
                eff[f"{lo:.1f}-{hi:.1f}"] = {"expR": round(fmean(f), 4), "sqn": _sqn(f), "n": len(f)}
        out["families"][fam] = {
            "n": len(trades),
            "fwd_expR": round(fmean(fwd), 4) if fwd else None, "fwd_sqn": _sqn(fwd),
            "rev_expR": round(fmean(rev), 4) if rev else None, "rev_sqn": _sqn(rev),
            "by_regime": reg, "by_efficiency": eff,
        }

    path = REPO / "tmp" / "diag_v3_direction_20260618.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
