#!/usr/bin/env python
"""Probe MT5 H4 candle grids across asset types.

Read-only diagnostic tool to detect H4 bucket alignment per symbol group.
"""

import argparse
import json
import sys
from datetime import datetime, timezone as tz
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from athena_app.services.market_state import (
    candle_timestamp_epoch,
    get_bucket_start_epoch,
    market_state_offset_hours,
)


def _get_active_pairs():
    """Return representative pairs for probing H4 grids."""
    return [
        {"symbol": "EURUSD=X", "display": "EUR/USD", "type": "forex", "source": "mt5", "enabled": True},
        {"symbol": "GBPUSD=X", "display": "GBP/USD", "type": "forex", "source": "mt5", "enabled": True},
        {"symbol": "XAUUSD=X", "display": "XAU/USD", "type": "commodity", "source": "mt5", "enabled": True},
        {"symbol": "XAGUSD=X", "display": "XAG/USD", "type": "commodity", "source": "mt5", "enabled": True},
        {"symbol": "AAPL.US", "display": "AAPL", "type": "stock", "source": "mt5", "enabled": True},
        {"symbol": "NVDA.US", "display": "NVDA", "type": "stock", "source": "mt5", "enabled": True},
        {"symbol": "NAS100", "display": "NASDAQ-100", "type": "index", "source": "mt5", "enabled": True},
        {"symbol": "^GSPC", "display": "S&P 500", "type": "index", "source": "mt5", "enabled": True},
        {"symbol": "WTI Oil", "display": "WTI Oil", "type": "commodity", "source": "mt5", "enabled": True},
    ]


def _fetch_candles_for_pair(pair, tf, limit):
    """Fetch candles for a pair/timeframe. Read-only."""
    from candle_manager import fetch_market_state
    
    try:
        state = fetch_market_state(pair, tf, limit)
        candles = []
        if isinstance(state, dict):
            confirmed = state.get("confirmed", [])
            forming = state.get("forming")
            candles = list(confirmed)
            if forming:
                candles.append(forming)
        return candles, "ok", None
    except Exception as e:
        return [], "error", str(e)


def _infer_h4_grid(candles):
    """Infer H4 grid from candle timestamps."""
    if not candles or len(candles) < 2:
        return "unknown", []
    
    # Extract hour of day from last 10 candles
    hours = []
    for candle in candles[-10:]:
        epoch = candle_timestamp_epoch(candle)
        if epoch:
            dt = datetime.fromtimestamp(epoch, tz.utc)
            hours.append(dt.hour)
    
    if not hours:
        return "unknown", []
    
    # Count frequency of each hour
    from collections import Counter
    hour_counts = Counter(hours)
    
    # Most common hours indicate the grid
    common_hours = [h for h, _ in hour_counts.most_common(6)]
    common_hours.sort()
    
    # Detect grid pattern
    # MT5 standard: 01/05/09/13/17/21 UTC
    # Session-based: varies by exchange
    mt5_grid = {1, 5, 9, 13, 17, 21}
    
    if set(common_hours) == mt5_grid:
        return "mt5_standard", common_hours
    elif set(common_hours).issubset(mt5_grid):
        return "mt5_partial", common_hours
    else:
        return "session_based", common_hours


