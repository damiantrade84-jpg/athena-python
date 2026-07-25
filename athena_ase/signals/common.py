"""Shared math and PTIS series access for ASE Layer 1 signals."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from athena_ase.data.ingest.common import compact_symbol
from athena_ase.data.ptis import PTISStore, load_window
from athena_ase.horizon import Horizon, HORIZONS


PRICE_SOURCES = ("EODHD", "MT5", "BINANCE", "BYBIT")

# Live-scan bar-age bounds per horizon. Wide enough to survive weekends and
# long holiday closures on TradFi venues, narrow enough to fail closed when a
# feed has actually died (observed: crypto BINANCE bars 45-134 days stale
# still produced TRADE/WATCH signals). Applies to live candidate generation
# only — historical backfill/training paths are unaffected.
MAX_LIVE_BAR_AGE_MS: dict[str, int] = {
    "intraday": 96 * 3_600_000,  # 4 days
    "swing": 7 * 86_400_000,  # 7 days
}


def price_series_id(source: str, symbol: str, tf: str, field: str) -> str:
    return f"{source}:{compact_symbol(symbol)}:{tf.upper()}:{field.lower()}"


def resolve_price_source(
    store: PTISStore,
    symbol: str,
    tf: str,
    *,
    end_ms: int | None = None,
) -> str | None:
    """Pick the source with the freshest last bar at/before end_ms.

    First-existing resolution proved unsafe: a long-dead series (e.g. stale
    BINANCE bars from the backtest cache) would shadow a live source appended
    later. Ties keep the earlier PRICE_SOURCES entry.
    """
    import time as _time

    horizon_end = end_ms if end_ms is not None else int(_time.time() * 1000)
    best: str | None = None
    best_t = -1
    for source in PRICE_SOURCES:
        sid = price_series_id(source, symbol, tf, "close")
        if not store.series_exists(sid):
            continue
        try:
            rows = store.asof(sid, horizon_end, 1)
        except KeyError:
            continue
        last_t = int(rows[-1]["value_time"]) if len(rows) else -1
        if last_t > best_t:
            best = source
            best_t = last_t
    return best


def live_bars_stale(
    series: "BarSeries",
    horizon: Horizon,
    decision_time_ms: int,
) -> bool:
    """True when the newest available bar is older than the live-age bound."""
    if len(series.value_time) == 0:
        return True
    max_age = MAX_LIVE_BAR_AGE_MS.get(horizon, MAX_LIVE_BAR_AGE_MS["intraday"])
    last_t = int(series.value_time[-1])
    return (decision_time_ms - last_t) > max_age


@dataclass
class BarSeries:
    value_time: np.ndarray
    available_time: np.ndarray
    close_log: np.ndarray
    high_log: np.ndarray
    low_log: np.ndarray
    sigma_bar: np.ndarray
    sigma_long: np.ndarray
    # Optional for backwards-compatible fixtures.  Entry-timing experiments
    # reject a series without it rather than falling back to the close.
    open_log: np.ndarray | None = None


def ewma_vol(logret: np.ndarray, span: int) -> np.ndarray:
    """Per-bar EWMA std of log returns; leading values NaN until warm."""
    if len(logret) == 0:
        return np.array([], dtype=float)
    if len(logret) == 1:
        out = np.full(1, np.nan)
        return out
    alpha = 2.0 / (span + 1.0)
    var = np.full(len(logret), np.nan)
    v = float(logret[1] ** 2) if len(logret) > 1 else 0.0
    var[1] = v
    for i in range(2, len(logret)):
        x = float(logret[i])
        if math.isnan(x):
            var[i] = var[i - 1]
        else:
            v = alpha * (x * x) + (1.0 - alpha) * v
            var[i] = v
    return np.sqrt(var)


def mask_available(rows: np.ndarray, decision_time_ms: int) -> np.ndarray:
    if len(rows) == 0:
        return rows
    return rows[rows["available_time"] <= decision_time_ms]


def asof_tail(rows: np.ndarray, decision_time_ms: int, n: int) -> np.ndarray:
    """Last n availability-resolved rows at decision_time."""
    masked = mask_available(rows, decision_time_ms)
    if n <= 0 or len(masked) == 0:
        return masked[:0]
    return masked[-n:]


def load_bar_series(
    store: PTISStore,
    symbol: str,
    horizon: Horizon,
    start_ms: int,
    end_ms: int,
) -> BarSeries | None:
    cfg = HORIZONS[horizon]
    tf = cfg.tf
    source = resolve_price_source(store, symbol, tf, end_ms=end_ms)
    if source is None:
        return None
    close_id = price_series_id(source, symbol, tf, "close")
    open_id = price_series_id(source, symbol, tf, "open")
    high_id = price_series_id(source, symbol, tf, "high")
    low_id = price_series_id(source, symbol, tf, "low")
    try:
        close_rows = store.load_window(close_id, start_ms, end_ms)
        open_rows = store.load_window(open_id, start_ms, end_ms)
        high_rows = store.load_window(high_id, start_ms, end_ms)
        low_rows = store.load_window(low_id, start_ms, end_ms)
    except KeyError:
        return None
    if len(close_rows) == 0:
        return None

    vt = close_rows["value_time"]
    close = close_rows["value"].astype(float)
    close_log = np.log(np.maximum(close, 1e-12))
    logret = np.full(len(close_log), np.nan)
    logret[1:] = close_log[1:] - close_log[:-1]

    sigma_bar = ewma_vol(logret, cfg.sigma_bar_span)
    sigma_long = ewma_vol(logret, cfg.sigma_long_span)

    high_map = {int(r["value_time"]): float(r["value"]) for r in high_rows}
    low_map = {int(r["value_time"]): float(r["value"]) for r in low_rows}
    open_map = {int(r["value_time"]): float(r["value"]) for r in open_rows}
    high_log = np.array(
        [math.log(max(high_map.get(int(t), math.exp(c)), 1e-12)) for t, c in zip(vt, close_log)],
        dtype=float,
    )
    low_log = np.array(
        [math.log(max(low_map.get(int(t), math.exp(c)), 1e-12)) for t, c in zip(vt, close_log)],
        dtype=float,
    )

    return BarSeries(
        value_time=vt,
        available_time=close_rows["available_time"],
        close_log=close_log,
        high_log=high_log,
        low_log=low_log,
        sigma_bar=sigma_bar,
        sigma_long=sigma_long,
        open_log=np.array(
            [math.log(max(open_map.get(int(t), float("nan")), 1e-12)) for t in vt],
            dtype=float,
        ),
    )


def bar_index_at_decision(series: BarSeries, decision_time_ms: int) -> int | None:
    """Index of last bar whose data is available at decision_time."""
    idx = None
    for i in range(len(series.value_time)):
        if series.available_time[i] <= decision_time_ms:
            idx = i
        else:
            break
    return idx


def load_window_series(store: PTISStore, series_id: str, start_ms: int, end_ms: int) -> np.ndarray:
    return store.load_window(series_id, start_ms, end_ms)
