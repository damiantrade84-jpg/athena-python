"""cot_feed.py — CFTC Commitment of Traders (COT) positioning factor.

Downloads CFTC reports and provides net non-commercial / managed-money
positioning z-scores as a directional factor for:

  Forex:       All 17 pairs via CME currency futures legs
  Crypto:      BTC/USDT, ETH/USDT via CME Bitcoin/Ether futures
  Indices:     S&P 500, Nasdaq (SPY/QQQ) via E-mini futures
  Commodities: XAU/USD, XAG/USD via COMEX gold/silver (managed money)

Signal:  z > 0 = speculators net long → bullish base / asset
         z < 0 = speculators net short → bearish

Data sources (CFTC — free, no API key):
  Weekly current:     https://www.cftc.gov/dea/newcot/FinFutWk.txt
  Annual TXT history: https://www.cftc.gov/files/dea/history/fut_fin_txt_{YYYY}.zip
  Disaggregated:      https://www.cftc.gov/files/dea/history/fut_disagg_txt_{YYYY}.zip

Strategy: seed 2 years of history from annual ZIPs on first run,
          then update weekly from FinFutWk.txt (current week).

Usage:
    from cot_feed import get_cot_z, refresh_cot
    z = get_cot_z("EUR/USD")    # +1.4  → significantly net long EUR futures
    z = get_cot_z("BTC/USDT")  # −0.6  → moderately net short BTC CME futures
    z = get_cot_z("XAU/USD")   # +2.1  → very crowded long gold
    refresh_cot()               # force re-download
"""

import os
import csv
import sqlite3
import logging
import threading
import time
import datetime
import zipfile
from io import StringIO, BytesIO
from typing import Optional

import requests

log = logging.getLogger("sentinel")

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cot_cache.db")
_db_lock = threading.Lock()
_TIMEOUT = 30
_WEEKLY_TTL = 7 * 86400  # re-fetch weekly file at most once per week
_HISTORY_TTL = 30 * 86400  # re-fetch annual ZIPs at most once per month

# In-memory cache: asset_key → (z_score, fetched_at)
_mem_cache: dict = {}
_MEM_TTL = 6 * 3600

# ── CFTC URLs ─────────────────────────────────────────────────────────────────
_WEEKLY_FIN_URL = "https://www.cftc.gov/dea/newcot/FinFutWk.txt"
_HIST_FIN_URL = "https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip"
_HIST_DISAGG_URL = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip"

# ── Contract fragment matching (upper-case substring in CFTC contract name) ───
_CONTRACT_FRAGMENTS: dict[str, str] = {
    # Financial futures (fin files)
    "EUR": "EURO FX",
    "GBP": "BRITISH POUND",
    "JPY": "JAPANESE YEN",
    "AUD": "AUSTRALIAN DOLLAR",
    # CFTC contract naming uses the abbreviated "NZ DOLLAR".
    "NZD": "NZ DOLLAR",
    "CAD": "CANADIAN DOLLAR",
    "CHF": "SWISS FRANC",
    "MXN": "MEXICAN PESO",
    # CFTC contract naming uses the abbreviated "S AFRICAN RAND".
    "ZAR": "S AFRICAN RAND",
    "BTC": "BITCOIN",
    "ETH": "ETHER",
    "SP500": "E-MINI S&P 500",
    "NQ100": "E-MINI NASDAQ-100",
    # Disaggregated (managed money) — commodities
    "XAU": "GOLD",
    "XAG": "SILVER",
    "OIL": "CRUDE OIL, LIGHT SWEET",
    "NG": "NATURAL GAS",
    "HG": "COPPER",
    "PL": "PLATINUM",
}

_DISAGG_ASSETS = {"XAU", "XAG", "OIL", "NG", "HG", "PL"}

