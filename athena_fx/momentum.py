"""Phase 4 — time-series and cross-sectional momentum family for athena_fx."""
from __future__ import annotations

import logging
import math
from statistics import pstdev
from typing import Any, Optional

from config import CONFIG

from athena_fx.models import (
    DIAG_MISSING_OHLC,
    FAMILY_MOMENTUM,
    Diagnostic,
    FamilyScore,
    G8_CURRENCIES,
    pair_legs,
)

log = logging.getLogger("athena_fx.momentum")

_WINDOWS = (63, 126, 252)
_WINDOW_KEYS = {63: "z_3m", 126: "z_6m", 252: "z_12m"}
_WINDOW_WEIGHTS = {63: 0.30, 126: 0.30, 252: 0.40}


def _filter_rows(rows: list[dict], as_of_date: str) -> list[dict]:
    filtered = [r for r in rows if str(r.get("date", "")) <= as_of_date]
    return sorted(filtered, key=lambda r: str(r["date"]))


def _return_field(side: str) -> str:
    return "long_total_return" if side.lower() == "long" else "short_total_return"


def _compound_return(daily_returns: list[float]) -> float:
    total = 1.0
    for r in daily_returns:
        total *= 1.0 + r
    return total - 1.0


def _window_z(daily_returns: list[float], window: int) -> Optional[float]:
    if len(daily_returns) < window:
        return None
    window_returns = daily_returns[-window:]
    ret_w = _compound_return(window_returns)
    if len(window_returns) < 2:
        return None
    vol_w = pstdev(window_returns) * math.sqrt(252.0)
    if vol_w <= 0:
        return None
    scale = vol_w * math.sqrt(window / 252.0)
    if scale <= 0:
        return None
    return ret_w / scale


def tsmom_score(
    total_return_rows: list[dict],
    as_of_date: str,
    *,
    side: str = "long",
) -> dict | None:
    """Compute volatility-scaled TSMOM z-scores from total-return rows."""
    rows = _filter_rows(total_return_rows, as_of_date)
    if len(rows) < 252:
        return None

    field = _return_field(side)
    daily = [float(r[field]) for r in rows if r.get(field) is not None]
    if len(daily) < 252:
        return None

    z_vals: dict[str, float] = {}
    windows_used: list[int] = []
    for window in _WINDOWS:
        z = _window_z(daily, window)
        if z is None:
            return None
        z_vals[_WINDOW_KEYS[window]] = z
        windows_used.append(window)

    tsmom = sum(_WINDOW_WEIGHTS[w] * z_vals[_WINDOW_KEYS[w]] for w in _WINDOWS)
    return {
        "z_3m": z_vals["z_3m"],
        "z_6m": z_vals["z_6m"],
        "z_12m": z_vals["z_12m"],
        "tsmom_score": tsmom,
        "windows_used": windows_used,
    }


def _index_return(index_rows: list[dict], window: int) -> Optional[float]:
    if len(index_rows) < window:
        return None
    window_rows = index_rows[-window:]
    start_idx = float(window_rows[0]["index"])
    end_idx = float(window_rows[-1]["index"])
    if start_idx <= 0:
        return None
    return (end_idx / start_idx) - 1.0


