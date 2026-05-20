#!/usr/bin/env python
"""CLI fallback for live feed diagnostics.

Read-only candle freshness diagnostics. Does not place trades or call broker APIs.
Safe to run while market is open or closed.

Usage:
    python tools/live_feed_diagnostics.py --symbols EURUSD,GBPUSD,XAUUSD,NAS100 --timeframes H1,H4,D1
    python tools/live_feed_diagnostics.py --asset-types forex,commodity,index,stock --json-output
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone as tz
from pathlib import Path

os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def _load_athena_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("athena_main", ROOT / "athena.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _normalize_symbol(sym: str) -> str:
    return sym.replace("/", "").replace("=X", "").replace("=", "").upper()


def _load_pairs(asset_types: set[str] | None) -> list[dict]:
    """Load pairs from athena.py ACTIVE_PAIRS with optional non-crypto filter."""
    mod = _load_athena_module()
    pairs = []
    for pair in mod.ACTIVE_PAIRS:
        ptype = str(pair.get("type") or "").lower()
        if asset_types and ptype not in asset_types:
            continue
        if ptype == "crypto" and asset_types and "crypto" not in asset_types:
            continue
        pairs.append(pair)
    return pairs


def _default_representative_pairs() -> list[dict]:
    """Representative non-crypto subset when no filter matches."""
    mod = _load_athena_module()

    wanted = {
        "EURUSD", "GBPUSD", "USDJPY", "EURCHF",
        "XAUUSD", "WTI", "NASDAQ100", "SP500",
        "AAPL", "TSLA", "SPY",
    }
    out = []
    for pair in mod.ACTIVE_PAIRS:
        keys = {
            _normalize_symbol(str(pair.get("display", ""))),
            _normalize_symbol(str(pair.get("symbol", ""))),
        }
        if keys & wanted:
            out.append(pair)
    return out or _load_pairs({"forex", "commodity", "index", "stock", "etf"})


def main():
    parser = argparse.ArgumentParser(
        description="Live feed diagnostics - read-only candle freshness check"
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="Comma-separated symbols (e.g., EURUSD,GBPUSD,XAUUSD,NAS100)",
    )
    parser.add_argument(
        "--asset-types",
        type=str,
        default="",
        help="Comma-separated asset types (forex,commodity,index,stock,etf). Excludes crypto by default.",
    )
    parser.add_argument(
        "--timeframes",
        type=str,
        default="H1,H4,D1",
        help="Comma-separated timeframes",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Number of candles to fetch",
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Output raw JSON",
    )

    args = parser.parse_args()

    asset_types = None
    if args.asset_types.strip():
        asset_types = {s.strip().lower() for s in args.asset_types.split(",") if s.strip()}
    elif not args.symbols.strip():
        asset_types = {"forex", "commodity", "index", "stock", "etf"}

    raw_symbols = args.symbols.strip()
    wanted_symbols = (
        {_normalize_symbol(s.strip()) for s in raw_symbols.split(",") if s.strip()}
        if raw_symbols
        else None
    )

    timeframes = [s.strip().upper() for s in args.timeframes.split(",") if s.strip()]

    if wanted_symbols:
        all_pairs = _load_pairs(asset_types)
        pairs = []
        for pair in all_pairs:
            keys = {
                _normalize_symbol(str(pair.get("display", ""))),
                _normalize_symbol(str(pair.get("symbol", ""))),
            }
            if wanted_symbols & keys:
                pairs.append(pair)
    elif asset_types:
        pairs = _load_pairs(asset_types)
    else:
        pairs = _default_representative_pairs()

    if not pairs:
        print("No matching pairs found.")
        return

    import config
    from athena_app.services.candle_service import engine_a_scoring_candles_from_state
    from athena_app.services.data_freshness import (
        build_live_feed_diagnostic,
        check_live_candle_consistency,
    )
    from athena_app.services.engine_b_market_state import engine_b_live_market_state
    from athena_app.services.market_state import get_tf_market_state
    from candles_cache import get_candle_fetch_meta

    mod = _load_athena_module()
    fetch_candles = mod.fetch_candles

    rows = []
    generated_at = datetime.now(tz.utc).isoformat()
    _off = float(config.CONFIG.get("FOREX_H4_RESAMPLE_OFFSET_HOURS", 1.0) or 1.0)

    for pair in pairs:
        for tf_u in timeframes:
            provider_status = "ok"
            provider_error = None
            candles = []
            try:
                candles = list(fetch_candles(pair, tf_u, args.limit) or [])
            except Exception as e:
                provider_status = "error"
                provider_error = str(e)

            meta = get_candle_fetch_meta(pair, tf_u, args.limit) or {}
            state_a = get_tf_market_state(pair, tf_u, candles=candles or [])
            engine_a_input = engine_a_scoring_candles_from_state(pair, state_a, fallback=candles)

            diag = build_live_feed_diagnostic(
                pair,
                tf_u,
                candles,
                fetch_meta=meta,
                source=meta.get("upstream") or pair.get("source"),
                provider_status=provider_status,
                provider_error=provider_error,
                market_state=state_a,
                scoring_candles=engine_a_input,
            )

            try:
                state_b = engine_b_live_market_state(
                    pair, tf_u, len(candles or []), candles=candles or []
                )
                engine_b_input = list(state_b.get("confirmed") or [])
                scanner_input = list(state_b.get("confirmed") or [])
                compare_input = list(state_b.get("confirmed") or [])

                diag["consistency"] = check_live_candle_consistency(
                    pair,
                    tf_u,
                    {
                        "raw_provider": candles or [],
                        "cache": diag,
                        "market_state": state_a,
                        "engine_a": engine_a_input,
                        "engine_b": engine_b_input,
                        "scanner": scanner_input,
                        "compare": compare_input,
                    },
                )
            except Exception as e:
                diag["consistency"] = {"error": str(e)}

            rows.append(diag)

    result = {
        "success": True,
        "generatedAt": generated_at,
        "count": len(rows),
        "diagnostics": rows,
        "tradesPlaced": 0,
        "forexH4ResampleOffsetHours": _off,
        "mt5H4OffsetNote": (
            "FOREX_H4_RESAMPLE_OFFSET_HOURS drives MT5 H4 bar grid in market_state"
        ),
    }

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"\nLive Feed Diagnostics - Generated: {generated_at}")
        print(f"Pairs checked: {len(pairs)} | Timeframes: {', '.join(timeframes)}")
        print(f"Total diagnostics: {len(rows)}\n")

        for row in rows:
            symbol = row.get("symbol", "UNKNOWN")
            tf = row.get("timeframe", "UNKNOWN")
            source = row.get("source", "UNKNOWN")
            stale = row.get("stale_status", row.get("stalenessSeverity", "unknown"))
            print(f"{symbol} [{tf}] source={source} stale={stale} "
                  f"raw={row.get('raw_count')} scoring={row.get('scoring_count')}")


if __name__ == "__main__":
    main()
