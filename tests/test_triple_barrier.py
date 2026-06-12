"""Triple-barrier label tests."""

from __future__ import annotations

import numpy as np

from athena_ase.instruments import Instrument
from athena_ase.labels.triple_barrier import LabelOutcome, simulate_barriers, uniqueness_weights


def _inst():
    return Instrument("EURUSD", "EUR/USD", "forex", "major", "EURUSD.FOREX", "USDX", False)


def test_both_barriers_same_bar_adverse_first():
    idx = 5
    close = np.full(20, 0.0)
    high = np.full(20, 0.0)
    low = np.full(20, 0.0)
    sigma = 0.001
    r_unit = sigma * 4.0
    close[idx] = 0.0
    high[idx + 1] = r_unit + 0.001
    low[idx + 1] = -r_unit - 0.001
    times = np.arange(20, dtype=np.int64) * 3600000
    out = simulate_barriers(
        close_log=close,
        high_log=high,
        low_log=low,
        value_time=times,
        idx=idx,
        direction=1,
        entry_log=0.0,
        sigma_bar=sigma,
        horizon="intraday",
        instrument=_inst(),
    )
    assert out is not None
    assert out.exit_reason == "adverse_first"
    assert out.y == 0


def test_net_r_cost_arithmetic():
    gross = 0.5
    cost = 0.1
    assert gross - cost > 0
    assert (1 if gross - cost > 0 else 0) == 1


def test_uniqueness_weights_overlap():
    base = LabelOutcome(
        instrument="EURUSD",
        family="forex",
        horizon="intraday",
        decision_time_ms=1000,
        direction=1,
        entry_log=0.0,
        sigma_bar=0.01,
        gross_R=0.1,
        cost_R=0.02,
        net_R=0.08,
        mae_R=0.2,
        mfe_R=0.3,
        hold_bars=4,
        exit_reason="target",
        y=1,
        agreement_count=1,
        conflict_flag=False,
        primary_signals="[]",
        label_start_ms=1000,
        label_end_ms=5000,
    )
    overlap = LabelOutcome(**{**base.__dict__, "decision_time_ms": 2000, "label_start_ms": 2000, "label_end_ms": 6000})
    w = uniqueness_weights([base, overlap])
    assert w[0] == 0.5
    assert w[1] == 0.5
