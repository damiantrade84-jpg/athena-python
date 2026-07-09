"""binance_ws.py — Binance WebSocket client for order book and trade streams.

Streams depth20@100ms, trade, and aggTrade (aggregated trades for Engine D buckets),
maintains local order book, computes order book imbalance and orderflow delta, and
emits metrics to Athena pipeline.
"""

import asyncio
import json
import logging
import random
import threading
import time
from typing import Dict, List, Tuple, Optional
import websockets

log = logging.getLogger("sentinel")
from athena.datafeeds.ws_ssl import default_ssl_context  # noqa: E402
from athena.microstructure.microstructure_store import store_metrics  # noqa: E402
from athena.microstructure.orderbook_metrics import (  # noqa: E402
    liquidity_wall_detection as _liq_wall,
    liquidity_pressure as _liq_pressure,
    orderflow_delta as _taker_imbalance_ratio,
)
from athena.microstructure.trade_bucket_store import store_trade  # noqa: E402

_micro_boot_lock = threading.Lock()
_micro_boot_logged = False
# Binance USDT-M multiplex often delivers @trade + @depth but not @aggTrade.
# After this grace window with zero aggTrade ticks, persist buckets from @trade.
_TRADE_BUCKET_FALLBACK_GRACE_SEC = 30.0


def _parse_binance_trade_tick(data: dict) -> tuple[float, float, bool, float] | None:
    try:
        price = float(data.get("p", 0))
        size = float(data.get("q", 0))
        is_buyer_maker = bool(data.get("m"))
        event_ts = float(data.get("T") or data.get("E") or 0) / 1000.0
        if event_ts <= 0:
            event_ts = time.time()
    except (TypeError, ValueError):
        return None
    if price <= 0 or size <= 0:
        return None
    return price, size, is_buyer_maker, event_ts


def _persist_binance_trade_tick(symbol: str, data: dict) -> None:
    parsed = _parse_binance_trade_tick(data)
    if not parsed:
        return
    price, size, is_buyer_maker, event_ts = parsed
    store_trade(
        exchange="binance",
        symbol=str(symbol).replace("/", "").upper(),
        price=price,
        quantity=size,
        is_buyer_maker=is_buyer_maker,
        ts=event_ts,
    )


def _trade_bucket_fallback_active(
    *,
    connected_at: float,
    last_aggtrade_ts: float,
    now_ts: float | None = None,
) -> bool:
    if last_aggtrade_ts > 0:
        return False
    now = now_ts if now_ts is not None else time.time()
    if connected_at <= 0:
        return False
    return (now - connected_at) >= _TRADE_BUCKET_FALLBACK_GRACE_SEC


