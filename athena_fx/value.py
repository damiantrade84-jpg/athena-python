"""Phase 6 — REER-based value family for athena_fx."""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

from config import CONFIG

from athena_fx.models import (
    DIAG_MISSING_REER,
    FAMILY_VALUE,
    Diagnostic,
    FamilyScore,
    pair_legs,
)
from athena_fx import store as fx_store

log = logging.getLogger("athena_fx.value")


def _conservative_available_date(observation_month: str) -> str:
    """First day of observation month + 2 months (minimum one full month lag)."""
    year, month = observation_month.split("-")[:2]
    first = _dt.date(int(year), int(month), 1)
    # Add 2 months
    m = first.month + 2
    y = first.year + (m - 1) // 12
    m = ((m - 1) % 12) + 1
    return _dt.date(y, m, 1).isoformat()


def ingest_reer_rows(rows: list[dict], *, db_path=None) -> int:
    """Persist REER observations with point-in-time availability dates."""
    to_save: list[dict[str, Any]] = []
    for row in rows:
        obs_month = str(row["observation_month"])
        pub = row.get("publication_date")
        if pub:
            available_date = str(pub)[:10]
            lag_method = "published"
        else:
            available_date = _conservative_available_date(obs_month)
            lag_method = "conservative"
        to_save.append({
            "currency": str(row["currency"]).upper(),
            "observation_month": obs_month,
            "reer_value": float(row["reer_value"]),
            "available_date": available_date,
            "lag_method": lag_method,
            "source": str(row.get("source", "unknown")),
        })
    return fx_store.save_reer_observations(to_save, db_path=db_path)


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _reer_zscore(history: list[dict[str, Any]], window_months: int = 60) -> Optional[float]:
    """5-year z-score of the latest available REER value vs trailing window."""
    values = [
        _safe_float(row.get("reer_value"))
        for row in sorted(history, key=lambda r: str(r.get("observation_month")))
    ]
    values = [v for v in values if v is not None]
    if len(values) < 24:
        return None
    window = values[-window_months:]
    current = window[-1]
    trailing = window[:-1]
    mean = sum(trailing) / len(trailing)
    var = sum((v - mean) ** 2 for v in trailing) / len(trailing)
    std = var ** 0.5
    if std <= 0:
        return None
    return (current - mean) / std


def reer_zscore(currency: str, as_of_date: str, *, db_path=None) -> dict | None:
    """Point-in-time REER z-score for one currency."""
    ccy = currency.upper()
    history = fx_store.load_reer_observations(
        currency=ccy,
        available_on_or_before=as_of_date,
        db_path=db_path,
    )
    z = _reer_zscore(history)
    if z is None:
        return None

    latest = sorted(history, key=lambda r: str(r.get("observation_month")))[-1]
    band = float(CONFIG.get("FX_FACTOR_VALUE_Z_BAND", 1.0) or 1.0)
    if z < -band:
        status_label = "undervalued"
    elif z > band:
        status_label = "overvalued"
    else:
        status_label = "neutral"

    return {
        "currency": ccy,
        "z": z,
        "status_label": status_label,
        "observation_month": latest.get("observation_month"),
        "available_date": latest.get("available_date"),
        "lag_method": latest.get("lag_method"),
    }


def value_family_score(symbol: str, as_of_date: str, *, db_path=None) -> FamilyScore:
    """Pair value score from base/quote REER z-scores."""
    sym = symbol.upper()
    base, quote = pair_legs(sym)

    base_info = reer_zscore(base, as_of_date, db_path=db_path)
    quote_info = reer_zscore(quote, as_of_date, db_path=db_path)

    if base_info is None or quote_info is None:
        missing = []
        if base_info is None:
            missing.append(base)
        if quote_info is None:
            missing.append(quote)
        return FamilyScore(
            family=FAMILY_VALUE,
            symbol=sym,
            as_of_date=as_of_date,
            score=None,
            direction="neutral",
            status="invalid",
            diagnostics=[
                Diagnostic(
                    DIAG_MISSING_REER,
                    "error",
                    f"insufficient REER history for {', '.join(missing)}",
                    {"symbol": sym, "missing": missing},
                )
            ],
        )

    base_z = float(base_info["z"])
    quote_z = float(quote_info["z"])
    pair_score = (quote_z - base_z) / 2.0

    band = float(CONFIG.get("FX_FACTOR_VALUE_NEUTRAL_BAND", 0.25) or 0.25)
    if pair_score > band:
        direction = "bullish"
    elif pair_score < -band:
        direction = "bearish"
    else:
        direction = "neutral"

    return FamilyScore(
        family=FAMILY_VALUE,
        symbol=sym,
        as_of_date=as_of_date,
        score=pair_score,
        direction=direction,
        subcomponents={
            "base_z": base_z,
            "quote_z": quote_z,
            "base_status": base_info["status_label"],
            "quote_status": quote_info["status_label"],
            "base_lag_method": base_info["lag_method"],
            "quote_lag_method": quote_info["lag_method"],
            "base_observation_month": base_info["observation_month"],
            "quote_observation_month": quote_info["observation_month"],
            "rebalance_note": "monthly",
        },
        status="ok",
        diagnostics=[],
    )
