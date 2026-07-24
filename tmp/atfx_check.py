"""ATFX broker diagnostics (read-only, standalone — does NOT import repo config).

Reads config.yaml + config.local.yaml directly for the handful of keys needed,
attaches to the pinned ATFX MT5 terminal, and reports:
  1) server clock offset (raw tick.time vs local UTC)
  2) H4/D1 bar grid alignment
  3) live spread vs configured execution spread caps for every enabled MT5 pair
Never prints credentials.
"""
from __future__ import annotations

import datetime as dt
import time

import yaml
import MetaTrader5 as mt5

CFG_DIR = "C:/dev/athena-python"

with open(f"{CFG_DIR}/config.yaml", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)
with open(f"{CFG_DIR}/config.local.yaml", encoding="utf-8") as f:
    local = yaml.safe_load(f) or {}


def get(key, default=None):
    return local.get(key, cfg.get(key, default))


path = get("MT5_PATH")
if not mt5.initialize(path=path, timeout=int(get("MT5_INIT_TIMEOUT_MS", 60000))):
    print("FATAL: mt5.initialize failed:", mt5.last_error())
    raise SystemExit(1)

ti = mt5.terminal_info()
ai = mt5.account_info()
print(f"terminal connected={ti.connected if ti else '?'} trade_allowed={ti.trade_allowed if ti else '?'}")
print(f"account server={ai.server if ai else '?'} (login redacted)")

disabled = {str(p).strip().upper() for p in (get("MT5_DISABLED_PAIRS") or [])}
overrides = get("MT5_SYMBOL_OVERRIDES") or {}
universe = [d for d in overrides if str(d).strip().upper() not in disabled]

_IDX = {"S&P 500", "NASDAQ-100", "DOW JONES", "DAX 40", "UK100", "NIKKEI 225",
        "HANG SENG", "ASX 200", "EU50", "US30", "US2000", "HK50", "AUS200", "JP225"}
_CMD = {"XAU/USD", "XAG/USD", "WTI OIL", "BRENT OIL", "NAT GAS"}
_ETF = {"GLD", "GDX", "EEM", "USO", "XLE", "XLF"}


def guess_type(display: str) -> str:
    d = display.strip().upper()
    if d in _IDX:
        return "index"
    if d in _CMD:
        return "commodity"
    if d in _ETF:
        return "etf"
    if len(d) == 7 and d[3] == "/" and d[:3].isalpha() and d[4:].isalpha():
        return "forex"
    return "stock"


def cap_for(display: str, ptype: str):
    key = display.replace("/", "").replace(" ", "").strip().upper()
    by_symbol = get("MAX_EXECUTION_SPREAD_PCT_BY_SYMBOL") or {}
    for cfg_key, raw in by_symbol.items():
        if str(cfg_key).replace("/", "").replace(" ", "").strip().upper() == key:
            if isinstance(raw, (int, float)) and raw > 0:
                return float(raw), f"symbol:{cfg_key}"
            break
    by_type = get("MAX_EXECUTION_SPREAD_PCT") or {}
    raw = by_type.get(ptype)
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw), f"class:{ptype}"
    return None, "none"


def utc(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


print("\n=== 1) SERVER CLOCK OFFSET (raw tick.time vs local UTC) ===")
now = time.time()
offset_samples = []
for disp in ("EUR/USD", "XAU/USD", "GBP/JPY"):
    sym = overrides.get(disp)
    if not sym or not mt5.symbol_select(sym, True):
        print(f"  {disp}: symbol_select failed for {sym!r}")
        continue
    t = mt5.symbol_info_tick(sym)
    if t is None or t.time == 0:
        print(f"  {disp}: no tick")
        continue
    off = t.time - now
    offset_samples.append(off)
    print(f"  {disp} ({sym}): tick.time={utc(t.time)} local_utc={utc(now)} "
          f"offset={off:+.0f}s ({off/3600:+.2f}h) naive_age={max(0.0, now - t.time):.1f}s")

cfg_off_h = float(get("MT5_BROKER_UTC_OFFSET", 0) or 0)
if offset_samples:
    med = sorted(offset_samples)[len(offset_samples) // 2]
    verdict = "MATCH" if abs(med / 3600 - cfg_off_h) < 0.3 else "** MISMATCH **"
    print(f"  => measured offset ~{med/3600:+.2f}h ; CONFIG MT5_BROKER_UTC_OFFSET={cfg_off_h:+.0f}h {verdict}")

print("\n=== 2) BAR GRID ALIGNMENT (EUR/USD) ===")
sym = overrides.get("EUR/USD")
mt5.symbol_select(sym, True)
for tf_name, tf, mod in (("M15", mt5.TIMEFRAME_M15, None), ("H1", mt5.TIMEFRAME_H1, None),
                         ("H4", mt5.TIMEFRAME_H4, 4), ("D1", mt5.TIMEFRAME_D1, None)):
    rates = mt5.copy_rates_from_pos(sym, tf, 0, 4)
    if rates is None or len(rates) == 0:
        print(f"  {tf_name}: no bars")
        continue
    rows = []
    for r in rates[-3:]:
        d = dt.datetime.fromtimestamp(int(r["time"]), dt.timezone.utc)
        extra = f" hour%4={d.hour % 4}" if mod else ""
        rows.append(f"{d:%m-%d %H:%M}Z{extra}")
    print(f"  {tf_name}: last bars: {' | '.join(rows)}")
print("  (Pepperstone-style GMT+3 grid: H4 hour%4==1, D1 opens 21:00Z; "
      f"configured FOREX_H4_RESAMPLE_OFFSET_HOURS={get('FOREX_H4_RESAMPLE_OFFSET_HOURS')})")

print("\n=== 3) LIVE SPREAD vs CONFIGURED CAP (all enabled MT5 pairs) ===")
print(f"  {'display':<14} {'mt5sym':<12} {'type':<10} {'spread%':>9} {'cap%':>8} {'src':<16} {'ratio':>6}  verdict")
for disp in sorted(universe):
    sym = overrides.get(disp)
    if not sym:
        print(f"  {disp:<14} {'(no map)':<12}")
        continue
    if not mt5.symbol_select(sym, True):
        print(f"  {disp:<14} {sym:<12} select-fail")
        continue
    t = mt5.symbol_info_tick(sym)
    if t is None or t.bid <= 0 or t.ask <= 0:
        print(f"  {disp:<14} {sym:<12} no-tick (market closed?)")
        continue
    ptype = guess_type(disp)
    mid = (t.ask + t.bid) / 2.0
    sp = (t.ask - t.bid) / mid if mid > 0 and t.ask >= t.bid else None
    cap, src = cap_for(disp, ptype)
    if sp is None:
        print(f"  {disp:<14} {sym:<12} {ptype:<10} spread n/a")
        continue
    ratio = (sp / cap) if cap else float("nan")
    verdict = ""
    if cap is None:
        verdict = "no-cap"
    elif sp > cap:
        verdict = "** OVER CAP (would block) **"
    elif ratio > 0.7:
        verdict = "near-cap"
    print(f"  {disp:<14} {sym:<12} {ptype:<10} {sp*100:>8.4f}% {((cap or 0)*100):>7.4f}% {src:<16} {ratio:>6.2f}  {verdict}")

mt5.shutdown()
print("\ndone.")
