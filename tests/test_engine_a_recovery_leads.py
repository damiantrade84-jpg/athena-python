"""Unit tests for the recovery-lead experiment helpers (research-only).

Covers the pure path/ATR/quantile helpers used by the frozen H1 forex-inverse
and H2 range-breakout experiments. Statistics (cluster bootstrap) are covered in
test_engine_a_ablation_paired.py.

Run: pytest tests/test_engine_a_recovery_leads.py -q
"""

from __future__ import annotations

import os

os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

from athena_research.engine_a_ablation.run_recovery_leads import (  # noqa: E402
    _atr14, _path_stats, _prob, _quantiles,
)


def test_atr14_constant_range():
    # 15 bars, each with true range exactly 2.0 -> ATR=2.0
    bars = [{"high": 12, "low": 10, "close": 11} for _ in range(15)]
    assert _atr14(bars) == 2.0
    assert _atr14(bars[:10]) is None  # not enough bars


def test_path_stats_long_favorable_first():
    # LONG, entry 100, risk 10; price rallies to +1R then drops to -1R later
    entry, risk = 100.0, 10.0
    window = [
        {"high": 105, "low": 99},    # t0: fav 0.5
        {"high": 111, "low": 104},   # t1: fav 1.1 -> +1R reached
        {"high": 108, "low": 89},    # t2: adv -1.1 -> -1R reached later
    ]
    s = _path_stats(window, 1.0, entry, risk)
    assert s["mfe"] >= 1.0 and s["mae"] <= -1.0
    assert s["t_mfe"] == 1 and s["t_mae"] == 2
    assert s["p1_first"] == 1        # +1R (t1) before -1R (t2)
    assert s["mfe_before_mae"] == 1


def test_path_stats_inverse_is_mirror():
    entry, risk = 100.0, 10.0
    window = [{"high": 111, "low": 104}, {"high": 108, "low": 89}]
    long_s = _path_stats(window, 1.0, entry, risk)
    short_s = _path_stats(window, -1.0, entry, risk)
    # a long's favorable peak is the short's adverse trough (sign-mirrored)
    assert abs(long_s["mfe"] - (-short_s["mae"])) < 1e-9


def test_quantiles_and_prob():
    q = _quantiles(list(range(100)))
    assert q["p50"] >= 49 and q["p90"] >= 89
    assert _prob([1, 0, 1, None, 1]) == 0.75   # None dropped: 3/4
    assert _prob([None, None]) is None
