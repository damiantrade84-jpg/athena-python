"""Unit tests for the Engine B persistent zone registry."""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CONFIG
from zone_registry import get_zone_registry, reset_zone_registry


@pytest.fixture(autouse=True)
def _reset_registry(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_B_ZONE_PERSISTENCE", False)
    reset_zone_registry()
    yield
    reset_zone_registry()


def test_upsert_merges_matching_zones_and_increments_scan_count():
    registry = get_zone_registry()

    registry.upsert_zones(
        "EURUSD",
        "H1",
        obs=[{"type": "bullish", "top": 1.1050, "bottom": 1.1000, "strength": 60}],
        fvgs=[],
        atr=0.0200,
    )
    registry.upsert_zones(
        "EURUSD",
        "H1",
        obs=[{"type": "bullish", "top": 1.1060, "bottom": 1.1010, "strength": 75}],
        fvgs=[],
        atr=0.0200,
    )

    active = registry.get_active_zones("EURUSD", "H1")

    assert len(active) == 1
    assert active[0]["type"] == "OB"
    assert active[0]["direction"] == "bullish"
    assert active[0]["top"] == pytest.approx(1.1060)
    assert active[0]["bottom"] == pytest.approx(1.1000)
    assert active[0]["strength"] == pytest.approx(75.0)
    assert active[0]["scan_count"] == 2
    assert active[0]["symbol"] == "EURUSD"
    assert active[0]["timeframe"] == "H1"


def test_mark_mitigated_flags_zone_once_price_crosses_midpoint():
    registry = get_zone_registry()

    registry.upsert_zones(
        "EURUSD",
        "H4",
        obs=[],
        fvgs=[{"type": "bullish", "top": 1.1100, "bottom": 1.1000, "strength": 0.0100}],
        atr=0.0200,
    )

    registry.mark_mitigated("EURUSD", "H4", current_price=1.1040, atr=0.0200)

    assert registry.get_active_zones("EURUSD", "H4") == []
    stored = registry._zones[("EURUSD", "H4")][0]
    assert stored["mitigated"] is True
    assert stored["mitigated_at"] is not None


def test_prune_old_zones_removes_stale_untouched_entries():
    registry = get_zone_registry()

    registry.upsert_zones(
        "XAUUSD",
        "H1",
        obs=[{"type": "bearish", "top": 2355.0, "bottom": 2350.0, "strength": 64}],
        fvgs=[],
        atr=10.0,
    )
    registry._zones[("XAUUSD", "H1")][0]["created_at"] = (
        datetime.now(timezone.utc) - timedelta(days=10)
    ).isoformat()

    registry.prune_old_zones(max_age_hours=168)

    assert registry.has_zones("XAUUSD", "H1") is False


def test_get_active_zones_excludes_mitigated_entries():
    registry = get_zone_registry()

    registry.upsert_zones(
        "GBPUSD",
        "H1",
        obs=[
            {"type": "bullish", "top": 1.2100, "bottom": 1.2000, "strength": 58},
            {"type": "bearish", "top": 1.2300, "bottom": 1.2200, "strength": 62},
        ],
        fvgs=[],
        atr=0.0200,
    )

    registry.mark_mitigated("GBPUSD", "H1", current_price=1.2040, atr=0.0200)

    active = registry.get_active_zones("GBPUSD", "H1")

    assert len(active) == 1
    assert active[0]["direction"] == "bearish"
    assert active[0]["mitigated"] is False
