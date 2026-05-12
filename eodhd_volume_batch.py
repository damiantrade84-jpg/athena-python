"""Batch delayed-quote volume feeder for ws:False US stocks/ETFs.

Uses EODHD Live v2 delayed US quotes to fetch cumulative session volume for
non-WS US stocks in one request, then injects delta volume into CandleBuilder's
existing stock tick accumulation path.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("sentinel")

try:
    from config import CONFIG
except ImportError:  # pragma: no cover
    CONFIG = {}

_LIVE_V2_URL = "https://eodhd.com/api/us-quote-delayed"

_live_v2_batcher: LiveV2VolumeBatcher | None = None
_batcher_lock = threading.Lock()


def get_live_v2_batcher() -> "LiveV2VolumeBatcher | None":
    """Return the active singleton batcher, if started."""
    return _live_v2_batcher


def set_live_v2_batcher(batcher: "LiveV2VolumeBatcher | None") -> None:
    """Install or clear the singleton batcher (used by tests and startup)."""
    global _live_v2_batcher
    _live_v2_batcher = batcher


class LiveV2VolumeBatcher:
    """Batch delayed-quote poller that injects volume deltas into CandleBuilder."""

    def __init__(self, api_key: str, poll_interval: int = 60) -> None:
        self._key = str(api_key or "").strip()
        self._poll_interval = max(30, int(poll_interval or 60))
        self._symbols: list[str] = []
        self._symbol_to_display: dict[str, str] = {}
        self._prev_cumvol: dict[str, float] = {}
        self._prev_event_marker_ms: dict[str, int] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False

    def configure(self, pairs: list[dict]) -> None:
        """Configure the current list of non-WS US stock/ETF pairs to poll."""
        symbols: list[str] = []
        symbol_to_display: dict[str, str] = {}

        for pair in pairs or []:
            if not pair.get("enabled", True):
                continue
            if str(pair.get("type") or "").lower() != "stock":
                continue
            symbol = str(pair.get("symbol") or "").strip().upper()
            display = str(pair.get("display") or pair.get("symbol") or "").strip()
            if not symbol.endswith(".US") or not display:
                continue
            symbols.append(symbol)
            symbol_to_display[symbol] = display

        symbols = sorted(dict.fromkeys(symbols))
        with self._lock:
            self._symbols = symbols
            self._symbol_to_display = symbol_to_display
            self._prev_cumvol = {}
            self._prev_event_marker_ms = {}

        log.info(
            "[LIVEV2] Configured %d non-WS US stock/ETF tickers",
            len(symbols),
        )

    def _normalise_quote_map(self, raw) -> dict[str, dict]:
        if isinstance(raw, dict):
            data = raw.get("data")
            if isinstance(data, dict):
                return {str(sym): quote for sym, quote in data.items() if isinstance(quote, dict)}
            return {str(sym): quote for sym, quote in raw.items() if isinstance(quote, dict)}
        if isinstance(raw, list):
            out: dict[str, dict] = {}
            for item in raw:
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol") or item.get("code") or "").strip()
                if symbol:
                    out[symbol] = item
            return out
        return {}

    @staticmethod
    def _quote_event_marker_ms(quote: dict) -> int:
        """Return a monotonic marker for quote freshness.

        Prefer last trade time; fall back to snapshot timestamp when trade time is
        absent. Timestamps are normalized to milliseconds.
        """
        raw = quote.get("lastTradeTime")
        if raw is None:
            raw = quote.get("timestamp")
            if raw is not None:
                try:
                    return int(float(raw) * 1000)
                except (TypeError, ValueError):
                    return 0
        try:
            val = int(float(raw))
        except (TypeError, ValueError):
            return 0
        return val if val > 10_000_000_000 else val * 1000

    def _poll_once(self) -> None:
        """Fetch one batch quote snapshot and inject positive volume deltas."""
        from candle_feeds import get_candle_builder
        from data_feeds import http_requests

        with self._lock:
            symbols = list(self._symbols)
            symbol_to_display = dict(self._symbol_to_display)
            prev_cumvol = dict(self._prev_cumvol)
            prev_event_marker_ms = dict(self._prev_event_marker_ms)

        if not symbols:
            return

        try:
            resp = http_requests.get(
                _LIVE_V2_URL,
                params={
                    "s": ",".join(symbols),
                    "api_token": self._key,
                    "fmt": "json",
                },
                timeout=15,
            )
        except Exception as exc:
            log.debug("[LIVEV2] Batch request failed: %s", exc)
            return

        if resp.status_code != 200:
            log.warning("[LIVEV2] Batch quote HTTP %s", resp.status_code)
            return

        try:
            quote_map = self._normalise_quote_map(resp.json())
        except Exception as exc:
            log.debug("[LIVEV2] JSON decode failed: %s", exc)
            return

        if not quote_map:
            return

        candle_builder = get_candle_builder()
        if candle_builder is None:
            return

        new_cumvol = dict(prev_cumvol)
        new_event_marker_ms = dict(prev_event_marker_ms)
        injected = 0
        max_quote_lag_s = 0.0
        _scalp_cfg = CONFIG.get("SCALP_ENGINE", {}) or {}
        _max_quote_lag_s = float(_scalp_cfg.get("EODHD_LIVE_V2_MAX_QUOTE_LAG_SEC", 0) or 0)

        for symbol, quote in quote_map.items():
            display = symbol_to_display.get(symbol)
            if not display:
                continue

            try:
                cum_vol = float(quote.get("volume") or 0)
                price = float(
                    quote.get("lastTradePrice")
                    or quote.get("close")
                    or quote.get("price")
                    or 0
                )
            except (TypeError, ValueError):
                continue

            if price <= 0 or cum_vol <= 0:
                continue

            marker_ms = self._quote_event_marker_ms(quote)
            lag_s: float | None = None
            if marker_ms:
                try:
                    lag_s = max(0.0, (time.time() * 1000.0 - float(marker_ms)) / 1000.0)
                    if lag_s > max_quote_lag_s:
                        max_quote_lag_s = lag_s
                except (TypeError, ValueError):
                    lag_s = None
            prev_marker = int(prev_event_marker_ms.get(display) or 0)
            if prev_marker and marker_ms and marker_ms <= prev_marker:
                continue
            if _max_quote_lag_s > 0 and lag_s is not None and lag_s > _max_quote_lag_s:
                continue

            prev_vol = float(prev_cumvol.get(display) or 0)
            if prev_vol > 0 and cum_vol < prev_vol:
                delta_vol = 0.0
            elif prev_vol > 0:
                delta_vol = cum_vol - prev_vol
            else:
                delta_vol = 0.0

            if delta_vol <= 0:
                new_cumvol[display] = cum_vol
                if marker_ms:
                    new_event_marker_ms[display] = marker_ms
                continue

            new_cumvol[display] = cum_vol
            if marker_ms:
                new_event_marker_ms[display] = marker_ms

            try:
                candle_builder.on_tick(display, price, delta_vol, marker_ms or int(time.time() * 1000))
                injected += 1
            except Exception as exc:
                log.debug("[LIVEV2] on_tick failed for %s: %s", display, exc)

        with self._lock:
            self._prev_cumvol = new_cumvol
            self._prev_event_marker_ms = new_event_marker_ms

        if injected:
            log.debug(
                "[LIVEV2] Injected volume deltas for %d tickers (max quote lag ~%.1fs vs wall; us-quote-delayed)",
                injected,
                max_quote_lag_s,
            )

    def start(self) -> None:
        """Start the polling thread if not already running."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True

        def _loop() -> None:
            time.sleep(20)
            log.info(
                "[LIVEV2] Batcher active for %d non-WS US stock/ETF tickers (interval=%ss)",
                len(self._symbols),
                self._poll_interval,
            )
            while self._running:
                try:
                    self._poll_once()
                except Exception as exc:
                    log.warning("[LIVEV2] Unexpected poll failure: %s", exc)
                time.sleep(self._poll_interval)

        self._thread = threading.Thread(target=_loop, daemon=True, name="LiveV2VolumeBatcher")
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    @property
    def ticker_count(self) -> int:
        with self._lock:
            return len(self._symbols)

    @property
    def last_cumvol(self) -> dict[str, float]:
        with self._lock:
            return dict(self._prev_cumvol)


