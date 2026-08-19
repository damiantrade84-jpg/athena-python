"""Closing out working orders.

Resting an order at the planned entry is only honest if something later says
what became of it. Three outcomes are possible and exactly one rule governs
all of them: an order is moved off `working` only on POSITIVE evidence.

    filled    the level actually traded (paper), or the broker says so (live)
    expired   the expiry passed AND the venue confirmed the cancel
    working   everything else, including every "I do not know"

The asymmetry is deliberate. Recording an order as expired while the broker
still has it live means the store shows no exposure that exists, which defeats
the position caps, the cooldown and the daily cap all at once. Leaving a dead
order marked working costs one slot until the next sweep - a cost worth paying.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

import opus.config as config
from opus.data.feed import get_feed
from opus.execution.broker import PaperBroker
from opus.execution.orders import STOP


@dataclass
class ReconcileReport:
    examined: int = 0
    filled: int = 0
    expired: int = 0
    working: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "examined": self.examined, "filled": self.filled,
            "expired": self.expired, "working": self.working,
            "skipped": self.skipped, "errors": self.errors[:20],
        }


def _broker_for_row(row: dict):
    """The live adapter that holds this order, or None."""
    from opus.execution.router import _broker_for  # local: avoids a cycle

    venue = str((row.get("detail") or {}).get("venue") or "")
    broker = _broker_for(venue, True)
    return broker if getattr(broker, "is_live", False) else None


def _expires_ts(row: dict) -> float | None:
    """When this order stops being valid.

    Falls back to submission plus the configured expiry when the row carries
    none - rows written before expiry was recorded, or by an adapter that did
    not report one. Without the fallback such an order can never leave
    `working`, and it holds a position slot and a correlation slot forever.
    """
    value = (row.get("detail") or {}).get("expiresTs")
    try:
        if value is not None:
            return float(value)
    except (TypeError, ValueError):
        pass

    submitted = float(row.get("submitted_ts") or 0.0)
    window = float(config.load()["EXECUTION"].get("limit_expiry_sec", 0.0) or 0.0)
    if submitted <= 0 or window <= 0:
        return None
    return submitted + window


def _paper_fill(row: dict, now: float) -> float | None:
    """The price this resting order filled at, or None if it has not.

    Only bars that printed AFTER the order was submitted can fill it; using
    the whole series would fill an order on history that predates it.
    """
    venue = str((row.get("detail") or {}).get("venue") or "")
    feed = get_feed(venue)
    if feed is None:
        return None

    entry = float(row.get("entry") or 0.0)
    if entry <= 0:
        return None

    asset_class = str((row.get("detail") or {}).get("assetClass") or "default")
    trigger_tf = str(config.timeframes_for(asset_class)["trigger"])
    candles = feed.candles(str(row["symbol"]), trigger_tf, 240)

    submitted = float(row.get("submitted_ts") or 0.0)
    after = np.flatnonzero((candles.ts > submitted) & (candles.ts <= now))
    if after.size == 0:
        return None

    highs = candles.high[after]
    lows = candles.low[after]
    if not bool(np.any((lows <= entry) & (highs >= entry))):
        return None

    direction_sign = 1 if str(row.get("direction", "")).upper() == "LONG" else -1
    kind = str(row.get("order_type") or "").lower()
    if kind != STOP:
        return entry
    return PaperBroker().fill_price(row, kind, direction_sign)


def reconcile(store, *, now: float | None = None, limit: int = 200) -> ReconcileReport:
    """Sweep every working order once."""
    now = float(now if now is not None else time.time())
    report = ReconcileReport()

    try:
        rows = [r for r in store.orders(limit=limit)
                if str(r.get("status", "")).lower() == "working"]
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"store unreadable: {exc}")
        return report

    for row in rows:
        report.examined += 1
        order_id = str(row.get("order_id") or "")
        is_live = str(row.get("mode", "")).lower() == "live"

        try:
            if is_live:
                filled_at = _live_fill(row)
            else:
                filled_at = _paper_fill(row, now)
        except Exception as exc:  # noqa: BLE001 - an unreadable venue is not
            report.errors.append(f"{order_id}: {exc}")   # evidence of anything
            report.working += 1
            continue

        if filled_at is not None:
            store.update_order(order_id, status="filled", entry=float(filled_at))
            report.filled += 1
            continue

        expires = _expires_ts(row)
        if expires is not None and now >= expires:
            if _cancel(row, report):
                store.update_order(order_id, status="expired")
                report.expired += 1
            else:
                report.working += 1
            continue

        report.working += 1

    return report


def _live_fill(row: dict) -> float | None:
    """Ask the venue. Anything other than an explicit fill means 'not yet'."""
    broker = _broker_for_row(row)
    if broker is None:
        return None
    state = broker.order_state(row) or {}
    if str(state.get("status", "")).lower() != "filled":
        return None
    entry = state.get("entry")
    try:
        return float(entry) if entry is not None else float(row.get("entry") or 0.0)
    except (TypeError, ValueError):
        return float(row.get("entry") or 0.0)


def _cancel(row: dict, report: ReconcileReport) -> bool:
    """Cancel at the venue. Paper cancels always succeed; live ones may not."""
    order_id = str(row.get("order_id") or "")
    if str(row.get("mode", "")).lower() != "live":
        return bool(PaperBroker().cancel(row).ok)

    broker = _broker_for_row(row)
    if broker is None:
        report.errors.append(
            f"{order_id}: expired but no live adapter is available to cancel it"
        )
        return False
    result = broker.cancel(row)
    if not result.ok:
        report.errors.append(f"{order_id}: cancel refused - {result.message}")
        return False
    return True


__all__ = ["ReconcileReport", "reconcile"]
