"""Compare Athena symbols with EODHD exchange symbol lists.

Read-only probe. Uses EODHD_KEY from the environment and writes:
  tests/_artifacts/eodhd_symbol_coverage.json

The report helps decide which Athena non-forex/non-crypto symbols can be
mapped directly to EODHD tickers for volume overlays.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "tests" / "_artifacts" / "eodhd_symbol_coverage.json"

EXCHANGES = ["US", "COMM", "INDX", "FOREX", "JSE"]

ATHENA_CANDIDATES = [
    {"display": "AAPL", "type": "stock", "symbols": ["AAPL.US", "AAPL"]},
    {"display": "SPY", "type": "stock", "symbols": ["SPY.US", "SPY"]},
    {"display": "QQQ", "type": "stock", "symbols": ["QQQ.US", "QQQ"]},
    {"display": "XAU/USD", "type": "commodity", "symbols": ["XAUUSD.FOREX", "GC", "GC.COMM"]},
    {"display": "XAG/USD", "type": "commodity", "symbols": ["XAGUSD.FOREX", "SI", "SI.COMM"]},
    {"display": "WTI Oil", "type": "commodity", "symbols": ["CL", "CL.COMM", "WTICOUSD.FOREX"]},
    {"display": "Brent Oil", "type": "commodity", "symbols": ["BZ", "BZ.COMM", "BRENTOIL.FOREX"]},
    {"display": "Nat Gas", "type": "commodity", "symbols": ["NG", "NG.COMM", "NATGASUSD.FOREX"]},
    {"display": "Copper", "type": "commodity", "symbols": ["HG", "HG.COMM", "XCUUSD.FOREX"]},
    {"display": "S&P 500", "type": "index", "symbols": ["GSPC.INDX", "GSPC", "SPX.INDX"]},
    {"display": "Nasdaq", "type": "index", "symbols": ["IXIC.INDX", "IXIC", "NAS100.INDX"]},
    {"display": "NASDAQ-100", "type": "index", "symbols": ["IXIC.INDX", "IXIC", "NDX.INDX", "NAS100.INDX"]},
    {"display": "Dow Jones", "type": "index", "symbols": ["DJI.INDX", "DJI"]},
    {"display": "DAX 40", "type": "index", "symbols": ["GDAXI.INDX", "GDAXI"]},
    {"display": "UK100", "type": "index", "symbols": ["FTSE.INDX", "FTSE"]},
    {"display": "Nikkei 225", "type": "index", "symbols": ["N225.INDX", "N225"]},
    {"display": "Hang Seng", "type": "index", "symbols": ["HSI.INDX", "HSI"]},
    {"display": "ASX 200", "type": "index", "symbols": ["AXJO.INDX", "AXJO"]},
]


def _fetch_exchange(session: requests.Session, key: str, exchange: str) -> dict[str, Any]:
    url = f"https://eodhd.com/api/exchange-symbol-list/{exchange}"
    try:
        resp = session.get(url, params={"api_token": key, "fmt": "json"}, timeout=45)
    except requests.RequestException as exc:
        return {"exchange": exchange, "status": None, "error": f"request failed: {exc.__class__.__name__}", "items": []}
    out: dict[str, Any] = {"exchange": exchange, "status": resp.status_code, "items": []}
    if resp.status_code != 200:
        out["error"] = resp.text[:300]
        return out
    try:
        data = resp.json()
    except ValueError as exc:
        out["error"] = f"invalid json: {exc}"
        return out
    if not isinstance(data, list):
        out["error"] = f"unexpected payload type: {type(data).__name__}"
        out["payload_preview"] = str(data)[:300]
        return out
    out["items"] = data
    out["count"] = len(data)
    return out


def _symbol_keys(item: dict[str, Any], exchange: str) -> set[str]:
    code = str(item.get("Code") or item.get("code") or item.get("symbol") or "").strip()
    if not code:
        return set()
    keys = {code.upper()}
    if "." in code:
        keys.add(code.upper().split(".")[0])
    keys.add(f"{code}.{exchange}".upper())
    return keys


def main() -> int:
    key = os.environ.get("EODHD_KEY", "").strip()
    if not key:
        print("EODHD_KEY not set", file=sys.stderr)
        return 2

    exchanges = sys.argv[1:] or EXCHANGES
    session = requests.Session()
    raw = [_fetch_exchange(session, key, ex.upper()) for ex in exchanges]

    index: dict[str, list[dict[str, Any]]] = {}
    for ex_payload in raw:
        ex = ex_payload["exchange"]
        for item in ex_payload.get("items") or []:
            for key_ in _symbol_keys(item, ex):
                index.setdefault(key_, []).append(
                    {
                        "exchange": ex,
                        "code": item.get("Code") or item.get("code") or item.get("symbol"),
                        "name": item.get("Name") or item.get("name"),
                        "type": item.get("Type") or item.get("type"),
                    }
                )

    coverage = []
    for candidate in ATHENA_CANDIDATES:
        matches = []
        for sym in candidate["symbols"]:
            matches.extend(index.get(sym.upper(), []))
        coverage.append(
            {
                **candidate,
                "matched": bool(matches),
                "matches": matches,
            }
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exchanges": [
            {
                "exchange": row["exchange"],
                "status": row.get("status"),
                "count": row.get("count", 0),
                "error": row.get("error"),
            }
            for row in raw
        ],
        "coverage": coverage,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    for row in report["exchanges"]:
        print(f"{row['exchange']}: status={row['status']} count={row['count']} error={row.get('error') or ''}")
    print("coverage:")
    for row in coverage:
        matched = "YES" if row["matched"] else "NO"
        detail = "; ".join(
            f"{m.get('code')}[{m.get('exchange')}]" for m in row.get("matches", [])[:3]
        )
        print(f"  {row['display']}: {matched} {detail}")
    print(f"report={OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
