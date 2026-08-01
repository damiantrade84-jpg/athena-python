from __future__ import annotations

from typing import Any, Mapping


def _previous_valid(series: list, index: int):
    for position in range(index, -1, -1):
        value = series[position]
        if value is not None:
            return value
    return None


def _valid_tail(series: list, index: int, limit: int) -> list:
    values = []
    for position in range(index, -1, -1):
        value = series[position]
        if value is None:
            continue
        values.append(value)
        if len(values) >= limit:
            break
    values.reverse()
    return values


def _legacy_percentile_at(series: list, index: int, lookback: int) -> tuple:
    valid = _valid_tail(series, index, lookback)
    if len(valid) < 20:
        return None, "insufficient data"
    current = valid[-1]
    percentile = round(sum(1 for value in valid if value <= current) / len(valid) * 100, 1)
    label = "expanding" if len(valid) >= 3 and valid[-1] > valid[-2] else "contracting"
    return percentile, label


def _adx_momentum_at(series: list, index: int, window: int = 5) -> tuple:
    valid = _valid_tail(series, index, 20)
    if len(valid) < window + 2:
        return 0.0, "stable"
    recent = valid[-(window + 1) :]
    slope = (recent[-1] - recent[0]) / window
    peak = max(valid)
    current = valid[-1]
    if slope > 0.5:
        return round(slope, 2), "strengthening"
    if peak > 30 and current < peak * 0.8 and slope < -0.3:
        return round(slope, 2), "collapsing"
    if peak > 25 and slope < -0.3:
        return round(slope, 2), "exhausting"
    return round(slope, 2), "stable"


def _snapshot_from_bundle(
    bundle: dict,
    *,
    length: int,
    adx_percentiles: list,
    atr_percentiles: list,
) -> dict[str, Any] | None:
    """Return the exact snapshot produced by ``indicator_snapshot(rows[:length])``.

    Indicator series are causal, so values calculated once over the full immutable
    backtest series are identical at every earlier index. Only the point-in-time
    latest-value reductions are done here.
    """
    latest = int(length) - 1
    closes = bundle["close"]
    if latest < 0 or latest >= len(closes):
        return None

    ema21 = bundle["ema21"]
    ema50 = bundle["ema50"]
    ema200 = bundle["ema200"]
    rsi = bundle["rsi"]
    macd = bundle["macd"]
    atr = bundle["atr"]
    adx = bundle["adx"]
    bb = bundle["bb"]
    stoch = bundle.get("stoch") or {}
    keltner = bundle.get("keltner") or {}

    adx_pct_legacy, adx_label = _legacy_percentile_at(adx["adx"], latest, 252)
    atr_pct_legacy, atr_label = _legacy_percentile_at(atr, latest, 100)
    adx_slope, adx_momentum = _adx_momentum_at(adx["adx"], latest)
    ema200_slope = (
        round((ema200[latest] - ema200[latest - 10]) / ema200[latest - 10] * 100, 3)
        if ema200[latest] and latest >= 10 and ema200[latest - 10]
        else 0
    )

    return {
        "ema21": ema21[latest],
        "ema50": ema50[latest],
        "ema200": ema200[latest],
        "close": closes[latest],
        "rsi": rsi[latest],
        "rsiPrev": _previous_valid(rsi, latest - 1),
        "macdLine": macd["macd"][latest],
        "macdSignal": macd["signal"][latest],
        "macdHist": macd["hist"][latest],
        "macdHistPrev": macd["hist"][latest - 1] if latest > 0 else None,
        "atr": atr[latest],
        "adx": adx["adx"][latest],
        "adxPrev": _previous_valid(adx["adx"], latest - 1),
        "adxPct": adx_pct_legacy,
        "adxLabel": adx_label,
        "atrPct": atr_pct_legacy,
        "atrLabel": atr_label,
        "adxSlope": adx_slope,
        "adxMomentum": adx_momentum,
        "plusDI": adx["plusDI"][latest],
        "minusDI": adx["minusDI"][latest],
        "bbUpper": bb["upper"][latest],
        "bbMid": bb["mid"][latest],
        "bbLower": bb["lower"][latest],
        "ema200Slope10": ema200_slope,
        "adx_pct": adx_percentiles[latest],
        "atr_pct": atr_percentiles[latest],
        # Tuning Lab indicator set — mirrors indicators.calc_indicators' snap
        # keys so the backtest fast path stays index-identical to live.
        "stochK": (stoch.get("k") or [None])[latest] if latest < len(stoch.get("k") or []) else None,
        "stochD": (stoch.get("d") or [None])[latest] if latest < len(stoch.get("d") or []) else None,
        "cci": bundle["cci"][latest] if "cci" in bundle and latest < len(bundle["cci"]) else None,
        "williamsR": bundle["williamsR"][latest] if "williamsR" in bundle and latest < len(bundle["williamsR"]) else None,
        "roc": bundle["roc"][latest] if "roc" in bundle and latest < len(bundle["roc"]) else None,
        "mfi": bundle["mfi"][latest] if "mfi" in bundle and latest < len(bundle["mfi"]) else None,
        "keltnerUpper": (keltner.get("upper") or [None])[latest] if latest < len(keltner.get("upper") or []) else None,
        "keltnerMid": (keltner.get("mid") or [None])[latest] if latest < len(keltner.get("mid") or []) else None,
        "keltnerLower": (keltner.get("lower") or [None])[latest] if latest < len(keltner.get("lower") or []) else None,
    }