# ── Pair → COT formula ────────────────────────────────────────────────────────
_PAIR_FORMULA: dict[str, list[tuple[float, str]]] = {
    "EUR/USD": [(1.0, "EUR")],
    "GBP/USD": [(1.0, "GBP")],
    "USD/JPY": [(-1.0, "JPY")],
    "AUD/USD": [(1.0, "AUD")],
    "NZD/USD": [(1.0, "NZD")],
    "EUR/GBP": [(1.0, "EUR"), (-1.0, "GBP")],
    "USD/CAD": [(-1.0, "CAD")],
    "USD/CHF": [(-1.0, "CHF")],
    "EUR/JPY": [(1.0, "EUR"), (-1.0, "JPY")],
    "GBP/JPY": [(1.0, "GBP"), (-1.0, "JPY")],
    "AUD/JPY": [(1.0, "AUD"), (-1.0, "JPY")],
    "EUR/AUD": [(1.0, "EUR"), (-1.0, "AUD")],
    "GBP/AUD": [(1.0, "GBP"), (-1.0, "AUD")],
    "EUR/CHF": [(1.0, "EUR"), (-1.0, "CHF")],
    "USD/MXN": [(-1.0, "MXN")],
    "USD/ZAR": [(-1.0, "ZAR")],
    "USD/SGD": [],
    "BTC/USDT": [(1.0, "BTC")],
    "ETH/USDT": [(1.0, "ETH")],
    "SOL/USDT": [],
    "BNB/USDT": [],
    "XRP/USDT": [],
    "AVAX/USDT": [],
    "LINK/USDT": [],
    "ADA/USDT": [],
    "DOGE/USDT": [],
    "DOT/USDT": [],
    "POL/USDT": [],
    "SUI/USDT": [],
    "APT/USDT": [],
    "LTC/USDT": [],
    "NEAR/USDT": [],
    "INJ/USDT": [],
    "RENDER/USDT": [],
    "AAVE/USDT": [],
    "ALGO/USDT": [],
    "ATOM/USDT": [],
    "BCH/USDT": [],
    "ETC/USDT": [],
    "TRX/USDT": [],
    "XLM/USDT": [],
    "UNI/USDT": [],
    "FIL/USDT": [],
    "ICP/USDT": [],
    "HBAR/USDT": [],
    "ARB/USDT": [],
    "OP/USDT": [],
    "SEI/USDT": [],
    "S&P 500": [(1.0, "SP500")],
    "SPY": [(1.0, "SP500")],
    "NASDAQ-100": [(1.0, "NQ100")],
    "QQQ": [(1.0, "NQ100")],
    "Dow Jones": [(1.0, "SP500")],
    "UK100": [(1.0, "GBP")],
    "DAX 40": [(1.0, "EUR")],
    "XAU/USD": [(1.0, "XAU")],
    "XAG/USD": [(1.0, "XAG")],
    "WTI Oil": [(1.0, "OIL")],
    "Brent Oil": [(1.0, "OIL")],
    "Nat Gas": [(1.0, "NG")],
    "Copper": [(1.0, "HG")],
    "XPT/USD": [(1.0, "PL")],
    # ── US Stocks — S&P 500 / Nasdaq E-mini as macro risk proxy ──────────
    "AAPL": [(1.0, "SP500")],
    "TSLA": [(1.0, "SP500")],
    "NVDA": [(1.0, "NQ100")],
    "MSFT": [(1.0, "NQ100")],
    "AMZN": [(1.0, "NQ100")],
    "META": [(1.0, "NQ100")],
    "GOOG": [(1.0, "NQ100")],
    "JPM": [(1.0, "SP500")],
    "V": [(1.0, "SP500")],
    "XOM": [(1.0, "SP500")],
    "NFLX": [(1.0, "NQ100")],
    "AMD": [(1.0, "NQ100")],
    "CRM": [(1.0, "SP500")],
    "DIS": [(1.0, "SP500")],
    "BA": [(1.0, "SP500")],
    "COIN": [(1.0, "BTC")],
    "PYPL": [(1.0, "NQ100")],
    "INTC": [(1.0, "NQ100")],
    "UBER": [(1.0, "SP500")],
    "PLTR": [(1.0, "NQ100")],
    # ── ETFs ─────────────────────────────────────────────────────────────
    "GLD": [(1.0, "XAU")],
    "SLV": [(1.0, "XAG")],
    "IWM": [(1.0, "SP500")],
    "XLE": [(1.0, "OIL")],
    "USO": [(1.0, "OIL")],
    # ── Index currency proxies ────────────────────────────────────────────
    "ASX 200": [(1.0, "AUD")],
    "Nikkei 225": [(-1.0, "JPY")],
}


# ── SQLite ────────────────────────────────────────────────────────────────────


