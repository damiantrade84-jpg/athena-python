"""ASE journal rows adapted for the shared performance/trade-history API."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Iterable

log = logging.getLogger("sentinel.performance.ase")


def _cell(value: Any) -> Any:
    item = getattr(value, "item", None)
    if callable(item):
        try:
            value = item()
        except (TypeError, ValueError):
            pass
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
    except TypeError:
        pass
    return value


def _float(value: Any) -> float | None:
    value = _cell(value)
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def _int(value: Any) -> int | None:
    value = _cell(value)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _iso_from_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def ase_journal_records_for_performance(
    records: Iterable[dict[str, Any]],
    *,
    realized_only: bool = True,
) -> list[dict[str, Any]]:
    """Return reconciled ASE fills in the shape used by /api/performance.

    By default, only filled rows with realizedNetR are included so headline
    metrics compare realized outcomes. With realized_only=False, unreconciled
    fills are also returned for the history table with null PnL/R fields.
    """
    out: list[dict[str, Any]] = []
    for row in records:
        status = str(_cell(row.get("executionStatus")) or "").strip().lower()
        if status != "filled":
            continue
        realized_r = _float(row.get("realizedNetR"))
        if realized_r is None and realized_only:
            continue

        entry = _float(row.get("entryReference"))
        sl = _float(row.get("sl"))
        tp1 = _float(row.get("tp1"))
        direction = str(_cell(row.get("direction")) or "").strip().upper()
        decision_ms = _int(row.get("decisionTimeMs"))
        risk = abs(entry - sl) if entry is not None and sl is not None else None
        exit_price = None
        if realized_r is not None and entry is not None and risk is not None:
            sign = -1 if direction == "SHORT" else 1
            exit_price = entry + sign * realized_r * risk

        out.append(
            {
                "id": decision_ms or 0,
                "ts": _cell(row.get("recordedAt")) or _iso_from_ms(decision_ms),
                "pair": _cell(row.get("instrument")),
                "direction": direction or None,
                "entry_price": entry,
                "exit_price": exit_price,
                "sl": sl,
                "tp": tp1,
                "pnl": realized_r,
                "r_multiple": realized_r,
                "exit_reason": "ASE_RECONCILED" if realized_r is not None else "ASE_FILLED_UNRECONCILED",
                "exit_time": _cell(row.get("reconciledAt")) or _cell(row.get("recordedAt")) or _iso_from_ms(decision_ms),
                "engine": "ase",
                "engine_resolved": "ase",
                "style": _cell(row.get("horizon")),
                "grade": "ASE",
                "score": _float(row.get("expectedNetR")),
                "ticket": _cell(row.get("orderId")),
                "asset_class": _cell(row.get("modelFamily")),
                "ase_model_family": _cell(row.get("modelFamily")),
            }
        )
    return out


def load_ase_journal_performance_rows(*, realized_only: bool = True) -> list[dict[str, Any]]:
    """Load reconciled ASE fills for shared performance history."""
    try:
        from pandas.io.parquet import get_engine

        get_engine("auto")
        from athena_ase.execution.journal import load_trade_journal

        df = load_trade_journal()
        if df is None or df.empty:
            return []
        return ase_journal_records_for_performance(
            df.to_dict(orient="records"),
            realized_only=realized_only,
        )
    except Exception as exc:
        log.warning("ASE performance journal load failed: %s", exc)
        return []
