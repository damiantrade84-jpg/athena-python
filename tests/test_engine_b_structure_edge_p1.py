"""Phase 1.1–1.3: CHoCH invalidation, FVG registry zone_tf, sweep mutual exclusion."""

from __future__ import annotations

import numpy as np

from market_structure import NakedEngine
from zone_registry import get_zone_registry, reset_zone_registry


def _choch_arrays():
    n = 30
    highs = np.linspace(90, 105, n)
    lows = highs - 2.0
    closes = np.full(n, 98.0)  # stay below ref_high 100 by default
    swings = {"peak_idx": [5, 12, 20], "trough_idx": [3, 10, 18]}
    bos_data = {
        "bos_bear": True,
        "bos_bull": False,
        "bos_reference_high": 100.0,
        "bos_reference_low": 90.0,
        "bos_bear_bar_index": n - 10,
        "bos_bull_bar_index": None,
    }
    return highs, lows, closes, swings, bos_data


def test_choch_invalidated_by_reclaim_close():
    """Bull CHoCH after bear BOS fails when a later close reclaims below the level."""
    engine = NakedEngine()
    highs, lows, closes, swings, bos_data = _choch_arrays()
    closes[-4] = 101.5  # CHoCH print
    closes[-3] = 101.0
    closes[-2] = 99.0   # reclaim → invalidate
    closes[-1] = 99.2   # still below — no fresh unbroken CHoCH
    result = engine._detect_choch(
        highs, lows, atr=1.0, closes=closes, swings=swings, bos_data=bos_data, min_break_atr=0.0
    )
    assert result["choch_bull"] is False


def test_choch_valid_on_pullback_without_reclaim():
    """BOS-parity: CHoCH print mid-window stays valid when last bar pulls back but never reclaims."""
    engine = NakedEngine()
    highs, lows, closes, swings, bos_data = _choch_arrays()
    closes[-4] = 101.5  # CHoCH print
    closes[-3] = 101.2
    closes[-2] = 100.8  # still above level
    closes[-1] = 100.2  # pullback but not reclaim below 100
    result = engine._detect_choch(
        highs, lows, atr=1.0, closes=closes, swings=swings, bos_data=bos_data, min_break_atr=0.0
    )
    assert result["choch_bull"] is True


def test_opposing_sweeps_resolve_to_one_direction():
    engine = NakedEngine()
    n = 20
    closes = np.full(n, 100.0)
    highs = np.full(n, 101.0)
    lows = np.full(n, 99.0)
    highs[-4] = 106.0
    closes[-4] = 100.5
    lows[-4] = 100.0
    lows[-1] = 94.0
    closes[-1] = 100.5
    highs[-1] = 101.0
    result = engine._detect_sweep(
        highs, lows, closes, atr=1.0, swing_high=105.0, swing_low=95.0
    )
    assert not (result["bull_sweep"] and result["bear_sweep"])
    assert result["bull_sweep"] is True
    assert result["bear_sweep"] is False
    assert result["sweep_direction"] == "LONG"


def test_fvg_registry_uses_zone_tf_for_swing(monkeypatch):
    reset_zone_registry()
    engine = NakedEngine()
    registry = get_zone_registry()
    symbol = "TEST_SWING_FVG"
    calls: list[tuple] = []
    original = registry.upsert_zones

    def _spy(sym, tf, obs, fvgs, **kwargs):
        calls.append((sym, str(tf).upper(), len(obs or []), len(fvgs or [])))
        return original(sym, tf, obs, fvgs, **kwargs)

    monkeypatch.setattr(registry, "upsert_zones", _spy)
    monkeypatch.setattr(
        "market_structure.resolve_engine_b_tfs",
        lambda asset_type, style: {
            "struct": "H4",
            "zone": "D1",
            "trigger": "H1",
            "atr": "H4",
        },
    )

    def _bars(n=80, base=100.0):
        rows = []
        for i in range(n):
            c = base + i * 0.1
            rows.append(
                {
                    "time": f"2025-01-{1 + (i // 24):02d}T{i % 24:02d}:00:00+00:00",
                    "open": c - 0.05,
                    "high": c + 0.5,
                    "low": c - 0.5,
                    "close": c,
                    "vol": 1000,
                }
            )
        return rows

    d1, h4, h1 = _bars(80), _bars(100), _bars(120)
    engine.set_registry_context(symbol)
    engine.precompute_structure_data(
        d1,
        h4,
        h1,
        current_price=float(h1[-1]["close"]),
        atr=1.0,
        asset_type="forex",
        style="swing",
        pair={"display": "EUR/USD", "symbol": symbol, "type": "forex"},
    )
    # FVG upserts (second arg TF) must use zone TF D1, never hardcoded H4-only.
    fvg_tf_calls = [c[1] for c in calls if c[3] >= 0 and c[2] == 0]
    assert "D1" in fvg_tf_calls, calls
    assert "H4" not in fvg_tf_calls or any(c[1] == "D1" for c in calls if c[2] == 0)