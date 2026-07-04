"""Phase 1 — shared factor-derivative resolution for live/scanner and backtest.

Both surfaces must call `resolve_factor_derivatives()` with an explicit
`as_of_date` so factor inputs are point-in-time and identical in semantics.
Missing data is never silent: every absent input yields a Diagnostic with a
canonical code (missing_carry / missing_cot / missing_fred / missing_reer /
missing_swap / stale_derivatives). In strict mode (the Phase 2+ default for
factor backtesting) missing required inputs raise StrictFactorDataError.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

from athena_fx.models import (
    DIAG_MISSING_CARRY,
    DIAG_MISSING_COT,
    DIAG_MISSING_FRED,
    DIAG_MISSING_REER,
    DIAG_MISSING_SWAP,
    DIAG_STALE_DERIVATIVES,
    Diagnostic,
    FactorDerivatives,
    StrictFactorDataError,
    pair_legs,
)
from athena_fx import store as fx_store

log = logging.getLogger("athena_fx.derivatives")

# Inputs required for a valid strict-mode factor decision. REER is required
# for the Value family; swap audit for tradable carry.
_REQUIRED_STRICT = ("carry", "cot", "fred", "reer", "swap")

_STALE_CARRY_MAX_AGE_DAYS = 45   # FRED short-rate series update monthly
_STALE_COT_MAX_AGE_DAYS = 28     # matches cot_feed._DEFAULT_MAX_REPORT_AGE_DAYS


def _display_from_symbol(symbol: str) -> str:
    base, quote = pair_legs(symbol)
    return f"{base}/{quote}"


def _safe_float(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f


def resolve_factor_derivatives(
    symbol: str,
    as_of_date: str,
    *,
    strict: bool = False,
    db_path: Optional[str] = None,
) -> FactorDerivatives:
    """Resolve carry/COT/REER/swap factor inputs point-in-time for one pair.

    `as_of_date` (YYYY-MM-DD) is the decision date; no data published after it
    is consumed. `strict=True` raises StrictFactorDataError when any required
    input is missing (Phase 2 factor backtesting default). `strict=False`
    returns a degraded bundle with explicit diagnostics.
    """
    diagnostics: list[Diagnostic] = []
    result = FactorDerivatives(symbol=symbol.upper(), as_of_date=as_of_date)
    display = _display_from_symbol(symbol)

    # ── Carry z (broker-agnostic FRED-based differential z-score) ──────────
    carry_z = None
    try:
        from carry_feed import get_carry_z

        carry_z = _safe_float(get_carry_z(display, as_of_date=as_of_date))
    except Exception as exc:
        diagnostics.append(Diagnostic(
            DIAG_MISSING_CARRY, "error", f"carry feed error: {exc}",
            {"symbol": symbol},
        ))
    if carry_z is None or carry_z == 0.0:
        # carry_feed returns 0.0 both for "neutral" and "missing"; treat the
        # exact-zero case as unresolved and surface it rather than silently
        # scoring neutral (this was the derivatives=None-style silence).
        diagnostics.append(Diagnostic(
            DIAG_MISSING_CARRY, "warning",
            "carry z unresolved (feed returned None/0.0)",
            {"symbol": symbol, "as_of_date": as_of_date},
        ))
        carry_z = None
    result.carry_z = carry_z

    # ── Theoretical carry differential (FRED short rates) ──────────────────
    differential = None
    try:
        from carry_feed import get_carry_differential

        differential = _safe_float(get_carry_differential(display))
    except Exception as exc:
        diagnostics.append(Diagnostic(
            DIAG_MISSING_FRED, "error", f"FRED differential error: {exc}",
            {"symbol": symbol},
        ))
    if differential is None:
        diagnostics.append(Diagnostic(
            DIAG_MISSING_FRED, "warning",
            "theoretical carry differential unavailable",
            {"symbol": symbol},
        ))
    result.carry_differential = differential

    # ── COT z (pair-level; leg percentiles resolved by cot_overlay) ────────
    cot_z = None
    try:
        from cot_feed import get_cot_z

        cot_z = _safe_float(get_cot_z(display, as_of_date=as_of_date))
    except Exception as exc:
        diagnostics.append(Diagnostic(
            DIAG_MISSING_COT, "error", f"COT feed error: {exc}",
            {"symbol": symbol},
        ))
    if cot_z is None or cot_z == 0.0:
        diagnostics.append(Diagnostic(
            DIAG_MISSING_COT, "warning",
            "COT z unresolved (feed returned None/0.0)",
            {"symbol": symbol, "as_of_date": as_of_date},
        ))
        cot_z = None
    result.cot_z = cot_z

    # ── REER (point-in-time via available_date) ─────────────────────────────
    base, quote = pair_legs(symbol)
    reer_rows = fx_store.load_reer_observations(
        available_on_or_before=as_of_date, db_path=db_path
    )
    by_ccy: dict[str, list[dict[str, Any]]] = {}
    for row in reer_rows:
        by_ccy.setdefault(str(row["currency"]).upper(), []).append(row)
    for leg, attr in ((base, "reer_base_z"), (quote, "reer_quote_z")):
        history = by_ccy.get(leg, [])
        z = _reer_zscore(history)
        if z is None:
            diagnostics.append(Diagnostic(
                DIAG_MISSING_REER, "warning",
                f"insufficient REER history for {leg}",
                {"currency": leg, "rows": len(history)},
            ))
        setattr(result, attr, z)

    # ── Swap audit validity ─────────────────────────────────────────────────
    audit_rows = fx_store.load_latest_swap_audit(symbol=symbol.upper(), db_path=db_path)
    valid_audit = bool(audit_rows) and audit_rows[0].get("status") == "ok"
    if not valid_audit:
        diagnostics.append(Diagnostic(
            DIAG_MISSING_SWAP, "warning",
            "no valid broker swap audit row",
            {"symbol": symbol},
        ))
    result.swap_audit_valid = valid_audit

    # ── Staleness ───────────────────────────────────────────────────────────
    if audit_rows:
        stale = _is_stale(audit_rows[0].get("generated_at"), as_of_date, _STALE_CARRY_MAX_AGE_DAYS)
        if stale:
            diagnostics.append(Diagnostic(
                DIAG_STALE_DERIVATIVES, "warning",
                "swap audit row is stale relative to decision date",
                {"symbol": symbol, "generated_at": audit_rows[0].get("generated_at")},
            ))

    result.diagnostics = diagnostics
    missing_codes = {d.code for d in diagnostics if d.severity in ("warning", "error")}
    if missing_codes:
        result.status = "degraded"
    if strict:
        required_missing = [
            d for d in diagnostics
            if d.code in (
                DIAG_MISSING_CARRY, DIAG_MISSING_COT, DIAG_MISSING_FRED,
                DIAG_MISSING_REER, DIAG_MISSING_SWAP,
            )
        ]
        if required_missing:
            result.status = "invalid"
            raise StrictFactorDataError(required_missing)
    return result


def _reer_zscore(history: list[dict[str, Any]], window_months: int = 60) -> Optional[float]:
    """5-year z-score of the latest available REER value vs trailing window."""
    values = [
        _safe_float(row.get("reer_value"))
        for row in sorted(history, key=lambda r: str(r.get("observation_month")))
    ]
    values = [v for v in values if v is not None]
    if len(values) < 24:  # need at least 2 years for a meaningful z
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


def _is_stale(generated_at: Any, as_of_date: str, max_age_days: int) -> bool:
    try:
        gen = _dt.datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        as_of = _dt.datetime.fromisoformat(f"{as_of_date}T00:00:00+00:00")
    except (TypeError, ValueError):
        return True
    return (as_of - gen).days > max_age_days
