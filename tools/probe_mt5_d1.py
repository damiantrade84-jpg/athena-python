#!/usr/bin/env python3
"""Probe MT5 D1 raw fetch - read-only diagnostic helper.

Bypasses scanner/engine trimming to directly check what MT5 returns.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description="Probe MT5 D1 raw fetch - read-only")
    parser.add_argument("--symbols", type=str, default="EURUSD,GBPUSD", help="Comma-separated symbols")
    parser.add_argument("--count", type=int, default=5, help="Number of bars to fetch")
    parser.add_argument("--json-output", action="store_true", help="Output JSON")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    # Lazy import MT5
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("Error: MetaTrader5 package not installed. Run: pip install MetaTrader5")
        sys.exit(1)

    # Initialize MT5
    mt5_initialized = mt5.initialize()
    if not mt5_initialized:
        print("Error: MT5 initialize failed")
        sys.exit(1)

    # Get terminal info
    terminal_info = mt5.terminal_info()
    account_info = mt5.account_info()

    results = []
    now = datetime.now(timezone.utc)
    expected_current_d1_epoch = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    expected_confirmed_d1_epoch = expected_current_d1_epoch - 86400  # Previous day

    for symbol in symbols:
        # Symbol selection
        symbol_selected = mt5.symbol_select(symbol, True)
        symbol_visible = mt5.symbol_info(symbol).visible if mt5.symbol_info(symbol) else False
        symbol_info = mt5.symbol_info(symbol)

        # Last tick
        tick = mt5.symbol_info_tick(symbol)
        last_tick_time_iso = None
        if tick and tick.time > 0:
            last_tick_time_iso = datetime.fromtimestamp(tick.time, timezone.utc).isoformat().replace("+00:00", "Z")

        # MT5 last error
        last_error = mt5.last_error()

        # Fetch D1 bars using copy_rates_from_pos (start_pos=0)
        copy_method = "copy_rates_from_pos"
        bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, args.count)

        if bars is None or len(bars) == 0:
            results.append({
                "symbol": symbol,
                "mt5Initialized": mt5_initialized,
                "terminalInfoAvailable": terminal_info is not None,
                "accountInfoAvailable": account_info is not None,
                "symbolSelected": symbol_selected,
                "symbolVisible": symbol_visible,
                "symbolInfoAvailable": symbol_info is not None,
                "lastTickTimeIso": last_tick_time_iso,
                "mt5LastError": last_error,
                "copyMethod": copy_method,
                "timeframe": "D1",
                "returnedCount": 0,
                "rawBarOpenTimesIso": [],
                "latestRawBarIso": None,
                "expectedCurrentD1Iso": datetime.fromtimestamp(expected_current_d1_epoch, timezone.utc).isoformat().replace("+00:00", "Z"),
                "expectedConfirmedD1Iso": datetime.fromtimestamp(expected_confirmed_d1_epoch, timezone.utc).isoformat().replace("+00:00", "Z"),
                "hasCurrentD1": False,
                "hasExpectedConfirmedD1": False,
                "providerStatus": "error",
                "providerError": f"MT5 copy_rates_from_pos returned None or empty: {last_error}",
            })
            continue

        # Extract bar times
        raw_bar_times = []
        for bar in bars:
            # MT5 returns time as a struct, need to convert
            bar_time = int(bar['time'])
            bar_iso = datetime.fromtimestamp(bar_time, timezone.utc).isoformat().replace("+00:00", "Z")
            raw_bar_times.append(bar_iso)

        # copy_rates_from_pos returns chronological order (oldest to newest), so latest is last
        latest_raw_bar_iso = raw_bar_times[-1] if raw_bar_times else None
        has_current_d1 = latest_raw_bar_iso == datetime.fromtimestamp(expected_current_d1_epoch, timezone.utc).isoformat().replace("+00:00", "Z")
        has_expected_confirmed_d1 = expected_confirmed_d1_epoch in [int(datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()) for t in raw_bar_times]

        results.append({
            "symbol": symbol,
            "mt5Initialized": mt5_initialized,
            "terminalInfoAvailable": terminal_info is not None,
            "accountInfoAvailable": account_info is not None,
            "symbolSelected": symbol_selected,
            "symbolVisible": symbol_visible,
            "symbolInfoAvailable": symbol_info is not None,
            "lastTickTimeIso": last_tick_time_iso,
            "mt5LastError": last_error,
            "copyMethod": copy_method,
            "timeframe": "D1",
            "returnedCount": len(bars),
            "rawBarOpenTimesIso": raw_bar_times,
            "latestRawBarIso": latest_raw_bar_iso,
            "expectedCurrentD1Iso": datetime.fromtimestamp(expected_current_d1_epoch, timezone.utc).isoformat().replace("+00:00", "Z"),
            "expectedConfirmedD1Iso": datetime.fromtimestamp(expected_confirmed_d1_epoch, timezone.utc).isoformat().replace("+00:00", "Z"),
            "hasCurrentD1": has_current_d1,
            "hasExpectedConfirmedD1": has_expected_confirmed_d1,
            "providerStatus": "ok" if has_current_d1 or has_expected_confirmed_d1 else "stale",
            "providerError": None if has_current_d1 or has_expected_confirmed_d1 else "MT5 D1 does not have current or expected confirmed D1 bar",
        })

    mt5.shutdown()

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }

    if args.json_output:
        print(json.dumps(output, indent=2))
    else:
        print(f"\nMT5 D1 Probe - Generated: {datetime.now(timezone.utc).isoformat()}")
        for r in results:
            print(f"\nSymbol: {r['symbol']}")
            print(f"  MT5 Initialized: {r['mt5Initialized']}")
            print(f"  Terminal Info: {r['terminalInfoAvailable']}")
            print(f"  Account Info: {r['accountInfoAvailable']}")
            print(f"  Symbol Selected: {r['symbolSelected']}")
            print(f"  Symbol Visible: {r['symbolVisible']}")
            print(f"  Symbol Info: {r['symbolInfoAvailable']}")
            print(f"  Last Tick Time: {r['lastTickTimeIso']}")
            print(f"  MT5 Last Error: {r['mt5LastError']}")
            print(f"  Copy Method: {r['copyMethod']}")
            print(f"  Timeframe: {r['timeframe']}")
            print(f"  Returned Count: {r['returnedCount']}")
            print(f"  Raw Bar Times: {r['rawBarOpenTimesIso']}")
            print(f"  Latest Raw Bar: {r['latestRawBarIso']}")
            print(f"  Expected Current D1: {r['expectedCurrentD1Iso']}")
            print(f"  Expected Confirmed D1: {r['expectedConfirmedD1Iso']}")
            print(f"  Has Current D1: {r['hasCurrentD1']}")
            print(f"  Has Expected Confirmed D1: {r['hasExpectedConfirmedD1']}")
            print(f"  Provider Status: {r['providerStatus']}")
            if r['providerError']:
                print(f"  Provider Error: {r['providerError']}")


if __name__ == "__main__":
    main()
