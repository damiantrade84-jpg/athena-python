"""MetaTrader 5 market data for OPUS.

Owns its own terminal handshake and its own symbol resolution. MT5 reports
`tick_volume` for FX rather than traded size; that is flagged on the Candles
object so ARC and OFP can discount it instead of treating tick count as size.
"""

from __future__ import annotations

import threading
import time

import numpy as np

from opus.data.feed import Feed, FeedError
from opus.types import Candles, Quote

SOURCE = "MT5"

_lock = threading.Lock()
_initialised = False
_symbol_cache: dict[str, str] = {}
_offset_lock = threading.Lock()
_server_offset: float | None = None
_clock_drift: float = 0.0
_offset_stamp: float = 0.0
_OFFSET_TTL_SEC = 900.0
# Ceiling on the sub-minute clock-drift correction. It exists to fix a few
# seconds of disagreement between this machine's clock and the broker's, so a
# cap well below max_quote_age_sec keeps a bad measurement from ageing an
# otherwise fresh quote past the freshness gate.
_MAX_CLOCK_DRIFT_SEC = 10.0
# A plausible broker timezone offset. Anything outside this is a measurement
# fault, not a timezone, and is rejected rather than applied.
_MAX_TZ_OFFSET_SEC = 14 * 3600.0
_OFFSET_SAMPLE_SYMBOLS = 12


def _measure_offsets(now: float) -> tuple[float, float] | None:
    """(timezone offset, clock drift) in seconds, or None if unmeasurable.

    Both come from the same probe. For a tick observed at true instant t:

        tick.time = t + tz + drift          (broker clock, broker timezone)
        raw       = tick.time - now = tz + drift - age

    so the FRESHEST tick in the sample (age -> 0) is the best estimator of
    tz + drift. That is why this takes the maximum raw rather than the median:
    a median over a set that includes an out-of-session instrument is biased
    downward by that instrument's staleness, and with a 117-pair universe the
    sample now routinely contains closed markets.
    """
    mt5 = _mt5()
    samples: list[float] = []

    # Prefer symbols already in use, then fill from the broker's list. Both
    # paths need the terminal up - see the ensure_connected() call in the
    # caller; probing before that returns an empty symbol list.
    candidates = list(_symbol_cache.values())[:_OFFSET_SAMPLE_SYMBOLS]
    if len(candidates) < _OFFSET_SAMPLE_SYMBOLS:
        for info in (mt5.symbols_get() or [])[:40]:
            if info.name not in candidates:
                candidates.append(info.name)
            if len(candidates) >= _OFFSET_SAMPLE_SYMBOLS:
                break

    for symbol in candidates:
        try:
            tick = mt5.symbol_info_tick(symbol)
        except Exception:  # noqa: BLE001 - one bad symbol must not end the probe
            continue
        if tick is not None and getattr(tick, "time", 0):
            samples.append(float(tick.time) - time.time())

    if not samples:
        return None

    raw = max(samples)
    # Snap to the nearest half hour: real broker offsets are whole or half
    # hours, so snapping recovers the timezone without the latency noise.
    tz = round(raw / 1800.0) * 1800.0
    if abs(tz) > _MAX_TZ_OFFSET_SEC:
        return None

    # Whatever the snap left over is this machine's clock disagreeing with the
    # broker's. Correct ONLY when the broker runs ahead (a positive residual):
    # that is the case that makes a live quote look future-dated and trips the
    # freshness gate. A negative residual is either a broker running behind -
    # which already makes quotes look older, the conservative direction - or a
    # sample set with no fresh tick in it at all, e.g. a weekend. Applying a
    # negative correction there would make a stale quote read as fresh, which
    # is the one failure mode this gate exists to prevent.
    drift = raw - tz
    drift = min(max(drift, 0.0), _MAX_CLOCK_DRIFT_SEC)
    return float(tz), float(drift)


