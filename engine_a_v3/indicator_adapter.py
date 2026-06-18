from __future__ import annotations

from typing import Any, Mapping


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
