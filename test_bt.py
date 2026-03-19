import sys
import logging

# Suppress debug logs for clean output
logging.getLogger("athena").level = logging.WARNING

try:
    from athena import backtest_pair_naked

    # Engine A compatible pair stub
    pair = {
        "symbol": "BTC/USDT",
        "display": "BTC/USDT",
        "type": "crypto",
        "exchange": "binance",
    }

    print("Running backtest for BTC/USDT...")
    result = backtest_pair_naked(pair)

    if result.get("success", True) is False:
        print("ERROR:", result.get("error"))
        sys.exit(1)

    print("\nRESULTS:")
    print(f"Total Trades: {result.get('totalTrades')}")
    print(f"Win Rate:     {result.get('winRate')}%")
    print(f"Profit Factor:{result.get('profitFactor')}")
    print(f"Expectancy:   {result.get('expectancy')}R")
    print(f"Max DD:       {result.get('maxDrawdownPct')}%")
    print(f"SQN:          {result.get('sqn')}")

except Exception as e:
    print(f"Script Crash: {e}")
    import traceback

    traceback.print_exc()