def _init_db():
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""
            CREATE TABLE IF NOT EXISTS cot_net (
                asset       TEXT    NOT NULL,
                report_date TEXT    NOT NULL,
                net_long    INTEGER NOT NULL,
                PRIMARY KEY (asset, report_date)
            )
        """)
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_cot ON cot_net (asset, report_date)"
        )
        con.execute("""
            CREATE TABLE IF NOT EXISTS cot_meta (
                source     TEXT PRIMARY KEY,
                last_fetch REAL NOT NULL
            )
        """)
        con.commit()
        con.close()


_init_db()


# ── CSV parsing ───────────────────────────────────────────────────────────────


def _parse_fin_csv(text: str) -> dict[str, dict[str, int]]:
    """Parse a financial futures CSV (with header) → {asset_key: {date: net}}."""
    fin_assets = {
        k: v for k, v in _CONTRACT_FRAGMENTS.items() if k not in _DISAGG_ASSETS
    }
    frag_list = sorted(fin_assets.items(), key=lambda x: len(x[1]), reverse=True)
    result: dict[str, dict[str, int]] = {k: {} for k in fin_assets}

    try:
        reader = csv.DictReader(StringIO(text))
        for row in reader:
            row = {k.strip().strip('"'): v.strip().strip('"') for k, v in row.items()}
            name = row.get("Market_and_Exchange_Names", "").upper()
            base = name.split(" - ")[0].strip()

            matched = None
            for key, frag in frag_list:
                if frag.upper() in base:
                    matched = key
                    break
            if not matched:
                continue

            date_str = row.get("Report_Date_as_YYYY-MM-DD", "").strip()
            if not date_str or len(date_str) != 10:
                continue

            try:
                # Annual ZIPs use TFF (Traders in Financial Futures) format:
                # Lev_Money = leveraged funds / hedge funds / CTAs (speculative positioning)
                long_pos = int(
                    row.get("Lev_Money_Positions_Long_All", "0").replace(",", "")
                )
                short_pos = int(
                    row.get("Lev_Money_Positions_Short_All", "0").replace(",", "")
                )
                net = long_pos - short_pos
            except Exception:
                continue

            result[matched][date_str] = net
    except Exception as e:
        log.warning(f"[COT] fin CSV parse error: {e}")
    return result


def _parse_disagg_csv(text: str) -> dict[str, dict[str, int]]:
    """Parse a disaggregated CSV (with header) → {asset_key: {date: net}}."""
    disagg = {k: v for k, v in _CONTRACT_FRAGMENTS.items() if k in _DISAGG_ASSETS}
    frag_list = sorted(disagg.items(), key=lambda x: len(x[1]), reverse=True)
    result: dict[str, dict[str, int]] = {k: {} for k in disagg}

    try:
        reader = csv.DictReader(StringIO(text))
        for row in reader:
            row = {k.strip().strip('"'): v.strip().strip('"') for k, v in row.items()}
            name = row.get("Market_and_Exchange_Names", "").upper()
            base = name.split(" - ")[0].strip()

            matched = None
            for key, frag in frag_list:
                if frag.upper() in base:
                    matched = key
                    break
            if not matched:
                continue

            date_str = row.get("Report_Date_as_YYYY-MM-DD", "").strip()
            if not date_str or len(date_str) != 10:
                continue

            try:
                long_pos = int(
                    row.get("M_Money_Positions_Long_All", "0").replace(",", "")
                )
                short_pos = int(
                    row.get("M_Money_Positions_Short_All", "0").replace(",", "")
                )
                net = long_pos - short_pos
            except Exception:
                continue

            result[matched][date_str] = net
    except Exception as e:
        log.warning(f"[COT] disagg CSV parse error: {e}")
    return result


def _parse_weekly_fin_no_header(text: str) -> dict[str, dict[str, int]]:
    """Parse FinFutWk.txt (no header, positional columns).

    Column layout (0-indexed, CSV):
      0: Market_and_Exchange_Names
      2: Report_Date_as_YYYY-MM-DD
      8: NonComm_Positions_Long_All
      9: NonComm_Positions_Short_All
    """
    fin_assets = {
        k: v for k, v in _CONTRACT_FRAGMENTS.items() if k not in _DISAGG_ASSETS
    }
    frag_list = sorted(fin_assets.items(), key=lambda x: len(x[1]), reverse=True)
    result: dict[str, dict[str, int]] = {k: {} for k in fin_assets}

    try:
        reader = csv.reader(StringIO(text))
        for row in reader:
            if len(row) < 10:
                continue
            name = row[0].strip().strip('"').upper()
            base = name.split(" - ")[0].strip()

            matched = None
            for key, frag in frag_list:
                if frag.upper() in base:
                    matched = key
                    break
            if not matched:
                continue

            date_str = row[2].strip().strip('"')
            if not date_str or len(date_str) != 10:
                continue

            try:
                # FinFutWk.txt is TFF format (no header), column layout:
                # 0=Name 1=YYMMDD 2=YYYY-MM-DD 3-6=codes 7=OI
                # 8-10=Dealer 11-13=AssetMgr 14-15=LevMoney(Long,Short) 16+=Other
                net = int(row[14].strip()) - int(row[15].strip())
            except Exception:
                continue

            result[matched][date_str] = net
    except Exception as e:
        log.warning(f"[COT] weekly parse error: {e}")
    return result


# ── DB helpers ────────────────────────────────────────────────────────────────


def _write_rows(data: dict[str, dict[str, int]]):
    rows = [
        (asset, date, net) for asset, dm in data.items() for date, net in dm.items()
    ]
    if not rows:
        return
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        con.executemany(
            "INSERT OR REPLACE INTO cot_net (asset, report_date, net_long) VALUES (?,?,?)",
            rows,
        )
        con.commit()
        con.close()


def _needs_refresh(source: str, ttl: float) -> bool:
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        row = con.execute(
            "SELECT last_fetch FROM cot_meta WHERE source=?", (source,)
        ).fetchone()
        con.close()
    return not row or (time.time() - row[0] > ttl)


def _mark_fetch(source: str):
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        con.execute(
            "INSERT OR REPLACE INTO cot_meta (source, last_fetch) VALUES (?,?)",
            (source, time.time()),
        )
        con.commit()
        con.close()


def _row_count(asset: str) -> int:
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        row = con.execute(
            "SELECT COUNT(*) FROM cot_net WHERE asset=?", (asset,)
        ).fetchone()
        con.close()
    return row[0] if row else 0


# ── Download helpers ──────────────────────────────────────────────────────────


def _fetch_zip_txt(url: str, inner_filename_hint: str = ".txt") -> Optional[str]:
    """Download a ZIP and return the text content of the first .txt file inside."""
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        zf = zipfile.ZipFile(BytesIO(resp.content))
        for name in zf.namelist():
            if name.lower().endswith(".txt"):
                return zf.read(name).decode("latin-1")
        return None
    except Exception as e:
        log.warning(f"[COT] ZIP fetch failed {url}: {e}")
        return None


def _seed_history(years: int = 2):
    """Download annual TXT ZIPs to seed 2 years of history."""
    current_year = datetime.date.today().year
    for year in range(current_year, current_year - years, -1):
        src = f"hist_fin_{year}"
        if not _needs_refresh(src, _HISTORY_TTL):
            continue
        text = _fetch_zip_txt(_HIST_FIN_URL.format(year=year))
        if text:
            data = _parse_fin_csv(text)
            total = sum(len(v) for v in data.values())
            _write_rows(data)
            _mark_fetch(src)
            log.info(f"[COT] fin history {year}: {total} rows cached")

        src_d = f"hist_disagg_{year}"
        if not _needs_refresh(src_d, _HISTORY_TTL):
            continue
        text = _fetch_zip_txt(_HIST_DISAGG_URL.format(year=year))
        if text:
            data = _parse_disagg_csv(text)
            total = sum(len(v) for v in data.values())
            _write_rows(data)
            _mark_fetch(src_d)
            log.info(f"[COT] disagg history {year}: {total} rows cached")


def _update_weekly():
    """Fetch the current week's financial futures data."""
    if not _needs_refresh("weekly_fin", _WEEKLY_TTL):
        return
    try:
        resp = requests.get(_WEEKLY_FIN_URL, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = _parse_weekly_fin_no_header(resp.text)
        total = sum(len(v) for v in data.values())
        if total:
            _write_rows(data)
            _mark_fetch("weekly_fin")
            log.info(f"[COT] weekly update: {total} current positions cached")
        else:
            _mark_fetch("weekly_fin")  # empty response — don't retry immediately
    except Exception as e:
        log.warning(f"[COT] weekly fetch failed: {e}")
        _mark_fetch("weekly_fin")  # failure cooldown — don't retry immediately


# ── Public refresh ────────────────────────────────────────────────────────────


def refresh_cot(force: bool = False):
    """Seed history (2 years) and fetch current week. Safe to call repeatedly."""
    if force:
        # Clear meta to force re-download
        with _db_lock:
            con = sqlite3.connect(_DB_PATH, timeout=15.0)
            con.execute("DELETE FROM cot_meta")
            con.commit()
            con.close()
    _seed_history(years=2)
    _update_weekly()


# ── Z-score computation ───────────────────────────────────────────────────────


def _get_net_series(asset: str, weeks: int = 104, as_of_date: str = None) -> list[int]:
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        if as_of_date:
            rows = con.execute(
                "SELECT net_long FROM cot_net WHERE asset=? AND report_date<=? ORDER BY report_date DESC LIMIT ?",
                (asset, as_of_date, weeks),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT net_long FROM cot_net WHERE asset=? ORDER BY report_date DESC LIMIT ?",
                (asset, weeks),
            ).fetchall()
        con.close()
    return [r[0] for r in reversed(rows)]


def _zscore(series: list[int], window: int = 52) -> Optional[float]:
    if len(series) < max(window // 2, 4):
        return None
    data = series[-window:]
    latest = data[-1]
    history = data[:-1]
    if len(history) < 3:
        return None
    mean = sum(history) / len(history)
    std = (sum((x - mean) ** 2 for x in history) / len(history)) ** 0.5
    if std <= 0:
        return None
    return max(-3.0, min(3.0, (latest - mean) / std))


def _asset_z(asset: str, as_of_date: str = None) -> Optional[float]:
    now = time.time()
    if not as_of_date:
        cached = _mem_cache.get(asset)
        if cached and now - cached[1] < _MEM_TTL:
            return cached[0]

    series = _get_net_series(asset, as_of_date=as_of_date)
    if len(series) < 4:
        # Not enough data yet — background seed hasn't completed.
        # Do NOT call refresh_cot() here: it blocks the scan thread for 30-60s
        # downloading CFTC ZIPs. Let the background COTSeed thread populate the DB.
        return None

    z = _zscore(series)
    if not as_of_date:
        _mem_cache[asset] = (z, now)
    return z


# ── Public API ────────────────────────────────────────────────────────────────


def get_cot_z(display: str, as_of_date: str = None) -> float:
    """Return COT net positioning z-score for a pair.

    Positive = speculators net long the base currency / asset (bullish signal).
    Returns 0.0 if no CFTC coverage or data unavailable.
    """
    formula = _PAIR_FORMULA.get(display, [])
    if not formula:
        return 0.0

    total, count = 0.0, 0
    for sign, key in formula:
        z = _asset_z(key, as_of_date=as_of_date)
        if z is not None:
            total += sign * z
            count += 1

    if count == 0 and formula:
        log.debug(
            f"[COT] {display}: formula has {len(formula)} legs but no data resolved. "
            f"Keys: {[k for _, k in formula]}"
        )
    elif count < len(formula):
        log.debug(
            f"[COT] {display}: only {count}/{len(formula)} legs resolved. "
            f"Result={round(total/count if count else 0, 3)}"
        )

    if count == 0:
        return 0.0

    result = total / count if len(formula) > 1 else total
    return round(max(-3.0, min(3.0, result)), 3)


def get_cot_net(display: str) -> Optional[dict]:
    """Return raw net position and z-score for display/diagnostic purposes."""
    formula = _PAIR_FORMULA.get(display, [])
    if not formula:
        return None
    results = {}
    for _, key in formula:
        series = _get_net_series(key, weeks=1)
        results[key] = {
            "net": series[-1] if series else None,
            "z": _asset_z(key),
            "weeks_of_data": _row_count(key),
        }
    return results


def seed_cot_background():
    """Trigger background download of COT history + current week. Call once at startup."""
    t = threading.Thread(target=refresh_cot, daemon=True, name="COTSeed")
    t.start()


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    refresh_cot(force="--force" in sys.argv)
    pairs = [
        "EUR/USD",
        "USD/JPY",
        "GBP/USD",
        "AUD/USD",
        "NZD/USD",
        "EUR/JPY",
        "GBP/JPY",
        "USD/CAD",
        "USD/CHF",
        "BTC/USDT",
        "ETH/USDT",
        "XAU/USD",
        "XAG/USD",
        "S&P 500",
        "Nasdaq",
    ]
    print("\nCOT z-scores (+ = net long = bullish base):")
    for p in pairs:
        z = get_cot_z(p)
        detail = get_cot_net(p) or {}
        weeks = max((v.get("weeks_of_data", 0) for v in detail.values()), default=0)
        bar = "█" * int(abs(z) * 4)
        print(
            f"  {p:18s}  z={z:+.3f}  [{weeks}wk]  {'▲ LONG' if z > 0.5 else '▼ SHORT' if z < -0.5 else '  neutral'}  {bar}"
        )