def _detect_offset_hours(candles, tf):
    """Detect actual offset hours from candle timestamps."""
    if not candles:
        return 0.0
    
    # For H4, expected bucket is 14400 seconds
    tf_sec = 14400
    
    # Get last candle epoch
    last_epoch = candle_timestamp_epoch(candles[-1])
    if not last_epoch:
        return 0.0
    
    # Compute expected bucket without offset
    expected_no_offset = (last_epoch // tf_sec) * tf_sec
    
    # Compute expected bucket with 1-hour offset
    expected_with_offset = ((last_epoch - 3600) // tf_sec) * tf_sec + 3600
    
    # Check which one matches
    if abs(last_epoch - expected_with_offset) < 60:
        return 1.0
    elif abs(last_epoch - expected_no_offset) < 60:
        return 0.0
    else:
        # Try other offsets
        for offset in [2.0, 3.0, 4.0, 5.0]:
            expected = ((last_epoch - offset * 3600) // tf_sec) * tf_sec + offset * 3600
            if abs(last_epoch - expected) < 60:
                return offset
        return 0.0


def main():
    parser = argparse.ArgumentParser(
        description="Probe MT5 H4 candle grids across asset types"
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="Comma-separated list of symbols. If empty, probes all representative pairs."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Number of candles to fetch for grid inference. Default: 10"
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Output raw JSON instead of formatted table"
    )

    args = parser.parse_args()

    # Parse symbols
    raw_symbols = args.symbols.strip()
    if raw_symbols:
        wanted_symbols = {s.strip().upper() for s in raw_symbols.split(",") if s.strip()}
    else:
        wanted_symbols = None

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

    # Probe each pair
    results = []
    time_now = datetime.now(tz.utc).timestamp()
    
    for pair in pairs:
        tf = "H4"
        candles, provider_status, provider_error = _fetch_candles_for_pair(pair, tf, args.count)
        
        # Extract raw bar times
        raw_bar_times = []
        for candle in candles[-10:]:
            epoch = candle_timestamp_epoch(candle)
            if epoch:
                dt = datetime.fromtimestamp(epoch, tz.utc)
                raw_bar_times.append(dt.isoformat())
        
        # Infer H4 grid
        grid_type, grid_hours = _infer_h4_grid(candles)
        
        # Get configured offset
        configured_offset = market_state_offset_hours(pair, tf)
        
        # Detect actual offset
        detected_offset = _detect_offset_hours(candles, tf)
        
        # Get latest raw bar
        latest_raw_bar_iso = None
        if candles:
            epoch = candle_timestamp_epoch(candles[-1])
            if epoch:
                latest_raw_bar_iso = datetime.fromtimestamp(epoch, tz.utc).isoformat()
        
        # Compute expected confirmed with configured offset
        expected_confirmed_epoch = get_bucket_start_epoch(tf, time_now, configured_offset) - 14400
        expected_confirmed_iso = datetime.fromtimestamp(expected_confirmed_epoch, tz.utc).isoformat() if expected_confirmed_epoch else None
        
        # Check matches
        matches_configured_offset = abs(detected_offset - configured_offset) < 0.1
        matches_session_grid = grid_type == "mt5_standard" or grid_type == "mt5_partial"
        
        result = {
            "symbol": pair.get("symbol"),
            "display": pair.get("display"),
            "asset_type": pair.get("type"),
            "source": pair.get("source"),
            "rawBarOpenTimesIso": raw_bar_times,
            "inferredH4Grid": grid_type,
            "gridHours": grid_hours,
            "configuredOffsetHours": configured_offset,
            "detectedOffsetHours": detected_offset,
            "expectedConfirmedWithConfiguredOffset": expected_confirmed_iso,
            "latestRawBarIso": latest_raw_bar_iso,
            "matchesConfiguredOffset": matches_configured_offset,
            "matchesSessionGrid": matches_session_grid,
            "providerStatus": provider_status,
            "providerError": provider_error,
        }
        results.append(result)

    # Output
    if args.json_output:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(f"\nMT5 H4 Grid Probe - Generated: {datetime.now(tz.utc).isoformat()}")
        print(f"Pairs probed: {len(results)}\n")
        
        for r in results:
            print(f"{r['display']} ({r['asset_type']})")
            print(f"  Source: {r['source']}")
            print(f"  Inferred H4 Grid: {r['inferredH4Grid']} (hours: {r['gridHours']})")
            print(f"  Configured Offset: {r['configuredOffsetHours']}h")
            print(f"  Detected Offset: {r['detectedOffsetHours']}h")
            print(f"  Latest Raw Bar: {r['latestRawBarIso']}")
            print(f"  Matches Configured Offset: {r['matchesConfiguredOffset']}")
            print(f"  Matches Session Grid: {r['matchesSessionGrid']}")
            print(f"  Provider: {r['providerStatus']}")
            if r['providerError']:
                print(f"  Error: {r['providerError']}")
            print()


if __name__ == "__main__":
    main()
