"""Resolve past signals into triple-barrier outcomes.

This closes the learning loop. Without it, the calibration layer has no labels
and every probability the engine reports is the prior forever.

A signal is only resolved once enough bars have printed to give the trade a
fair chance to reach a barrier. Resolving early would label a still-open trade
as a time-barrier loss and bias the whole model toward pessimism.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

import opus.config as config
from opus.data.feed import get_feed
from opus.indicators.base import TF_SECONDS
from opus.scoring.calibration import triple_barrier
from opus.types import Direction


@dataclass
class ResolveReport:
    examined: int = 0
    resolved: int = 0
    pending: int = 0
    skipped: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    def as_dict(self) -> dict:
        return {
            "examined": self.examined, "resolved": self.resolved,
            "pending": self.pending, "skipped": self.skipped,
            "errors": self.errors[:20],
        }


def resolve_pending(store, *, limit: int = 200, now: float | None = None) -> ResolveReport:
    """Label every stored signal whose horizon has fully elapsed."""
    now = float(now if now is not None else time.time())
    cfg = config.load()["CALIBRATION"]
    max_bars = int(cfg["time_barrier_bars"])
    report = ResolveReport()

    with store._cursor() as conn:  # noqa: SLF001 - same package, deliberate
        rows = conn.execute(
            """
            SELECT s.signal_id, s.symbol, s.asset_class, s.direction,
                   s.entry, s.stop, s.target, s.bar_ts, s.venue, s.decision
            FROM signals s
            LEFT JOIN outcomes o ON o.signal_id = s.signal_id
            WHERE o.signal_id IS NULL
            ORDER BY s.created_ts ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    for row in rows:
        report.examined += 1

        entry = row["entry"]
        stop = row["stop"]
        target = row["target"]
        if entry is None or stop is None or target is None:
            report.skipped += 1
            continue

        tfs = config.timeframes_for(str(row["asset_class"]))
        trigger_tf = str(tfs["trigger"]).upper()
        span = TF_SECONDS.get(trigger_tf, 300)

        bar_ts = float(row["bar_ts"] or 0.0)
        if bar_ts <= 0:
            report.skipped += 1
            continue

        # Only resolve once the FULL time barrier has elapsed. Resolving a
        # trade that is still live would record a time-barrier exit that never
        # happened and drag the calibration toward pessimism.
        if now < bar_ts + span * (max_bars + 2):
            report.pending += 1
            continue

        feed = get_feed(str(row["venue"] or ""))
        if feed is None:
            report.skipped += 1
            continue

        try:
            candles = feed.candles(str(row["symbol"]), trigger_tf, max_bars * 3)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"{row['symbol']}: {exc}")
            continue

        after = np.flatnonzero(candles.ts > bar_ts)
        if after.size == 0:
            report.pending += 1
            continue
        start = int(after[0])

        outcome = triple_barrier(
            high=candles.high[start:], low=candles.low[start:],
            close=candles.close[start:],
            entry=float(entry), stop=float(stop), target=float(target),
            direction=Direction(str(row["direction"])),
            max_bars=max_bars,
        )
        if outcome is None:
            report.pending += 1
            continue

        store.record_outcome(str(row["signal_id"]), outcome)
        report.resolved += 1

    return report


__all__ = ["ResolveReport", "resolve_pending"]
