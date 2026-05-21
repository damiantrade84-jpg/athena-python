"""Read-only probe for crypto chart feed provider fields (TRX/USDT default).

Usage (Athena running locally):
  python tools/crypto_feed_parity_probe.py
  python tools/crypto_feed_parity_probe.py --base http://127.0.0.1:5000 --symbol TRX/USDT --tf H4
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request

PROVIDER_KEYS = (
    "asset_group",
    "execution_provider",
    "chart_provider",
    "candle_provider",
    "scoring_provider",
    "indicator_provider",
    "volume_provider",
    "turnover_provider",
    "vwap_provider",
    "atr_provider",
    "live_tick_provider",
    "bybit_symbol",
    "bybit_category",
    "fallback_used",
    "fallback_reason",
    "provider_mismatch",
    "provider_mismatch_reason",
    "candle_confirmed_policy",
    "last_candle_ts",
    "vwap_formula",
    "chart_status",
)


def _get(base: str, path: str) -> dict:
    url = f"{base.rstrip('/')}{path}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe crypto chart feed parity fields")
    parser.add_argument("--base", default="http://127.0.0.1:5000")
    parser.add_argument("--symbol", default="TRX/USDT")
    parser.add_argument("--tf", default="H4")
    parser.add_argument("--limit", type=int, default=300)
    args = parser.parse_args()

    sym_q = urllib.parse.quote(args.symbol, safe="")
    candles_path = f"/api/candles?symbol={sym_q}&tf={args.tf}&limit={args.limit}"
    tick_path = f"/api/chart-tick?symbol={sym_q}"

    try:
        candles = _get(args.base, candles_path)
        tick = _get(args.base, tick_path)
    except Exception as exc:
        print(f"probe_failed: {exc}", file=sys.stderr)
        return 1

    summary = {k: candles.get(k) for k in PROVIDER_KEYS if k in candles}
    print("=== candles provider map ===")
    print(json.dumps(summary, indent=2, sort_keys=True))

    last = (candles.get("candles") or [{}])[-1] if candles.get("candles") else {}
    indicator_tail = {
        k: last.get(k)
        for k in (
            "c",
            "v",
            "volume",
            "turnover",
            "provider",
            "confirmed",
            "ema21",
            "ema50",
            "vwap",
            "adx14",
            "atr14",
            "volume_ma",
        )
        if k in last
    }
    print("\n=== last candle indicators ===")
    print(json.dumps(indicator_tail, indent=2, sort_keys=True))

    tick_summary = {k: tick.get(k) for k in PROVIDER_KEYS if k in tick}
    print("\n=== chart-tick provider map ===")
    print(json.dumps(tick_summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
