#!/usr/bin/env python
"""Engine D crypto live-ticker / quote freshness diagnostic.

Read-only. Compares the Binance Futures public ticker (the price source Engine
D's crypto signals are built from) against the Bybit Linear public ticker (the
side that ``bybit_execute`` will hit when an Engine D crypto order is placed).
No credentials, no execution.

Prints per-symbol diagnostics that mirror the fields proposed in the audit:
binance_last, binance_age_s, bybit_bid, bybit_ask, bybit_mid, bybit_age_s,
spread_pct, provider_drift_pct, executable, reason.

Usage:
    python tools/engine_d_crypto_quote_diagnostics.py
    python tools/engine_d_crypto_quote_diagnostics.py --symbols BTC/USDT,ETH/USDT
    python tools/engine_d_crypto_quote_diagnostics.py --max-drift-pct 0.005
    python tools/engine_d_crypto_quote_diagnostics.py --json
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT",
    "BNB/USDT", "SUI/USDT", "LTC/USDT",
]

BINANCE_24H_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers"


def _binance_symbol(display: str) -> str:
    return display.replace("/", "").upper()


def _http_get_json(url: str, timeout_s: float) -> dict | list:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read())


def _fetch_binance_ticker(display: str, timeout_s: float) -> dict | None:
    sym = _binance_symbol(display)
    url = f"{BINANCE_24H_URL}?symbol={sym}"
    try:
        payload = _http_get_json(url, timeout_s)
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"_error": f"binance_http: {exc}"}
    if not isinstance(payload, dict):
        return {"_error": "binance_payload_not_dict"}
    try:
        last = float(payload.get("lastPrice") or 0)
        bid = float(payload.get("bidPrice") or 0)
        ask = float(payload.get("askPrice") or 0)
    except (TypeError, ValueError):
        return {"_error": "binance_bad_price_field"}
    close_time_ms = payload.get("closeTime")
    ts_sec = float(close_time_ms) / 1000.0 if isinstance(close_time_ms, (int, float)) else None
    return {
        "last": last,
        "bid": bid if bid > 0 else None,
        "ask": ask if ask > 0 else None,
        "ts": ts_sec,
    }


def _fetch_bybit_ticker(display: str, timeout_s: float) -> dict | None:
    sym = _binance_symbol(display)
    url = f"{BYBIT_TICKERS_URL}?category=linear&symbol={sym}"
    try:
        payload = _http_get_json(url, timeout_s)
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"_error": f"bybit_http: {exc}"}
    if not isinstance(payload, dict):
        return {"_error": "bybit_payload_not_dict"}
    result = payload.get("result") or {}
    rows = result.get("list") or []
    if not rows:
        return {"_error": "bybit_empty_result"}
    row = rows[0] or {}
    try:
        bid = float(row.get("bid1Price") or 0)
        ask = float(row.get("ask1Price") or 0)
        last = float(row.get("lastPrice") or 0)
    except (TypeError, ValueError):
        return {"_error": "bybit_bad_price_field"}
    ts_ms_field = row.get("ts") or row.get("updatedTime") or payload.get("time")
    ts_sec = float(ts_ms_field) / 1000.0 if isinstance(ts_ms_field, (int, float)) else None
    return {
        "last": last,
        "bid": bid if bid > 0 else None,
        "ask": ask if ask > 0 else None,
        "ts": ts_sec,
    }


def _age_seconds(ts_sec: float | None, now_s: float) -> float | None:
    if not isinstance(ts_sec, (int, float)) or ts_sec <= 0:
        return None
    return max(0.0, now_s - float(ts_sec))


def _spread_pct(bid: float | None, ask: float | None) -> float | None:
    if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (ask + bid) / 2.0
    return (ask - bid) / mid if mid > 0 else None


def _provider_drift_pct(signal_price: float | None, broker_price: float | None) -> float | None:
    if not signal_price or not broker_price or signal_price <= 0 or broker_price <= 0:
        return None
    return abs(broker_price - signal_price) / signal_price


def _diagnose_symbol(display: str, timeout_s: float, now_s: float, max_drift_pct: float) -> dict:
    binance = _fetch_binance_ticker(display, timeout_s)
    bybit = _fetch_bybit_ticker(display, timeout_s)
    row: dict = {"symbol": display}

    if isinstance(binance, dict) and binance.get("_error"):
        row["binance_error"] = binance["_error"]
        binance = None
    if isinstance(bybit, dict) and bybit.get("_error"):
        row["bybit_error"] = bybit["_error"]
        bybit = None

    if binance:
        row["binance_last"] = binance.get("last")
        row["binance_age_s"] = _age_seconds(binance.get("ts"), now_s)
    if bybit:
        row["bybit_bid"] = bybit.get("bid")
        row["bybit_ask"] = bybit.get("ask")
        mid = None
        if isinstance(bybit.get("bid"), (int, float)) and isinstance(bybit.get("ask"), (int, float)):
            mid = (float(bybit["bid"]) + float(bybit["ask"])) / 2.0  # type: ignore[arg-type]
        row["bybit_mid"] = mid
        row["bybit_age_s"] = _age_seconds(bybit.get("ts"), now_s)
        row["spread_pct"] = _spread_pct(bybit.get("bid"), bybit.get("ask"))

    row["provider_drift_pct"] = _provider_drift_pct(
        row.get("binance_last"), row.get("bybit_mid")
    )

    reasons: list[str] = []
    if not binance:
        reasons.append("binance_missing")
    if not bybit:
        reasons.append("bybit_missing")
    if isinstance(row.get("bybit_bid"), (int, float)) is False or isinstance(row.get("bybit_ask"), (int, float)) is False:
        reasons.append("bybit_bid_or_ask_missing")
    drift = row.get("provider_drift_pct")
    if isinstance(drift, (int, float)) and drift > max_drift_pct:
        reasons.append(f"drift_above_{max_drift_pct*100:.2f}pct")
    executable = not reasons
    row["executable"] = executable
    row["reason"] = ",".join(reasons) or "OK"
    return row


def _fmt_num(v, fmt: str = "{:.6f}") -> str:
    return fmt.format(v) if isinstance(v, (int, float)) else "—"


def _fmt_pct(v) -> str:
    return f"{v*100:.4f}%" if isinstance(v, (int, float)) else "—"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Engine D crypto live-ticker / quote freshness diagnostic (read-only)"
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=",".join(DEFAULT_SYMBOLS),
        help=f"Comma-separated symbols (default: {','.join(DEFAULT_SYMBOLS)})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="HTTP timeout seconds (default: 8.0)",
    )
    parser.add_argument(
        "--max-drift-pct",
        type=float,
        default=0.005,
        help="Provider drift threshold (fraction) for flagging non-executable; default 0.005 = 0.5%%",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit raw JSON results instead of a table",
    )
    args = parser.parse_args()

    syms = [s.strip() for s in str(args.symbols).split(",") if s.strip()]
    if not syms:
        print("ERROR: no symbols", file=sys.stderr)
        return 2

    now_s = time.time()
    rows = [
        _diagnose_symbol(sym, args.timeout, now_s, float(args.max_drift_pct))
        for sym in syms
    ]

    if args.json:
        print(json.dumps({"now": now_s, "rows": rows}, indent=2, default=str))
        return 0

    print(f"\nEngine D crypto quote diagnostic — now={now_s:.1f}  max_drift={args.max_drift_pct*100:.2f}%")
    print(
        f"\n{'SYMBOL':<11} {'BIN_LAST':>13} {'BIN_AGE':>9} "
        f"{'BYB_BID':>13} {'BYB_ASK':>13} {'BYB_AGE':>9} "
        f"{'SPREAD':>9} {'DRIFT':>9} {'EXEC':<5} REASON"
    )
    print("-" * 130)
    for r in rows:
        print(
            f"{r['symbol']:<11} "
            f"{_fmt_num(r.get('binance_last')):>13} "
            f"{_fmt_num(r.get('binance_age_s'), '{:.1f}'):>9} "
            f"{_fmt_num(r.get('bybit_bid')):>13} "
            f"{_fmt_num(r.get('bybit_ask')):>13} "
            f"{_fmt_num(r.get('bybit_age_s'), '{:.1f}'):>9} "
            f"{_fmt_pct(r.get('spread_pct')):>9} "
            f"{_fmt_pct(r.get('provider_drift_pct')):>9} "
            f"{('YES' if r.get('executable') else 'NO'):<5} "
            f"{r.get('reason', '')}"
        )

    total = len(rows)
    exec_count = sum(1 for r in rows if r.get("executable"))
    drift_fail = sum(
        1
        for r in rows
        if isinstance(r.get("provider_drift_pct"), (int, float))
        and r["provider_drift_pct"] > float(args.max_drift_pct)
    )
    binance_missing = sum(1 for r in rows if r.get("binance_error") or r.get("binance_last") is None)
    bybit_missing = sum(1 for r in rows if r.get("bybit_error") or r.get("bybit_mid") is None)

    def _age_key(r: dict) -> float:
        a = r.get("bybit_age_s")
        b = r.get("binance_age_s")
        return max(
            float(a) if isinstance(a, (int, float)) else -1.0,
            float(b) if isinstance(b, (int, float)) else -1.0,
        )

    def _drift_key(r: dict) -> float:
        d = r.get("provider_drift_pct")
        return float(d) if isinstance(d, (int, float)) else -1.0

    worst_age = sorted(rows, key=_age_key, reverse=True)[:10]
    worst_drift = sorted(rows, key=_drift_key, reverse=True)[:10]

    print("\nSummary")
    print(f"  total                = {total}")
    print(f"  executable_now       = {exec_count}")
    print(f"  binance_missing      = {binance_missing}")
    print(f"  bybit_missing        = {bybit_missing}")
    print(f"  drift_fail           = {drift_fail}  (>{args.max_drift_pct*100:.2f}%)")
    print("\nTop 10 worst ages (max of bybit/binance):")
    for r in worst_age:
        print(
            f"  {r['symbol']:<11}  bybit_age={_fmt_num(r.get('bybit_age_s'), '{:.1f}'):>8}  "
            f"binance_age={_fmt_num(r.get('binance_age_s'), '{:.1f}'):>8}"
        )
    print("\nTop 10 worst provider drift:")
    for r in worst_drift:
        print(f"  {r['symbol']:<11}  drift={_fmt_pct(r.get('provider_drift_pct'))}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
