#!/usr/bin/env python3
"""Read-only crypto candle freshness audit (configured Bybit signal feed).

Does not import athena.py (avoids unrelated service startup). Parses CRYPTO_PAIRS
from athena.py, fetches through the production Bybit kline helpers, and runs the
same freshness diagnostics as live_feed_diagnostics.

Usage:
    python tools/crypto_candle_freshness_audit.py
    python tools/crypto_candle_freshness_audit.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

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
from config import CONFIG, scan_candle_limits
from data_feeds import _fetch_bybit_klines, _fetch_bybit_klines_paginated
from engine_a_v3.profile import baseline_profile
from engine_a_v3.routing import route_specialist
from feature_normalizer import get_normalization_lookback

SUPPORTED_TFS = {"M15", "M30", "H1", "H4", "D1"}
_FAMILY_ASSET = {
    "forex": "forex",
    "crypto": "crypto",
    "commodity": "commodity",
    "index": "index",
    "equity_etf": "stock",
}


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


def _fetch_klines(symbol: str, tf: str, limit: int) -> list[dict]:
    candles = (
        _fetch_bybit_klines_paginated(symbol, tf, limit)
        if limit > 1000
        else _fetch_bybit_klines(symbol, tf, limit)
    )
    if not candles:
        raise RuntimeError("Bybit signal-feed candles unavailable")
    return list(candles)


def _required_scoring_bars(pair: dict, tf: str) -> tuple[int, dict]:
    """Derive the minimum depth needed for current Engine A normalized indicators.

    The normalized ATR/ADX features require a complete normalization window after
    the indicator warm-up. Engine B's current structural/ATR minima are lower, so
    this Engine A requirement is the controlling depth for shared scan candles.
    """
    route = route_specialist(pair)
    profile = baseline_profile(route.score_group, "intraday")
    periods = dict(profile.indicator_periods)
    normalized_asset = _FAMILY_ASSET.get(route.family, "other")
    normalization = int(get_normalization_lookback(normalized_asset))
    ema_long = int(periods["ema_long"])
    atr_period = int(periods["atr"])
    adx_period = int(periods["adx"])
    indicator_min = max(
        ema_long,
        normalization + atr_period,
        normalization + (2 * adx_period) + 1,
    )
    core_min = int(CONFIG.get(f"ENGINE_A_MIN_{tf}_BARS", 0) or 0)
    required = max(indicator_min, core_min)
    return required, {
        "score_group": route.score_group,
        "normalization_lookback": normalization,
        "ema_long": ema_long,
        "atr_period": atr_period,
        "adx_period": adx_period,
        "engine_a_core_min": core_min,
    }


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
    limits = scan_candle_limits()
    for tf in ("H4", "D1", "H1"):
        candles = _fetch_klines(pair["symbol"], tf, limits[tf])
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
            pair, tf, series, source="bybit_linear_kline"
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
        default="M15,M30,H1,H4,D1",
        help="Comma-separated timeframes",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Optional comma-separated symbol/display filter",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write the JSON report",
    )
    args = parser.parse_args()
    timeframes = [s.strip().upper() for s in args.timeframes.split(",") if s.strip()]

    pairs = _parse_crypto_pairs()
    if args.symbols.strip():
        wanted = {
            item.strip().replace("/", "").upper()
            for item in args.symbols.split(",")
            if item.strip()
        }
        pairs = [
            pair
            for pair in pairs
            if {
                str(pair.get("symbol") or "").replace("/", "").upper(),
                str(pair.get("display") or "").replace("/", "").upper(),
            }
            & wanted
        ]
    if not pairs:
        print("No enabled CRYPTO_PAIRS found.", file=sys.stderr)
        return 1

    now = time.time()
    limits = scan_candle_limits()
    rows: list[dict] = []
    summary: dict[str, int] = {}

    for pair in pairs:
        for tf in timeframes:
            if tf not in SUPPORTED_TFS:
                continue
            try:
                limit = int(limits[tf])
                candles = _fetch_klines(pair["symbol"], tf, limit)
            except Exception as exc:
                rows.append({"symbol": pair["display"], "tf": tf, "error": str(exc)})
                continue

            observed_now = time.time()

            state = split_market_state(
                candles,
                tf,
                pair["display"],
                time_now=observed_now,
                offset_hours=market_state_offset_hours(pair, tf),
            )
            scoring = engine_a_scoring_candles_from_state(pair, state, fallback=candles)
            diag = build_live_feed_diagnostic(
                pair,
                tf,
                candles,
                source="bybit_linear_kline",
                market_state=state,
                scoring_candles=scoring,
            )
            consistency = check_live_candle_consistency(
                pair,
                tf,
                _consistency_paths(pair, tf, candles, state, observed_now),
                time_now=observed_now,
            )
            stale = str(diag.get("stale_status") or "unknown")
            required_bars, requirement = _required_scoring_bars(pair, tf)
            scoring_count = len(scoring)
            summary[stale] = summary.get(stale, 0) + 1
            rows.append(
                {
                    "symbol": pair["display"],
                    "tf": tf,
                    "stale_status": stale,
                    "bucket_lag": diag.get("bucket_lag"),
                    "lag_seconds": diag.get("lag_seconds"),
                    "consistency": (consistency or {}).get("status"),
                    "requested_count": limit,
                    "raw_count": len(candles),
                    "scoring_count": scoring_count,
                    "required_scoring_bars": required_bars,
                    "count_sufficient": scoring_count >= required_bars,
                    "count_requirement": requirement,
                    "lastBarIso": diag.get("lastBarIso"),
                    "expectedIso": diag.get("expectedCurrentBucketIso"),
                }
            )
            time.sleep(0.4)

    btc = next((p for p in pairs if p["symbol"] == "BTCUSDT"), pairs[0])
    snapshot_error = None
    try:
        btc_signal = _build_signal_snapshot(btc, now)
    except Exception as exc:
        btc_signal = {}
        snapshot_error = str(exc)

    abort_reasons: list[str] = []
    df = btc_signal.get("dataFreshness") or {}
    for item in df.get("blocked") or []:
        abort_reasons.append(f"{item.get('timeframe')}:{item.get('severity')}")
    for item in df.get("warnings") or []:
        abort_reasons.append(f"{item.get('timeframe')}:{item.get('severity')}")

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "provider": "bybit_linear_kline",
        "pair_count": len(pairs),
        "stale_status_distribution": summary,
        "rows": rows,
        "btc_snapshot": {
            "symbol": btc["display"],
            "error": snapshot_error,
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
            or not r.get("count_sufficient", False)
        ],
    }

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

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
