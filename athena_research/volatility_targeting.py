"""Research-only portfolio/position volatility targeting helpers.

WARNING: Disabled by default (``RESEARCH_VOL_TARGETING_ENABLED: false``).
This module does not change live order sizing, Engine A/B scoring, or SL/TP.
Backtests may record ``original_size`` and ``vol_target_adjusted_size`` metadata
when enabled; equity curves still use the pre-existing backtest sizing path.
"""

from __future__ import annotations

import math
from typing import Any, Iterable


def _float_or_none(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def realized_volatility_from_returns(
    returns: Iterable[Any],
    *,
    annualization: float = 252.0,
    lookback: int | None = None,
) -> float | None:
    """Annualized realized volatility from arithmetic returns."""
    vals = [_float_or_none(v) for v in list(returns or [])]
    clean = [v for v in vals if v is not None]
    if lookback is not None and lookback > 0:
        clean = clean[-int(lookback):]
    if len(clean) < 2:
        return None
    mean = sum(clean) / len(clean)
    variance = sum((v - mean) ** 2 for v in clean) / (len(clean) - 1)
    if variance < 0:
        return None
    ann = _float_or_none(annualization)
    if ann is None or ann <= 0:
        ann = 252.0
    vol = math.sqrt(variance) * math.sqrt(ann)
    return round(vol, 10) if math.isfinite(vol) and vol > 0 else None


def atr_percent_volatility(atr: Any, close: Any) -> float | None:
    """ATR as a percent of price. This is not annualized."""
    atr_f = _float_or_none(atr)
    close_f = _float_or_none(close)
    if atr_f is None or close_f is None or atr_f <= 0 or close_f <= 0:
        return None
    return round(atr_f / close_f, 10)


def capped_position_multiplier(
    multiplier: Any,
    *,
    min_multiplier: float = 0.25,
    max_multiplier: float = 1.50,
) -> float:
    value = _float_or_none(multiplier)
    if value is None or value <= 0:
        return 1.0
    low = _float_or_none(min_multiplier)
    high = _float_or_none(max_multiplier)
    low = 0.25 if low is None else max(0.0, low)
    high = 1.50 if high is None else max(0.0, high)
    if high < low:
        low, high = high, low
    return round(max(low, min(high, value)), 10)


def inverse_volatility_weight(
    volatility: Any,
    *,
    target_volatility: float = 0.12,
    min_multiplier: float = 0.25,
    max_multiplier: float = 1.50,
) -> float:
    vol = _float_or_none(volatility)
    target = _float_or_none(target_volatility)
    if vol is None or vol <= 0 or target is None or target <= 0:
        return 1.0
    return capped_position_multiplier(
        target / vol,
        min_multiplier=min_multiplier,
        max_multiplier=max_multiplier,
    )


def apply_research_vol_target_size(
    *,
    original_size: Any,
    returns: Iterable[Any] | None = None,
    atr: Any = None,
    close: Any = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return research-only volatility-targeted sizing metadata.

    The caller decides whether to record this metadata. This function does not
    mutate live risk settings, SL/TP, or order quantities.
    """
    cfg = cfg or {}
    original = _float_or_none(original_size)
    original = 0.0 if original is None else max(0.0, original)
    enabled = bool(cfg.get("RESEARCH_VOL_TARGETING_ENABLED", False))
    if not enabled:
        return {
            "vol_targeting_applied": False,
            "original_size": round(original, 10),
            "vol_target_adjusted_size": round(original, 10),
            "multiplier": 1.0,
            "volatility": None,
            "volatility_source": "disabled",
        }

    lookback = int(cfg.get("RESEARCH_VOL_TARGET_LOOKBACK", 20) or 20)
    target = float(cfg.get("RESEARCH_VOL_TARGET_ANNUALIZED", 0.12) or 0.12)
    max_mult = float(cfg.get("RESEARCH_VOL_TARGET_MAX_MULTIPLIER", 1.50) or 1.50)
    min_mult = float(cfg.get("RESEARCH_VOL_TARGET_MIN_MULTIPLIER", 0.25) or 0.25)

    vol = realized_volatility_from_returns(
        returns or [],
        annualization=252.0,
        lookback=lookback,
    )
    source = "realized_returns"
    if vol is None:
        vol = atr_percent_volatility(atr, close)
        source = "atr_percent" if vol is not None else "unavailable"

    multiplier = inverse_volatility_weight(
        vol,
        target_volatility=target,
        min_multiplier=min_mult,
        max_multiplier=max_mult,
    )
    adjusted = round(original * multiplier, 10)
    return {
        "vol_targeting_applied": source != "unavailable",
        "original_size": round(original, 10),
        "vol_target_adjusted_size": adjusted,
        "multiplier": multiplier,
        "volatility": round(vol, 10) if vol is not None else None,
        "volatility_source": source,
        "target_volatility": round(target, 10),
        "min_multiplier": round(min_mult, 10),
        "max_multiplier": round(max_mult, 10),
        "lookback": lookback,
    }
