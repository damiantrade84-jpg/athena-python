with open("athena.py", "r", encoding="utf-8") as f:
    content = f.read()

print("=== Pair Count Summary ===")

# Count enabled pairs by section
lines = content.split("\n")
in_crypto = in_forex = in_commodity = in_index = in_stock = in_etf = in_jse = False

crypto_enabled = crypto_disabled = 0
forex_enabled = forex_disabled = 0
commodity_enabled = commodity_disabled = 0
index_enabled = index_disabled = 0
stock_enabled = stock_disabled = 0
etf_enabled = etf_disabled = 0
jse_enabled = jse_disabled = 0

for line in lines:
    if "CRYPTO_PAIRS = [" in line:
        in_crypto = True
        in_forex = in_commodity = in_index = in_stock = in_etf = in_jse = False
    elif "FOREX_PAIRS = [" in line:
        in_forex = True
        in_crypto = in_commodity = in_index = in_stock = in_etf = in_jse = False
    elif "COMMODITY_PAIRS = [" in line:
        in_commodity = True
        in_crypto = in_forex = in_index = in_stock = in_etf = in_jse = False
    elif "INDEX_PAIRS = [" in line:
        in_index = True
        in_crypto = in_forex = in_commodity = in_stock = in_etf = in_jse = False
    elif "US_STOCK_PAIRS = [" in line:
        in_stock = True
        in_crypto = in_forex = in_commodity = in_index = in_etf = in_jse = False
    elif "ETF_PAIRS = [" in line:
        in_etf = True
        in_crypto = in_forex = in_commodity = in_index = in_stock = in_jse = False
    elif "JSE_PAIRS = [" in line:
        in_jse = True
        in_crypto = in_forex = in_commodity = in_index = in_stock = in_etf = False
    elif "]" in line and line.strip() == "]":
        in_crypto = in_forex = in_commodity = in_index = in_stock = in_etf = in_jse = (
            False
        )

    if 'enabled":True' in line:
        if in_crypto:
            crypto_enabled += 1
        elif in_forex:
            forex_enabled += 1
        elif in_commodity:
            commodity_enabled += 1
        elif in_index:
            index_enabled += 1
        elif in_stock:
            stock_enabled += 1
        elif in_etf:
            etf_enabled += 1
        elif in_jse:
            jse_enabled += 1
    elif 'enabled":False' in line:
        if in_crypto:
            crypto_disabled += 1
        elif in_forex:
            forex_disabled += 1
        elif in_commodity:
            commodity_disabled += 1
        elif in_index:
            index_disabled += 1
        elif in_stock:
            stock_disabled += 1
        elif in_etf:
            etf_disabled += 1
        elif in_jse:
            jse_disabled += 1

print(
    f"CRYPTO: {crypto_enabled}/{crypto_enabled + crypto_disabled} enabled, {crypto_disabled} disabled"
)
print(
    f"FOREX: {forex_enabled}/{forex_enabled + forex_disabled} enabled, {forex_disabled} disabled"
)
print(
    f"COMMODITIES: {commodity_enabled}/{commodity_enabled + commodity_disabled} enabled, {commodity_disabled} disabled"
)
print(
    f"INDICES: {index_enabled}/{index_enabled + index_disabled} enabled, {index_disabled} disabled"
)
print(
    f"US_STOCKS: {stock_enabled}/{stock_enabled + stock_disabled} enabled, {stock_disabled} disabled"
)
print(
    f"ETF: {etf_enabled}/{etf_enabled + etf_disabled} enabled, {etf_disabled} disabled"
)
print(
    f"JSE: {jse_enabled}/{jse_enabled + jse_disabled} enabled, {jse_disabled} disabled (should be 0 enabled)"
)

total_enabled = (
    crypto_enabled
    + forex_enabled
    + commodity_enabled
    + index_enabled
    + stock_enabled
    + etf_enabled
    + jse_enabled
)
total_pairs = (
    total_enabled
    + crypto_disabled
    + forex_disabled
    + commodity_disabled
    + index_disabled
    + stock_disabled
    + etf_disabled
    + jse_disabled
)
print()
print(f"TOTAL: {total_enabled}/{total_pairs} pairs enabled")
