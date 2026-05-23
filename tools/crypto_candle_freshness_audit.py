#!/usr/bin/env python3
"""Read-only crypto H4/D1/H1 candle freshness audit (Binance Futures REST).

Does not import athena.py (avoids eodhd and other heavy deps). Parses CRYPTO_PAIRS
from athena.py and runs the same freshness diagnostics as live_feed_diagnostics.

Usage:
    python tools/crypto_candle_freshness_audit.py
    python tools/crypto_candle_freshness_audit.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from athena_app.services.candle_service import engine_a_scoring_candles_from_state
from athena_app.services.data_freshness import (
    build_live_feed_diagnostic,
    check_live_candle_consistency,
    evaluate_execution_data_freshness,
)
from athena_app.services.market_state import (
    candle_freshness_diagnostic,
    market_state_offset_hours,
    split_market_state,
)
from config import CONFIG

TF_MAP = {"H1": "1h", "H4": "4h", "D1": "1d"}


def _parse_crypto_pairs() -> list[dict]:
    text = (ROOT / "athena.py").read_text(encoding="utf-8")
    start = text.index("CRYPTO_PAIRS = [")
    end = text.index("\nALL_PAIRS = (", start)
    block = text[start:end]
    pairs: list[dict] = []
    for chunk in re.findall(r"\{[^{}]+\}", block):
        if '"type": "crypto"' not in chunk:
            continue
        sym = re.search(r'"symbol":\s*"([^"]+)"', chunk)
        disp = re.search(r'"display":\s*"([^"]+)"', chunk)
        src = re.search(r'"source":\s*"([^"]+)"', chunk)
        en = re.search(r'"enabled":\s*(True|False)', chunk)
        if not sym or not disp or not src:
            continue
        if en and en.group(1) == "False":
            continue
        pairs.append(
            {
                "symbol": sym.group(1),
                "type": "crypto",
                "display": disp.group(1),
                "source": src.group(1),
                "enabled": True,
            }
        )
    return pairs


def _fetch_klines(symbol: str, interval: str, limit: int = 500) -> list[dict]:
    url = (
        f"https://fapi.binance.com/fapi/v1/klines"
        f"?symbol={symbol}&interval={interval}&limit={limit}"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    out: list[dict] = []
    for k in data:
        ts = int(k[0] // 1000)
        out.append(
            {
                "time": ts,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "vol": float(k[5]),
            }
        )
    return out


def _consistency_paths(pair: dict, tf: str, candles: list[dict], state: dict, now: float) -> dict:
    engine_a_input = list(state.get("confirmed") or [])
    if state.get("forming"):
        engine_a_input.append(state["forming"])
    return {
        "raw_provider": candles,
        "market_state": state,
        "engine_a": engine_a_input,
        "engine_b": list(state.get("confirmed") or []),
        "scanner": engine_a_input,
        "compare": engine_a_input,
    }


def _build_signal_snapshot(pair: dict, now: float) -> dict:
    signal: dict = {"symbol": pair["symbol"], "candleFreshness": {}, "candleConsistency": {}}
    for tf in ("H4", "D1", "H1"):
        candles = _fetch_klines(pair["symbol"], TF_MAP[tf])
        state = split_market_state(
            candles,
            tf,
            pair["display"],
            time_now=now,
            offset_hours=market_state_offset_hours(pair, tf),
        )
        series = list(state.get("confirmed") or [])
        if state.get("forming"):
            series.append(state["forming"])
        signal["candleFreshness"][tf] = candle_freshness_diagnostic(
            pair, tf, series, source="binance"
        )
        signal["candleConsistency"][tf] = check_live_candle_consistency(
            pair,
            tf,
            _consistency_paths(pair, tf, candles, state, now),
            time_now=now,
        )
        time.sleep(0.1)
    signal["dataFreshness"] = evaluate_execution_data_freshness(signal, CONFIG)
    return signal


def main() -> int:
    parser = argparse.ArgumentParser(description="Crypto candle freshness audit (read-only)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--timeframes",
        default="H4,D1,H1",
        help="Comma-separated timeframes",
    )
    args = parser.parse_args()
    timeframes = [s.strip().upper() for s in args.timeframes.split(",") if s.strip()]

    pairs = _parse_crypto_pairs()
    if not pairs:
        print("No enabled CRYPTO_PAIRS found.", file=sys.stderr)
        return 1

    now = time.time()
    rows: list[dict] = []
    summary: dict[str, int] = {}

    for pair in pairs:
        for tf in timeframes:
            if tf not in TF_MAP:
                continue
            try:
                candles = _fetch_klines(pair["symbol"], TF_MAP[tf])
            except Exception as exc:
                rows.append({"symbol": pair["display"], "tf": tf, "error": str(exc)})
                continue

            state = split_market_state(
                candles,
                tf,
                pair["display"],
                time_now=now,
                offset_hours=market_state_offset_hours(pair, tf),
            )
            scoring = engine_a_scoring_candles_from_state(pair, state, fallback=candles)
            diag = build_live_feed_diagnostic(
                pair,
                tf,
                candles,
                source="binance",
                market_state=state,
                scoring_candles=scoring,
            )
            consistency = check_live_candle_consistency(
                pair,
                tf,
                _consistency_paths(pair, tf, candles, state, now),
                time_now=now,
            )
            stale = str(diag.get("stale_status") or "unknown")
            summary[stale] = summary.get(stale, 0) + 1
            rows.append(
                {
                    "symbol": pair["display"],
                    "tf": tf,
                    "stale_status": stale,
                    "bucket_lag": diag.get("bucket_lag"),
                    "lag_seconds": diag.get("lag_seconds"),
                    "consistency": (consistency or {}).get("status"),
                    "lastBarIso": diag.get("lastBarIso"),
                    "expectedIso": diag.get("expectedCurrentBucketIso"),
                }
            )
            time.sleep(0.12)

    btc = next((p for p in pairs if p["symbol"] == "BTCUSDT"), pairs[0])
    btc_signal = _build_signal_snapshot(btc, now)

    abort_reasons: list[str] = []
    df = btc_signal.get("dataFreshness") or {}
    for item in df.get("blocked") or []:
        abort_reasons.append(f"{item.get('timeframe')}:{item.get('severity')}")
    for item in df.get("warnings") or []:
        abort_reasons.append(f"{item.get('timeframe')}:{item.get('severity')}")

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "pair_count": len(pairs),
        "stale_status_distribution": summary,
        "rows": rows,
        "btc_snapshot": {
            "symbol": btc["display"],
            "dataFreshness": df,
            "candleFreshness_severity": {
                k: v.get("stalenessSeverity")
                for k, v in (btc_signal.get("candleFreshness") or {}).items()
            },
            "candleConsistency_status": {
                k: (v or {}).get("status")
                for k, v in (btc_signal.get("candleConsistency") or {}).items()
            },
            "abortReasons_equivalent": abort_reasons,
        },
        "non_ok_rows": [
            r
            for r in rows
            if r.get("error")
            or r.get("stale_status") not in ("fresh", "stale_1_bucket")
        ],
    }

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"Crypto Candle Freshness Audit — {report['generated_at']}")
        print(f"Pairs: {report['pair_count']}  Timeframes: {','.join(timeframes)}")
        print("\nStale status distribution:")
        for k, v in sorted(summary.items()):
            print(f"  {k}: {v}")
        print("\nBTC dataFreshness.allowed:", df.get("allowed"))
        print("BTC abortReasons equivalent:", abort_reasons)
        print("BTC candleFreshness:", report["btc_snapshot"]["candleFreshness_severity"])
        print("BTC candleConsistency:", report["btc_snapshot"]["candleConsistency_status"])
        bad = report["non_ok_rows"]
        if bad:
            print(f"\nNon-OK rows ({len(bad)}):")
            for r in bad[:20]:
                print(f"  {r}")
        else:
            print("\nAll rows fresh or stale_1_bucket (policy-normal).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
