"""Quantile head and bracket tests."""

from __future__ import annotations

from athena_ase.models.quantile_heads import clamp_brackets, fix_quantile_crossing


def test_quantile_crossing_fix_sorts_values():
    row = {"q10": 0.5, "q50": 0.2, "q90": 0.8}
    fixed = fix_quantile_crossing(row)
    vals = [fixed["q10"], fixed["q50"], fixed["q90"]]
    assert vals == sorted(vals)


def test_rr_floor_watch_demotion():
    sl, tp1, reason = clamp_brackets(entry=100.0, direction=1, sl=99.0, tp1=100.1, r_unit=1.0)
    assert reason == "rr_floor_watch"
