"""Stage 6 verification: seeded bootstrap determinism, deflated-SQN sanity,
temporal holdout / rolling-window accounting."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from athena_backtest.metrics import (
    deflated_sqn,
    max_drawdown_r,
    stationary_block_bootstrap,
    summarize_trades,
)
from athena_backtest.validation import (
    TrialRegistry,
    rolling_windows,
    temporal_holdout_split,
    validation_block,
)


def _results(n: int = 80, seed: int = 7) -> list[float]:
    rng = random.Random(seed)
    return [round(rng.gauss(0.05, 1.0), 4) for _ in range(n)]


def _trades(results: list[float]) -> list[dict]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        {"entryTime": (start + timedelta(hours=4 * i)).isoformat(), "resultR": r}
        for i, r in enumerate(results)
    ]


def test_bootstrap_is_deterministic_for_fixed_seed():
    results = _results()
    a = stationary_block_bootstrap(results, seed=42)
    b = stationary_block_bootstrap(results, seed=42)
    assert a == b
    c = stationary_block_bootstrap(results, seed=43)
    assert c != a  # a different seed must actually change the resampling


def test_deflated_sqn_monotonically_decreases_with_trials():
    values = [deflated_sqn(2.0, 100, n) for n in (1, 2, 8, 64, 512)]
    assert values[0] == 2.0
    assert all(
        earlier is not None and later is not None and later < earlier
        for earlier, later in zip(values, values[1:])
    )


def test_max_drawdown_tracks_peak_to_trough():
    assert max_drawdown_r([1.0, -0.5, -0.5, 2.0, -3.0]) == 3.0
    assert max_drawdown_r([1.0, 1.0]) == 0.0


def test_temporal_holdout_splits_by_time_not_by_count():
    # 8 early trades in one week, 2 late trades months later: a 30%-of-time
    # holdout catches only the late cluster even though it is 20% of trades.
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    trades = [
        {"entryTime": (start + timedelta(days=i)).isoformat(), "resultR": 0.1}
        for i in range(8)
    ] + [
        {"entryTime": (start + timedelta(days=100 + i)).isoformat(), "resultR": -0.2}
        for i in range(2)
    ]
    split = temporal_holdout_split(trades, holdout_fraction=0.3)
    assert len(split["holdout"]) == 2
    assert len(split["inSample"]) == 8


def test_rolling_windows_partition_all_trades_without_purge():
    trades = _trades(_results(40))
    windows = rolling_windows(trades, n_windows=4)
    assert len(windows) == 4
    assert sum(w["trades"] for w in windows) == 40


def test_validation_block_floors_and_registry():
    trades = _trades(_results(20))
    block = validation_block(trades)
    assert block["verdict"] == "INSUFFICIENT_SAMPLE"
    assert block["verdictFloors"]["sqnHeadlineEligible"] is False

    registry = TrialRegistry()
    registry.record(symbol="EUR/USD", engine="A", style="intraday")
    registry.record(symbol="EUR/USD", engine="A", style="intraday")  # dupe
    registry.record(symbol="BTCUSDT", engine="B", style="intraday")
    assert registry.count == 2

    full = summarize_trades(_trades(_results(80)), n_trials=registry.count)
    assert full["trialCount"] == 2
    assert full["deflatedSqn"] is not None and full["sqn"] is not None
    assert full["deflatedSqn"] < full["sqn"]