def build_non_ws_stock_pairs(all_pairs: list[dict]) -> list[dict]:
    """Return enabled US stock/ETF pairs that are not subscribed to EODHD WS."""
    return [
        pair
        for pair in (all_pairs or [])
        if pair.get("enabled", True)
        and str(pair.get("type") or "").lower() == "stock"
        and str(pair.get("symbol") or "").upper().endswith(".US")
        and not pair.get("ws", True)
    ]


def start_live_v2_batcher_for_non_ws_stocks(
    all_pairs: list[dict],
    api_key: str,
    poll_interval: int = 60,
) -> "LiveV2VolumeBatcher | None":
    """Start or refresh the singleton Live v2 batcher for non-WS US stocks."""
    global _live_v2_batcher

    api_key = str(api_key or "").strip()
    if not api_key:
        log.warning("[LIVEV2] No EODHD_KEY - batcher not started")
        return None

    non_ws_pairs = build_non_ws_stock_pairs(all_pairs)
    if not non_ws_pairs:
        log.info("[LIVEV2] No non-WS US stock/ETF pairs found - batcher not started")
        return None

    with _batcher_lock:
        if _live_v2_batcher is None:
            _live_v2_batcher = LiveV2VolumeBatcher(api_key=api_key, poll_interval=poll_interval)
            _live_v2_batcher.configure(non_ws_pairs)
            _live_v2_batcher.start()
        else:
            _live_v2_batcher.configure(non_ws_pairs)

    log.info(
        "[LIVEV2] Batcher configured for %d non-WS US stock/ETF tickers",
        len(non_ws_pairs),
    )
    return _live_v2_batcher
