"""binance_ws.py — Binance WebSocket client for order book and trade streams.

Streams depth20@100ms and trade streams, maintains local order book, computes
order book imbalance and orderflow delta, and emits metrics to Athena pipeline.
"""

import asyncio
import json
import logging
import time
from typing import Dict, List, Tuple, Optional
import websockets

log = logging.getLogger("sentinel")
from athena.microstructure.microstructure_store import store_metrics  # noqa: E402
from athena.microstructure.orderbook_metrics import (  # noqa: E402
    liquidity_wall_detection as _liq_wall,
    liquidity_pressure as _liq_pressure,
    orderflow_delta as _taker_imbalance_ratio,
)


class BinanceWS:
    """Binance WebSocket client for depth20@100ms and trade streams."""

    def __init__(self, symbol: str = "btcusdt", emit_interval: float = 1.0):
        self.symbol = symbol.lower()
        # Crypto execution + live pricing use Binance Futures; keep microstructure on the
        # same venue to avoid spot/futures mismatch and inconsistent symbol behavior.
        self.base_url = "wss://fstream.binance.com/stream"
        self.emit_interval = emit_interval
        self.orderbook: Dict[str, List[Tuple[float, float]]] = {"bids": [], "asks": []}
        self.last_update_id: Optional[int] = None
        # Per emit-interval taker volumes (futures trade stream: m = buyer is maker)
        self.buy_taker_volume: float = 0.0
        self.sell_taker_volume: float = 0.0
        # Signed net taker flow for the interval (buy taker − sell taker); mirrors buy/sell split
        self.orderflow_delta: float = 0.0
        self._running = False
        self._tasks: List[asyncio.Task] = []
        # Metrics callbacks
        self.on_metrics: Optional[callable] = None

    async def _connect(self) -> None:
        """Connect to combined Binance WebSocket stream."""
        depth_stream = f"{self.symbol}@depth20@100ms"
        trade_stream = f"{self.symbol}@trade"
        url = f"{self.base_url}?streams={depth_stream}/{trade_stream}"
        try:
            async with websockets.connect(
                url,
                ping_interval=None,
                ping_timeout=None,
                open_timeout=45,
                close_timeout=10,
            ) as ws:
                log.info(f"[BinanceWS] Connected to combined stream for {self.symbol}")
                while self._running:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=120)
                        if not raw:
                            continue
                        msg = json.loads(raw)
                        await self._handle_message(msg)
                    except asyncio.TimeoutError:
                        log.warning(
                            f"[BinanceWS] {self.symbol}: receive timeout after 120s; reconnecting"
                        )
                        break
                    except json.JSONDecodeError as e:
                        log.warning(
                            f"[BinanceWS] {self.symbol}: non-JSON frame; reconnecting: {e}"
                        )
                        break
                    except websockets.exceptions.ConnectionClosed as e:
                        log.warning(
                            f"[BinanceWS] {self.symbol}: connection closed ({e}); reconnecting"
                        )
                        break
                    except Exception as e:
                        log.error(f"[BinanceWS] {self.symbol}: error receiving: {e}")
                        break
        except Exception as e:
            log.error(f"[BinanceWS] {self.symbol}: failed to connect: {e}")
            # Reconnect after delay
            await asyncio.sleep(5)
            if self._running:
                asyncio.create_task(self._connect())

    async def _handle_message(self, msg: Dict) -> None:
        """Route message to appropriate handler."""
        stream = msg.get("stream", "")
        data = msg.get("data")
        if not data:
            return
        if stream.endswith("@depth20@100ms"):
            self._handle_depth(data)
        elif stream.endswith("@trade"):
            self._handle_trade(data)

    def _handle_depth(self, data: Dict) -> None:
        """Update local order book with depth20 snapshot."""
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        # Convert to (price, size) tuples as floats
        self.orderbook["bids"] = [(float(p), float(s)) for p, s in bids]
        self.orderbook["asks"] = [(float(p), float(s)) for p, s in asks]
        self.last_update_id = data.get("lastUpdateId")

    def _handle_trade(self, data: Dict) -> None:
        """Accumulate taker volume from trade stream (taker side from ``m``)."""
        size = float(data.get("q", 0))
        is_buyer_maker = data.get("m")  # true if buyer is maker (seller is taker)
        # Binance: m True → seller taker (sell aggressor); m False → buyer taker
        if not is_buyer_maker:
            self.buy_taker_volume += size
            self.orderflow_delta += size
        else:
            self.sell_taker_volume += size
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
            # Bounded [-1, 1] taker imbalance preserving magnitude within the interval
            norm_delta = _taker_imbalance_ratio(
                self.buy_taker_volume, self.sell_taker_volume
            )
            pressure = _liq_pressure(imbalance, norm_delta)
            metrics = {
                "timestamp": time.time(),
                "exchange": "binance",
                "symbol": self.symbol.upper(),
                "order_book_imbalance": imbalance,
                "orderflow_delta": norm_delta,
                "liquidity_wall_detection": wall,
                "liquidity_pressure": pressure,
            }
            # Persist aggregated metrics (raw orderbook excluded)
            store_metrics(metrics)
            log.debug(f"[BinanceWS] Stored metrics: {metrics}")
            # Emit to pipeline (without raw orderbook)
            if self.on_metrics:
                try:
                    self.on_metrics(metrics)
                except Exception as e:
                    log.error(f"[BinanceWS] Metrics callback error: {e}")
            # Reset interval accumulators
            self.buy_taker_volume = 0.0
            self.sell_taker_volume = 0.0
            self.orderflow_delta = 0.0

    async def start(self, on_metrics: Optional[callable] = None) -> None:
        """Start WebSocket streams and metrics emitter."""
        if self._running:
            log.warning("[BinanceWS] Already running")
            return
        self._running = True
        self.on_metrics = on_metrics
        # Start combined connection + metrics emitter
        self._tasks = [
            asyncio.create_task(self._connect()),
            asyncio.create_task(self._emit_metrics()),
        ]
        log.info(f"[BinanceWS] Started streams for {self.symbol}")

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
        log.info("[BinanceWS] Stopped")

    # Convenience sync wrappers for use in non-async contexts
    def run(self, on_metrics: Optional[callable] = None) -> None:
        """Run the client synchronously (blocking)."""
        try:
            asyncio.run(self.start(on_metrics))
        except KeyboardInterrupt:
            log.info("[BinanceWS] Interrupted")
        finally:
            asyncio.run(self.stop())

    def stop_sync(self) -> None:
        """Synchronous stop wrapper."""
        asyncio.run(self.stop())


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    def metrics_cb(metrics):
        print(
            f"[METRICS] Imbalance: {metrics['order_book_imbalance']:.4f}, Delta: {metrics['orderflow_delta']:.4f}"
        )

    client = BinanceWS(symbol="btcusdt", emit_interval=1.0)
    try:
        client.run(on_metrics=metrics_cb)
    except KeyboardInterrupt:
        client.stop_sync()
