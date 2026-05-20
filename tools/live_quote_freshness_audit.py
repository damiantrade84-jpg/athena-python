#!/usr/bin/env python
"""Read-only live quote freshness audit.

Queries a running Athena server's /api/prices snapshot and classifies quote
entries for freshness, executable bid/ask availability, spread, and source
quality. It does not call brokers, place orders, or mutate runtime state.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.error
import urllib.request
from collections import Counter
from typing import Any


DEFAULT_MAX_AGE_SEC = {
    "mt5": 10.0,
    "binance_ws": 10.0,
    "binance_rest": 15.0,
    "eodhd_ws": 90.0,
    "eodhd_rest": 180.0,
}

LAST_ONLY_SOURCES = {"binance_rest", "eodhd_rest", "yfinance", "polygon_rest"}


def _fetch_json(url: str, timeout_s: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read())


def _as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _asset_type(symbol: str, entry: dict[str, Any]) -> str:
    raw = str(entry.get("asset_type") or entry.get("type") or "").strip().lower()
    if raw:
        return raw
    source = str(entry.get("source") or "").lower()
    if "binance" in source:
        return "crypto"
    if "/" in symbol and symbol.replace("/", "").isalpha():
        return "forex"
    if symbol.upper().endswith("USDT"):
        return "crypto"
    return "unknown"


def _source(entry: dict[str, Any]) -> str:
    return str(entry.get("source") or "unknown").strip().lower() or "unknown"


def _age(entry: dict[str, Any]) -> float | None:
    return _as_float(entry.get("ageSec") if "ageSec" in entry else entry.get("quoteAgeSec"))


def _spread_pct(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return (ask - bid) / mid


def _row(symbol: str, entry: dict[str, Any]) -> dict[str, Any]:
    src = _source(entry)
    bid = _as_float(entry.get("bid"))
    ask = _as_float(entry.get("ask"))
    last = _as_float(entry.get("last"))
    price = _as_float(entry.get("price"))
    mid = (bid + ask) / 2.0 if bid and ask and ask >= bid else price
    age = _age(entry)
    max_age = DEFAULT_MAX_AGE_SEC.get(src)
    spread = _spread_pct(bid, ask)
    synthetic = bool(bid is not None and ask is not None and bid == ask and price == bid)

    reasons: list[str] = []
    if src == "unknown":
        reasons.append("source_missing")
    if age is None:
        reasons.append("quote_age_missing")
    elif max_age is not None and age > max_age:
        reasons.append("quote_stale")
    if src in LAST_ONLY_SOURCES:
        reasons.append("last_only_source")
    if bid is None or ask is None:
        reasons.append("bid_ask_missing")
    if synthetic:
        reasons.append("synthetic_spread")

    executable = not reasons
    return {
        "engine": "runtime_quote_cache",
        "symbol": symbol,
        "asset_type": _asset_type(symbol, entry),
        "signal_price": None,
        "signal_source": None,
        "quote_provider": src,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "last": last if last is not None else price,
        "quote_ts": entry.get("ts"),
        "quote_age": age,
        "spread_pct": spread,
        "drift_pct": None,
        "stale": "quote_stale" in reasons or "quote_age_missing" in reasons,
        "executable": executable,
        "reason": "OK" if executable else ",".join(reasons),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _print_table(rows: list[dict[str, Any]]) -> None:
    headers = [
        "engine", "symbol", "asset_type", "signal_price", "signal_source",
        "quote_provider", "bid", "ask", "mid", "last", "quote_ts",
        "quote_age", "spread_pct", "drift_pct", "stale", "executable", "reason",
    ]
    print(" | ".join(headers))
    print(" | ".join("-" * len(h) for h in headers))
    for row in rows:
        print(" | ".join(_fmt(row.get(h)) for h in headers))


def _print_summary(rows: list[dict[str, Any]]) -> None:
    stale_by_asset = Counter(r["asset_type"] for r in rows if r["stale"])
    stale_by_provider = Counter(r["quote_provider"] for r in rows if r["stale"])
    missing_bid_ask = [r for r in rows if "bid_ask_missing" in r["reason"]]
    synthetic = [r for r in rows if "synthetic_spread" in r["reason"]]

    print("\nSummary")
    print(f"total checked: {len(rows)}")
    print(f"stale count by asset type: {dict(stale_by_asset)}")
    print(f"stale count by provider: {dict(stale_by_provider)}")
    print(f"missing bid/ask count: {len(missing_bid_ask)}")
    print(f"synthetic spread count: {len(synthetic)}")
    print("candle-close-as-signal count: not measured by /api/prices")
    print("candle-close-as-executable count: not measured by /api/prices")
    print("stale _live_prices consumers: see source audit")
    print("execution paths without quote-age guard: see source audit")
    print("execution paths without spread guard: see source audit")

    def top(label: str, key: str) -> None:
        vals = [r for r in rows if isinstance(r.get(key), (int, float))]
        vals.sort(key=lambda r: float(r[key]), reverse=True)
        print(f"{label}: " + ", ".join(f"{r['symbol']}={_fmt(r[key])}" for r in vals[:10]))

    top("top 10 quote ages", "quote_age")
    top("top 10 drift pct", "drift_pct")
    top("top 10 spread pct", "spread_pct")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Athena live quote freshness from /api/prices")
    parser.add_argument("--host", default="http://127.0.0.1:5000")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        payload = _fetch_json(args.host.rstrip("/") + "/api/prices", args.timeout)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"ERROR: could not read /api/prices from {args.host}: {exc}", file=sys.stderr)
        return 2

    prices = payload.get("prices") or {}
    if not isinstance(prices, dict):
        print("ERROR: /api/prices payload missing prices object", file=sys.stderr)
        return 2

    rows = [_row(symbol, entry) for symbol, entry in sorted(prices.items()) if isinstance(entry, dict)]
    if args.json:
        print(json.dumps({"server_ts": payload.get("ts"), "rows": rows}, indent=2, default=str))
        return 0

    _print_table(rows)
    _print_summary(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
