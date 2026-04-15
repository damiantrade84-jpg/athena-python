"""Audit EODHD intraday/EOD volume coverage for Athena non-forex/non-crypto symbols.

Read-only probe. It does not modify Athena routing, config, caches, or DBs.
Writes a JSON report to tests/_artifacts/eodhd_intraday_volume_audit.json.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUT_PATH = ROOT / "tests" / "_artifacts" / "eodhd_intraday_volume_audit.json"

DEFAULT_SYMBOLS = [
    {"display": "AAPL", "type": "stock", "daily": "AAPL.US", "intraday": "AAPL.US"},
    {"display": "SPY", "type": "stock", "daily": "SPY.US", "intraday": "SPY.US"},
    {"display": "QQQ", "type": "stock", "daily": "QQQ.US", "intraday": "QQQ.US"},
    {"display": "Gold futures", "type": "commodity", "daily": "GC", "intraday": "GC"},
    {"display": "Silver futures", "type": "commodity", "daily": "SI", "intraday": "SI"},
    {"display": "WTI futures", "type": "commodity", "daily": "CL", "intraday": "CL"},
    {"display": "Brent futures", "type": "commodity", "daily": "BZ", "intraday": "BZ"},
    {"display": "Nat Gas", "type": "commodity", "daily": "NG", "intraday": "NG"},
    {"display": "Copper", "type": "commodity", "daily": "HG", "intraday": "HG"},
    {"display": "XAU/USD", "type": "commodity", "daily": "XAUUSD.FOREX", "intraday": "XAUUSD.FOREX"},
    {"display": "Nasdaq index", "type": "index", "daily": "IXIC", "intraday": "IXIC.INDX"},
    {"display": "S&P 500 index", "type": "index", "daily": "GSPC.INDX", "intraday": "GSPC.INDX"},
    {"display": "Dow index", "type": "index", "daily": "DJI.INDX", "intraday": "DJI.INDX"},
]

INTERVALS = ["1m", "5m", "1h", "D1"]

FOREX_SYMBOLS = [
    {"display": "EUR/USD", "type": "forex", "daily": "EURUSD.FOREX", "intraday": "EURUSD.FOREX"},
    {"display": "GBP/USD", "type": "forex", "daily": "GBPUSD.FOREX", "intraday": "GBPUSD.FOREX"},
    {"display": "USD/JPY", "type": "forex", "daily": "USDJPY.FOREX", "intraday": "USDJPY.FOREX"},
    {"display": "AUD/USD", "type": "forex", "daily": "AUDUSD.FOREX", "intraday": "AUDUSD.FOREX"},
    {"display": "USD/CHF", "type": "forex", "daily": "USDCHF.FOREX", "intraday": "USDCHF.FOREX"},
    {"display": "USD/MXN", "type": "forex", "daily": "USDMXN.FOREX", "intraday": "USDMXN.FOREX"},
    {"display": "XAU/USD", "type": "commodity", "daily": "XAUUSD.FOREX", "intraday": "XAUUSD.FOREX"},
    {"display": "XAG/USD", "type": "commodity", "daily": "XAGUSD.FOREX", "intraday": "XAGUSD.FOREX"},
]


def _volume_stats(rows: list[dict[str, Any]], interval: str) -> dict[str, Any]:
    volumes = []
    timestamps = []
    for row in rows or []:
        raw_vol = row.get("volume")
        try:
            volumes.append(float(raw_vol or 0))
        except (TypeError, ValueError):
            volumes.append(0.0)
        timestamps.append(row.get("datetime") or row.get("date"))
    nonzero = [v for v in volumes if v > 0]
    count = len(rows or [])
    return {
        "bars": count,
        "nonzero_volume_bars": len(nonzero),
        "nonzero_volume_pct": round((len(nonzero) / count * 100.0), 2) if count else 0.0,
        "latest_time": timestamps[-1] if timestamps else None,
        "latest_volume": volumes[-1] if volumes else None,
        "max_volume": max(volumes) if volumes else None,
        "volume_field_present": any("volume" in r for r in rows or []),
        "interval": interval,
    }


def _fetch_intraday(session: requests.Session, key: str, ticker: str, interval: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    days = 2 if interval == "1m" else 7 if interval == "5m" else 30
    params = {
        "api_token": key,
        "fmt": "json",
        "from": int((now - timedelta(days=days)).timestamp()),
        "to": int(now.timestamp()),
        "interval": interval,
    }
    url = f"https://eodhd.com/api/intraday/{ticker}"
    out = {"ticker": ticker, "status": None, "endpoint": "/api/intraday"}
    try:
        resp = session.get(url, params=params, timeout=30)
    except requests.RequestException as exc:
        out["error"] = f"request failed: {exc.__class__.__name__}"
        return out
    out["status"] = resp.status_code
    if resp.status_code != 200:
        out["error"] = resp.text[:300]
        return out
    try:
        rows = resp.json()
    except ValueError as exc:
        out["error"] = f"invalid json: {exc}"
        return out
    if not isinstance(rows, list):
        out["error"] = f"unexpected payload type: {type(rows).__name__}"
        out["payload_preview"] = str(rows)[:300]
        return out
    out.update(_volume_stats(rows, interval))
    return out


def _fetch_daily(session: requests.Session, key: str, ticker: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    params = {
        "api_token": key,
        "fmt": "json",
        "period": "d",
        "from": (now - timedelta(days=90)).strftime("%Y-%m-%d"),
        "to": now.strftime("%Y-%m-%d"),
    }
    url = f"https://eodhd.com/api/eod/{ticker}"
    out = {"ticker": ticker, "status": None, "endpoint": "/api/eod"}
    try:
        resp = session.get(url, params=params, timeout=30)
    except requests.RequestException as exc:
        out["error"] = f"request failed: {exc.__class__.__name__}"
        return out
    out["status"] = resp.status_code
    if resp.status_code != 200:
        out["error"] = resp.text[:300]
        return out
    try:
        rows = resp.json()
    except ValueError as exc:
        out["error"] = f"invalid json: {exc}"
        return out
    if not isinstance(rows, list):
        out["error"] = f"unexpected payload type: {type(rows).__name__}"
        out["payload_preview"] = str(rows)[:300]
        return out
    out.update(_volume_stats(rows, "D1"))
    return out


def _mt5_comparison(display: str) -> dict[str, Any]:
    try:
        import mt5_executor
        mt5 = mt5_executor._get_mt5()
        if mt5 is None:
            return {"available": False, "detail": "MetaTrader5 package unavailable"}
        if not mt5_executor.mt5_connect():
            return {"available": False, "detail": "MT5 not connected"}
        mt5_symbol = mt5_executor.mt5_map_symbol(display)
        if not mt5_symbol:
            return {"available": False, "detail": "no MT5 symbol mapping"}
        mt5.symbol_select(mt5_symbol, True)
        tf_map = {"1m": mt5.TIMEFRAME_M1, "5m": mt5.TIMEFRAME_M5, "1h": mt5.TIMEFRAME_H1}
        out = {"available": True, "symbol": mt5_symbol, "intervals": {}}
        for interval, mt5_tf in tf_map.items():
            rates = mt5.copy_rates_from_pos(mt5_symbol, mt5_tf, 0, 200)
            rows = []
            if rates is not None:
                for r in rates:
                    rows.append(
                        {
                            "datetime": datetime.fromtimestamp(int(r["time"]), tz=timezone.utc).isoformat(),
                            "volume": float(r["tick_volume"] or 0),
                        }
                    )
            out["intervals"][interval] = _volume_stats(rows, interval)
        return out
    except Exception as exc:
        return {"available": False, "detail": f"{exc.__class__.__name__}: {exc}"}


def _duka_comparison(display: str) -> dict[str, Any]:
    try:
        from duka_volume import DUKA_SYMBOL_MAP, get_forex_volume_bars

        duka_symbol = DUKA_SYMBOL_MAP.get(display)
        if not duka_symbol:
            return {"available": False, "detail": "no Dukascopy mapping"}
        out = {"available": True, "symbol": duka_symbol, "intervals": {}}
        for tf in ("H1", "H4"):
            bars = get_forex_volume_bars(display, tf=tf, days=30, blocking_fetch=False)
            rows = [
                {
                    "datetime": datetime.fromtimestamp(int(b["time"]), tz=timezone.utc).isoformat(),
                    "volume": float(b.get("vol") or 0),
                }
                for b in bars
            ]
            out["intervals"][tf] = _volume_stats(rows, tf)
        return out
    except Exception as exc:
        return {"available": False, "detail": f"{exc.__class__.__name__}: {exc}"}


def main() -> int:
    key = os.environ.get("EODHD_KEY", "").strip()
    if not key:
        print("EODHD_KEY not set", file=sys.stderr)
        return 2

    candidates = DEFAULT_SYMBOLS + FOREX_SYMBOLS
    selected = DEFAULT_SYMBOLS
    args = [arg for arg in sys.argv[1:] if arg.strip()]
    if "--forex" in args:
        selected = FOREX_SYMBOLS
        args = [arg for arg in args if arg != "--forex"]
    if args:
        wanted = {arg.strip().lower() for arg in args if arg.strip()}
        selected = [
            item for item in candidates
            if item["display"].lower() in wanted
            or item["daily"].lower() in wanted
            or item["intraday"].lower() in wanted
        ]
        if not selected:
            print("No matching default symbols for arguments", file=sys.stderr)
            return 2

    session = requests.Session()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "intervals": INTERVALS,
        "symbols": [],
    }
    for item in selected:
        symbol_report = {"display": item["display"], "type": item["type"], "results": {}}
        for interval in INTERVALS:
            if interval == "D1":
                res = _fetch_daily(session, key, item["daily"])
            else:
                res = _fetch_intraday(session, key, item["intraday"], interval)
            symbol_report["results"][interval] = res
            time.sleep(0.25)
        if item["type"] in {"forex", "commodity"} and item["intraday"].endswith(".FOREX"):
            symbol_report["mt5_comparison"] = _mt5_comparison(item["display"])
            symbol_report["duka_comparison"] = _duka_comparison(item["display"])
        report["symbols"].append(symbol_report)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    for item in report["symbols"]:
        print(f"{item['display']} ({item['type']})")
        for interval, res in item["results"].items():
            status = res.get("status")
            bars = res.get("bars", 0)
            pct = res.get("nonzero_volume_pct", 0)
            err = f" error={res.get('error')}" if res.get("error") else ""
            print(f"  {interval}: status={status} bars={bars} nonzero_vol={pct}%{err}")
        if "mt5_comparison" in item:
            mt5 = item["mt5_comparison"]
            print(f"  MT5: available={mt5.get('available')} detail={mt5.get('detail', '')}")
        if "duka_comparison" in item:
            duka = item["duka_comparison"]
            print(f"  Dukascopy: available={duka.get('available')} detail={duka.get('detail', '')}")
    print(f"report={OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
