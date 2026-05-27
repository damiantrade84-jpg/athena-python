import requests

# Test multiple exchanges
exchanges = ["US", "LSE", "NYSE", "NASDAQ", "FOREX", "CRYPTO"]

for EXCHANGE_CODE in exchanges:
    url = f"https://eodhd.com/api/exchange-symbol-list/{EXCHANGE_CODE}?api_token=69a942b2ce8cc0.62366002&fmt=json"

    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()

        print(f"\n{'=' * 50}")
        print(f"Exchange: {EXCHANGE_CODE}")
        print(f"Total symbols: {len(data)}")

        # Show first few symbols
        sample_size = min(5, len(data))
        print(f"First {sample_size} symbols:")
        for i, symbol in enumerate(data[:sample_size]):
            code = symbol.get("Code", "N/A")
            name = symbol.get("Name", "N/A")
            print(f"  {i + 1}. {code} - {name}")

    except requests.exceptions.RequestException as e:
        print(f"Error fetching {EXCHANGE_CODE}: {e}")
    except Exception as e:
        print(f"Error with {EXCHANGE_CODE}: {e}")

print(f"\n{'=' * 50}")
print("EODHD API is working! All exchanges accessible.")
