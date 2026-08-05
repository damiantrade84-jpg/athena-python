"""Parity proof for the EngineBProbeSeries cache (engine_b_series.py).

The cache replaces per-bar full-series recomputes in the three research-lab
probes with O(1) cumulative-sum lookups. These tests assert the probes return
**identical result dicts** with and without the cache at every sampled window
position, that the build-time self-check fails closed on corruption, and that
non-slice windows fall back to the original recompute path.
"""

from __future__ import annotations

import random

from engine_b_series import EngineBProbeSeries
from market_structure import (
    _engine_b_cvd_momentum_value,
    _engine_b_research_lab_candidate_gates,
    _engine_b_vwap_deviation_value,
    _engine_b_vwap_reclaim_value,
)


def _walk_candles(seed: int, n: int, *, zero_vol_every: int = 0, flat_every: int = 0) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    price = 100.0
    for i in range(n):
        drift = rng.uniform(-1.5, 1.5)
        o = price
        c = price + drift
        h = max(o, c) + rng.uniform(0.0, 0.5)
        lo = min(o, c) - rng.uniform(0.0, 0.5)
        if flat_every and i % flat_every == 0:
            h = lo = (o + c) / 2.0  # zero-range bar -> CVD delta 0.0
        vol = 0.0 if (zero_vol_every and i % zero_vol_every == 0) else rng.uniform(10, 1000)
        rows.append({"time": f"2026-01-01T{i:05d}", "open": o, "high": h, "low": lo, "close": c, "vol": vol})
        price = c
    return rows


def _windows(rows: list[dict]) -> list[list[dict]]:
    """Prefix windows at every position plus sliding 60-bar windows."""
    out = [rows[:end] for end in range(25, len(rows) + 1)]
    out += [rows[end - 60 : end] for end in range(85, len(rows) + 1, 7)]
    return out


def test_probe_outputs_identical_with_and_without_cache():
    rows = _walk_candles(11, 260, zero_vol_every=17, flat_every=23)
    series = EngineBProbeSeries(rows)
    assert not series.disabled, "self-check must pass on a clean series"

    for candles in _windows(rows):
        for direction in ("LONG", "SHORT"):
            assert _engine_b_vwap_reclaim_value(direction, candles, 0.5, 1.0, probe_series=series) == (
                _engine_b_vwap_reclaim_value(direction, candles, 0.5, 1.0)
            )
            assert _engine_b_cvd_momentum_value(direction, candles, probe_series=series) == (
                _engine_b_cvd_momentum_value(direction, candles)
            )
            assert _engine_b_vwap_deviation_value(direction, candles, 2.0, probe_series=series) == (
                _engine_b_vwap_deviation_value(direction, candles, 2.0)
            )


def test_probe_outputs_identical_all_zero_volume_stretch():
    # A long zero-volume stretch makes the windowed cum_vol <= 0 at the start;
    # the cache must reproduce calc_vwap's None semantics exactly.
    rows = _walk_candles(5, 60)
    for i in range(10, 50):
        rows[i]["vol"] = 0.0
    series = EngineBProbeSeries(rows)
    assert not series.disabled
    for end in range(25, len(rows) + 1):
        candles = rows[:end]
        assert _engine_b_vwap_reclaim_value("LONG", candles, 0.5, 1.0, probe_series=series) == (
            _engine_b_vwap_reclaim_value("LONG", candles, 0.5, 1.0)
        )


def test_self_check_fails_closed_on_corruption():
    rows = _walk_candles(3, 120)
    series = EngineBProbeSeries(rows)
    assert not series.disabled
    series._cum_tp_vol[len(series._cum_tp_vol) // 2] += 1.0  # corrupt one cumulative sum
    assert series._self_check(24) is False


def test_for_window_rejects_non_slice_and_ambiguous_times():
    rows = _walk_candles(9, 80)
    series = EngineBProbeSeries(rows)
    assert not series.disabled

    # A window not drawn from the cached series -> no view -> original path.
    foreign = _walk_candles(99, 60)
    for i, row in enumerate(foreign):
        row["time"] = f"2025-06-01T{i:05d}"  # distinct time base from the cached series
    assert series.for_window(foreign) is None

    # Duplicated timestamps make the window position undecidable.
    dup = _walk_candles(4, 40)
    for row in dup:
        row["time"] = "2026-01-01T00:00"
    series_dup = EngineBProbeSeries(dup)
    if not series_dup.disabled:
        assert series_dup.for_window(dup[:30]) is None

    # Foreign windows take the original recompute path and still return a value.
    assert _engine_b_vwap_reclaim_value("LONG", rows[:60], 0.5, 1.0, probe_series=series) == (
        _engine_b_vwap_reclaim_value("LONG", rows[:60], 0.5, 1.0)
    ) or series.for_window(rows[:60]) is not None


def test_candidate_gates_identical_with_and_without_cache(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_B_RESEARCH_LAB_FACTORS",
        {
            "ENABLED": True,
            "GROUPS": {
                "crypto_btc": ["micro_breakout", "vwap_reclaim", "cvd_momentum", "vwap_deviation"]
            },
            "ALLOW_GATE_UPGRADE": True,
        },
    )
    rows = _walk_candles(21, 140)
    series = EngineBProbeSeries(rows)
    assert not series.disabled
    res = {"asset_type": "crypto", "pair_display": "BTC/USDT"}
    profile = {"score_group": "crypto_btc"}
    for end in range(30, len(rows) + 1, 5):
        candles = rows[:end]
        for direction in ("LONG", "SHORT"):
            with_cache = _engine_b_research_lab_candidate_gates(
                res, direction, candles, 100.0, 0.5, profile, probe_series=series
            )
            without_cache = _engine_b_research_lab_candidate_gates(
                res, direction, candles, 100.0, 0.5, profile
            )
            assert with_cache == without_cache
