#!/usr/bin/env python
"""CLI fallback for live feed diagnostics.

Read-only candle freshness diagnostics. Does not place trades or call broker APIs.
Safe to run while market is open or closed.

Usage:
    python tools/live_feed_diagnostics.py --symbols EURUSD,GBPUSD,BTCUSDT,ETHUSDT --timeframes H1,H4,D1
"""

import argparse
import json
import sys
from datetime import datetime, timezone as tz
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from athena_app.services.data_freshness import build_live_feed_diagnostic, check_live_candle_consistency
from athena_app.services.engine_b_market_state import engine_b_live_market_state
from athena_app.services.market_state import get_tf_market_state


def _get_active_pairs():
    """Return ACTIVE_PAIRS from config."""
    return config.ACTIVE_PAIRS


def _fetch_candles_for_pair(pair, tf, limit):
    """Fetch candles for a pair/timeframe. Read-only."""
    from data_feeds import fetch_candles_live
    
    try:
        candles = fetch_candles_live(pair, tf, limit=limit)
        return candles, "ok", None
    except Exception as e:
        return [], "error", str(e)


def main():
    parser = argparse.ArgumentParser(
        description="Live feed diagnostics - read-only candle freshness check"
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="Comma-separated list of symbols (e.g., EURUSD,GBPUSD,BTCUSDT,ETHUSDT). "
             "If empty, checks all active pairs."
    )
    parser.add_argument(
        "--timeframes",
        type=str,
        default="H1,H4,D1",
        help="Comma-separated list of timeframes (e.g., H1,H4,D1). Default: H1,H4,D1"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Number of candles to fetch. Default: 500"
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Output raw JSON instead of formatted table"
    )

    args = parser.parse_args()

    # Parse inputs
    raw_symbols = args.symbols.strip()
    if raw_symbols:
        wanted_symbols = {s.strip().upper() for s in raw_symbols.split(",") if s.strip()}
    else:
        wanted_symbols = None

    raw_tfs = args.timeframes.strip()
    timeframes = [s.strip().upper() for s in raw_tfs.split(",") if s.strip()] if raw_tfs else ["H1", "H4", "D1"]

    # Get active pairs
    active_pairs = _get_active_pairs()
    pairs = []
    for pair in active_pairs:
        keys = {
            str(pair.get("display", "")).upper(),
            str(pair.get("symbol", "")).upper(),
        }
        if wanted_symbols and not (wanted_symbols & keys):
            continue
        pairs.append(pair)

    if not pairs:
        print("No matching pairs found.")
        return

    # Collect diagnostics
    rows = []
    generated_at = datetime.now(tz.utc).isoformat()
    
    for pair in pairs:
        for tf_u in timeframes:
            limit = args.limit
            
            # Fetch candles
            candles, provider_status, provider_error = _fetch_candles_for_pair(pair, tf_u, limit)
            
            # Build diagnostic
            diag = build_live_feed_diagnostic(
                pair,
                tf_u,
                candles,
                source="cli",
                provider_status=provider_status,
                provider_error=provider_error,
            )
            
            # Add market state consistency check
            try:
                state_a = get_tf_market_state(pair, tf_u)
                state_b = engine_b_live_market_state(pair, tf_u)
                
                engine_a_input = []
                if isinstance(state_a, dict):
                    engine_a_input = list(state_a.get("forming", [])) + list(state_a.get("confirmed", []))
                engine_b_input = list(state_b.get("confirmed", [])) if isinstance(state_b, dict) else []
                scanner_input = list(engine_a_input)
                
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
                        "compare": scanner_input,
                    },
                )
            except Exception as e:
                diag["consistency"] = {"error": str(e)}
            
            rows.append(diag)

    # Output
    result = {
        "success": True,
        "generatedAt": generated_at,
        "count": len(rows),
        "diagnostics": rows,
        "tradesPlaced": 0,
    }

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        # Formatted table output
        print(f"\nLive Feed Diagnostics - Generated: {generated_at}")
        print(f"Pairs checked: {len(pairs)} | Timeframes: {', '.join(timeframes)}")
        print(f"Total diagnostics: {len(rows)}\n")
        
        for row in rows:
            symbol = row.get("symbol", "UNKNOWN")
            tf = row.get("timeframe", "UNKNOWN")
            source = row.get("source", "UNKNOWN")
            candle_count = row.get("candleCount", 0)
            last_bar_iso = row.get("lastBarIso", "N/A")
            staleness = row.get("stalenessSeverity", "unknown")
            provider_status = row.get("providerStatus", "unknown")
            provider_error = row.get("providerError", "")
            
            print(f"{symbol} [{tf}]")
            print(f"  Source: {source}")
            print(f"  Candles: {candle_count}")
            print(f"  Last bar: {last_bar_iso}")
            print(f"  Staleness: {staleness}")
            print(f"  Provider: {provider_status}")
            if provider_error:
                print(f"  Error: {provider_error}")
            
            consistency = row.get("consistency", {})
            if consistency and "error" not in consistency:
                print(f"  Consistency: OK")
            elif consistency:
                print(f"  Consistency: {consistency.get('error', 'unknown')}")
            
            print()


if __name__ == "__main__":
    main()