def binance_futures_micro_stream_url(symbol: str) -> str:
    """Combined Binance USDT-M URL for depth + trade + aggTrade (strict Engine D path)."""
    sym = str(symbol or "btcusdt").lower().strip()
    base = "wss://fstream.binance.com/stream"
    return (
        f"{base}?streams={sym}@depth20@100ms/{sym}@trade/{sym}@aggTrade"
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
        self._reconnect_attempt: int = 0
        self._connected_at: float = 0.0
        self._last_aggtrade_ts: float = 0.0

    def _next_reconnect_delay(self) -> float:
        base = 2.0
        cap = 60.0
        exp = min(cap, base * (2 ** min(self._reconnect_attempt, 6)))
        jitter = random.uniform(0.0, min(3.0, exp * 0.2))
        return exp + jitter

    async def _connect(self) -> None:
        """Connect to combined Binance WebSocket stream."""
        url = binance_futures_micro_stream_url(self.symbol)
        try:
            # ping_interval=None: do NOT send outbound WS-level pings. Binance Futures
            # under our ~30-connection fan-out delivers slow/missed pongs which trip
            # the library keepalive (code 1011 keepalive ping timeout). Binance sends
            # its own ping frame every 3 min; the library auto-responds with pong, so
            # the connection stays alive. Dead streams are caught by the recv timeout
            # below, mirroring the working pattern in bybit_ws.py.
            async with websockets.connect(
                url,
                ping_interval=None,
                ping_timeout=None,
                open_timeout=45,
                close_timeout=10,
                ssl=default_ssl_context(),
            ) as ws:
                self._reconnect_attempt = 0
                self._connected_at = time.time()
                global _micro_boot_logged
                log.info(f"[BinanceWS] Connected to combined stream for {self.symbol}")
                with _micro_boot_lock:
                    if not _micro_boot_logged:
                        _micro_boot_logged = True
                        log.warning(
                            "[BinanceWS] First micro futures stream connected (%s, depth+trade+aggTrade). "
                            "Per-symbol lines are INFO (console shows WARNING only by default). "
                            "Set env SENTINEL_CONSOLE_LEVEL=INFO to see all connect lines, or tail logs/sentinel.log.",
                            self.symbol,
                        )
                while self._running:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        if not raw:
                            continue
                        msg = json.loads(raw)
                        await self._handle_message(msg)
                    except asyncio.TimeoutError:
                        log.warning(
                            f"[BinanceWS] {self.symbol}: receive timeout after 60s; reconnecting"
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
        if self._running:
            self._reconnect_attempt += 1
            delay = self._next_reconnect_delay()
            log.warning(
                "[BinanceWS] %s: reconnect backoff attempt=%s delay=%.1fs",
                self.symbol,
                self._reconnect_attempt,
                delay,
            )
            await asyncio.sleep(delay)
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
        elif stream.endswith("@aggTrade"):
            self._handle_agg_trade(data)

    def _handle_depth(self, data: Dict) -> None:
        """Update local order book with depth20 snapshot."""
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        # Convert to (price, size) tuples as floats
        self.orderbook["bids"] = [(float(p), float(s)) for p, s in bids]
        self.orderbook["asks"] = [(float(p), float(s)) for p, s in asks]
        self.last_update_id = data.get("lastUpdateId")

    def _handle_trade(self, data: Dict) -> None:
        """Accumulate taker volume from the @trade stream (taker side from ``m``).

        When @aggTrade is silent on Binance USDT-M multiplex, persist buckets from
        @trade after a short grace window so Engine B/CVD gates stay fed.
        """
        try:
            size = float(data.get("q", 0))
        except (TypeError, ValueError):
            return
        is_buyer_maker = data.get("m")  # true if buyer is maker (seller is taker)
        # Binance: m True → seller taker (sell aggressor); m False → buyer taker
        if not is_buyer_maker:
            self.buy_taker_volume += size
            self.orderflow_delta += size
        else:
            self.sell_taker_volume += size
            self.orderflow_delta -= size
        if _trade_bucket_fallback_active(
            connected_at=self._connected_at,
            last_aggtrade_ts=self._last_aggtrade_ts,
        ):
            _persist_binance_trade_tick(self.symbol, data)

    def _handle_agg_trade(self, data: Dict) -> None:
        """Persist aggregate-trade volume by price bucket for Engine D orderflow."""
        self._last_aggtrade_ts = time.time()
        _persist_binance_trade_tick(self.symbol, data)

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


def binance_futures_multi_micro_stream_url(symbols: list[str]) -> str:
    """Combined Binance USDT-M URL multiplexing depth+trade+aggTrade for many symbols.

    One physical WebSocket connection carries all subscribed symbol streams, removing
    the per-symbol-connection failure mode that left BTCUSDT/ETHUSDT/XRPUSDT silent
    when individual streams half-closed without a close frame.
    """
    norm: list[str] = []
    seen: set[str] = set()
    for s in symbols or []:
        sym = str(s or "").lower().strip().replace("/", "")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        norm.append(sym)
    norm.sort()
    if not norm:
        raise ValueError("binance_futures_multi_micro_stream_url: no symbols")
    base = "wss://fstream.binance.com/stream"
    streams = "/".join(
        f"{sym}@{kind}"
        for sym in norm
        for kind in ("depth20@100ms", "trade", "aggTrade")
    )
    return f"{base}?streams={streams}"


class _SymbolState:
    """Per-symbol micro state inside the multiplexed connection."""

    __slots__ = (
        "orderbook",
        "last_update_id",
        "buy_taker_volume",
        "sell_taker_volume",
        "orderflow_delta",
        "last_msg_ts",
        "last_aggtrade_ts",
    )

    def __init__(self) -> None:
        self.orderbook: Dict[str, List[Tuple[float, float]]] = {"bids": [], "asks": []}
        self.last_update_id: Optional[int] = None
        self.buy_taker_volume: float = 0.0
        self.sell_taker_volume: float = 0.0
        self.orderflow_delta: float = 0.0
        self.last_msg_ts: float = 0.0
        self.last_aggtrade_ts: float = 0.0


class BinanceMicroMultiWS:
    """Multiplexed Binance Futures micro WebSocket for many symbols on one connection.

    Mirrors the proven ``BinanceCandleWS`` pattern from ``candle_feeds.py``:
    one combined-stream URL, one event loop, one daemon thread, 90 s recv timeout
    + 20 s ``ws.ping()`` heartbeat, exponential backoff reconnect. Adds a
    per-symbol liveness watchdog that forces a full reconnect when any one
    symbol has been silent past ``stale_symbol_seconds`` while the connection
    has been up long enough to be past cold-start.

    Replaces the per-symbol ``BinanceWS`` fan-out which intermittently left
    individual streams half-closed without a close frame, never tripping the
    socket-level recv timeout because Binance kept sending tiny keepalive frames.
    """

    _MAX_STREAMS = 180  # Binance allows up to 200 streams per connection; keep margin.

    def __init__(
        self,
        symbols: list[str],
        emit_interval: float = 1.0,
        stale_symbol_seconds: float = 120.0,
    ) -> None:
        norm: list[str] = []
        seen: set[str] = set()
        for s in symbols or []:
            sym = str(s or "").lower().strip().replace("/", "")
            if not sym or sym in seen:
                continue
            seen.add(sym)
            norm.append(sym)
        norm.sort()
        if not norm:
            raise ValueError("BinanceMicroMultiWS: no symbols supplied")
        if len(norm) * 3 > self._MAX_STREAMS:
            raise ValueError(
                f"BinanceMicroMultiWS: too many symbols ({len(norm)}) "
                f"for one connection (cap={self._MAX_STREAMS // 3})"
            )
        self._symbols: list[str] = norm
        self._state: Dict[str, _SymbolState] = {sym.upper(): _SymbolState() for sym in norm}
        self.emit_interval = float(emit_interval)
        self.stale_symbol_seconds = float(stale_symbol_seconds)
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self.on_metrics: Optional[callable] = None
        self._reconnect_attempt: int = 0
        self._connected_at: float = 0.0
        self._force_reconnect: bool = False
        self._last_stale_warn_ts: float = 0.0
        self._ws = None  # current websocket connection, set inside _connect

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    def _next_reconnect_delay(self) -> float:
        base = 2.0
        cap = 60.0
        exp = min(cap, base * (2 ** min(self._reconnect_attempt, 6)))
        jitter = random.uniform(0.0, min(3.0, exp * 0.2))
        return exp + jitter

    async def _connect(self) -> None:
        """Outer reconnect loop; mirrors BinanceCandleWS pattern."""
        url = binance_futures_multi_micro_stream_url(self._symbols)
        while self._running:
            try:
                async with websockets.connect(
                    url,
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
                    # Seed last_msg_ts so the watchdog has a baseline.
                    now = time.time()
                    for st in self._state.values():
                        st.last_msg_ts = now
                    log.info(
                        "[BinanceMicroMultiWS] Connected: %s symbols (depth+trade+aggTrade)",
                        len(self._symbols),
                    )
                    global _micro_boot_logged
                    with _micro_boot_lock:
                        if not _micro_boot_logged:
                            _micro_boot_logged = True
                            log.warning(
                                "[BinanceMicroMultiWS] First multiplexed micro stream connected (%s symbols, depth+trade+aggTrade). "
                                "Per-message lines are DEBUG (set SENTINEL_CONSOLE_LEVEL=DEBUG to inspect).",
                                len(self._symbols),
                            )
                    last_ping = time.time()
                    while self._running and not self._force_reconnect:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=90)
                            if not raw:
                                continue
                            # Proactive heartbeat keeps the path warm even when
                            # individual streams briefly idle.
                            if time.time() - last_ping > 20:
                                try:
                                    await ws.ping()
                                    last_ping = time.time()
                                except Exception:
                                    pass
                            try:
                                msg = json.loads(raw)
                            except json.JSONDecodeError as e:
                                log.warning(
                                    "[BinanceMicroMultiWS] non-JSON frame; reconnecting: %s", e
                                )
                                break
                            await self._handle_message(msg)
                        except asyncio.TimeoutError:
                            try:
                                await asyncio.wait_for(ws.ping(), timeout=10)
                                last_ping = time.time()
                                continue
                            except Exception:
                                log.warning(
                                    "[BinanceMicroMultiWS] recv timeout + ping failed; reconnecting"
                                )
                                break
                        except websockets.exceptions.ConnectionClosed as e:
                            log.warning(
                                "[BinanceMicroMultiWS] connection closed (%s); reconnecting", e
                            )
                            break
                        except Exception as e:
                            log.error(
                                "[BinanceMicroMultiWS] error receiving: %s", e
                            )
                            break
                    if self._force_reconnect:
                        log.warning(
                            "[BinanceMicroMultiWS] watchdog-forced reconnect"
                        )
            except Exception as e:
                log.error("[BinanceMicroMultiWS] failed to connect: %s", e)
            finally:
                self._ws = None
            if not self._running:
                break
            self._reconnect_attempt += 1
            delay = self._next_reconnect_delay()
            log.warning(
                "[BinanceMicroMultiWS] reconnect backoff attempt=%s delay=%.1fs",
                self._reconnect_attempt,
                delay,
            )
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break

    async def _handle_message(self, msg: Dict) -> None:
        """Route a combined-stream envelope to the right per-symbol handler."""
        stream = str(msg.get("stream") or "")
        data = msg.get("data")
        if not data or "@" not in stream:
            return
        head, _, kind = stream.partition("@")
        sym_u = head.upper()
        st = self._state.get(sym_u)
        if st is None:
            return
        st.last_msg_ts = time.time()
        if kind == "depth20@100ms":
            self._handle_depth(st, data)
        elif kind == "trade":
            self._handle_trade(sym_u, st, data)
        elif kind == "aggTrade":
            self._handle_agg_trade(sym_u, data)

    def _handle_depth(self, st: _SymbolState, data: Dict) -> None:
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        st.orderbook["bids"] = [(float(p), float(s)) for p, s in bids]
        st.orderbook["asks"] = [(float(p), float(s)) for p, s in asks]
        st.last_update_id = data.get("lastUpdateId")

    def _handle_trade(self, sym_u: str, st: _SymbolState, data: Dict) -> None:
        try:
            size = float(data.get("q", 0))
        except (TypeError, ValueError):
            return
        is_buyer_maker = data.get("m")
        if not is_buyer_maker:
            st.buy_taker_volume += size
            st.orderflow_delta += size
        else:
            st.sell_taker_volume += size
            st.orderflow_delta -= size
        if _trade_bucket_fallback_active(
            connected_at=self._connected_at,
            last_aggtrade_ts=st.last_aggtrade_ts,
        ):
            _persist_binance_trade_tick(sym_u, data)

    def _handle_agg_trade(self, sym_u: str, data: Dict) -> None:
        st = self._state.get(sym_u)
        if st is not None:
            st.last_aggtrade_ts = time.time()
        _persist_binance_trade_tick(sym_u, data)

    @staticmethod
    def _compute_imbalance(orderbook: Dict[str, List[Tuple[float, float]]]) -> float:
        bid_vol = sum(size for _, size in orderbook["bids"])
        ask_vol = sum(size for _, size in orderbook["asks"])
        total = bid_vol + ask_vol
        if total == 0:
            return 0.0
        return (bid_vol - ask_vol) / total

    async def _emit_metrics(self) -> None:
        """One emitter loop iterating over every per-symbol state."""
        while self._running:
            await asyncio.sleep(self.emit_interval)
            now = time.time()
            for sym_u, st in self._state.items():
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
                    "exchange": "binance",
                    "symbol": sym_u,
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
                            "[BinanceMicroMultiWS] metrics callback error for %s: %s",
                            sym_u,
                            e,
                        )
                # Reset interval accumulators per symbol.
                st.buy_taker_volume = 0.0
                st.sell_taker_volume = 0.0
                st.orderflow_delta = 0.0

    def _check_stale_symbols(self, now: Optional[float] = None) -> list[str]:
        """Return list of symbols whose last_msg_ts is older than the stale threshold.

        Returns an empty list if the connection is too young for the watchdog to
        be meaningful (cold start) or if no symbol is stale.
        """
        if now is None:
            now = time.time()
        if self._connected_at <= 0:
            return []
        if now - self._connected_at < self.stale_symbol_seconds:
            return []
        threshold = self.stale_symbol_seconds
        stale: list[str] = []
        for sym_u, st in self._state.items():
            if st.last_msg_ts <= 0:
                continue
            if (now - st.last_msg_ts) > threshold:
                stale.append(sym_u)
        return stale

    async def _watch_stale_symbols(self) -> None:
        """Force a full reconnect when any subscribed symbol has gone silent."""
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
                "[BinanceMicroMultiWS] forced reconnect: %s symbols silent > %.0fs: %s",
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
            log.warning("[BinanceMicroMultiWS] already running")
            return
        self._running = True
        self.on_metrics = on_metrics
        self._tasks = [
            asyncio.create_task(self._connect()),
            asyncio.create_task(self._emit_metrics()),
            asyncio.create_task(self._watch_stale_symbols()),
        ]
        log.info(
            "[BinanceMicroMultiWS] Started (%s symbols, stale_after=%.0fs)",
            len(self._symbols),
            self.stale_symbol_seconds,
        )
        # Keep the start() coroutine alive while tasks run so the calling
        # ``loop.run_until_complete(client.start(cb))`` stays in the event loop.
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
        log.info("[BinanceMicroMultiWS] Stopped")

    def run(self, on_metrics: Optional[callable] = None) -> None:
        """Sync wrapper for thread targets."""
        try:
            asyncio.run(self.start(on_metrics))
        except KeyboardInterrupt:
            log.info("[BinanceMicroMultiWS] Interrupted")
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

    client = BinanceWS(symbol="btcusdt", emit_interval=1.0)
    try:
        client.run(on_metrics=metrics_cb)
    except KeyboardInterrupt:
        client.stop_sync()
