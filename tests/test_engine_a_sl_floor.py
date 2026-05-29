"""Focused tests for the Engine A structural-SL overlay floor.

Covers indicators.select_overlay_sl, which the Engine A overlay in athena.py
uses to combine the ATR (math) SL with the Engine B structural SL. Default
(floor off) keeps the legacy tighter-stop behavior; floor on guarantees the
structural SL can only widen the ATR stop, never tighten it.
"""

from __future__ import annotations

from indicators import select_overlay_sl


# --- legacy behavior (floor off): pick the tighter (closer-to-price) stop ----
def test_legacy_long_picks_tighter_when_struct_tighter():
    # LONG entry 100, ATR stop 98, structural stop 99 (tighter/closer).
    assert select_overlay_sl("LONG", 98.0, 99.0, floor_atr=False) == 99.0


def test_legacy_long_keeps_structural_when_wider():
    # LONG, structural stop 97 is wider than ATR 98 -> legacy still max() = 98.
    assert select_overlay_sl("LONG", 98.0, 97.0, floor_atr=False) == 98.0


def test_legacy_short_picks_tighter_when_struct_tighter():
    # SHORT entry 100, ATR stop 102, structural stop 101 (tighter/closer).
    assert select_overlay_sl("SHORT", 102.0, 101.0, floor_atr=False) == 101.0


# --- floor on: structural SL may only widen, never tighten -------------------
def test_floor_long_rejects_tighter_structural():
    # LONG, structural 99 is tighter than ATR 98 -> floor keeps the wider ATR 98.
    assert select_overlay_sl("LONG", 98.0, 99.0, floor_atr=True) == 98.0


def test_floor_long_allows_wider_structural():
    # LONG, structural 97 is wider than ATR 98 -> allowed (97 is further from price).
    assert select_overlay_sl("LONG", 98.0, 97.0, floor_atr=True) == 97.0


def test_floor_short_rejects_tighter_structural():
    # SHORT, structural 101 is tighter than ATR 102 -> floor keeps wider ATR 102.
    assert select_overlay_sl("SHORT", 102.0, 101.0, floor_atr=True) == 102.0


def test_floor_short_allows_wider_structural():
    # SHORT, structural 103 is wider than ATR 102 -> allowed.
    assert select_overlay_sl("SHORT", 102.0, 103.0, floor_atr=True) == 103.0


def test_floor_never_tighter_than_atr_distance():
    # Property: with the floor on, |entry - final_sl| >= |entry - atr_sl|.
    entry = 100.0
    for direction, atr_sl, struct_sl in [
        ("LONG", 98.0, 99.5),   # struct tighter
        ("LONG", 98.0, 96.0),   # struct wider
        ("SHORT", 102.0, 100.8),  # struct tighter
        ("SHORT", 102.0, 104.0),  # struct wider
    ]:
        final = select_overlay_sl(direction, atr_sl, struct_sl, floor_atr=True)
        assert abs(entry - final) >= abs(entry - atr_sl) - 1e-9
