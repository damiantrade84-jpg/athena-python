"""Phase 5 — broker-swap carry family for athena_fx."""
from __future__ import annotations

import logging
from typing import Optional

from config import CONFIG

from athena_fx.models import (
    DIAG_MISSING_FRED,
    DIAG_MISSING_SWAP,
    FAMILY_CARRY,
    Diagnostic,
    FamilyScore,
)

log = logging.getLogger("athena_fx.carry")


def carry_family_score(
    symbol: str,
    as_of_date: str,
    *,
    swap_audit_row: dict | None,
    theoretical_carry: float | None,
    db_path=None,
) -> FamilyScore:
    """Score tradable carry from broker swap audit; theoretical carry is advisory only."""
    sym = symbol.upper()
    diagnostics: list[Diagnostic] = []

    if swap_audit_row is None or swap_audit_row.get("status") != "ok":
        return FamilyScore(
            family=FAMILY_CARRY,
            symbol=sym,
            as_of_date=as_of_date,
            score=None,
            direction="neutral",
            status="invalid",
            diagnostics=[
                Diagnostic(
                    DIAG_MISSING_SWAP,
                    "error",
                    "carry blocked: no valid broker swap audit",
                    {"symbol": sym},
                )
            ],
        )

    net_long = swap_audit_row.get("net_carry_long_annualized")
    net_short = swap_audit_row.get("net_carry_short_annualized")
    carry_long_eligible = bool(swap_audit_row.get("carry_long_eligible"))
    carry_short_eligible = bool(swap_audit_row.get("carry_short_eligible"))
    markup_warning = bool(swap_audit_row.get("markup_warning"))

    carry_long_score = float(net_long) if net_long is not None else None
    carry_short_score = float(net_short) if net_short is not None else None

    threshold = float(CONFIG.get("FX_FACTOR_CARRY_MIN_NET_ANNUAL", 0.005) or 0.005)
    direction = "neutral"
    score: Optional[float] = 0.0

    long_ok = carry_long_eligible and net_long is not None and float(net_long) >= threshold
    short_ok = (
        carry_short_eligible and net_short is not None and float(net_short) >= threshold
    )

    if long_ok and short_ok:
        if float(net_long) >= float(net_short):
            direction = "bullish"
            score = float(net_long)
        else:
            direction = "bearish"
            score = -float(net_short)
    elif long_ok:
        direction = "bullish"
        score = float(net_long)
    elif short_ok:
        direction = "bearish"
        score = -float(net_short)

    if markup_warning:
        diagnostics.append(Diagnostic(
            "broker_markup_high",
            "warning",
            "broker markup exceeds warning threshold",
            {"symbol": sym},
        ))
        if CONFIG.get("FX_FACTOR_CARRY_BLOCK_ON_MARKUP", False):
            direction = "neutral"
            score = 0.0

    if theoretical_carry is None:
        diagnostics.append(Diagnostic(
            DIAG_MISSING_FRED,
            "warning",
            "theoretical carry differential unavailable",
            {"symbol": sym},
        ))

    broker_markup_long = swap_audit_row.get("broker_markup_long")
    broker_markup_short = swap_audit_row.get("broker_markup_short")
    markup_estimate = broker_markup_long if direction == "bullish" else broker_markup_short
    if markup_estimate is None and broker_markup_long is not None:
        markup_estimate = broker_markup_long
    elif markup_estimate is None:
        markup_estimate = broker_markup_short

    return FamilyScore(
        family=FAMILY_CARRY,
        symbol=sym,
        as_of_date=as_of_date,
        score=score,
        direction=direction,
        subcomponents={
            "carry_long_score": carry_long_score,
            "carry_short_score": carry_short_score,
            "net_broker_carry_long": net_long,
            "net_broker_carry_short": net_short,
            "theoretical_carry": theoretical_carry,
            "broker_markup_estimate": markup_estimate,
            "carry_eligible_long": carry_long_eligible,
            "carry_eligible_short": carry_short_eligible,
        },
        status="ok",
        diagnostics=diagnostics,
    )
