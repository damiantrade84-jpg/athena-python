"""bybit_ws.py — Bybit WebSocket client for order book and trade streams.

Streams orderbook.50 and publicTrade for linear futures, maintains local order book,
computes order book imbalance and orderflow delta, and emits metrics to Athena pipeline.
"""
import asyncio
import json
import logging
import time
from typing import Dict, List, Tuple, Optional
import websockets
import telegram_notify

log = logging.getLogger("sentinel")
from athena.microstructure.microstructure_store import store_metrics
from athena.microstructure.orderbook_metrics import (
    liquidity_wall_detection as _liq_wall,
    liquidity_pressure as _liq_pressure,
)


class BybitWS:
    """Bybit WebSocket client for orderbook.50 and publicTrade streams."""
    def __init__(self, symbol: str = "BTCUSDT", emit_interval: float = 1.0):
        self.symbol = symbol.upper()
        self.base_url = "wss://stream.bybit.com/v5/public/linear"
        self.emit_interval = emit_interval
        self.orderbook: Dict[str, List[Tuple[float, float]]] = {"bids": [], "asks": []}
        self.orderflow_delta: float = 0.0
        self._running = False
        self._ws: Optional[websockets.WebSocketServerProtocol] = None
        self._tasks: List[asyncio.Task] = []
        # Metrics callbacks
        self.on_metrics: Optional[callable] = None

    async def _connect(self) -> None:
        """Connect to Bybit WebSocket and subscribe to streams."""
        try:
            self._ws = await websockets.connect(
                self.base_url,
                ping_interval=None,  # disable built-in keepalive — app-level ping/pong handles this
            )
            log.info(f"[BybitWS] Connected to {self.base_url}")
            # Subscribe to orderbook.50 and publicTrade
            subscribe_msg = {
                "req_id": str(int(time.time() * 1000)),
                "op": "subscribe",
                "args": [
                    f"orderbook.50.{self.symbol}",
                    f"publicTrade.{self.symbol}",
                ],
            }
            await self._ws.send(json.dumps(subscribe_msg))
            log.info(f"[BybitWS] Subscribed to orderbook.50 and publicTrade for {self.symbol}")
            # Listen for messages
            _last_ping = time.time()
            while self._running:
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=60)
                    msg = json.loads(raw)
                    # Handle server-side ping — respond with pong to keep connection alive.
                    # Bybit sends {"op":"ping"} every ~20s; without a pong reply the server
                    # closes the connection, causing the 6+/min reconnect storm seen in logs.
                    if isinstance(msg, dict) and msg.get("op") == "ping":
                        await self._ws.send(json.dumps({"op": "pong"}))
                        _last_ping = time.time()
                        continue
                    await self._handle_message(msg)
                except asyncio.TimeoutError:
                    # Proactive heartbeat if server hasn't pinged us in 40s
                    if time.time() - _last_ping > 40:
                        try:
                            await self._ws.send(json.dumps({"op": "ping"}))
                            _last_ping = time.time()
                            continue
                        except Exception:
                            pass
                    log.warning("[BybitWS] Receive timeout; reconnecting")
                    break
                except Exception as e:
                    log.error(f"[BybitWS] Error receiving message: {e}")
                    break
        except Exception as e:
            log.error(f"[BybitWS] Connection error: {e}")
            # Send Telegram notification for WebSocket disconnect
            try:
                telegram_notify.notify_bybit_ws_disconnect()
            except Exception as _tn_e:
                log.debug(f"[TELEGRAM] WS disconnect notification failed: {_tn_e}")
        finally:
            if self._ws:
                await self._ws.close()
                self._ws = None
        # Reconnect after delay
        if self._running:
            await asyncio.sleep(5)
            asyncio.create_task(self._connect())

    async def _handle_message(self, msg: Dict) -> None:
        """Route message to appropriate handler."""
        if "topic" not in msg:
            return
        topic = msg["topic"]
        data = msg.get("data", {})
        if topic == f"orderbook.50.{self.symbol}":
            self._handle_orderbook(data)
        elif topic == f"publicTrade.{self.symbol}":
            self._handle_trade(data)
        elif msg.get("op") == "subscribe" and msg.get("success"):
            log.info(f"[BybitWS] Subscription confirmation: {msg}")
        elif msg.get("op") == "subscribe" and not msg.get("success"):
            log.error(f"[BybitWS] Subscription failed: {msg}")

    def _handle_orderbook(self, data: Dict) -> None:
        """Update local order book with orderbook.50 snapshot."""
        # Bybit orderbook.50 provides bids/asks as lists of [price, size]
        bids = data.get("b", [])
        asks = data.get("a", [])
        # Convert to (price, size) tuples as floats
        self.orderbook["bids"] = [(float(p), float(s)) for p, s in bids]
        self.orderbook["asks"] = [(float(p), float(s)) for p, s in asks]

    def _handle_trade(self, data: Dict) -> None:
        """Update orderflow delta from publicTrade stream."""
        # Bybit V5 publicTrade fields: v=size, S=side (Buy/Sell), s=symbol (string)
        trades = data if isinstance(data, list) else [data]
        for trade in trades:
            size = float(trade.get("v", 0))
            side = trade.get("S")
            if side == "Buy":
                self.orderflow_delta += size
            elif side == "Sell":
                self.orderflow_delta -= size

    def _compute_imbalance(self) -> float:
        """Compute order book imbalance: (bid_vol - ask_vol) / (bid_vol + ask_vol)."""
        bid_vol = sum(size for _, size in self.orderbook["bids"])
        ask_vol = sum(size for _, size in self.orderbook["asks"])
        total = bid_vol + ask_vol
        if total == 0:
            return 0.0
        return (bid_vol - ask_vol) / total

    async def _emit_metrics(self) -> None:
        """Emit metrics at regular intervals and store aggregated metrics only."""
        while self._running:
            await asyncio.sleep(self.emit_interval)
            imbalance = self._compute_imbalance()
            # Prepare aggregated metrics (no raw orderbook)
            # Compute liquidity metrics from current orderbook
            bids = self.orderbook["bids"]
            asks = self.orderbook["asks"]
            mid = (bids[0][0] + asks[0][0]) / 2.0 if bids and asks else 0.0
            wall = _liq_wall(bids, asks, mid) if mid > 0 else 0.0
            norm_delta = self.orderflow_delta / max(abs(self.orderflow_delta), 1e-9) if self.orderflow_delta != 0 else 0.0
            pressure = _liq_pressure(imbalance, norm_delta)
            metrics = {
                "timestamp": time.time(),
                "exchange": "bybit",
                "symbol": self.symbol,
                "order_book_imbalance": imbalance,
                "orderflow_delta": norm_delta,
                "liquidity_wall_detection": wall,
                "liquidity_pressure": pressure,
            }
            # Persist aggregated metrics (raw orderbook excluded)
            store_metrics(metrics)
            log.debug(f"[BybitWS] Stored metrics: {metrics}")
            # Emit to pipeline (without raw orderbook)
            if self.on_metrics:
                try:
                    self.on_metrics(metrics)
                except Exception as e:
                    log.error(f"[BybitWS] Metrics callback error: {e}")
            # Reset orderflow delta for next interval
            self.orderflow_delta = 0.0

    async def start(self, on_metrics: Optional[callable] = None) -> None:
        """Start WebSocket streams and metrics emitter."""
        if self._running:
            log.warning("[BybitWS] Already running")
            return
        self._running = True
        self.on_metrics = on_metrics
        self._tasks = [
            asyncio.create_task(self._connect()),
            asyncio.create_task(self._emit_metrics()),
        ]
        log.info(f"[BybitWS] Started streams for {self.symbol}")

    async def stop(self) -> None:
        """Stop all WebSocket connections and tasks."""
        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        if self._ws:
            await self._ws.close()
            self._ws = None
        log.info("[BybitWS] Stopped")

    # Convenience sync wrappers for use in non-async contexts
    def run(self, on_metrics: Optional[callable] = None) -> None:
        """Run the client synchronously (blocking)."""
        try:
            asyncio.run(self.start(on_metrics))
        except KeyboardInterrupt:
            log.info("[BybitWS] Interrupted")
        finally:
            asyncio.run(self.stop())

    def stop_sync(self) -> None:
        """Synchronous stop wrapper."""
        asyncio.run(self.stop())


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    def metrics_cb(metrics):
        print(f"[METRICS] Imbalance: {metrics['order_book_imbalance']:.4f}, Delta: {metrics['orderflow_delta']:.4f}")

    client = BybitWS(symbol="BTCUSDT", emit_interval=1.0)
    try:
        client.run(on_metrics=metrics_cb)
    except KeyboardInterrupt:
        client.stop_sync()
