from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import TradeRecord, ValidationPlan
from .statistics import block_bootstrap_ci, deflated_sharpe_ratio, probabilistic_sharpe_ratio, summarize_r


@dataclass(frozen=True)
class ValidationBoundary:
    name: str
    start: datetime
    end: datetime
    purge_start: datetime | None = None
    embargo_end: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "purgeStart": self.purge_start.isoformat() if self.purge_start else None,
            "embargoEnd": self.embargo_end.isoformat() if self.embargo_end else None,
        }


def anchored_boundaries(
    start: datetime,
    end: datetime,
    plan: ValidationPlan,
    *,
    execution_seconds: int,
) -> dict[str, Any]:
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    total = end - start
    holdout_start = start + total * (1.0 - plan.holdout_fraction)
    purge = timedelta(seconds=execution_seconds * plan.max_hold_bars)
    embargo = timedelta(seconds=execution_seconds)
    folds = []
    for index in range(plan.folds):
        oos_start = start + total * (plan.initial_train_fraction + index * plan.oos_fraction)
        oos_end = start + total * (plan.initial_train_fraction + (index + 1) * plan.oos_fraction)
        folds.append(
            ValidationBoundary(
                name=f"WF{index + 1}",
                start=oos_start,
                end=min(oos_end, holdout_start),
                purge_start=max(start, oos_start - purge),
                embargo_end=min(holdout_start, oos_end + embargo),
            )
        )
    return {
        "trainStart": start.isoformat(),
        "initialTrainEnd": (start + total * plan.initial_train_fraction).isoformat(),
        "holdoutStart": holdout_start.isoformat(),
        "holdoutEnd": end.isoformat(),
        "purgeSeconds": int(purge.total_seconds()),
        "embargoSeconds": int(embargo.total_seconds()),
        "folds": [boundary.to_dict() for boundary in folds],
    }


def apply_splits(trades: list[TradeRecord], boundaries: dict[str, Any]) -> None:
    holdout_start = datetime.fromisoformat(boundaries["holdoutStart"])
    fold_rows = boundaries["folds"]
    for trade in trades:
        timestamp = datetime.fromisoformat(trade.entry_time)
        if timestamp >= holdout_start:
            trade.split = "HOLDOUT"
            continue
        assigned = "IS"
        for row in fold_rows:
            start = datetime.fromisoformat(row["start"])
            end = datetime.fromisoformat(row["end"])
            purge_start = datetime.fromisoformat(row["purgeStart"])
            embargo_end = datetime.fromisoformat(row["embargoEnd"])
            if purge_start <= timestamp < start:
                assigned = f"{row['name']}_PURGED"
                break
            if start <= timestamp < end:
                assigned = f"{row['name']}_OOS"
                break
            if end <= timestamp < embargo_end:
                assigned = f"{row['name']}_EMBARGO"
                break
        trade.split = assigned


def validate_trades(
    trades: list[TradeRecord],
    boundaries: dict[str, Any],
    plan: ValidationPlan,
    *,
    seed: int,
    trial_count: int,
) -> dict[str, Any]:
    usable = [trade for trade in trades if not trade.split.endswith("_PURGED") and not trade.split.endswith("_EMBARGO")]
    groups: dict[str, list[TradeRecord]] = {
        "ALL": usable,
        "IS": [trade for trade in usable if trade.split == "IS"],
        "OOS": [trade for trade in usable if trade.split.endswith("_OOS")],
        "HOLDOUT": [trade for trade in usable if trade.split == "HOLDOUT"],
    }
    for index in range(1, 6):
        groups[f"WF{index}_OOS"] = [trade for trade in usable if trade.split == f"WF{index}_OOS"]
    metrics = {
        name: summarize_r([trade.net_r for trade in rows], [trade.cost_r for trade in rows])
        for name, rows in groups.items()
    }
    values = [trade.net_r for trade in usable]
    return {
        "boundaries": boundaries,
        "metrics": metrics,
        "confidenceIntervals": block_bootstrap_ci(
            values,
            samples=plan.bootstrap_samples,
            seed=seed,
        ),
        "psr": probabilistic_sharpe_ratio(values),
        "dsr": deflated_sharpe_ratio(values, trial_count),
        "trialCount": int(trial_count),
        "pbo": None,
        "pboReason": "requires_multiple_comparison_variants" if trial_count <= 1 else "computed_at_comparison_family_level",
    }
