"""Probe Bybit linear kline endpoint for each configured crypto symbol.

Mirrors the exact request shape used by data_feeds._fetch_bybit_klines and
athena._bybit_atr_for_levels so we can see which symbols actually return ATR.
"""
import sys
import time

sys.path.insert(0, ".")

import requests

from indicators import calc_atr  # noqa: E402

URL = "https://api.bybit.com/v5/market/kline"

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT", "DOGEUSDT",
    "AVAXUSDT", "LINKUSDT", "POLUSDT", "BNBUSDT", "DOTUSDT", "LTCUSDT",
    "SUIUSDT", "NEARUSDT", "APTUSDT", "INJUSDT", "RENDERUSDT", "AAVEUSDT",
    "ALGOUSDT", "ATOMUSDT", "BCHUSDT", "ETCUSDT", "TRXUSDT", "XLMUSDT",
    "UNIUSDT", "FILUSDT", "ICPUSDT", "HBARUSDT", "ARBUSDT", "OPUSDT",
    "SEIUSDT",
]

TFS = {"H1": "60", "H4": "240", "D1": "D"}


def fetch(sym: str, tf: str) -> tuple[bool, int, str]:
    params = {"category": "linear", "symbol": sym, "interval": TFS[tf], "limit": "120"}
    try:
        r = requests.get(URL, params=params, timeout=15)
    except Exception as e:
        return False, 0, f"EXC:{e!s}"
    if r.status_code != 200:
        return False, 0, f"HTTP{r.status_code}"
    try:
        data = r.json()
    except Exception as e:
        return False, 0, f"JSON:{e!s}"
    rc = int(data.get("retCode", -1))
    if rc != 0:
        return False, 0, f"retCode={rc}:{data.get('retMsg')!r}"
    rows = (data.get("result") or {}).get("list") or []
    return True, len(rows), ""


def atr_from_rows(rows: list[list]) -> float | None:
    if not rows or len(rows) < 20:
        return None
    rows = sorted(rows, key=lambda r: int(r[0]))
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    closes = [float(r[4]) for r in rows]
    series = calc_atr(highs, lows, closes, 14)
    for v in reversed(series or []):
        if v:
            return float(v)
    return None


def fetch_full(sym: str, tf: str):
    params = {"category": "linear", "symbol": sym, "interval": TFS[tf], "limit": "120"}
    r = requests.get(URL, params=params, timeout=15)
    if r.status_code != 200:
        return None, f"HTTP{r.status_code}"
    data = r.json()
    if int(data.get("retCode", -1)) != 0:
        return None, f"retCode={data.get('retCode')}:{data.get('retMsg')!r}"
    rows = (data.get("result") or {}).get("list") or []
    return atr_from_rows(rows), f"rows={len(rows)}"


print(f"{'SYMBOL':<12} {'D1_ATR':>14} {'H4_ATR':>14} {'H1_ATR':>14}  detail")
print("-" * 90)
for sym in SYMBOLS:
    line = [sym]
    detail = []
    for tf in ("D1", "H4", "H1"):
        atr, info = fetch_full(sym, tf)
        line.append(f"{atr:>14.6g}" if atr else f"{'NONE':>14}")
        if not atr:
            detail.append(f"{tf}:{info}")
        time.sleep(0.05)
    print(f"{line[0]:<12} {line[1]} {line[2]} {line[3]}  " + " ".join(detail))
