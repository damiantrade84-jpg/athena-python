import sys
import importlib.util

spec = importlib.util.spec_from_file_location(
    "athena_mod", r"c:\Users\damia\OneDrive\Desktop\athena-python\athena.py"
)
athena = importlib.util.module_from_spec(spec)
sys.modules["athena_mod"] = athena
spec.loader.exec_module(athena)

pair = {
    "symbol": "BTCUSDT",
    "display": "BTC/USDT",
    "type": "crypto",
    "source": "binance",
}
print("Running analyze_pair with use_naked_engine=True...")
result = athena.analyze_pair(
    pair, btc_bias="bullish", style="intraday", use_naked_engine=True
)

if result:
    print("\nSUCCESS!")
    print(
        f"Signal: {result.get('direction')} | Score: {result.get('confluenceScore')} | SL: {result.get('sl')} | TP1: {result.get('tp1')}"
    )
    print(f"Warnings: {result.get('warnings')}")
else:
    print("\nNO SIGNAL or FILTERED BY ENGINE B")
