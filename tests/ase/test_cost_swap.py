"""T0.4 — swap/financing enters net_R; one cost formula survives."""

from __future__ import annotations

import inspect

import numpy as np
import pytest

import athena_ase.labels.triple_barrier as tb
from athena_ase.data.costs import swap_bps_per_day_for
from athena_ase.instruments import instrument_by_symbol


class _FakeStore:
    """PTIS stub: EUR 2%, USD 5% → EURUSD carry = -3% annual."""

    RATES = {"FRED:ECBDFR:rate": 2.0, "FRED:DFF:rate": 5.0}

    def asof(self, series_id, decision_time_ms, n=1):
        if series_id not in self.RATES:
            raise KeyError(series_id)
        return [{"value": self.RATES[series_id]}]


class _EmptyStore:
    def asof(self, series_id, decision_time_ms, n=1):
        raise KeyError(series_id)


EURUSD = instrument_by_symbol("EURUSD")


def _flat_series(n: int = 40, entry: float = 0.0):
    close = np.full(n, entry)
    high = close + 1e-6
    low = close - 1e-6
    value_time = np.arange(n) * 86_400_000
    return close, high, low, value_time


def test_adverse_carry_debits_swing_hold():
    swap_bps = swap_bps_per_day_for(EURUSD, _FakeStore(), 1_000_000, 1)
    assert swap_bps == pytest.approx(3.0 / 100.0 / 365.0 * 1e4)  # ~0.82 bps/day

    close, high, low, value_time = _flat_series()
    out = tb.simulate_barriers(
        close_log=close,
        high_log=high,
        low_log=low,
        value_time=value_time,
        idx=5,
        direction=1,
        entry_log=0.0,
        sigma_bar=0.01,
        horizon="swing",
        instrument=EURUSD,
        swap_bps_per_day=swap_bps,
    )
    assert out is not None
    assert out.hold_bars > 1  # multi-day vertical exit on flat series
    assert out.swap_R > 0
    assert out.net_R < out.gross_R - out.cost_R
    assert out.total_cost_R == pytest.approx(out.cost_R + out.swap_R)


def test_favorable_carry_gets_no_credit():
    # SHORT EURUSD with EUR 2% / USD 5%: carry favors the position → 0 (v1 rule).
    assert swap_bps_per_day_for(EURUSD, _FakeStore(), 1_000_000, -1) == 0.0
    # Missing rates → fail to zero.
    assert swap_bps_per_day_for(EURUSD, _EmptyStore(), 1_000_000, 1) == 0.0


def test_intraday_has_zero_swap():
    close, high, low, value_time = _flat_series()
    out = tb.simulate_barriers(
        close_log=close,
        high_log=high,
        low_log=low,
        value_time=value_time,
        idx=5,
        direction=1,
        entry_log=0.0,
        sigma_bar=0.01,
        horizon="intraday",
        instrument=EURUSD,
        swap_bps_per_day=5.0,  # even if a caller passes one
    )
    assert out is not None
    assert out.swap_R == 0.0
    assert out.net_R == pytest.approx(out.gross_R - out.cost_R)


def test_single_cost_formula():
    # The old duplicate formula is gone; the live entry-cost path delegates to
    # the single spec'd function costs.cost_r_units.
    assert not hasattr(tb, "_cost_r")
    assert "cost_r_units(" in inspect.getsource(tb._entry_cost_r)


def test_entry_cost_matches_prefix_formula():
    # Regression guard: the cost_r_units-based wrapper reproduces the original
    # entry-cost arithmetic exactly.
    import math

    from athena_ase.data.costs import cost_model_for_instrument
    from athena_ase.horizon import K_SL

    sigma_bar, max_hold, range_frac = 0.01, 10, 0.004
    cm = cost_model_for_instrument(EURUSD.family, subclass=EURUSD.subclass)
    r_unit = K_SL * sigma_bar * math.sqrt(max_hold)
    legacy = (cm.round_trip_cost_bps() / 1e4 + cm.slippage_frac_of_range * range_frac * 2.0) / r_unit
    assert tb._entry_cost_r(EURUSD, sigma_bar, max_hold, range_frac) == pytest.approx(legacy)