class BacktestIndicatorSnapshotCache(dict):
    """Lazy full-series indicator cache for point-in-time V3 backtests.

    The normal dictionary entries retain the existing ``(tf, prefix_length)``
    contract. Full indicator arrays are built once per timeframe/period set and
    then reduced to exact historical snapshots in O(lookback) time.
    """

    def __init__(self, candles: Mapping[str, list[dict]]):
        super().__init__()
        self._candles = candles
        self._series: dict[tuple, dict | None] = {}

    def snapshot_at(
        self,
        timeframe: str,
        length: int,
        periods: Mapping[str, int],
        asset_type: str,
    ) -> dict[str, Any] | None:
        rows = self._candles.get(timeframe) or []
        if length <= 0 or length > len(rows):
            return None
        try:
            period_key = tuple(sorted((str(key), int(value)) for key, value in periods.items()))
        except (TypeError, ValueError):
            return None
        key = (timeframe, period_key, str(asset_type or ""))
        if key not in self._series:
            try:
                from indicators import _calc_indicator_bundle, get_normalization_lookback, percentile_rank

                bundle = _calc_indicator_bundle(rows, periods=dict(periods))
                lookback = get_normalization_lookback(asset_type)
                self._series[key] = {
                    "bundle": bundle,
                    "adx_percentiles": percentile_rank(bundle["adx"]["adx"], lookback),
                    "atr_percentiles": percentile_rank(bundle["atr"], lookback),
                }
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                # Preserve the legacy prefix path for malformed/unexpected data.
                self._series[key] = None
        precomputed = self._series[key]
        if precomputed is None:
            return None
        return _snapshot_from_bundle(
            precomputed["bundle"],
            length=length,
            adx_percentiles=precomputed["adx_percentiles"],
            atr_percentiles=precomputed["atr_percentiles"],
        )


def indicator_snapshot(candles: list[dict], periods: Mapping[str, int], asset_type: str) -> dict[str, Any]:
    from indicators import (
        _calc_indicator_bundle,
        calc_indicators,
        get_normalization_lookback,
        percentile_rank,
    )

    bundle = _calc_indicator_bundle(candles, periods=dict(periods))
    base = calc_indicators(candles, _bundle=bundle)
    latest = len(candles) - 1
    lookback = get_normalization_lookback(asset_type)
    adx_pct = percentile_rank(bundle["adx"]["adx"], lookback)
    atr_pct = percentile_rank(bundle["atr"], lookback)
    snap = dict(base["snap"])
    snap["adx_pct"] = adx_pct[latest]
    snap["atr_pct"] = atr_pct[latest]
    return snap