def _refresh_offsets(force: bool = False) -> tuple[float, float]:
    global _server_offset, _clock_drift, _offset_stamp
    now = time.time()
    with _offset_lock:
        if not force and _server_offset is not None and                 (now - _offset_stamp) < _OFFSET_TTL_SEC:
            return _server_offset, _clock_drift

        # The terminal must be up before probing. Without this the symbol list
        # comes back empty, the probe finds nothing, and a 0.0 offset gets
        # cached for the full TTL - during which every MT5 timestamp is left
        # uncorrected and every quote reads hours in the future.
        try:
            ensure_connected()
        except Exception:  # noqa: BLE001
            return (_server_offset or 0.0), _clock_drift

        measured = _measure_offsets(now)
        if measured is None:
            # Do NOT cache a failed measurement. Returning an uncorrected 0.0
            # makes quotes look future-dated and fail closed, which is correct;
            # pinning it for 15 minutes is not.
            return (_server_offset or 0.0), _clock_drift

        _server_offset, _clock_drift = measured
        _offset_stamp = now
        return _server_offset, _clock_drift


def server_time_offset(force: bool = False) -> float:
    """Seconds to SUBTRACT from MT5 BAR timestamps to reach true UTC.

    MT5 reports bar times and tick times in the BROKER'S server timezone, not
    UTC, and the API exposes no timezone field. Read as epoch UTC, an MT5
    server on UTC+3 dates every bar and tick three hours in the future. That
    corrupts three things at once: freshness gates (a future-dated quote reads
    as zero seconds old and passes), session assignment for SAV, and the
    time-of-day buckets TCI learns from.

    Bars sit on the server's own hour boundaries, so they take the snapped
    timezone offset only. Quotes additionally carry the clock drift - see
    quote_time_offset.
    """
    return _refresh_offsets(force)[0]


def quote_time_offset(force: bool = False) -> float:
    """Seconds to SUBTRACT from MT5 TICK timestamps to reach true UTC.

    The timezone offset plus this machine's clock drift against the broker's.
    Snapping the timezone to the nearest half hour deliberately discards the
    residual, but for a tick that residual is not noise: it is the reason a
    quote that arrived a moment ago can read as several seconds in the FUTURE
    and be rejected by the freshness gate as a clock mismatch. Bars are
    unaffected because they are stamped on server hour boundaries.
    """
    tz, drift = _refresh_offsets(force)
    return tz + drift


def clock_drift_sec(force: bool = False) -> float:
    """Measured broker-ahead-of-local clock drift, for diagnostics."""
    return _refresh_offsets(force)[1]


def _mt5():
    try:
        import MetaTrader5 as mt5  # noqa: N813
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise FeedError("MetaTrader5 package is not installed") from exc
    return mt5


def _timeframe(mt5, timeframe: str):
    table = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    key = timeframe.upper()
    if key not in table:
        raise FeedError(f"MT5: unsupported timeframe {timeframe}")
    return table[key]


def ensure_connected() -> bool:
    global _initialised
    with _lock:
        if _initialised:
            return True
        mt5 = _mt5()
        if not mt5.initialize():
            code, message = mt5.last_error()
            raise FeedError(f"MT5 initialize failed ({code}): {message}")
        _initialised = True
        return True


def resolve_symbol(symbol: str) -> str:
    """Match an OPUS symbol to a broker symbol, allowing for suffixes.

    Brokers routinely append suffixes (EURUSD.raw, EURUSDm, XAUUSD_i). Failing
    on an exact-match miss would make the engine unusable on most live
    accounts, so an unambiguous prefix match is accepted and cached.
    """
    if symbol in _symbol_cache:
        return _symbol_cache[symbol]
    ensure_connected()
    mt5 = _mt5()

    info = mt5.symbol_info(symbol)
    if info is not None:
        if not info.visible:
            mt5.symbol_select(symbol, True)
        _symbol_cache[symbol] = symbol
        return symbol

    everything = mt5.symbols_get() or []
    candidates = [s.name for s in everything if s.name.upper().startswith(symbol.upper())]
    if not candidates:
        raise FeedError(f"MT5: no symbol matching '{symbol}'")
    # Shortest match is the plainest instrument rather than an exotic variant.
    chosen = sorted(candidates, key=len)[0]
    mt5.symbol_select(chosen, True)
    _symbol_cache[symbol] = chosen
    return chosen


