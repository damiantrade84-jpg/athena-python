import logging
import sys
import importlib.util

logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)
log = logging.getLogger("athena")

spec = importlib.util.spec_from_file_location("athena_module", "athena.py")
athena_module = importlib.util.module_from_spec(spec)
sys.modules["athena_module"] = athena_module
spec.loader.exec_module(athena_module)

def test():
    pair = {"display": "EUR/USD", "type": "forex", "symbol": "EURUSD", "source": "mt5", "enabled": True}
    print(f"Testing analyze_pair for {pair['display']}...")
    
    res = athena_module.analyze_pair(pair, btc_bias="neutral", style="swing")
    
    if res:
        print(f"Result score: {res.get('score')} | direction: {res.get('direction')} | evalThreshold: {res.get('evalThreshold')}")
        print("Components:", res.get("components"))
    else:
        print("analyze_pair returned None (No signal or skipped)")

if __name__ == "__main__":
    test()
