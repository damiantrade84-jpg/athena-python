"""Phase 3 — swap-adjusted total return series for the athena_fx factor lane.

Combines spot returns with constant broker swap accrual from the latest audit row.
Historical swap series are unavailable from MT5; a single audit row is applied
across the bar history (documented limitation — no lookahead on spot returns).
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

from athena_fx.models import (
    DIAG_MISSING_SWAP,
    Diagnostic,
    StrictFactorDataError,
    TotalReturnRow,
    G8_CURRENCIES,
    G8_PAIRS,
    pair_legs,
)
from athena_fx import store as fx_store

log = logging.getLogger("athena_fx.total_return")


def _parse_date(time_val: Any) -> str:
    """Normalize bar time to YYYY-MM-DD."""
    s = str(time_val)
    if "T" in s:
        s = s.split("T", 1)[0]
    return s[:10]


def _parse_date_obj(time_val: Any) -> _dt.date:
    return _dt.date.fromisoformat(_parse_date(time_val))


def _is_triple_swap_day(d: _dt.date, swap_rollover3days: int) -> bool:
    """Map MT5 weekday index (1=Mon .. 5=Fri) to Python weekday() and test match."""
    if swap_rollover3days < 1 or swap_rollover3days > 5:
        return False
    return d.weekday() == swap_rollover3days - 1


def _swap_multiplier(d: _dt.date, swap_rollover3days: int) -> float:
    return 3.0 if _is_triple_swap_day(d, swap_rollover3days) else 1.0


def build_total_return_series(
    daily_bars: list[dict],
    swap_audit_row: dict | None,
    *,
    symbol: str,
    strict: bool = True,
) -> list[TotalReturnRow]:
    """Build swap-adjusted total return rows from ascending daily OHLC dicts."""
    sym = symbol.upper()
    diagnostics: list[Diagnostic] = []

    audit_ok = (
        swap_audit_row is not None
        and swap_audit_row.get("status") == "ok"
        and swap_audit_row.get("generated_at")
    )
    daily_long_rate: Optional[float] = None
    daily_short_rate: Optional[float] = None
    rollover3 = 3

    if audit_ok:
        daily_long_rate = swap_audit_row.get("daily_swap_return_long")  # type: ignore[union-attr]
        daily_short_rate = swap_audit_row.get("daily_swap_return_short")  # type: ignore[union-attr]
        rollover3 = int(swap_audit_row.get("swap_rollover3days", 3) or 3)  # type: ignore[union-attr]
        if daily_long_rate is None or daily_short_rate is None:
            audit_ok = False
    else:
        diagnostics.append(Diagnostic(
            DIAG_MISSING_SWAP, "warning",
            "swap audit row missing or invalid for total return",
            {"symbol": sym},
        ))
        if strict:
            raise StrictFactorDataError(diagnostics)

    quality = "ok" if audit_ok else "degraded"
    bars = sorted(daily_bars, key=lambda b: _parse_date(b.get("time", "")))

    rows: list[TotalReturnRow] = []
    long_index = 100.0
    short_index = 100.0
    prev_close: Optional[float] = None

    for bar in bars:
        close = float(bar["close"])
        if prev_close is None:
            prev_close = close
            continue

        bar_date = _parse_date_obj(bar["time"])
        spot_return = close / prev_close - 1.0

        if audit_ok:
            mult = _swap_multiplier(bar_date, rollover3)
            long_swap = float(daily_long_rate) * mult  # type: ignore[arg-type]
            short_swap = float(daily_short_rate) * mult  # type: ignore[arg-type]
        else:
            long_swap = 0.0
            short_swap = 0.0

        long_tr = spot_return + long_swap
        short_tr = -spot_return + short_swap
        long_index *= 1.0 + long_tr
        short_index *= 1.0 + short_tr

        rows.append(TotalReturnRow(
            date=_parse_date(bar["time"]),
            symbol=sym,
            spot_return=spot_return,
            long_swap_return=long_swap,
            short_swap_return=short_swap,
            long_total_return=long_tr,
            short_total_return=short_tr,
            long_total_return_index=long_index,
            short_total_return_index=short_index,
            data_quality_status=quality,
        ))
        prev_close = close

    return rows


def persist_total_return(
    rows: list[TotalReturnRow],
    db_path: Optional[str] = None,
) -> int:
    """Persist total return rows via the fx store."""
    return fx_store.save_total_return_rows(rows, db_path=db_path)


def currency_total_return_indexes(
    pair_rows_by_symbol: dict[str, list[TotalReturnRow]],
) -> dict[str, list[dict[str, Any]]]:
    """Build per-currency long total-return index vs USD from available USD pairs."""
    usd_pairs = {p: p for p in G8_PAIRS if "USD" in p}
    indexes: dict[str, list[dict[str, Any]]] = {}

    for ccy in G8_CURRENCIES:
        if ccy == "USD":
            continue

        pair: Optional[str] = None
        use_long = True
        for p in usd_pairs:
            base, quote = pair_legs(p)
            if base == ccy and quote == "USD":
                pair = p
                use_long = True
                break
            if quote == ccy and base == "USD":
                pair = p
                use_long = False
                break

        if pair is None or pair not in pair_rows_by_symbol:
            continue

        attr = "long_total_return_index" if use_long else "short_total_return_index"
        indexes[ccy] = [
            {"date": row.date, "index": getattr(row, attr)}
            for row in pair_rows_by_symbol[pair]
        ]

    return indexes