class MT5Feed(Feed):
    name = SOURCE

    def candles(self, symbol: str, timeframe: str, count: int) -> Candles:
        ensure_connected()
        mt5 = _mt5()
        broker_symbol = resolve_symbol(symbol)
        rates = mt5.copy_rates_from_pos(
            broker_symbol, _timeframe(mt5, timeframe), 0, int(count)
        )
        if rates is None or len(rates) == 0:
            code, message = mt5.last_error()
            raise FeedError(
                f"MT5: no {timeframe} bars for {broker_symbol} ({code}: {message})"
            )

        arr = np.array(rates)
        fields = set(arr.dtype.names or ())
        volume = (
            arr["real_volume"].astype(float)
            if "real_volume" in fields
            else np.zeros(len(arr))
        )
        tick_based = False
        if volume.sum() <= 0 and "tick_volume" in fields:
            # FX brokers report tick count, not traded size. Flagged so ARC and
            # OFP discount it rather than treating a tick count as volume.
            volume = arr["tick_volume"].astype(float)
            tick_based = True

        # Normalise broker server time to UTC before anything downstream
        # sees it; every freshness gate and session boundary depends on it.
        offset = server_time_offset()
        return Candles(
            symbol=symbol, timeframe=timeframe.upper(),
            ts=arr["time"].astype(float) - offset,
            open=arr["open"].astype(float),
            high=arr["high"].astype(float),
            low=arr["low"].astype(float),
            close=arr["close"].astype(float),
            volume=volume,
            source=SOURCE,
            volume_is_tick_count=tick_based,
        )

    def quote(self, symbol: str) -> Quote | None:
        ensure_connected()
        mt5 = _mt5()
        broker_symbol = resolve_symbol(symbol)
        tick = mt5.symbol_info_tick(broker_symbol)
        if tick is None:
            return None
        bid, ask = float(tick.bid), float(tick.ask)
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        ts = float(getattr(tick, "time_msc", 0) or 0) / 1000.0
        if ts <= 0:
            ts = float(getattr(tick, "time", 0) or 0.0)
        if ts <= 0:
            return None          # no usable timestamp: fail closed, not "now"
        ts -= quote_time_offset()
        return Quote(symbol=symbol, bid=bid, ask=ask, ts=ts, source=SOURCE)

    def book(self, symbol: str, depth: int = 10) -> dict | None:
        """Depth of market, when the broker publishes it.

        Most retail FX brokers do not, so a None here is normal and OFP falls
        back to its bar-derived estimator rather than fabricating a book.
        """
        try:
            ensure_connected()
            mt5 = _mt5()
            broker_symbol = resolve_symbol(symbol)
            if not mt5.market_book_add(broker_symbol):
                return None
            try:
                items = mt5.market_book_get(broker_symbol)
            finally:
                mt5.market_book_release(broker_symbol)
        except Exception:  # noqa: BLE001
            return None
        if not items:
            return None

        bids, asks = [], []
        for item in items:
            price, volume = float(item.price), float(item.volume)
            if price <= 0 or volume <= 0:
                continue
            # BUY-side book entries are bids; SELL-side are offers.
            if int(item.type) in (0, 2):
                bids.append([price, volume])
            else:
                asks.append([price, volume])
        if not bids or not asks:
            return None
        bids.sort(key=lambda r: -r[0])
        asks.sort(key=lambda r: r[0])
        return {"bids": bids[:depth], "asks": asks[:depth], "source": SOURCE}


__all__ = [
    "MT5Feed", "SOURCE", "ensure_connected", "resolve_symbol",
    "server_time_offset",
]
