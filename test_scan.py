import requests
import json

r = requests.post('http://localhost:5000/api/scan', json={'style':'auto'}, timeout=180)
result = r.json()
print(f"Trade signals: {len(result.get('tradeSignals', []))}")
print(f"Watchlist: {len(result.get('watchlist', []))}")
