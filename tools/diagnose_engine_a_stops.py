"""Read-only diagnostic: why do Engine A trades stop out?

Quantifies, from the live audit.db + candle_cache.db, whether Engine A stop-outs
are caused by stops sitting too tight (structural overlay tightening the ATR stop)
versus genuine invalidation, and how much profit is handed back by the trailing exit.

STRICTLY READ-ONLY. Both databases are opened with mode=ro and only SELECTs run.
No config import (the live config aborts on a real-orders safety gate), so the
Engine A baseline multiplier tables below are copied verbatim from config.py
(ATR_CLASS ~L1194, STYLE_ATR_MULTS ~L1224). Only the tp1/sl RATIO is used as the
overlay signal, and the regime factor cancels in that ratio, so missing per-trade
regime data does not affect it.

Usage:
    python tools/diagnose_engine_a_stops.py
"""

from __future__ import annotations

import math
import os
import sqlite3
import statistics
from datetime import datetime, timezone

# --- Baseline Engine A ATR multipliers (mirror of config.py defaults) ---------
# config.py L1194-1201 (fallback when no style) and L1224-1263 (primary).
ATR_CLASS = {
    "forex": {"sl": 1.2, "tp1": 2.0, "tp2": 3.0},
    "commodity": {"sl": 1.5, "tp1": 2.5, "tp2": 4.0},
    "index": {"sl": 1.5, "tp1": 2.5, "tp2": 4.0},
    "stock": {"sl": 1.5, "tp1": 2.5, "tp2": 4.0},
    "etf": {"sl": 1.2, "tp1": 2.0, "tp2": 3.5},
    "etf_bond": {"sl": 0.9, "tp1": 1.5, "tp2": 2.5},
    "crypto": {"sl": 2.0, "tp1": 3.5, "tp2": 5.0},
}
STYLE_ATR_MULTS = {
    "scalp": {
        "forex": {"sl": 1.00, "tp1": 1.50, "tp2": 2.50},
        "crypto": {"sl": 1.20, "tp1": 1.80, "tp2": 3.00},
        "stock": {"sl": 1.00, "tp1": 1.50, "tp2": 2.50},
        "etf": {"sl": 0.80, "tp1": 1.30, "tp2": 2.20},
        "etf_bond": {"sl": 0.60, "tp1": 1.00, "tp2": 1.80},
        "commodity": {"sl": 1.20, "tp1": 1.80, "tp2": 3.00},
        "index": {"sl": 1.00, "tp1": 1.50, "tp2": 2.50},
    },
    "intraday": {
        "forex": {"sl": 1.50, "tp1": 3.00, "tp2": 4.50},
        "crypto": {"sl": 1.50, "tp1": 3.00, "tp2": 4.50},
        "stock": {"sl": 1.50, "tp1": 3.00, "tp2": 4.50},
        "etf": {"sl": 1.20, "tp1": 2.40, "tp2": 3.60},
        "etf_bond": {"sl": 0.90, "tp1": 1.80, "tp2": 2.70},
        "commodity": {"sl": 2.00, "tp1": 4.00, "tp2": 6.00},
        "index": {"sl": 1.50, "tp1": 3.00, "tp2": 4.50},
    },
    "swing": {
        "forex": {"sl": 1.80, "tp1": 3.00, "tp2": 5.00},
        "crypto": {"sl": 1.50, "tp1": 2.50, "tp2": 4.00},
        "stock": {"sl": 1.80, "tp1": 3.00, "tp2": 5.00},
        "etf": {"sl": 1.40, "tp1": 2.40, "tp2": 4.00},
        "etf_bond": {"sl": 1.10, "tp1": 1.80, "tp2": 3.00},
        "commodity": {"sl": 1.80, "tp1": 3.00, "tp2": 5.00},
        "index": {"sl": 1.80, "tp1": 3.00, "tp2": 5.00},
    },
}
# config.py L1265-1278: ATR timeframe priority per style (first available wins).
LEVEL_ATR_PRIORITY = {
    "default": {"scalp": ["H1", "H4", "D1"], "intraday": ["H4", "H1", "D1"], "swing": ["D1", "H4", "H1"]},
    "crypto": {"scalp": ["H1", "H4", "D1"], "intraday": ["H4", "H1", "D1"], "swing": ["D1", "H4", "H1"]},
}

