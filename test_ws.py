import asyncio, websockets, json

KEY = "69a942b2ce8cc0.62366002"

async def test(endpoint, symbols, n=3):
    url = f"wss://ws.eodhistoricaldata.com/ws/{endpoint}?api_token={KEY}"
    print(f"\n=== {endpoint} ({symbols}) ===")
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"action": "subscribe", "symbols": symbols}))
        for i in range(n):
            msg = await asyncio.wait_for(ws.recv(), timeout=15)
            d = json.loads(msg)
            print(f"  {i}: {d}")
            # Check types
            for k, v in d.items():
                print(f"    {k}: {type(v).__name__} = {repr(v)}")

async def main():
    await test("crypto", "BTC-USD", 3)
    await test("forex", "EURUSD", 3)
    await test("us", "GOOG", 3)

asyncio.run(main())
