"""Tests for proposed H4 indicator overlay (research-only, not live)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CONFIG

ROOT = Path(__file__).resolve().parents[1]
OVERLAY_PATH = ROOT / "configs" / "proposed_h4_indicator_overlay.yaml"


@pytest.fixture
def overlay_doc() -> dict:
    return yaml.safe_load(OVERLAY_PATH.read_text(encoding="utf-8"))


def test_overlay_parses(overlay_doc):
    assert overlay_doc["PROPOSED_H4_OVERLAY_META"]["import_into_live_config"] is False


def test_overlay_not_loaded_by_default():
    assert "PROPOSED_H4_OVERLAY_META" not in CONFIG


def test_config_py_does_not_import_h4_overlay():
    text = (ROOT / "config.py").read_text(encoding="utf-8")
    assert "proposed_h4_indicator_overlay" not in text


def test_active_settings_have_evidence_metadata(overlay_doc):
    active = overlay_doc["_proposed_active_settings"]
    for key, meta in active.items():
        assert meta.get("evidence_file"), key
        assert meta.get("rollback_rule"), key
        assert meta.get("live_change_allowed") is False, key


def test_overlay_contains_scaled_tables(overlay_doc):
    ema = overlay_doc["ENGINE_A_EMA_PERIODS_BY_CLASS"]["forex_majors"]
    assert ema["trend"] == 13
    assert ema["momentum"] == 15
    assert ema["long"] == 200
    assert overlay_doc["ENGINE_A_RSI_PERIOD_BY_CLASS"]["forex_majors"] == 9
    assert overlay_doc["ENGINE_B_SWEEP_LOOKBACK_BARS"] == 2


def test_overlay_does_not_enable_live_execution_flags(overlay_doc):
    assert overlay_doc.get("REAL_ORDERS_ALLOWED") is not True
    assert overlay_doc.get("AUTO_EXECUTE") is not True