def _centered_rank(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    if len(values) == 1:
        return {next(iter(values)): 0.0}
    ranked = sorted(values.items(), key=lambda kv: kv[1])
    n = len(ranked)
    out: dict[str, float] = {}
    for rank, (ccy, _) in enumerate(ranked, start=1):
        out[ccy] = ((rank - 1) / (n - 1)) * 2.0 - 1.0
    return out


def xs_momentum_scores(
    currency_indexes: dict[str, list[dict]],
    as_of_date: str,
) -> dict[str, float]:
    """Cross-sectional momentum ranks for G8 currencies only."""
    raw_returns: dict[str, float] = {}
    for ccy in G8_CURRENCIES:
        rows = currency_indexes.get(ccy)
        if not rows:
            continue
        filtered = sorted(
            [r for r in rows if str(r.get("date", "")) <= as_of_date],
            key=lambda r: str(r["date"]),
        )
        window_returns: list[float] = []
        for window in _WINDOWS:
            ret = _index_return(filtered, window)
            if ret is None:
                break
            window_returns.append(ret)
        if len(window_returns) == len(_WINDOWS):
            raw_returns[ccy] = sum(window_returns) / len(window_returns)
    return _centered_rank(raw_returns)


def momentum_family_score(
    symbol: str,
    total_return_rows: list[dict],
    currency_indexes: dict[str, list[dict]],
    as_of_date: str,
) -> FamilyScore:
    """Blend TSMOM (pair total return) with cross-sectional currency momentum."""
    sym = symbol.upper()
    base, quote = pair_legs(sym)
    diagnostics: list[Diagnostic] = []

    tsmom = tsmom_score(total_return_rows, as_of_date, side="long")
    if tsmom is None:
        return FamilyScore(
            family=FAMILY_MOMENTUM,
            symbol=sym,
            as_of_date=as_of_date,
            score=None,
            direction="neutral",
            status="invalid",
            diagnostics=[
                Diagnostic(
                    DIAG_MISSING_OHLC,
                    "error",
                    "insufficient total-return history for TSMOM",
                    {"symbol": sym, "required_rows": 252},
                )
            ],
        )

    xs_ranks = xs_momentum_scores(currency_indexes, as_of_date)
    base_xs = xs_ranks.get(base)
    quote_xs = xs_ranks.get(quote)
    xs_pair: Optional[float] = None
    if base_xs is not None and quote_xs is not None:
        xs_pair = (base_xs - quote_xs) / 2.0

    w_ts = float(CONFIG.get("FX_FACTOR_MOMENTUM_TSMOM_WEIGHT", 0.6) or 0.6)
    w_xs = float(CONFIG.get("FX_FACTOR_MOMENTUM_XS_WEIGHT", 0.4) or 0.4)
    tsmom_norm = math.tanh(tsmom["tsmom_score"])

    if xs_pair is None:
        combined = tsmom_norm
    else:
        combined = w_ts * tsmom_norm + w_xs * xs_pair

    band = float(CONFIG.get("FX_FACTOR_MOMENTUM_NEUTRAL_BAND", 0.1) or 0.1)
    if combined > band:
        direction = "bullish"
    elif combined < -band:
        direction = "bearish"
    else:
        direction = "neutral"

    return FamilyScore(
        family=FAMILY_MOMENTUM,
        symbol=sym,
        as_of_date=as_of_date,
        score=combined,
        direction=direction,
        subcomponents={
            "tsmom_score": tsmom["tsmom_score"],
            "z_3m": tsmom["z_3m"],
            "z_6m": tsmom["z_6m"],
            "z_12m": tsmom["z_12m"],
            "xs_momentum_score": xs_pair,
            "tsmom_weight": w_ts,
            "xs_weight": w_xs,
            "tsmom_norm": tsmom_norm,
        },
        status="ok",
        diagnostics=diagnostics,
    )


def _percentile_value(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return d0 + d1


def export_family_distribution(
    family_scores: list[dict],
    *,
    percentiles: tuple[int, ...] = (10, 20, 30, 40, 50, 60, 70, 80, 90, 95),
) -> dict[str, Any]:
    """Summarize family score distributions with percentile maps."""
    scores_with_vals = [
        s for s in family_scores if s.get("score") is not None
    ]
    dates = [str(s.get("as_of_date", "")) for s in family_scores if s.get("as_of_date")]
    date_range = [min(dates), max(dates)] if dates else [None, None]

    by_family: dict[str, dict[str, dict[int, float]]] = {}
    for score_dict in scores_with_vals:
        family = str(score_dict.get("family", "unknown"))
        val = float(score_dict["score"])
        bucket = "long" if val > 0 else "short"
        by_family.setdefault(family, {}).setdefault(bucket, []).append(val)

    by_family_pct: dict[str, dict[str, dict[int, float]]] = {}
    for family, buckets in by_family.items():
        by_family_pct[family] = {}
        for bucket, vals in buckets.items():
            sorted_vals = sorted(vals)
            by_family_pct[family][bucket] = {
                p: _percentile_value(sorted_vals, p) for p in percentiles
            }

    by_symbol: dict[str, dict[int, float]] = {}
    symbol_groups: dict[str, list[float]] = {}
    for score_dict in scores_with_vals:
        sym = str(score_dict.get("symbol", ""))
        symbol_groups.setdefault(sym, []).append(float(score_dict["score"]))
    for sym, vals in symbol_groups.items():
        sorted_vals = sorted(vals)
        by_symbol[sym] = {p: _percentile_value(sorted_vals, p) for p in percentiles}

    return {
        "by_family": by_family_pct,
        "by_symbol": by_symbol,
        "count": len(family_scores),
        "date_range": date_range,
    }
