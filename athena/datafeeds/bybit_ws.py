"""bybit_ws.py — Bybit WebSocket client for order book and trade streams.

Streams orderbook.50 and publicTrade for linear futures, maintains local order book,
computes order book imbalance and orderflow delta, and emits metrics to Athena pipeline.

Two clients live here:

``BybitWS``            one connection per symbol (legacy rollback path).
``BybitMicroMultiWS``  one connection for every symbol, with a per-symbol liveness
                       watchdog. This is the default: the per-symbol fan-out was
                       reverted to Binance on 2026-07-02 because individual sockets
                       went silent for >15 min on BTC/ETH/SOL without ever tripping
                       the recv timeout — Bybit kept the connection warm with
                       protocol pings while the data topics were dead, so nothing
                       noticed until the aggTrade freshness gate failed the pair.
"""

import asyncio
import json
import logging
import random
import threading
import time
from typing import Dict, List, Tuple, Optional
import websockets
import telegram_notify

log = logging.getLogger("sentinel")
from athena.datafeeds.ws_ssl import default_ssl_context  # noqa: E402
from athena.microstructure.microstructure_store import store_metrics  # noqa: E402
from athena.microstructure.trade_bucket_store import store_trade  # noqa: E402
from athena.microstructure.orderbook_metrics import (  # noqa: E402
    liquidity_wall_detection as _liq_wall,
    liquidity_pressure as _liq_pressure,
    orderflow_delta as _taker_imbalance_ratio,
)

_BYBIT_SYMBOL_MAP = {
    "MATICUSDT": "POLUSDT",  # Bybit rebranded MATIC perpetual to POL
}

BYBIT_PUBLIC_LINEAR_URL = "wss://stream.bybit.com/v5/public/linear"

# Bybit caps a single subscribe frame by request size, not topic count. Chunking
# keeps each frame small and makes a rejected batch identifiable in the logs.
_SUBSCRIBE_CHUNK = 10

_micro_boot_lock = threading.Lock()
_micro_boot_logged = False


def bybit_micro_topics(stream_symbol: str) -> list[str]:
    """Topics one symbol needs for Engine B/D microstructure + trade buckets."""
    return [f"orderbook.50.{stream_symbol}", f"publicTrade.{stream_symbol}"]


def _parse_bybit_trade(trade: Dict) -> tuple[float, float, str, float] | None:
    """Parse one v5 publicTrade row -> (price, size, side, event_ts_seconds)."""
    try:
        size = float(trade.get("v", 0))
        price = float(trade.get("p", 0))
        side = str(trade.get("S") or "")
        event_ts = float(trade.get("T") or trade.get("ts") or 0) / 1000.0
    except (TypeError, ValueError):
        return None
    if event_ts <= 0:
        event_ts = time.time()
    if size <= 0 or price <= 0 or side not in ("Buy", "Sell"):
        return None
    return price, size, side, event_ts


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
        self._reconnect_attempt: int = 0

    def _next_reconnect_delay(self) -> float:
        base = 2.0
        cap = 60.0
        exp = min(cap, base * (2 ** min(self._reconnect_attempt, 6)))
        jitter = random.uniform(0.0, min(3.0, exp * 0.2))
        return exp + jitter

    async def _connect(self) -> None:
        """Connect to Bybit WebSocket and subscribe to streams."""
        try:
            self._ws = await websockets.connect(
                self.base_url,
                ping_interval=None,
                ping_timeout=None,
                open_timeout=45,
                close_timeout=10,
                ssl=default_ssl_context(),
            )
            self._reconnect_attempt = 0
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
        if self._running:
            self._reconnect_attempt += 1
            delay = self._next_reconnect_delay()
            log.warning(
                "[BybitWS] %s: reconnect backoff attempt=%s delay=%.1fs",
                self.symbol,
                self._reconnect_attempt,
                delay,
            )
            await asyncio.sleep(delay)
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
        # Bybit V5 publicTrade fields: p=price, v=size, S=side (Buy/Sell), T=timestamp ms
        trades = data if isinstance(data, list) else [data]
        for trade in trades:
            try:
                size = float(trade.get("v", 0))
                price = float(trade.get("p", 0))
                side = trade.get("S")
                event_ts = float(trade.get("T") or trade.get("ts") or 0) / 1000.0
                if event_ts <= 0:
                    event_ts = time.time()
            except (TypeError, ValueError):
                size = 0.0
                price = 0.0
                side = None
                event_ts = time.time()
            if side == "Buy":
                self.buy_taker_volume += size
                self.orderflow_delta += size
            elif side == "Sell":
                self.sell_taker_volume += size
                self.orderflow_delta -= size
            if size > 0 and price > 0 and side in ("Buy", "Sell"):
                # Bybit S=Buy → buy taker; S=Sell → sell taker (buyer is maker).
                store_trade(
                    exchange="bybit",
                    symbol=self.symbol.upper(),
                    price=price,
                    quantity=size,
                    is_buyer_maker=(side == "Sell"),
                    ts=event_ts,
                )

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


