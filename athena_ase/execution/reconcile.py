"""Reconcile ASE trade journal realized R from PTIS bars."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from athena_ase.execution.journal import load_trade_journal, trade_journal_path

log = logging.getLogger("ase.execution.reconcile")


def _simulate_fill_outcome(
    *,
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    decision_time_ms: int,
    max_hold_bars: int,
    horizon: str,
    series: Any,
    now_ms: int,
) -> dict[str, Any] | None:
    """Reconstruct a filled bracket's outcome from confirmed PTIS bars.

    Conservative adverse-first tie-break inside a bar (matches the labeler).
    Returns None while the trade is still live (no barrier hit and the
    vertical window has not fully elapsed in bar data yet).
    """
    d = 1 if str(direction).upper() == "LONG" else -1
    risk_abs = abs(entry - sl)
    if not (risk_abs > 0) or not math.isfinite(risk_abs):
        return None
    bar_ms = 3_600_000 if horizon == "intraday" else 86_400_000

    bars_held = 0
    for j in range(len(series.value_time)):
        t = int(series.value_time[j])
        if t <= decision_time_ms:
            continue
        hi = math.exp(float(series.high_log[j]))
        lo = math.exp(float(series.low_log[j]))
        close = math.exp(float(series.close_log[j]))
        bars_held += 1
        hit_sl = lo <= sl if d > 0 else hi >= sl
        hit_tp = hi >= tp1 if d > 0 else lo <= tp1
        if hit_sl:  # adverse-first when both hit in one bar
            realized = (sl - entry) * d / risk_abs
            return {"realizedNetR": realized, "realizedWin": realized > 0, "exit": "stop", "holdBars": bars_held}
        if hit_tp:
            realized = (tp1 - entry) * d / risk_abs
            return {"realizedNetR": realized, "realizedWin": realized > 0, "exit": "target", "holdBars": bars_held}
        if bars_held >= max_hold_bars:
            realized = (close - entry) * d / risk_abs
            return {"realizedNetR": realized, "realizedWin": realized > 0, "exit": "vertical", "holdBars": bars_held}
    # No barrier hit yet; only give up as unresolvable if the vertical window
    # elapsed long ago and bars still do not cover it (dead feed).
    if now_ms - decision_time_ms > (max_hold_bars + 10) * bar_ms * 2:
        return {"realizedNetR": None, "realizedWin": None, "exit": "unresolved", "holdBars": bars_held}
    return None


def reconcile_filled_from_ptis(
    *,
    path: Path | None = None,
    store: Any | None = None,
    now_ms: int | None = None,
) -> int:
    """Fill realizedNetR/realizedWin for filled journal rows from PTIS bars.

    Approximation: entry at entryReference, exits at journal SL/TP1 or the
    close of the maxHoldBars-th bar; spreads/fees/slippage are not modelled.
    R is measured against the executed risk distance |entry - sl|.
    """
    out_path = path or trade_journal_path()
    if not out_path.exists():
        return 0
    df = load_trade_journal(out_path)
    if df.empty or "executionStatus" not in df.columns:
        return 0

    from athena_ase.data.ptis import PTISStore, default_ptis_root
    from athena_ase.horizon import HORIZONS
    from athena_ase.signals.common import load_bar_series

    ptis = store or PTISStore(default_ptis_root())
    now = now_ms or int(time.time() * 1000)

    pending = df[(df["executionStatus"] == "filled") & (df["reconciledAt"].isna())]
    if pending.empty:
        return 0

    series_cache: dict[tuple[str, str], Any] = {}
    updated = 0
    for idx, row in pending.iterrows():
        instrument = str(row.get("instrument") or "")
        horizon = str(row.get("horizon") or "intraday")
        if horizon not in HORIZONS:
            horizon = "intraday"
        decision_ms = int(row.get("decisionTimeMs") or 0)
        entry = float(row.get("entryReference") or 0.0)
        sl = float(row.get("sl") or 0.0)
        tp1 = float(row.get("tp1") or 0.0)
        max_hold = int(row.get("maxHoldBars") or 0)
        if decision_ms <= 0 or entry <= 0 or max_hold <= 0:
            continue

        key = (instrument, horizon)
        if key not in series_cache:
            bar_ms = 3_600_000 if horizon == "intraday" else 86_400_000
            start = decision_ms - 5 * bar_ms
            series_cache[key] = load_bar_series(ptis, instrument, horizon, start, now)  # type: ignore[arg-type]
        series = series_cache[key]
        if series is None or len(series.value_time) == 0:
            continue

        outcome = _simulate_fill_outcome(
            direction=str(row.get("direction") or ""),
            entry=entry,
            sl=sl,
            tp1=tp1,
            decision_time_ms=decision_ms,
            max_hold_bars=max_hold,
            horizon=horizon,
            series=series,
            now_ms=now,
        )
        if outcome is None:
            continue
        df.at[idx, "realizedNetR"] = outcome["realizedNetR"]
        df.at[idx, "realizedWin"] = outcome["realizedWin"]
        df.at[idx, "reconciledAt"] = datetime.now(timezone.utc).isoformat()
        updated += 1

    if updated:
        from athena_ase.data.parquet_io import write_parquet_atomic

        write_parquet_atomic(out_path, df)
        log.info("ASE reconciliation updated %d filled rows", updated)
    return updated


def reconcile_trade_outcomes(
    *,
    path: Path | None = None,
    outcome_lookup: dict[tuple[str, int], dict[str, Any]] | None = None,
) -> int:
    """Fill realized outcome columns when lookup provided.

    Keys: (instrument, decisionTimeMs) → {realizedNetR, realizedWin}.
    """
    out_path = path or trade_journal_path()
    if not out_path.exists() or not outcome_lookup:
        return 0
    df = load_trade_journal(out_path)
    if df.empty:
        return 0
    updated = 0
    for idx, row in df.iterrows():
        if row.get("reconciledAt") is not None:
            continue
        key = (str(row.get("instrument") or ""), int(row.get("decisionTimeMs") or 0))
        hit = outcome_lookup.get(key)
        if not hit:
            continue
        df.at[idx, "realizedNetR"] = hit.get("realizedNetR")
        df.at[idx, "realizedWin"] = hit.get("realizedWin")
        df.at[idx, "reconciledAt"] = datetime.now(timezone.utc).isoformat()
        updated += 1
    if updated:
        df.to_parquet(out_path, index=False)
    return updated
