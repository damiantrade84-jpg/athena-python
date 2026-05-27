#!/usr/bin/env python3
import importlib.util

spec = importlib.util.spec_from_file_location("athena_main", "athena.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

print(f"Total pairs: {len(mod.ALL_PAIRS)}")
print(f"Active pairs: {len(mod.ACTIVE_PAIRS)}")
print(f"JSE pairs: {len(mod.JSE_PAIRS)}")
print(f"FOREX pairs: {len(mod.FOREX_PAIRS)}")
print(f"COMMODITY pairs: {len(mod.COMMODITY_PAIRS)}")
print(f"INDEX pairs: {len(mod.INDEX_PAIRS)}")
print(f"US_STOCK pairs: {len(mod.US_STOCK_PAIRS)}")
print(f"ETF pairs: {len(mod.ETF_PAIRS)}")
print(f"CRYPTO pairs: {len(mod.CRYPTO_PAIRS)}")

# Check JSE pairs are in ALL_PAIRS
jse_symbols = [p["display"] for p in mod.JSE_PAIRS]
all_symbols = [p["display"] for p in mod.ALL_PAIRS]
missing_jse = [s for s in jse_symbols if s not in all_symbols]
if missing_jse:
    print(f"Missing JSE pairs from ALL_PAIRS: {missing_jse}")
else:
    print("All JSE pairs are included in ALL_PAIRS")
