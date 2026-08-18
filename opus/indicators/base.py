"""Shared market context passed to every OPUS indicator.

Built once per symbol per scan. Holds the three timeframe rungs, the live
quote, and the volatility denominator that every sigma-normalised threshold in
the engine is expressed against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

import opus.config as config
from opus.core.stats import yang_zhang_sigma
from opus.types import Candles, Quote

# Bar duration in seconds, used for staleness and for session bucketing.
TF_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400,
}


@dataclass
class MarketContext:
    """Everything an indicator is allowed to look at."""

    symbol: str
    display: str
    asset_class: str
    context: Candles
    structure: Candles
    trigger: Candles
    quote: Quote | None
    now: float

    sigma: float = 0.0           # structure-TF Yang-Zhang sigma, price units
    sigma_trigger: float = 0.0
    sigma_context: float = 0.0
    _cache: dict = field(default_factory=dict, repr=False)

    @classmethod
    def build(
        cls,
        *,
        symbol: str,
        display: str,
        asset_class: str,
        context: Candles,
        structure: Candles,
        trigger: Candles,
        quote: Quote | None,
        now: float,
    ) -> "MarketContext":
        cfg = config.indicator("SIGMA")
        window = int(cfg["window"])
        min_window = int(cfg["min_window"])

        def _sigma(c: Candles) -> float:
            if len(c) < min_window:
                return 0.0
            return yang_zhang_sigma(c.open, c.high, c.low, c.close, window)

        ctx = cls(
            symbol=symbol,
            display=display,
            asset_class=asset_class,
            context=context,
            structure=structure,
            trigger=trigger,
            quote=quote,
            now=now,
        )
        ctx.sigma = _sigma(structure)
        ctx.sigma_trigger = _sigma(trigger)
        ctx.sigma_context = _sigma(context)
        return ctx

    @property
    def last_price(self) -> float:
        """Mid of the live quote when available, else the last structure close.

        Candle close is a fallback for scoring only. Anything that prices an
        actual order goes through `Quote.entry_price`, which crosses the spread.
        """
        if self.quote is not None and self.quote.mid > 0:
            return self.quote.mid
        if len(self.structure):
            return float(self.structure.close[-1])
        return 0.0

    @property
    def has_sigma(self) -> bool:
        return np.isfinite(self.sigma) and self.sigma > 0

    def cached(self, key: str, factory):
        """Memoise a derived structure (pools, voids) across indicators."""
        if key not in self._cache:
            self._cache[key] = factory()
        return self._cache[key]

    def bar_age_sec(self, timeframe: str, candles: Candles) -> float:
        """Seconds since the CLOSE of the most recent confirmed bar."""
        if not len(candles):
            return float("inf")
        span = TF_SECONDS.get(timeframe.upper(), 0)
        return max(0.0, self.now - (candles.last_ts + span))

    def session_of(self, ts: float) -> str:
        return session_for_ts(ts)


def session_for_ts(ts: float) -> str:
    """Name the configured UTC session containing `ts`."""
    try:
        hour = datetime.fromtimestamp(float(ts), tz=timezone.utc).hour
    except (OverflowError, OSError, ValueError):
        return "UNKNOWN"
    for name, window in config.sessions().items():
        start = int(window["start"])
        end = int(window["end"])
        if start <= hour < end:
            return str(name)
    return "UNKNOWN"


def session_start_index(ts: np.ndarray, now_ts: float) -> int:
    """Index of the first bar of the session containing the newest bar.

    Returns 0 when the series does not reach back to a session boundary, so a
    short history degrades to "anchor at the start of what we have" rather than
    raising or silently anchoring to the wrong session.
    """
    if ts.shape[0] == 0:
        return 0
    current = session_for_ts(now_ts)
    idx = ts.shape[0] - 1
    while idx > 0 and session_for_ts(float(ts[idx - 1])) == current:
        idx -= 1
    return idx


__all__ = ["MarketContext", "TF_SECONDS", "session_for_ts", "session_start_index"]
