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
from athena.microstructure.microstructure_store import store_metrics  # noqa: E402
from athena.microstructure.orderbook_metrics import (  # noqa: E402
    liquidity_wall_detection as _liq_wall,
    liquidity_pressure as _liq_pressure,
    orderflow_delta as _taker_imbalance_ratio,
)

_BYBIT_SYMBOL_MAP = {
    "MATICUSDT": "POLUSDT",  # Bybit rebranded MATIC perpetual to POL
}


def _normalize_orderbook_rows(rows: List) -> List[Tuple[float, float]]:
    """Parse Bybit [price, size] rows to (float, float) tuples."""
    out: List[Tuple[float, float]] = []
    for row in rows or []:
        if not row or len(row) < 2:
            continue
        out.append((float(row[0]), float(row[1])))
    return out


def _sort_bids_desc(levels: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    return sorted(levels, key=lambda x: x[0], reverse=True)


def _sort_asks_asc(levels: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    return sorted(levels, key=lambda x: x[0], reverse=False)


def _orderbook_data_dict(msg: Dict) -> Dict:
    """Extract orderbook `data` object from a v5 public topic message."""
    data = msg.get("data")
    if isinstance(data, list):
        return data[0] if data else {}
    if isinstance(data, dict):
        return data
    return {}


def apply_bybit_orderbook_envelope(
    orderbook: Dict[str, List[Tuple[float, float]]], msg: Dict
) -> None:
    """Apply a full Bybit orderbook.50 websocket message (snapshot or delta).

    Mutates ``orderbook`` in place (keys ``bids``, ``asks``). Used by :class:`BybitWS`
    and unit tests. Does not persist anything to the database.

    Rules:
    - ``type`` == ``snapshot`` (or missing / unknown): replace local book from ``b``/``a``.
    - ``data['u']`` == 1: full reset from this message's ``b``/``a`` (Bybit sequence reset).
    - ``type`` == ``delta``: merge changes — size 0 removes a level; else insert/update.
    - Bids sorted descending, asks ascending after each update.
    """
    data = _orderbook_data_dict(msg)
    bids_raw = data.get("b", [])
    asks_raw = data.get("a", [])

    raw_type = msg.get("type")
    if raw_type is None:
        msg_type = "snapshot"
    else:
        msg_type = str(raw_type).lower()

    u_val = data.get("u")
    u_int: Optional[int]
    try:
        u_int = int(float(u_val)) if u_val is not None else None
    except (TypeError, ValueError):
        u_int = None

    # Unknown/malformed type: treat as snapshot (full replace) for safe recovery.
    reset = (
        msg_type == "snapshot"
        or u_int == 1
        or msg_type not in ("snapshot", "delta")
    )

    if reset:
        orderbook["bids"] = _sort_bids_desc(_normalize_orderbook_rows(bids_raw))
        orderbook["asks"] = _sort_asks_asc(_normalize_orderbook_rows(asks_raw))
        return

    # delta
    bid_map = {p: s for p, s in orderbook["bids"]}
    ask_map = {p: s for p, s in orderbook["asks"]}

    for row in bids_raw or []:
        if not row or len(row) < 2:
            continue
        price_f, size_f = float(row[0]), float(row[1])
        if size_f == 0.0:
            bid_map.pop(price_f, None)
        else:
            bid_map[price_f] = size_f

    for row in asks_raw or []:
        if not row or len(row) < 2:
            continue
        price_f, size_f = float(row[0]), float(row[1])
        if size_f == 0.0:
            ask_map.pop(price_f, None)
        else:
            ask_map[price_f] = size_f

    orderbook["bids"] = _sort_bids_desc(list(bid_map.items()))
    orderbook["asks"] = _sort_asks_asc(list(ask_map.items()))


class BybitWS:
    """Bybit WebSocket client for orderbook.50 and publicTrade streams."""

    def __init__(self, symbol: str = "BTCUSDT", emit_interval: float = 1.0):
        self.symbol = symbol.upper()
        self.stream_symbol = _BYBIT_SYMBOL_MAP.get(self.symbol, self.symbol)
        self.base_url = "wss://stream.bybit.com/v5/public/linear"
        self.emit_interval = emit_interval
        self.orderbook: Dict[str, List[Tuple[float, float]]] = {"bids": [], "asks": []}
        self.buy_taker_volume: float = 0.0
        self.sell_taker_volume: float = 0.0
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
                ping_interval=None,
                ping_timeout=None,
                open_timeout=45,
                close_timeout=10,
            )
            log.info(f"[BybitWS] Connected to {self.base_url} for {self.symbol}")
            # Subscribe to orderbook.50 and publicTrade
            subscribe_msg = {
                "req_id": str(int(time.time() * 1000)),
                "op": "subscribe",
                "args": [
                    f"orderbook.50.{self.stream_symbol}",
                    f"publicTrade.{self.stream_symbol}",
                ],
            }
            await self._ws.send(json.dumps(subscribe_msg))
            log.info(
                f"[BybitWS] Subscribed to orderbook.50 and publicTrade for {self.symbol}"
            )
            # Listen for messages
            _last_ping = time.time()
            while self._running:
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=60)
                    if not raw:
                        continue
                    if isinstance(raw, (bytes, bytearray)):
                        raw = raw.decode("utf-8", errors="replace")
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        log.warning("[BybitWS] Non-JSON frame; reconnecting")
                        break
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
                    log.warning(
                        f"[BybitWS] {self.symbol}: receive timeout after 60s; reconnecting"
                    )
                    break
                except websockets.exceptions.ConnectionClosed as e:
                    log.warning(
                        f"[BybitWS] {self.symbol}: connection closed ({e}); reconnecting"
                    )
                    break
                except Exception as e:
                    log.error(f"[BybitWS] {self.symbol}: error receiving message: {e}")
                    break
        except Exception as e:
            log.error(f"[BybitWS] {self.symbol}: connection error: {e}")
            # Send Telegram notification for WebSocket disconnect
            try:
                telegram_notify.notify_bybit_ws_disconnect()
            except Exception as _tn_e:
                log.debug(f"[TELEGRAM] WS disconnect notification failed: {_tn_e}")
        finally:
            ws = self._ws
            self._ws = None
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass
        # Reconnect after delay
        if self._running:
            await asyncio.sleep(5)
            asyncio.create_task(self._connect())

    async def _handle_message(self, msg: Dict) -> None:
        """Route message to appropriate handler."""
        if msg.get("op") == "subscribe" and msg.get("success"):
            log.info(f"[BybitWS] Subscription confirmation: {msg}")
            return
        if msg.get("op") == "subscribe" and not msg.get("success"):
            log.error(f"[BybitWS] Subscription failed: {msg}")
            return
        if "topic" not in msg:
            return
        topic = msg["topic"]
        data = msg.get("data", {})
        if topic == f"orderbook.50.{self.stream_symbol}":
            self._handle_orderbook(msg)
        elif topic == f"publicTrade.{self.stream_symbol}":
            self._handle_trade(data)

    def _handle_orderbook(self, msg: Dict) -> None:
        """Apply orderbook.50 message (snapshot or delta) from full websocket envelope."""
        apply_bybit_orderbook_envelope(self.orderbook, msg)

    def _handle_trade(self, data: Dict) -> None:
        """Accumulate taker volume from publicTrade (S=Buy / S=Sell)."""
        # Bybit V5 publicTrade fields: v=size, S=side (Buy/Sell), s=symbol (string)
        trades = data if isinstance(data, list) else [data]
        for trade in trades:
            size = float(trade.get("v", 0))
            side = trade.get("S")
            if side == "Buy":
                self.buy_taker_volume += size
                self.orderflow_delta += size
            elif side == "Sell":
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
            norm_delta = _taker_imbalance_ratio(
                self.buy_taker_volume, self.sell_taker_volume
            )
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
            self.buy_taker_volume = 0.0
            self.sell_taker_volume = 0.0
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
        print(
            f"[METRICS] Imbalance: {metrics['order_book_imbalance']:.4f}, Delta: {metrics['orderflow_delta']:.4f}"
        )

    client = BybitWS(symbol="BTCUSDT", emit_interval=1.0)
    try:
        client.run(on_metrics=metrics_cb)
    except KeyboardInterrupt:
        client.stop_sync()
