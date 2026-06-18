"""Engine A v3 quant-scorer reachability probe — confluence vs threshold per group.

Walk-forward over the frozen 2026-05-30 candle layer. For each pair, at many H4
cut-offs, slice D1/H4/H1 to that timestamp and call the REAL score_pair(); collect
the confluence_score distribution per score_group and compare to the live
ENGINE_A_SCORE_GROUP_THRESHOLDS. Answers: "is the threshold reachable, and how
often?" — purely measurement, no config mutation, no orders, no backtest engine.

context=None here (subsystem feeds absent) -> carry/sentiment/intermarket/micro
neutral & excluded, isolating the price-component ceiling. Populated feeds can
only ADD aligned quality, so this is the conservative floor.

Usage:  python tmp/diag_v3_group_scores_20260618.py
"""
from __future__ import annotations

import bisect
import glob
import json
import os
import sys
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
os.chdir(REPO)

from engine_a_v3.quant_scorer import score_pair
from engine_a_v3.routing import route_specialist
from config import CONFIG

CANDLES = REPO / "data" / "frozen" / "2026-05-30" / "candles"

# filename-SYM -> (display, type)
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

STEP = 6           # take every 6th H4 bar as a cut-off
MAX_CUTOFFS = 160  # most recent N cut-offs per pair
MIN_ROWS = 60


def _load(sym: str, tf: str) -> list[dict] | None:
    hits = glob.glob(str(CANDLES / f"{sym}__{tf}__*.json"))
    if not hits:
        return None
    rows = json.load(open(hits[0]))
    rows = [r for r in rows if isinstance(r, dict) and r.get("time")]
    rows.sort(key=lambda r: r["time"])
    return rows


def _threshold(group: str) -> float:
    from engine_a_v3.profile import baseline_profile
    return float(baseline_profile(group, "swing").trade_threshold)


def main() -> int:
    groups: dict[str, dict] = {}
    for sym, (display, ptype) in PAIRS.items():
        d1, h4, h1 = _load(sym, "D1"), _load(sym, "H4"), _load(sym, "H1")
        if not (d1 and h4 and h1):
            print(f"[skip] {sym}: missing TF", flush=True)
            continue
        pair = {"display": display, "symbol": display, "type": ptype}
        route = route_specialist(pair)
        group = route.score_group
        d1_t = [r["time"] for r in d1]
        h1_t = [r["time"] for r in h1]
        cutoffs = list(range(MIN_ROWS, len(h4)))[::STEP][-MAX_CUTOFFS:]
        scored = 0
        for ci in cutoffs:
            t = h4[ci]["time"]
            h4w = h4[: ci + 1]
            d1w = d1[: bisect.bisect_right(d1_t, t)]
            h1w = h1[: bisect.bisect_right(h1_t, t)]
            if min(len(d1w), len(h4w), len(h1w)) < MIN_ROWS:
                continue
            try:
                q = score_pair(route, "swing", {"D1": d1w, "H4": h4w, "H1": h1w})
            except Exception as exc:
                print(f"[err] {display}@{t}: {exc!r}", flush=True)
                continue
            g = groups.setdefault(
                group, {"family": route.family, "conf": [], "dirs": {}, "pairs": set()}
            )
            g["conf"].append(q.confluence_score)
            g["dirs"][q.direction] = g["dirs"].get(q.direction, 0) + 1
            g["pairs"].add(display)
            scored += 1
        print(f"[ok] {display:12s} {group:20s} scored={scored}", flush=True)

    out = {"data_as_of": "2026-05-30", "context": "none(feeds absent->neutral)", "groups": {}}
    print("\n%-20s %5s %5s %6s %6s %6s %6s %s" % (
        "group", "n", "thr", "med", "p90", "max", ">=thr%", "dir(L/S/F)"))
    for group in sorted(groups):
        g = groups[group]
        conf = sorted(g["conf"])
        n = len(conf)
        if n == 0:
            continue
        thr = _threshold(group)
        p90 = conf[min(n - 1, int(0.9 * n))]
        pass_pct = round(100.0 * sum(1 for c in conf if c >= thr) / n, 1)
        d = g["dirs"]
        out["groups"][group] = {
            "family": g["family"], "n": n, "threshold": thr,
            "median": round(median(conf), 3), "p90": round(p90, 3),
            "max": round(conf[-1], 3), "pass_pct": pass_pct,
            "pairs": sorted(g["pairs"]),
            "dir": {"LONG": d.get("LONG", 0), "SHORT": d.get("SHORT", 0), "FLAT": d.get("FLAT", 0)},
        }
        print("%-20s %5d %5.2f %6.3f %6.3f %6.3f %6.1f  L%d/S%d/F%d" % (
            group, n, thr, round(median(conf), 3), round(p90, 3), round(conf[-1], 3),
            pass_pct, d.get("LONG", 0), d.get("SHORT", 0), d.get("FLAT", 0)))

    path = REPO / "tmp" / "diag_v3_group_scores_20260618.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
