"""Phase 7 — COT crowding overlay (currency-leg, never pair-level trigger)."""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Optional

from config import CONFIG

from athena_fx.models import (
    DIAG_MISSING_COT,
    SCORE_CAP_COT_MODIFIER,
    CotOverlayResult,
    Diagnostic,
    pair_legs,
)

log = logging.getLogger("athena_fx.cot_overlay")

# G8 currencies with direct CFTC IMM assets in cot_feed._CONTRACT_FRAGMENTS.
_CURRENCY_TO_COT_ASSET: dict[str, str] = {
    "EUR": "EUR",
    "GBP": "GBP",
    "JPY": "JPY",
    "AUD": "AUD",
    "NZD": "NZD",
    "CAD": "CAD",
    "CHF": "CHF",
}


def _days_between(report_date: str, as_of_date: str) -> Optional[int]:
    try:
        rd = _dt.date.fromisoformat(str(report_date)[:10])
        ao = _dt.date.fromisoformat(str(as_of_date)[:10])
        return (ao - rd).days
    except (TypeError, ValueError):
        return None


def _percentile_rank(series: list[int], latest: int) -> float:
    if len(series) < 2:
        return 50.0
    count_le = sum(1 for x in series if x <= latest)
    return 100.0 * (count_le - 1) / (len(series) - 1)


def currency_cot_percentile(
    currency: str,
    as_of_date: str,
    *,
    lookback_years: int | None = None,
) -> dict | None:
    """Historical percentile of latest net speculator positioning for a currency."""
    ccy = currency.upper()
    asset = _CURRENCY_TO_COT_ASSET.get(ccy)
    if asset is None:
        # USD has no direct IMM asset; DXY proxy not implemented.
        return None

    if lookback_years is None:
        lookback_years = int(
            CONFIG.get("FX_FACTOR_COT_PERCENTILE_LOOKBACK_YEARS", 3) or 3
        )

    from cot_feed import _get_net_series, _latest_report_date

    weeks = lookback_years * 52
    series = _get_net_series(asset, weeks=weeks, as_of_date=as_of_date)
    if len(series) < 26:
        return None

    latest_net = series[-1]
    percentile = _percentile_rank(series, latest_net)
    report_date = _latest_report_date(asset)
    lag_days = _days_between(report_date, as_of_date) if report_date else None

    return {
        "currency": ccy,
        "percentile": percentile,
        "net": latest_net,
        "report_date": report_date,
        "n_weeks": len(series),
        "cot_lag_days": lag_days,
    }


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def cot_overlay(
    symbol: str,
    direction: str,
    as_of_date: str,
    *,
    db_path=None,
) -> CotOverlayResult:
    """Apply currency-leg COT crowding overlay to a proposed pair direction."""
    sym = symbol.upper()
    base, quote = pair_legs(sym)
    dir_upper = direction.upper()
    diagnostics: list[Diagnostic] = []

    base_info = currency_cot_percentile(base, as_of_date)
    quote_info = currency_cot_percentile(quote, as_of_date)

    if base_info is None or quote_info is None:
        missing = []
        if base_info is None:
            missing.append(base)
        if quote_info is None:
            missing.append(quote)
        diagnostics.append(Diagnostic(
            DIAG_MISSING_COT,
            "warning",
            f"COT percentile unavailable for {', '.join(missing)}",
            {"symbol": sym, "missing": missing},
        ))
        return CotOverlayResult(
            symbol=sym,
            as_of_date=as_of_date,
            base_currency_cot_percentile=base_info["percentile"] if base_info else None,
            quote_currency_cot_percentile=quote_info["percentile"] if quote_info else None,
            pair_crowding_status="unknown",
            action="none",
            modifier=0.0,
            cot_release_used=None,
            cot_lag_days=None,
            diagnostics=diagnostics,
        )

    base_pct = float(base_info["percentile"])
    quote_pct = float(quote_info["percentile"])
    hi = float(CONFIG.get("FX_FACTOR_COT_EXTREME_HIGH_PCT", 90) or 90)
    lo = float(CONFIG.get("FX_FACTOR_COT_EXTREME_LOW_PCT", 10) or 10)
    downweight = float(CONFIG.get("FX_FACTOR_COT_DOWNWEIGHT_POINTS", 5.0) or 5.0)

    pair_status = "none"
    action = "none"
    modifier = 0.0

    if dir_upper == "LONG":
        base_crowded = base_pct >= hi
        quote_crowded = quote_pct <= lo
    else:
        base_crowded = base_pct <= lo
        quote_crowded = quote_pct >= hi

    if base_crowded and quote_crowded:
        pair_status = "double_crowded"
        action = "veto"
        modifier = 0.0
    elif base_crowded or quote_crowded:
        pair_status = "one_leg"
        action = "downweight"
        modifier = -downweight
    else:
        pair_status = "none"
        action = "none"
        modifier = 0.0

    modifier = _clamp(modifier, -SCORE_CAP_COT_MODIFIER, SCORE_CAP_COT_MODIFIER)

    report_dates = [
        d for d in (base_info.get("report_date"), quote_info.get("report_date")) if d
    ]
    cot_release_used = min(report_dates) if report_dates else None

    lag_values = [
        v for v in (base_info.get("cot_lag_days"), quote_info.get("cot_lag_days"))
        if v is not None
    ]
    cot_lag_days = max(lag_values) if lag_values else None

    return CotOverlayResult(
        symbol=sym,
        as_of_date=as_of_date,
        base_currency_cot_percentile=base_pct,
        quote_currency_cot_percentile=quote_pct,
        pair_crowding_status=pair_status,
        action=action,
        modifier=modifier,
        cot_release_used=cot_release_used,
        cot_lag_days=cot_lag_days,
        diagnostics=diagnostics,
    )
