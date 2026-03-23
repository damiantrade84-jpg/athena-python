import asyncio, json, time, websockets

async def probe_binance():
    url = 'wss://stream.binance.com:9443/stream?streams=btcusdt@depth20@100ms/btcusdt@trade'
    t0 = time.time()
    n = 0
    async with websockets.connect(url, ping_interval=None, ping_timeout=None, open_timeout=45, close_timeout=10) as ws:
        while time.time() - t0 < 20:
            raw = await asyncio.wait_for(ws.recv(), timeout=45)
            json.loads(raw)
            n += 1
    print('binance_msgs', n)

async def probe_bybit():
    url = 'wss://stream.bybit.com/v5/public/linear'
    ws = await websockets.connect(url, ping_interval=None, ping_timeout=None, open_timeout=45, close_timeout=10)
    await ws.send(json.dumps({'req_id': str(int(time.time() * 1000)), 'op': 'subscribe', 'args': ['orderbook.50.BTCUSDT', 'publicTrade.BTCUSDT']}))
    t0 = time.time()
    last_ping = time.time()
    n = 0
    try:
        while time.time() - t0 < 20:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode('utf-8', errors='replace')
                msg = json.loads(raw)
                if isinstance(msg, dict) and msg.get('op') == 'ping':
                    await ws.send(json.dumps({'op': 'pong'}))
                    last_ping = time.time()
                    continue
                n += 1
            except asyncio.TimeoutError:
                if time.time() - last_ping > 40:
                    await ws.send(json.dumps({'op': 'ping'}))
                    last_ping = time.time()
                    continue
                raise
    finally:
        await ws.close()
    print('bybit_msgs', n)

async def main():
    await probe_binance()
    await probe_bybit()

asyncio.run(main())
