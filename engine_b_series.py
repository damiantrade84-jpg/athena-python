"""Run-scoped causal series cache for the Engine B research-lab probes.

The three research-lab probes in market_structure.py (_engine_b_vwap_reclaim_value,
_engine_b_cvd_momentum_value, _engine_b_vwap_deviation_value) recompute full
indicator series over the sliding live-parity window at every evaluated bar —
three probes x two directions per bar, and calc_vwap additionally recomputes an
ATR series whose band outputs the probes never read. Every value the probes
actually consume is derivable in O(1) from per-series cumulative arrays:

- windowed VWAP: ``(CumTPV[end] - CumTPV[start]) / (CumV[end] - CumV[start])``
  (calc_vwap anchors at the window start and rounds to 8dp; the parity test in
  tests/test_engine_b_probe_series_parity.py asserts the rounded values match
  the original per-window recompute at every index)
- smoothed CVD delta: SMA-5 over per-bar deltas (each bar's delta is bar-local
  and therefore prefix-invariant — no cumsum associativity concern)

Default-off contract: probes fall back to the original per-window recompute
when no cache is supplied, when the window is not a contiguous slice of the
cached series, or when the build-time self-check fails (fail closed). Live
scanning never passes a cache and is byte-identical.

Never share a cache across runs: the Tuning Lab variant run mutates CONFIG
between runs, and each backtest run rebuilds this from its own store data.
"""

from __future__ import annotations

from typing import Any

# Result marker for ambiguous bar times (a duplicated timestamp makes a
# window's position in the full series undecidable -> caller falls back).
_AMBIGUOUS = object()


class _WindowView:
    """O(1) probe values for one contiguous slice of the parent series."""

    __slots__ = ("_parent", "_start", "_end")

    def __init__(self, parent: "EngineBProbeSeries", start: int, end: int):
        self._parent = parent
        self._start = start
        self._end = end

    def _vwap_at(self, index: int) -> float | None:
        p = self._parent
        cum_vol = p._cum_vol[index + 1] - p._cum_vol[self._start]
        if cum_vol <= 0:
            return None
        cum_tp_vol = p._cum_tp_vol[index + 1] - p._cum_tp_vol[self._start]
        return round(cum_tp_vol / cum_vol, 8)

    def vwap_last(self) -> float | None:
        return self._vwap_at(self._end - 1)

    def vwap_prev(self) -> float | None:
        return self._vwap_at(self._end - 2)

    def _cvd_smoothed_at(self, index: int) -> float | None:
        p = self._parent
        if index < 4:
            return None
        # calc_sma sums the five rounded deltas left-to-right; deltas are never
        # None here, so this is bit-identical to calc_sma's window mean.
        return sum(p._delta[index - 4 : index + 1]) / 5

    def cvd_smoothed_last(self) -> float | None:
        return self._cvd_smoothed_at(self._end - 1)

    def cvd_smoothed_prev(self) -> float | None:
        return self._cvd_smoothed_at(self._end - 2)


class EngineBProbeSeries:
    """Cumulative VWAP/CVD arrays over one full entry-TF candle series."""

    def __init__(self, rows: list[dict[str, Any]], *, self_check_samples: int = 24):
        self.disabled = True
        self._rows = list(rows or [])
        n = len(self._rows)
        self._cum_tp_vol = [0.0] * (n + 1)
        self._cum_vol = [0.0] * (n + 1)
        self._delta = [0.0] * n
        self._time_to_index: dict[Any, Any] = {}
        if n < 2:
            return
        try:
            for i, c in enumerate(self._rows):
                # Same coercions as indicators.calc_vwap / calc_cvd. An
                # unparseable value disables the cache (fail closed) — the
                # probes then take their original recompute path verbatim.
                close = float(c.get("close", 0))
                high = float(c.get("high", close))
                low = float(c.get("low", close))
                vol = float(c.get("vol", 0) or 0.0)
                tp = (high + low + close) / 3.0
                if vol > 0:
                    self._cum_tp_vol[i + 1] = self._cum_tp_vol[i] + tp * vol
                    self._cum_vol[i + 1] = self._cum_vol[i] + vol
                else:
                    self._cum_tp_vol[i + 1] = self._cum_tp_vol[i]
                    self._cum_vol[i + 1] = self._cum_vol[i]

                open_ = float(c.get("open", 0))
                candle_range = high - low
                if candle_range <= 0:
                    self._delta[i] = 0.0
                else:
                    eff_vol = vol if vol > 0 else candle_range
                    buy_vol = eff_vol * (close - low) / candle_range
                    sell_vol = eff_vol * (high - close) / candle_range
                    self._delta[i] = round(buy_vol - sell_vol, 4)

                t = c.get("time")
                if t in self._time_to_index:
                    self._time_to_index[t] = _AMBIGUOUS
                else:
                    self._time_to_index[t] = i
        except (TypeError, ValueError):
            self._time_to_index = {}
            return
        self.disabled = not self._self_check(self_check_samples)

    # ── window resolution ────────────────────────────────────────────────────
    def for_window(self, candles: list[dict[str, Any]] | None) -> _WindowView | None:
        if self.disabled or not candles or len(candles) < 2:
            return None
        end_marker = self._time_to_index.get(candles[-1].get("time"))
        if end_marker is None or end_marker is _AMBIGUOUS:
            return None
        end = int(end_marker) + 1
        start = end - len(candles)
        if start < 0:
            return None
        if self._rows[start].get("time") != candles[0].get("time"):
            return None
        return _WindowView(self, start, end)

    # ── build-time verification (fail closed) ────────────────────────────────
    def _self_check(self, samples: int) -> bool:
        """Compare cached VWAP against the original calc_vwap recompute on a
        spread of window positions. Any mismatch (or an exception) disables the
        cache, so a parity break can never silently change probe outputs."""
        try:
            from indicators import calc_vwap
        except Exception:
            return False
        n = len(self._rows)
        positions = {0, n // 2, n - 1}
        if samples > 3 and n > 4:
            step = max(1, (n - 1) // (samples - 1))
            positions.update(range(0, n, step))
        for end in sorted(p + 1 for p in positions if 0 <= p < n):
            for start in (0, max(0, end - 500)):
                if end - start < 2:
                    continue
                window = self._rows[start:end]
                try:
                    original = calc_vwap(window).get("vwap") or []
                except Exception:
                    return False
                if len(original) < 2:
                    return False
                view = _WindowView(self, start, end)
                if view.vwap_last() != original[-1] or view.vwap_prev() != original[-2]:
                    return False
        return True
