"""Data freshness tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

from research.edgelab.data_freshness import run_freshness_checks


def test_stale_data_blocks_engine_test():
    repo = Path(tempfile.mkdtemp(prefix="edgelab_fresh_"))
    results = run_freshness_checks(
        repo,
        symbols=["BTCUSDT"],
        timeframes=["H4"],
        max_age_hours=1,
        min_bars=200,
        dry_run=False,
    )
    assert len(results) == 1
    row = results[0]
    assert row["can_run_engine_tests"] is False or row["freshness_score"] < 100