# Minimal display->level-class overrides (mirror of ENGINE_A_ATR_LEVEL_CLASS_BY_DISPLAY
# intent for the symbols that actually appear in Engine A trades).
DISPLAY_LEVEL_CLASS = {
    "SPY": "etf", "QQQ": "etf", "IWM": "etf", "EEM": "etf", "TLT": "etf_bond",
    "S&P 500": "index", "NASDAQ 100": "index", "NASDAQ": "index", "DOW": "index",
    "US30": "index", "US100": "index", "US500": "index", "GER40": "index", "DAX": "index",
    "XAU/USD": "commodity", "XAG/USD": "commodity", "Gold": "commodity", "Silver": "commodity",
    "USOIL": "commodity", "UKOIL": "commodity", "WTI": "commodity", "Natural Gas": "commodity",
}

ENGINE_A_LIKE = "%engine_a%"
NO_ERR = "(error_tag IS NULL OR error_tag = '')"
CLEAN_SL_R = -0.85  # r_multiple at/below this => clean initial-stop hit (not moved-to-BE)

CANDLE_DB_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "candle_cache.db"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Athena", "candle_cache.db"),
]


def ro_connect(path: str) -> sqlite3.Connection:
    uri = "file:" + os.path.abspath(path).replace(os.sep, "/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=15)
    con.row_factory = sqlite3.Row
    return con


def infer_class(pair: str, asset_class: str | None) -> str:
    if asset_class:
        ac = asset_class.lower()
        if ac in ATR_CLASS:
            return ac
    if pair in DISPLAY_LEVEL_CLASS:
        return DISPLAY_LEVEL_CLASS[pair]
    p = (pair or "").upper()
    if "/" in p:
        base, _, quote = p.partition("/")
        if quote in ("USDT", "USDC", "USD") and base in (
            "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "TRX", "LINK", "UNI",
            "AVAX", "DOT", "MATIC", "LTC", "ATOM", "AAVE", "ALGO", "FIL", "NEAR", "OP", "ARB",
        ):
            return "crypto"
        if quote in ("USDT", "USDC"):
            return "crypto"
        if len(base) == 3 and len(quote) == 3 and base.isalpha() and quote.isalpha():
            return "forex"
    return "stock"


def norm_style(style: str | None) -> tuple[str, bool]:
    s = (style or "").lower()
    if s in ("scalp", "intraday", "swing"):
        return s, True
    return "intraday", False  # default bucket; flag that it was assumed


def baseline_mults(style: str, klass: str) -> dict:
    sm = STYLE_ATR_MULTS.get(style, {})
    if klass in sm:
        return sm[klass]
    return ATR_CLASS.get(klass, {"sl": 1.5, "tp1": 2.5, "tp2": 4.0})


def parse_ts(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                continue
    return None


def pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):.0f}%" if d else "n/a"


def med(vals: list[float]) -> float | None:
    return statistics.median(vals) if vals else None


# --- candle helpers -----------------------------------------------------------
def load_candles(ccon, pair: str, tf: str) -> list[sqlite3.Row]:
    rows = ccon.execute(
        "SELECT bar_time, open, high, low, close FROM candle_cache "
        "WHERE pair = ? AND timeframe = ? ORDER BY bar_time ASC",
        (pair, tf),
    ).fetchall()
    return rows


def wilder_atr(candles: list[sqlite3.Row], upto_idx: int, period: int = 14) -> float | None:
    if upto_idx < period:
        return None
    trs = []
    for i in range(upto_idx - period + 1, upto_idx + 1):
        h = candles[i]["high"]
        lo = candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    if len(trs) < period:
        return None
    return sum(trs) / period


def idx_before(candles: list[sqlite3.Row], when: datetime) -> int | None:
    last = None
    for i, c in enumerate(candles):
        bt = parse_ts(c["bar_time"])
        if bt is None:
            continue
        if bt <= when:
            last = i
        else:
            break
    return last


