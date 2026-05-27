import sys
import logging
logging.getLogger("athena").level = logging.WARNING
try:
    from athena import backtest_pair_naked

    pair = {
        "symbol": "USDJPY.FOREX",
        "display": "USD/JPY",
        "type": "forex",
        "exchange": "eodhd"
    }

    print("Running backtest for USD/JPY...")
    # By default, backtest_pair_naked uses style="naked" which resolves to intraday
    result = backtest_pair_naked(pair, style="intraday")

    if result.get("success", True) is False:
        print("ERROR:", result.get("error"))
        sys.exit(1)

    print("\nRESULTS:")
    print(f"Total Trades: {result.get('totalTrades')}")
    print(f"Win Rate:     {result.get('winRate', 0)}%")
    print(f"Expectancy:   {result.get('expectancy', 0)}R")
    print(f"SQN:          {result.get('sqn', 0)}")
    
    outcomes = {}
    for t in result.get("trades", []):
        o = t.get("outcome", "UNKNOWN")
        outcomes[o] = outcomes.get(o, 0) + 1
    print(f"Outcomes (last 50): {outcomes}")

except Exception as e:
    print(f"Script Crash: {e}")
    import traceback
    traceback.print_exc()
