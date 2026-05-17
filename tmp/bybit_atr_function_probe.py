"""Run the actual _bybit_atr_for_levels function used in production for each crypto pair.

This bypasses scoring and directly confirms which pairs return None vs a float.
"""
import sys
sys.path.insert(0, ".")

import logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s %(message)s")

from data_feeds import _fetch_bybit_klines  # noqa: E402
from indicators import calc_atr  # noqa: E402

# Mirror athena._bybit_atr_for_levels exactly (without importing athena.py monolith)
STYLE_TF = {"scalp": "H1", "intraday": "H4", "swing": "D1"}

def normalize_style(style):
    s = str(style or "").lower()
    if s in ("scalp", "intraday", "swing"):
        return s
    return "swing"

def bybit_atr_for_levels(pair, style):
    symbol = (pair.get("symbol") or pair.get("display") or "").replace("/", "").upper()
    if not symbol:
        return None, "empty_symbol"
    rs = normalize_style(style)
    tf = STYLE_TF.get(rs, "D1")
    candles = _fetch_bybit_klines(symbol, tf, 120, end_ms=None)
    if not candles:
        return None, f"no_candles({tf})"
    if len(candles) < 20:
        return None, f"thin_candles({len(candles)})"
    series = calc_atr(
        [c["high"] for c in candles],
        [c["low"] for c in candles],
        [c["close"] for c in candles],
        14,
    )
    for v in reversed(series or []):
        if v:
            return float(v), f"ok({tf})"
    return None, "atr_series_empty"

PAIRS = [
    ("BTCUSDT", "BTC/USDT"), ("ETHUSDT", "ETH/USDT"), ("XRPUSDT", "XRP/USDT"),
    ("SOLUSDT", "SOL/USDT"), ("ADAUSDT", "ADA/USDT"), ("DOGEUSDT", "DOGE/USDT"),
    ("AVAXUSDT", "AVAX/USDT"), ("LINKUSDT", "LINK/USDT"), ("POLUSDT", "POL/USDT"),
    ("BNBUSDT", "BNB/USDT"), ("DOTUSDT", "DOT/USDT"), ("LTCUSDT", "LTC/USDT"),
    ("SUIUSDT", "SUI/USDT"), ("NEARUSDT", "NEAR/USDT"), ("APTUSDT", "APT/USDT"),
    ("INJUSDT", "INJ/USDT"), ("RENDERUSDT", "RENDER/USDT"), ("AAVEUSDT", "AAVE/USDT"),
    ("ALGOUSDT", "ALGO/USDT"), ("ATOMUSDT", "ATOM/USDT"), ("BCHUSDT", "BCH/USDT"),
    ("ETCUSDT", "ETC/USDT"), ("TRXUSDT", "TRX/USDT"), ("XLMUSDT", "XLM/USDT"),
    ("UNIUSDT", "UNI/USDT"), ("FILUSDT", "FIL/USDT"), ("ICPUSDT", "ICP/USDT"),
    ("HBARUSDT", "HBAR/USDT"), ("ARBUSDT", "ARB/USDT"), ("OPUSDT", "OP/USDT"),
    ("SEIUSDT", "SEI/USDT"),
]
STYLES = ["scalp", "intraday", "swing"]

print(f"{'PAIR':<14} {'scalp(H1)':>16} {'intraday(H4)':>20} {'swing(D1)':>20}")
print("-" * 80)
miss = 0
total = 0
for sym, disp in PAIRS:
    pair = {"symbol": sym, "display": disp, "type": "crypto", "source": "binance"}
    cells = []
    for st in STYLES:
        atr, why = bybit_atr_for_levels(pair, st)
        total += 1
        if atr is None:
            miss += 1
            cells.append(f"NONE/{why}")
        else:
            cells.append(f"{atr:.6g}")
    print(f"{disp:<14} {cells[0]:>16} {cells[1]:>20} {cells[2]:>20}")

print("-" * 80)
print(f"Misses: {miss}/{total}")