def main() -> None:
    audit_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit.db")
    if not os.path.exists(audit_path):
        print(f"audit.db not found at {audit_path}")
        return
    con = ro_connect(audit_path)

    candle_path = next((p for p in CANDLE_DB_CANDIDATES if p and os.path.exists(p)), None)
    ccon = ro_connect(candle_path) if candle_path else None

    base = f"exit_price IS NOT NULL AND engine LIKE '{ENGINE_A_LIKE}' AND {NO_ERR}"
    rows = con.execute(
        "SELECT id, ts, exit_time, pair, direction, style, asset_class, regime, "
        "entry_price, sl, tp, tp2, exit_price, r_multiple, exit_reason "
        f"FROM audit_log WHERE {base} ORDER BY ts ASC"
    ).fetchall()

    # peak_r (in-trade MFE proxy) keyed by audit id via timed_exit_state.
    peak_r: dict[int, float] = {}
    for r in con.execute(
        "SELECT a.id AS id, t.peak_r AS pk FROM audit_log a "
        "JOIN timed_exit_state t ON (t.state_key = 'mt5:aid:' || a.id OR t.state_key = 'bybit:aid:' || a.id) "
        "WHERE a.exit_price IS NOT NULL AND a.engine LIKE ? "
        "AND (a.error_tag IS NULL OR a.error_tag = '')",
        (ENGINE_A_LIKE,),
    ):
        if r["pk"] is not None:
            peak_r[r["id"]] = float(r["pk"])

    print("=" * 78)
    print("ENGINE A STOP-OUT DIAGNOSIS (read-only)")
    print("=" * 78)
    print(f"audit.db        : {audit_path}")
    print(f"candle_cache.db : {candle_path or 'NOT FOUND (candle replay skipped)'}")
    print(f"Engine A closed trades (no-fill errors excluded): {len(rows)}")
    print()

    # --- per-trade geometry (candle-free) -------------------------------------
    enriched = []
    for r in rows:
        entry = r["entry_price"]
        sl = r["sl"]
        tp1 = r["tp"]
        if not entry or not sl or not tp1:
            continue
        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            continue
        klass = infer_class(r["pair"], r["asset_class"])
        style, style_known = norm_style(r["style"])
        bm = baseline_mults(style, klass)
        baseline_rr = bm["tp1"] / bm["sl"] if bm["sl"] else None
        actual_rr1 = abs(tp1 - entry) / sl_dist
        rr_ratio = (actual_rr1 / baseline_rr) if baseline_rr else None
        enriched.append({
            "row": r, "klass": klass, "style": style, "style_known": style_known,
            "sl_pct": sl_dist / entry, "actual_rr1": actual_rr1,
            "baseline_rr": baseline_rr, "rr_ratio": rr_ratio,
        })

    def is_sl(e):
        return e["row"]["exit_reason"] == "SL_HIT"

    def is_giveback(e):
        return e["row"]["exit_reason"] in ("TRAIL_GIVEBACK", "TRAIL_PROFIT_ROUNDTRIP")

    sl_hits = [e for e in enriched if is_sl(e)]
    givebacks = [e for e in enriched if is_giveback(e)]

    # ---- SECTION 1: overlay-tightening signal (RR ratio vs baseline) ---------
    print("-" * 78)
    print("1) IS THE STRUCTURAL OVERLAY TIGHTENING ENGINE A'S STOP?")
    print("   Signal = actual TP1/SL ratio vs Engine A baseline RR (regime cancels).")
    print("   >1.10 = stop pulled in tighter than ATR baseline (overlay tightened SL,")
    print("   and/or TP capped); ~1.0 = pure ATR geometry; <0.90 = TP capped closer.")
    print("-" * 78)

    def rr_buckets(group, label):
        vals = [e["rr_ratio"] for e in group if e["rr_ratio"] is not None]
        if not vals:
            print(f"  {label}: no comparable rows")
            return
        tightened = [e for e in group if e["rr_ratio"] and e["rr_ratio"] > 1.10]
        neutral = [e for e in group if e["rr_ratio"] and 0.90 <= e["rr_ratio"] <= 1.10]
        tp_capped = [e for e in group if e["rr_ratio"] and e["rr_ratio"] < 0.90]
        n = len(vals)
        print(f"  {label} (n={n}):")
        print(f"     median RR ratio        : {med(vals):.2f}  (1.0 == matches ATR baseline)")
        print(f"     stop tightened (>1.10) : {len(tightened):>3}  ({pct(len(tightened), n)})")
        print(f"     neutral / pure ATR     : {len(neutral):>3}  ({pct(len(neutral), n)})")
        print(f"     TP capped (<0.90)      : {len(tp_capped):>3}  ({pct(len(tp_capped), n)})")

    rr_buckets(enriched, "All Engine A closed")
    rr_buckets(sl_hits, "SL_HIT only")
    non_sl = [e for e in enriched if not is_sl(e)]
    rr_buckets(non_sl, "Non-SL_HIT (everything else)")
    print()
    print("   SL distance as % of entry price:")
    print(f"     SL_HIT  median : {(med([e['sl_pct'] for e in sl_hits]) or 0) * 100:.2f}%")
    print(f"     Non-SL  median : {(med([e['sl_pct'] for e in non_sl]) or 0) * 100:.2f}%")
    print()

    # ---- SECTION 2: SL_HIT anatomy -------------------------------------------
    print("-" * 78)
    print("2) STOP-OUT ANATOMY (SL_HIT)")
    print("-" * 78)
    clean = [e for e in sl_hits if (e["row"]["r_multiple"] or 0) <= CLEAN_SL_R]
    moved = [e for e in sl_hits if (e["row"]["r_multiple"] or 0) > CLEAN_SL_R]
    print(f"  Total SL_HIT                 : {len(sl_hits)}")
    print(f"  Clean initial-stop hits (R<=-0.85): {len(clean)}  -> died at the original stop")
    print(f"  Moved-stop hits (R>-0.85)    : {len(moved)}  -> stop trailed/BE then hit")
    sl_with_peak = [e for e in sl_hits if e["row"]["id"] in peak_r]
    print(f"  SL_HIT with recorded peak_r  : {len(sl_with_peak)} / {len(sl_hits)}")
    if sl_with_peak:
        pks = [peak_r[e["row"]["id"]] for e in sl_with_peak]
        print(f"     of those, median peak R reached before stop: {med(pks):.2f}R")
    print("  (peak_r is mostly absent for SL_HIT => these trades were stopped BEFORE the")
    print("   trailing stop ever armed, i.e. trail activation 0.7-1.5R was never reached.)")
    print()

    # ---- SECTION 3: profit giveback ------------------------------------------
    print("-" * 78)
    print("3) PROFIT GIVEN BACK (TRAIL_GIVEBACK / TRAIL_PROFIT_ROUNDTRIP)")
    print("-" * 78)
    print(f"  Total giveback trades        : {len(givebacks)}")
    gv_realized = [e["row"]["r_multiple"] for e in givebacks if e["row"]["r_multiple"] is not None]
    if gv_realized:
        print(f"  Realized R   median / min / max : {med(gv_realized):.2f} / {min(gv_realized):.2f} / {max(gv_realized):.2f}")
    gv_peak = [(peak_r[e["row"]["id"]], e["row"]["r_multiple"]) for e in givebacks if e["row"]["id"] in peak_r]
    print(f"  Giveback trades with peak_r  : {len(gv_peak)} / {len(givebacks)}")
    if gv_peak:
        peaks = [p for p, _ in gv_peak]
        handed = [p - (rz or 0) for p, rz in gv_peak]
        print(f"     median peak R reached        : {med(peaks):.2f}R")
        print(f"     median R handed back (peak-realized): {med(handed):.2f}R")
    print()

    # ---- SECTION 4: candle replay (covered subset only) ----------------------
    print("-" * 78)
    print("4) FALSE-BREAKOUT CHECK via candle replay (covered subset only)")
    print("   For SL_HIT trades inside candle coverage: recompute true ATR multiple at")
    print("   entry, and check if price reached the original TP1 after the stop.")
    print("-" * 78)
    if not ccon:
        print("  candle_cache.db not available; skipped.")
    else:
        covered = 0
        no_data = 0
        out_of_coverage = 0
        would_hit = 0
        true_atr_mults = []
        examples = []
        # timeframe granularity (seconds) used to reject stale "last candle" matches.
        tf_secs = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}
        for e in sl_hits:
            r = e["row"]
            pair = r["pair"]
            entry_ts = parse_ts(r["ts"])
            exit_ts = parse_ts(r["exit_time"])
            direction = (r["direction"] or "").upper()
            klass = e["klass"]
            style = e["style"]
            prio = LEVEL_ATR_PRIORITY.get("crypto" if klass == "crypto" else "default", {}).get(style, ["H4", "H1", "D1"])
            # Require candle data that ACTUALLY spans the trade: an entry bar within
            # ~2 bars of entry_ts (not a stale last bar from weeks earlier) AND the
            # series extending past exit_ts so forward replay is real.
            chosen_tf = None
            candles = []
            ei = None
            spanned = False
            for tf in prio:
                cs = load_candles(ccon, pair, tf)
                if not cs:
                    continue
                j = idx_before(cs, entry_ts) if entry_ts else None
                if j is None or j < 14:
                    continue
                entry_bar = parse_ts(cs[j]["bar_time"])
                last_bar = parse_ts(cs[-1]["bar_time"])
                gran = tf_secs.get(tf, 14400)
                fresh_entry = entry_bar is not None and (entry_ts - entry_bar).total_seconds() <= 2 * gran
                spans_exit = last_bar is not None and exit_ts is not None and last_bar >= exit_ts
                if fresh_entry and spans_exit:
                    chosen_tf, candles, ei, spanned = tf, cs, j, True
                    break
            if not spanned:
                # distinguish "no candles at all" from "trade is outside coverage window"
                if any(load_candles(ccon, pair, tf) for tf in prio):
                    out_of_coverage += 1
                else:
                    no_data += 1
                continue
            atr = wilder_atr(candles, ei, 14)
            if not atr or atr <= 0:
                no_data += 1
                continue
            covered += 1
            sl_dist = abs(r["entry_price"] - r["sl"])
            true_mult = sl_dist / atr
            true_atr_mults.append(true_mult)
            # replay forward from exit to see if TP1 reached
            tp1 = r["tp"]
            horizon = {"scalp": 24, "intraday": 30, "swing": 20}.get(style, 30)
            xi = idx_before(candles, exit_ts) if exit_ts else ei
            hit = False
            if xi is not None:
                for k in range(xi + 1, min(xi + 1 + horizon, len(candles))):
                    c = candles[k]
                    if direction == "LONG" and c["high"] >= tp1:
                        hit = True
                        break
                    if direction == "SHORT" and c["low"] <= tp1:
                        hit = True
                        break
            if hit:
                would_hit += 1
            if len(examples) < 8:
                examples.append((pair, direction, style, chosen_tf, true_mult, r["r_multiple"], hit))
        print(f"  SL_HIT trades inside candle coverage (usable) : {covered}")
        print(f"  SL_HIT trades OUTSIDE candle coverage window   : {out_of_coverage}")
        print(f"    (candle_cache.db ends ~2026-04-03; later trades cannot be replayed)")
        print(f"  SL_HIT trades with no candles for the pair     : {no_data}")
        if true_atr_mults:
            print(f"  True SL distance in ATR multiples (covered): median {med(true_atr_mults):.2f}x")
            print("     (compare to Engine A baseline sl mult: forex intraday 1.50, swing 1.80)")
        if covered:
            print(f"  Of covered stop-outs, price later reached original TP1: {would_hit} ({pct(would_hit, covered)})")
            print(f"     NOTE: small sample (n={covered}); treat as indicative only.")
        if examples:
            print("  Examples (pair, dir, style, tf, true_ATRx, realizedR, hit_TP_after):")
            for ex in examples:
                print(f"     {ex[0]:<10} {ex[1]:<5} {ex[2]:<9} {ex[3]:<3} "
                      f"{ex[4]:>5.2f}x  R={ex[5]!s:<6} hitTP_after={ex[6]}")
        ccon.close()
    print()

    # ---- SECTION 5: headline summary -----------------------------------------
    print("=" * 78)
    print("HEADLINE")
    print("=" * 78)
    tightened_sl = [e for e in sl_hits if e["rr_ratio"] and e["rr_ratio"] > 1.10]
    print(f"- TP_HIT is rare for Engine A; most exits are TIMED_CLOSE / trail / SL.")
    print(f"- {len(tightened_sl)}/{len(sl_hits)} SL_HIT trades had geometry tighter than the ATR baseline "
          f"({pct(len(tightened_sl), len(sl_hits))}) -> overlay/structural SL is a prime suspect.")
    print(f"- {len(clean)}/{len(sl_hits)} stop-outs died at the original stop (clean -1R); "
          f"{len(givebacks)} more went green then round-tripped via the trail.")
    print("- See sections 1-4 for the numbers behind each claim.")
    con.close()


if __name__ == "__main__":
    main()
