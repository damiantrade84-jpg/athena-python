#!/usr/bin/env python
"""CLI for live-quote (tick) freshness diagnostics.

Read-only. Queries the running Athena server's /api/prices endpoint and prints
the live-price snapshot decorated by Fix #2 with `quoteSource`/`ageSec`. Does
not place trades, call brokers directly, or mutate any state.

Distinct from tools/live_feed_diagnostics.py, which inspects candle bar
freshness. This tool inspects the in-memory _live_prices tick dict.

Usage:
    python tools/live_price_diagnostics.py
    python tools/live_price_diagnostics.py --host http://127.0.0.1:5000
    python tools/live_price_diagnostics.py --stale-over 30
    python tools/live_price_diagnostics.py --source mt5,binance_ws
    python tools/live_price_diagnostics.py --json
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def _fetch_prices(host: str, timeout_s: float) -> dict:
    url = host.rstrip("/") + "/api/prices"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = resp.read()
    return json.loads(body)


def _matches_source(entry: dict, wanted: set[str] | None) -> bool:
    if not wanted:
        return True
    src = str((entry or {}).get("source") or "").strip().lower()
    return src in wanted


def _passes_stale(entry: dict, threshold_s: float | None) -> bool:
    if threshold_s is None:
        return True
    age = (entry or {}).get("ageSec")
    if not isinstance(age, (int, float)):
        return False
    return float(age) >= float(threshold_s)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live-quote freshness diagnostics (read-only, hits /api/prices)"
    )
    parser.add_argument(
        "--host",
        default="http://127.0.0.1:5000",
        help="Base URL of the running Athena server (default: http://127.0.0.1:5000)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="HTTP timeout seconds (default: 5.0)",
    )
    parser.add_argument(
        "--stale-over",
        type=float,
        default=None,
        help="Only show entries with ageSec >= this many seconds",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="",
        help="Comma-separated quote source filter (e.g. mt5,binance_ws,eodhd_ws)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit raw JSON (filtered) instead of a table",
    )

    args = parser.parse_args()

    try:
        payload = _fetch_prices(args.host, args.timeout)
    except urllib.error.URLError as e:
        print(f"ERROR: could not reach {args.host}/api/prices: {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"ERROR: /api/prices returned non-JSON: {e}", file=sys.stderr)
        return 2

    prices = payload.get("prices") or {}
    if not isinstance(prices, dict):
        print("ERROR: /api/prices payload missing 'prices' dict", file=sys.stderr)
        return 2

    wanted_sources = (
        {s.strip().lower() for s in args.source.split(",") if s.strip()}
        if args.source.strip()
        else None
    )

    filtered: dict[str, dict] = {}
    for pair_key, entry in prices.items():
        if not isinstance(entry, dict):
            continue
        if not _matches_source(entry, wanted_sources):
            continue
        if not _passes_stale(entry, args.stale_over):
            continue
        filtered[pair_key] = entry

    if args.json:
        print(json.dumps(
            {
                "host": args.host,
                "serverTs": payload.get("ts"),
                "totalCount": payload.get("count"),
                "matchedCount": len(filtered),
                "prices": filtered,
            },
            indent=2,
            default=str,
        ))
        return 0

    print(f"\nLive-quote diagnostics — host={args.host} serverTs={payload.get('ts')}")
    print(f"Total entries: {payload.get('count')}   Matched: {len(filtered)}")
    if not filtered:
        print("(no entries match the current filter)")
        return 0

    def _sort_key(kv: tuple[str, dict]) -> float:
        age = kv[1].get("ageSec")
        return float(age) if isinstance(age, (int, float)) else -1.0

    rows = sorted(filtered.items(), key=_sort_key, reverse=True)

    print(f"\n{'PAIR':<14} {'SOURCE':<14} {'PRICE':>14} {'AGE_SEC':>10} {'TS':<28}")
    print("-" * 84)
    for pair_key, entry in rows:
        src = str(entry.get("source") or "")[:14]
        price = entry.get("price")
        price_str = f"{price:.6f}" if isinstance(price, (int, float)) else str(price)
        age = entry.get("ageSec")
        age_str = f"{age:.1f}" if isinstance(age, (int, float)) else "—"
        ts = entry.get("ts")
        print(f"{pair_key:<14} {src:<14} {price_str:>14} {age_str:>10} {str(ts):<28}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
