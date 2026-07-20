"""Honest out-of-sample reporting for the rebuilt backtester.

Nothing here refits anything — the engines have no fitted parameters at
backtest time — so the rolling replay is labeled exactly what it is:
`rolling_oos_replay_no_refit`. The module provides:

- rolling window replay accounting with purge + embargo between windows,
- a temporal holdout split (last fraction of TIME, not of trades),
- a trial registry for multiple-testing disclosure (feeds deflatedSqn),
- verdict floors: n>=30 for any verdict, n>=60 for an SQN headline.

Pure functions over trade lists; deterministic.
"""

from __future__ import annotations

from datetime import datetime, timezone

from athena_backtest.metrics import summarize_trades

VALIDATION_TYPE = "rolling_oos_replay_no_refit"
VERDICT_MIN_TRADES = 30
SQN_HEADLINE_MIN_TRADES = 60


def _epoch(value) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def _trade_epoch(trade: dict) -> int | None:
    # Engine B trades stamp "entryTime"; Engine A trades stamp "date"
    # (the signal decision time). Either anchors the temporal ordering.
    return _epoch(trade.get("entryTime") or trade.get("date"))


def temporal_holdout_split(
    trades: list[dict],
    *,
    holdout_fraction: float = 0.3,
    purge_seconds: int = 0,
) -> dict:
    """Split trades by TIME: the last `holdout_fraction` of the covered time
    span is the holdout. Trades entering inside the purge gap before the
    boundary are dropped from BOTH sides (their exits could leak across).
    """
    holdout_fraction = min(0.9, max(0.0, float(holdout_fraction)))
    stamped = [(trade, _trade_epoch(trade)) for trade in trades]
    stamped = [(trade, epoch) for trade, epoch in stamped if epoch is not None]
    if not stamped or holdout_fraction <= 0:
        return {"inSample": [t for t, _ in stamped], "holdout": [], "boundaryEpoch": None}
    first = min(epoch for _, epoch in stamped)
    last = max(epoch for _, epoch in stamped)
    boundary = last - int((last - first) * holdout_fraction)
    in_sample = [
        trade for trade, epoch in stamped if epoch < boundary - purge_seconds
    ]
    holdout = [trade for trade, epoch in stamped if epoch >= boundary]
    return {"inSample": in_sample, "holdout": holdout, "boundaryEpoch": boundary}


def rolling_windows(
    trades: list[dict],
    *,
    n_windows: int = 4,
    purge_seconds: int = 0,
) -> list[dict]:
    """Partition the covered time span into `n_windows` equal windows and
    report per-window metrics. Trades entering within `purge_seconds` before a
    boundary are dropped from the earlier window (embargo against exit leak).
    """
    stamped = [(trade, _trade_epoch(trade)) for trade in trades]
    stamped = [(trade, epoch) for trade, epoch in stamped if epoch is not None]
    if not stamped or n_windows < 1:
        return []
    first = min(epoch for _, epoch in stamped)
    last = max(epoch for _, epoch in stamped)
    span = max(1, last - first)
    windows: list[dict] = []
    for w in range(n_windows):
        lo = first + (span * w) // n_windows
        hi = first + (span * (w + 1)) // n_windows
        is_last = w == n_windows - 1
        rows = [
            trade
            for trade, epoch in stamped
            if epoch >= lo
            and (epoch <= hi if is_last else epoch < hi - purge_seconds)
        ]
        results = [float(t["resultR"]) for t in rows]
        windows.append(
            {
                "window": w + 1,
                "fromEpoch": lo,
                "toEpoch": hi,
                "trades": len(rows),
                "totalR": round(sum(results), 4),
                "expectancyR": round(sum(results) / len(results), 4) if results else None,
            }
        )
    return windows


class TrialRegistry:
    """Counts distinct evaluation trials for multiple-testing disclosure.

    Every distinct (symbol, engine, style, parameter-variant) combination that
    was LOOKED AT counts as one trial; the count feeds deflatedSqn. This is a
    disclosure device, not an access-controlled ledger.
    """

    def __init__(self) -> None:
        self._trials: set[tuple] = set()

    def record(self, *, symbol: str, engine: str, style: str, variant: str = "default") -> None:
        self._trials.add((str(symbol).upper(), str(engine).upper(), str(style).lower(), str(variant)))

    @property
    def count(self) -> int:
        return max(1, len(self._trials))

    def to_dict(self) -> dict:
        return {"trialCount": self.count}


def validation_block(
    trades: list[dict],
    *,
    n_trials: int = 1,
    holdout_fraction: float = 0.3,
    purge_seconds: int = 0,
    n_windows: int = 4,
    bootstrap_seed: int = 1729,
) -> dict:
    """Full validation disclosure block for one backtest's trade list."""
    split = temporal_holdout_split(
        trades, holdout_fraction=holdout_fraction, purge_seconds=purge_seconds
    )
    n = len(trades)
    verdict: str
    if n < VERDICT_MIN_TRADES:
        verdict = "INSUFFICIENT_SAMPLE"
    else:
        holdout_results = [float(t["resultR"]) for t in split["holdout"]]
        holdout_positive = bool(holdout_results) and sum(holdout_results) > 0
        verdict = "HOLDOUT_POSITIVE" if holdout_positive else "HOLDOUT_NEGATIVE"
    return {
        "validationType": VALIDATION_TYPE,
        "trialCount": n_trials,
        "verdict": verdict,
        "verdictFloors": {
            "minTradesForVerdict": VERDICT_MIN_TRADES,
            "minTradesForSqnHeadline": SQN_HEADLINE_MIN_TRADES,
            "sqnHeadlineEligible": n >= SQN_HEADLINE_MIN_TRADES,
        },
        "holdout": {
            "fractionOfTime": holdout_fraction,
            "boundaryEpoch": split["boundaryEpoch"],
            "metrics": summarize_trades(
                split["holdout"], n_trials=n_trials, bootstrap_seed=bootstrap_seed
            ),
        },
        "inSample": {
            "metrics": summarize_trades(
                split["inSample"], n_trials=n_trials, bootstrap_seed=bootstrap_seed
            ),
        },
        "rollingWindows": rolling_windows(
            trades, n_windows=n_windows, purge_seconds=purge_seconds
        ),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }
