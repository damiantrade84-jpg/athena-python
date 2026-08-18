"""OFP - Order Flow Pressure.

The literature is consistent that signed order flow imbalance is the strongest
short-horizon predictor available intraday, that the relationship to forward
returns is close to linear, and that aggregating across multiple book levels
beats reading the top of book alone. It is also consistent that the effect is
regime-dependent, which is why OPUS conditions this reading rather than
trusting it flat.

Two estimators, because depth is not available on every venue:

  bar-derived  Signed volume split by where the bar closed inside its range.
               A bar closing on its high traded predominantly at the offer.
               Available everywhere, including MT5 where "volume" is a tick
               count - still a valid participation proxy.

  book-derived Depth imbalance across the top N levels, size-weighted and
               distance-decayed. Used when a live order book is attached.

Divergence is scored separately and matters more than level: price making a new
high while cumulative delta fails to confirm means the move is being sold into.
"""

from __future__ import annotations

import numpy as np

import opus.config as config
from opus.indicators.base import MarketContext
from opus.types import IndicatorReading


def bar_delta(candles, lookback: int) -> np.ndarray:
    """Per-bar signed volume from close location within the range.

    buy_share  = (close - low)  / (high - low)
    sell_share = (high - close) / (high - low)
    delta      = volume * (buy_share - sell_share)

    which reduces to volume * (2*close - high - low) / (high - low).
    """
    n = len(candles)
    if n == 0:
        return np.zeros(0)
    start = max(0, n - lookback)
    h = candles.high[start:]
    lo = candles.low[start:]
    c = candles.close[start:]
    v = candles.volume[start:]

    rng = h - lo
    share = np.zeros(c.shape[0])
    ok = np.isfinite(rng) & (rng > 0) & np.isfinite(c)
    share[ok] = (2.0 * c[ok] - h[ok] - lo[ok]) / rng[ok]

    vol = np.where(np.isfinite(v) & (v > 0), v, 0.0)
    if vol.sum() <= 0:
        # No usable volume: the close-location share alone still carries the
        # directional information, just unweighted by participation.
        vol = np.ones(c.shape[0])
    return share * vol


def book_imbalance(book: dict | None, levels: int) -> float | None:
    """Distance-decayed depth imbalance across the top `levels`, in [-1, 1].

    `book` is {"bids": [[price, size], ...], "asks": [[price, size], ...]}
    sorted best-first. Returns None when the book is unusable so the caller
    falls back to the bar estimator instead of scoring a fabricated zero.
    """
    if not isinstance(book, dict):
        return None
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if len(bids) < 1 or len(asks) < 1:
        return None

    def _side(rows) -> float:
        total = 0.0
        for depth, row in enumerate(rows[:levels]):
            try:
                size = float(row[1])
            except (TypeError, ValueError, IndexError):
                continue
            if not np.isfinite(size) or size <= 0:
                continue
            # Size resting five levels away is far less likely to transact
            # than size at the touch.
            total += size / (1.0 + depth)
        return total

    bid_depth = _side(bids)
    ask_depth = _side(asks)
    denom = bid_depth + ask_depth
    if denom <= 0:
        return None
    return float((bid_depth - ask_depth) / denom)


def compute(ctx: MarketContext, book: dict | None = None) -> IndicatorReading:
    cfg = config.indicator("OFP")
    candles = ctx.trigger if len(ctx.trigger) >= 30 else ctx.structure
    n = len(candles)
    if n < 20:
        return IndicatorReading("OFP", 0.0, 0.0, {"reason": "insufficient_bars"})

    lookback = int(cfg["lookback"])
    delta_window = int(cfg["delta_window"])
    delta = bar_delta(candles, lookback)
    if delta.shape[0] < 10:
        return IndicatorReading("OFP", 0.0, 0.0, {"reason": "no_delta"})

    cum = np.cumsum(delta)

    # Net flow over the recent window, scaled by how large a window of that
    # size typically is. Note this deliberately does NOT z-score `cum` against
    # its own trailing mean: a cumulative sum is itself a random walk, its
    # level drifts without bound, and the last point sits far from any trailing
    # average by construction. Doing that reads ~0.8 on pure noise. Comparing a
    # k-bar flow against the distribution of k-bar flows is stationary.
    k = int(max(3, min(delta_window, delta.shape[0] // 3)))
    window_sums = np.convolve(delta, np.ones(k), mode="valid")
    recent_flow = float(window_sums[-1])
    if window_sums.shape[0] >= 8:
        baseline = window_sums[:-1]
        med = float(np.median(baseline))
        mad = float(np.median(np.abs(baseline - med)))
        scale = 1.4826 * mad
        if scale <= 1e-12:
            scale = float(np.std(baseline)) or 1.0
        delta_z = (recent_flow - med) / scale
    else:
        delta_z = 0.0
    bar_component = float(np.tanh(delta_z / float(cfg["squash_scale"])))

    # Divergence: compare the direction of the price extreme against the
    # direction of the cumulative-delta extreme over the same window.
    div_look = int(cfg["divergence_lookback"])
    divergence = 0.0
    if delta.shape[0] > div_look + 2:
        c = candles.close[-div_look:]
        d = cum[-div_look:]
        price_slope = float(c[-1] - c[0])
        delta_slope = float(d[-1] - d[0])
        price_rng = float(np.nanmax(c) - np.nanmin(c))
        delta_rng = float(np.nanmax(d) - np.nanmin(d))
        if price_rng > 0 and delta_rng > 0:
            pn = price_slope / price_rng
            dn = delta_slope / delta_rng
            if np.sign(pn) != 0 and np.sign(pn) != np.sign(dn):
                # Effort and result disagree; the sign follows FLOW, not price.
                divergence = float(np.clip(dn - pn, -2.0, 2.0)) * 0.5

    book_component = book_imbalance(book, int(cfg["book_levels"]))
    if book_component is None:
        value = float(np.clip(bar_component + divergence, -1.0, 1.0))
        confidence = float(np.tanh(delta.shape[0] / 30.0)) * 0.85
        source = "bar"
    else:
        w = float(cfg["book_weight"])
        blended = w * book_component + (1.0 - w) * bar_component
        value = float(np.clip(blended + divergence, -1.0, 1.0))
        confidence = float(np.tanh(delta.shape[0] / 30.0))
        source = "book+bar"

    if candles.volume_is_tick_count:
        confidence *= 0.85

    # `bar_component` is already a tanh, and `value` is already clipped to
    # [-1, 1]. Squashing again here (as an earlier revision did) compressed the
    # midrange while inflating the tails, so ordinary noise presented as strong
    # flow. One compression stage only.
    return IndicatorReading(
        "OFP",
        value,
        confidence,
        {
            "source": source,
            "deltaZ": round(float(delta_z), 4),
            "barComponent": round(bar_component, 4),
            "bookComponent": round(book_component, 4) if book_component is not None else None,
            "divergence": round(float(divergence), 4),
            "cumDelta": round(float(cum[-1]), 4),
            "bars": int(delta.shape[0]),
            "timeframe": candles.timeframe,
            "volumeIsTickCount": bool(candles.volume_is_tick_count),
        },
    )


__all__ = ["compute", "bar_delta", "book_imbalance"]
