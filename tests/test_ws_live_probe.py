import asyncio
import json
import os

import pytest

websockets = pytest.importorskip(
    "websockets",
    reason="websockets package is required for the opt-in live websocket probe.",
)


KEY = os.environ.get("EODHD_API_KEY", "")


async def probe_endpoint(endpoint, symbols, n=3):
    url = f"wss://ws.eodhistoricaldata.com/ws/{endpoint}?api_token={KEY}"
    print(f"\n=== {endpoint} ({symbols}) ===")
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"action": "subscribe", "symbols": symbols}))
        messages = []
        for i in range(n):
            msg = await asyncio.wait_for(ws.recv(), timeout=15)
            data = json.loads(msg)
            messages.append(data)
            print(f"  {i}: {data}")
            for k, v in data.items():
                print(f"    {k}: {type(v).__name__} = {repr(v)}")
        return messages


async def main():
    await probe_endpoint("crypto", "BTC-USD", 3)
    await probe_endpoint("forex", "EURUSD", 3)
    await probe_endpoint("us", "GOOG", 3)


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_WS_TESTS") != "1",
    reason="Live websocket probe is opt-in. Set RUN_LIVE_WS_TESTS=1 to execute.",
)
@pytest.mark.skipif(
    not KEY,
    reason="Live websocket probe requires EODHD_API_KEY.",
)
def test_ws_live_probe():
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