class _BybitSymbolState:
    """Per-symbol micro state inside the multiplexed connection."""

    __slots__ = (
        "orderbook",
        "buy_taker_volume",
        "sell_taker_volume",
        "orderflow_delta",
        "last_msg_ts",
        "last_trade_ts",
    )

    def __init__(self) -> None:
        self.orderbook: Dict[str, List[Tuple[float, float]]] = {"bids": [], "asks": []}
        self.buy_taker_volume: float = 0.0
        self.sell_taker_volume: float = 0.0
        self.orderflow_delta: float = 0.0
        self.last_msg_ts: float = 0.0
        self.last_trade_ts: float = 0.0


class BybitMicroMultiWS:
    """Multiplexed Bybit v5 linear micro WebSocket for many symbols on one connection.

    Mirrors ``BinanceMicroMultiWS``: one connection, one event loop, one daemon
    thread, exponential-backoff reconnect, and a per-symbol liveness watchdog that
    forces a full reconnect when any symbol has been silent past
    ``stale_symbol_seconds``.

    The watchdog is the whole point. Bybit's protocol keeps a half-dead connection
    looking healthy — it answers pings while individual topics deliver nothing — so
    a socket-level recv timeout alone never fires. Tracking last_msg_ts per symbol
    is the only signal that distinguishes "quiet market" from "topic is dead", and
    a full reconnect (not a resubscribe) is what actually recovers it.
    """

    def __init__(
        self,
        symbols: list[str],
        emit_interval: float = 1.0,
        stale_symbol_seconds: float = 120.0,
    ) -> None:
        norm: list[str] = []
        seen: set[str] = set()
        for s in symbols or []:
            sym = str(s or "").upper().strip().replace("/", "")
            if not sym or sym in seen:
                continue
            seen.add(sym)
            norm.append(sym)
        norm.sort()
        if not norm:
            raise ValueError("BybitMicroMultiWS: no symbols supplied")
        self._symbols: list[str] = norm
        # Stream symbol can differ from the canonical one (MATIC -> POL). Metrics and
        # trade buckets must key off the canonical symbol so downstream queries match.
        self._stream_by_symbol: Dict[str, str] = {
            sym: _BYBIT_SYMBOL_MAP.get(sym, sym) for sym in norm
        }
        self._symbol_by_stream: Dict[str, str] = {
            stream: sym for sym, stream in self._stream_by_symbol.items()
        }
        self._state: Dict[str, _BybitSymbolState] = {
            sym: _BybitSymbolState() for sym in norm
        }
        self.base_url = BYBIT_PUBLIC_LINEAR_URL
        self.emit_interval = float(emit_interval)
        self.stale_symbol_seconds = float(stale_symbol_seconds)
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self.on_metrics: Optional[callable] = None
        self._reconnect_attempt: int = 0
        self._connected_at: float = 0.0
        self._force_reconnect: bool = False
        self._last_stale_warn_ts: float = 0.0
        self._last_disconnect_notify_ts: float = 0.0
        self._ws = None

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    def _topics(self) -> list[str]:
        out: list[str] = []
        for sym in self._symbols:
            out.extend(bybit_micro_topics(self._stream_by_symbol[sym]))
        return out

    def _next_reconnect_delay(self) -> float:
        base = 2.0
        cap = 60.0
        exp = min(cap, base * (2 ** min(self._reconnect_attempt, 6)))
        jitter = random.uniform(0.0, min(3.0, exp * 0.2))
        return exp + jitter

    def _notify_disconnect(self) -> None:
        """Telegram disconnect alert, rate-limited so a flapping socket can't storm."""
        now = time.time()
        if now - self._last_disconnect_notify_ts < 300:
            return
        self._last_disconnect_notify_ts = now
        try:
            telegram_notify.notify_bybit_ws_disconnect()
        except Exception as exc:
            log.debug("[TELEGRAM] WS disconnect notification failed: %s", exc)

    async def _subscribe(self, ws) -> None:
        topics = self._topics()
        for start in range(0, len(topics), _SUBSCRIBE_CHUNK):
            chunk = topics[start:start + _SUBSCRIBE_CHUNK]
            await ws.send(
                json.dumps(
                    {
                        "req_id": f"{int(time.time() * 1000)}-{start}",
                        "op": "subscribe",
                        "args": chunk,
                    }
                )
            )
        log.info(
            "[BybitMicroMultiWS] Subscribed %s topics across %s symbols",
            len(topics),
            len(self._symbols),
        )

    async def _connect(self) -> None:
        """Outer reconnect loop; mirrors BinanceMicroMultiWS."""
        while self._running:
            try:
                async with websockets.connect(
                    self.base_url,
                    ping_interval=None,
                    ping_timeout=None,
                    open_timeout=45,
                    close_timeout=10,
                    ssl=default_ssl_context(),
                ) as ws:
                    self._ws = ws
                    self._reconnect_attempt = 0
                    self._connected_at = time.time()
                    self._force_reconnect = False
                    now = time.time()
                    for st in self._state.values():
                        st.last_msg_ts = now
                    await self._subscribe(ws)
                    log.info(
                        "[BybitMicroMultiWS] Connected: %s symbols (orderbook.50 + publicTrade)",
                        len(self._symbols),
                    )
                    global _micro_boot_logged
                    with _micro_boot_lock:
                        if not _micro_boot_logged:
                            _micro_boot_logged = True
                            log.warning(
                                "[BybitMicroMultiWS] First multiplexed Bybit micro stream connected "
                                "(%s symbols). Per-message lines are DEBUG "
                                "(set SENTINEL_CONSOLE_LEVEL=DEBUG to inspect).",
                                len(self._symbols),
                            )
                    last_ping = time.time()
                    while self._running and not self._force_reconnect:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=90)
                            if not raw:
                                continue
                            if isinstance(raw, (bytes, bytearray)):
                                raw = raw.decode("utf-8", errors="replace")
                            # Bybit expects a client ping roughly every 20s.
                            if time.time() - last_ping > 20:
                                try:
                                    await ws.send(json.dumps({"op": "ping"}))
                                    last_ping = time.time()
                                except Exception:
                                    pass
                            try:
                                msg = json.loads(raw)
                            except json.JSONDecodeError as e:
                                log.warning(
                                    "[BybitMicroMultiWS] non-JSON frame; reconnecting: %s", e
                                )
                                break
                            if isinstance(msg, dict) and msg.get("op") == "ping":
                                await ws.send(json.dumps({"op": "pong"}))
                                last_ping = time.time()
                                continue
                            await self._handle_message(msg)
                        except asyncio.TimeoutError:
                            try:
                                await asyncio.wait_for(
                                    ws.send(json.dumps({"op": "ping"})), timeout=10
                                )
                                last_ping = time.time()
                                continue
                            except Exception:
                                log.warning(
                                    "[BybitMicroMultiWS] recv timeout + ping failed; reconnecting"
                                )
                                break
                        except websockets.exceptions.ConnectionClosed as e:
                            log.warning(
                                "[BybitMicroMultiWS] connection closed (%s); reconnecting", e
                            )
                            break
                        except Exception as e:
                            log.error("[BybitMicroMultiWS] error receiving: %s", e)
                            break
                    if self._force_reconnect:
                        log.warning("[BybitMicroMultiWS] watchdog-forced reconnect")
            except Exception as e:
                log.error("[BybitMicroMultiWS] failed to connect: %s", e)
                self._notify_disconnect()
            finally:
                self._ws = None
            if not self._running:
                break
            self._reconnect_attempt += 1
            delay = self._next_reconnect_delay()
            log.warning(
                "[BybitMicroMultiWS] reconnect backoff attempt=%s delay=%.1fs",
                self._reconnect_attempt,
                delay,
            )
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break

    async def _handle_message(self, msg: Dict) -> None:
        """Route a v5 topic envelope to the right per-symbol handler."""
        if not isinstance(msg, dict):
            return
        if msg.get("op") == "subscribe":
            if msg.get("success"):
                log.debug("[BybitMicroMultiWS] subscribe ack: %s", msg.get("req_id"))
            else:
                log.error("[BybitMicroMultiWS] subscription failed: %s", msg)
            return
        topic = str(msg.get("topic") or "")
        if not topic:
            return
        if topic.startswith("orderbook.50."):
            stream_sym = topic[len("orderbook.50."):]
            sym = self._symbol_by_stream.get(stream_sym)
            st = self._state.get(sym) if sym else None
            if st is None:
                return
            st.last_msg_ts = time.time()
            apply_bybit_orderbook_envelope(st.orderbook, msg)
        elif topic.startswith("publicTrade."):
            stream_sym = topic[len("publicTrade."):]
            sym = self._symbol_by_stream.get(stream_sym)
            st = self._state.get(sym) if sym else None
            if st is None or sym is None:
                return
            st.last_msg_ts = time.time()
            self._handle_trade(sym, st, msg.get("data"))

    def _handle_trade(self, sym: str, st: _BybitSymbolState, data) -> None:
        trades = data if isinstance(data, list) else [data]
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            parsed = _parse_bybit_trade(trade)
            if not parsed:
                continue
            price, size, side, event_ts = parsed
            if side == "Buy":
                st.buy_taker_volume += size
                st.orderflow_delta += size
            else:
                st.sell_taker_volume += size
                st.orderflow_delta -= size
            st.last_trade_ts = time.time()
            # Bybit S=Buy -> buy taker; S=Sell -> sell taker (buyer is maker).
            store_trade(
                exchange="bybit",
                symbol=sym,
                price=price,
                quantity=size,
                is_buyer_maker=(side == "Sell"),
                ts=event_ts,
            )

    @staticmethod
    def _compute_imbalance(orderbook: Dict[str, List[Tuple[float, float]]]) -> float:
        bid_vol = sum(size for _, size in orderbook["bids"])
        ask_vol = sum(size for _, size in orderbook["asks"])
        total = bid_vol + ask_vol
        if total == 0:
            return 0.0
        return (bid_vol - ask_vol) / total

    async def _emit_metrics(self) -> None:
        while self._running:
            await asyncio.sleep(self.emit_interval)
            now = time.time()
            for sym, st in self._state.items():
                imbalance = self._compute_imbalance(st.orderbook)
                bids = st.orderbook["bids"]
                asks = st.orderbook["asks"]
                mid = (bids[0][0] + asks[0][0]) / 2.0 if bids and asks else 0.0
                wall = _liq_wall(bids, asks, mid) if mid > 0 else 0.0
                norm_delta = _taker_imbalance_ratio(
                    st.buy_taker_volume, st.sell_taker_volume
                )
                pressure = _liq_pressure(imbalance, norm_delta)
                metrics = {
                    "timestamp": now,
                    "exchange": "bybit",
                    "symbol": sym,
                    "order_book_imbalance": imbalance,
                    "orderflow_delta": norm_delta,
                    "liquidity_wall_detection": wall,
                    "liquidity_pressure": pressure,
                }
                store_metrics(metrics)
                if self.on_metrics:
                    try:
                        self.on_metrics(metrics)
                    except Exception as e:
                        log.error(
                            "[BybitMicroMultiWS] metrics callback error for %s: %s", sym, e
                        )
                st.buy_taker_volume = 0.0
                st.sell_taker_volume = 0.0
                st.orderflow_delta = 0.0

    def _check_stale_symbols(self, now: Optional[float] = None) -> list[str]:
        """Symbols whose last_msg_ts is older than the stale threshold.

        Empty while the connection is younger than the threshold (cold start).
        """
        if now is None:
            now = time.time()
        if self._connected_at <= 0:
            return []
        if now - self._connected_at < self.stale_symbol_seconds:
            return []
        stale: list[str] = []
        for sym, st in self._state.items():
            if st.last_msg_ts <= 0:
                continue
            if (now - st.last_msg_ts) > self.stale_symbol_seconds:
                stale.append(sym)
        return stale

    async def _watch_stale_symbols(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                return
            stale = self._check_stale_symbols()
            if not stale:
                continue
            now = time.time()
            if now - self._last_stale_warn_ts < 30:
                continue
            self._last_stale_warn_ts = now
            log.warning(
                "[BybitMicroMultiWS] forced reconnect: %s symbols silent > %.0fs: %s",
                len(stale),
                self.stale_symbol_seconds,
                ", ".join(sorted(stale)[:10]),
            )
            self._force_reconnect = True
            ws = self._ws
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass

    async def start(self, on_metrics: Optional[callable] = None) -> None:
        if self._running:
            log.warning("[BybitMicroMultiWS] already running")
            return
        self._running = True
        self.on_metrics = on_metrics
        self._tasks = [
            asyncio.create_task(self._connect()),
            asyncio.create_task(self._emit_metrics()),
            asyncio.create_task(self._watch_stale_symbols()),
        ]
        log.info(
            "[BybitMicroMultiWS] Started (%s symbols, stale_after=%.0fs)",
            len(self._symbols),
            self.stale_symbol_seconds,
        )
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._force_reconnect = True
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._tasks.clear()
        log.info("[BybitMicroMultiWS] Stopped")

    def run(self, on_metrics: Optional[callable] = None) -> None:
        """Sync wrapper for thread targets."""
        try:
            asyncio.run(self.start(on_metrics))
        except KeyboardInterrupt:
            log.info("[BybitMicroMultiWS] Interrupted")
        finally:
            try:
                asyncio.run(self.stop())
            except Exception:
                pass

    def stop_sync(self) -> None:
        try:
            asyncio.run(self.stop())
        except Exception:
            pass


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
