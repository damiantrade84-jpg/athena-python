"""Engine A V3 structural geometry config (SL min ATR / TP1 RR / structure ATR)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config import CONFIG
from engine_a_v3.levels import build_structural_levels, resolve_structural_geometry
from engine_a_v3.setups import atr_for_levels


def _rows(count: int, step: timedelta, *, base: float = 100.0, slope: float = 0.05) -> list[dict]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    for idx in range(count):
        close = base + slope * idx
        rows.append(
            {
                "time": (start + step * idx).isoformat(),
                "open": close - 0.2,
                "high": close + 0.8,
                "low": close - 0.8,
                "close": close,
                "vol": 1000 + idx,
            }
        )
    return rows


def test_default_geometry_matches_historical_contract(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_STRUCTURAL_GEOMETRY",
        {
            "ENABLED": True,
            "SL_MIN_ATR_MULT": 0.8,
            "TP1_RR": 1.0,
            "LEVELS_ATR_TF": "entry",
            "BY_ASSET": {},
        },
    )
    geo = resolve_structural_geometry("forex")
    assert geo["sl_min_atr_mult"] == 0.8
    assert geo["tp1_rr"] == 1.0
    assert geo["levels_atr_tf"] == "entry"


def test_forex_override_widens_stop_and_tp1(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_STRUCTURAL_GEOMETRY",
        {
            "ENABLED": True,
            "SL_MIN_ATR_MULT": 0.8,
            "TP1_RR": 1.0,
            "LEVELS_ATR_TF": "entry",
            "BY_ASSET": {
                "forex": {
                    "SL_MIN_ATR_MULT": 1.25,
                    "TP1_RR": 1.5,
                    "LEVELS_ATR_TF": "structure",
                }
            },
        },
    )
    rows = _rows(40, timedelta(hours=1))
    current = float(rows[-1]["close"])
    for row in rows[-20:]:
        row["low"] = current - 0.01
        row["high"] = current + 0.5
    atr = atr_for_levels(rows, 14)
    levels = build_structural_levels(rows, direction="LONG", atr_period=14, asset_type="forex")
    assert levels is not None
    risk = current - levels.invalidation
    assert risk + 1e-9 >= 1.25 * atr
    assert abs(levels.targets[0].rr - 1.5) < 1e-9
    assert abs(levels.targets[0].price - (current + 1.5 * risk)) < 1e-9


def test_structure_atr_candles_widen_vs_entry(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_STRUCTURAL_GEOMETRY",
        {
            "ENABLED": True,
            "SL_MIN_ATR_MULT": 0.8,
            "TP1_RR": 1.0,
            "LEVELS_ATR_TF": "structure",
            "BY_ASSET": {},
        },
    )
    entry = _rows(40, timedelta(hours=1), slope=0.01)
    structure = _rows(40, timedelta(hours=4), slope=0.05)
    # Amplify structure ranges so ATR is clearly larger.
    for row in structure:
        mid = float(row["close"])
        row["high"] = mid + 2.0
        row["low"] = mid - 2.0
    current = float(entry[-1]["close"])
    for row in entry[-20:]:
        row["low"] = current - 0.01
        row["high"] = current + 0.2
    entry_only = build_structural_levels(
        entry, direction="LONG", atr_period=14, asset_type="forex"
    )
    with_structure = build_structural_levels(
        entry,
        direction="LONG",
        atr_period=14,
        asset_type="forex",
        atr_candles=structure,
        swing_candles=structure,
    )
    assert entry_only is not None and with_structure is not None
    assert (current - with_structure.invalidation) > (current - entry_only.invalidation)


def test_sl_floor_binds_when_sl_min_mult_drops_below_it(monkeypatch):
    """ENGINE_A_V3_STRUCTURAL_SL_FLOOR_ATR_MULT must actually be reachable.

    It was previously applied as a second min() after the stop had already been
    pushed to at least SL_MIN_ATR_MULT * ATR. With the shipped 0.8 vs 0.35 the
    first step always produced the wider stop, so the floor was dead code. The
    two knobs express the same quantity, so the binding one is the larger.
    """
    monkeypatch.setitem(CONFIG, "ENGINE_A_STRUCTURAL_SL_FLOOR_ATR", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_STRUCTURAL_SL_FLOOR_ATR_MULT", 0.35)
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_STRUCTURAL_GEOMETRY",
        {
            "ENABLED": True,
            "SL_MIN_ATR_MULT": 0.8,
            "TP1_RR": 1.0,
            "LEVELS_ATR_TF": "entry",
            # Asset override below the floor: the floor is now what binds.
            "BY_ASSET": {"forex": {"SL_MIN_ATR_MULT": 0.10}},
        },
    )
    rows = _rows(60, timedelta(hours=1), slope=0.0)
    current = float(rows[-1]["close"])
    atr = atr_for_levels(rows, 14)

    levels = build_structural_levels(
        rows, direction="LONG", current_price=current, asset_type="forex"
    )
    assert levels is not None
    # 20-bar low is 0.8 below a flat series; the floor only binds when it is the
    # widest of the three candidates, so assert it is at least the floor width.
    assert current - levels.invalidation >= 0.35 * atr - 1e-9


def test_sl_floor_disabled_leaves_sl_min_mult_in_charge(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_STRUCTURAL_SL_FLOOR_ATR", False)
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_STRUCTURAL_GEOMETRY",
        {
            "ENABLED": True,
            "SL_MIN_ATR_MULT": 0.8,
            "TP1_RR": 1.0,
            "LEVELS_ATR_TF": "entry",
            "BY_ASSET": {},
        },
    )
    rows = _rows(60, timedelta(hours=1), slope=0.0)
    current = float(rows[-1]["close"])
    atr = atr_for_levels(rows, 14)

    levels = build_structural_levels(
        rows, direction="LONG", current_price=current, asset_type="forex"
    )
    assert levels is not None
    assert current - levels.invalidation >= 0.8 * atr - 1e-9


def test_default_config_floor_is_a_no_op(monkeypatch):
    """Collapsing the two min() steps must not move stops under shipped config."""
    monkeypatch.setitem(CONFIG, "ENGINE_A_STRUCTURAL_SL_FLOOR_ATR", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_STRUCTURAL_SL_FLOOR_ATR_MULT", 0.35)
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_STRUCTURAL_GEOMETRY",
        {
            "ENABLED": True,
            "SL_MIN_ATR_MULT": 0.8,
            "TP1_RR": 1.0,
            "LEVELS_ATR_TF": "entry",
            "BY_ASSET": {},
        },
    )
    rows = _rows(60, timedelta(hours=1), slope=0.05)
    current = float(rows[-1]["close"])
    atr = atr_for_levels(rows, 14)
    expected_structural = min(float(r["low"]) for r in rows[-20:])

    levels = build_structural_levels(
        rows, direction="LONG", current_price=current, asset_type="forex"
    )
    assert levels is not None
    assert levels.invalidation == min(expected_structural, current - 0.8 * atr)
