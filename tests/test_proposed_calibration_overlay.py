"""Tests for proposed calibration overlay (research-only, not live)."""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CONFIG

ROOT = Path(__file__).resolve().parents[1]
OVERLAY_PATH = ROOT / "configs" / "proposed_calibration_overlay.yaml"


@pytest.fixture
def overlay_doc() -> dict:
    return yaml.safe_load(OVERLAY_PATH.read_text(encoding="utf-8"))


def test_overlay_parses(overlay_doc):
    assert overlay_doc["PROPOSED_OVERLAY_META"]["import_into_live_config"] is False


def test_overlay_not_loaded_by_default():
    assert "PROPOSED_OVERLAY_META" not in CONFIG
    # Diagnostics may be enabled via config.yaml for demo (PC-RES-001); overlay file is not merged.
    assert isinstance(CONFIG.get("CALIBRATION_DIAGNOSTICS_ENABLED"), bool)


def test_config_py_does_not_import_overlay():
    text = (ROOT / "config.py").read_text(encoding="utf-8")
    assert "proposed_calibration_overlay" not in text


def test_config_yaml_does_not_import_overlay():
    text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "proposed_calibration_overlay" not in text


def test_active_settings_have_evidence_metadata(overlay_doc):
    active = overlay_doc["_proposed_active_settings"]
    for key, meta in active.items():
        assert meta.get("evidence_file"), key
        assert meta.get("rollback_rule"), key
        assert meta.get("live_change_allowed") is False, key


def test_gate_proposals_apply_false(overlay_doc):
    for item in overlay_doc["_proposed_gate_adjustments"]:
        assert item["apply"] is False
        assert item.get("evidence_files")
        assert item.get("rollback_rule")


def test_validate_overlay_script_passes(monkeypatch):
    import tools.validate_proposed_calibration_overlay as validator

    monkeypatch.setattr(validator, "_check_protected_live_files_unchanged", lambda errors: None)
    errors = validator.validate_overlay(OVERLAY_PATH)
    assert errors == [], errors


def test_overlay_does_not_enable_live_execution_flags(overlay_doc):
    assert overlay_doc.get("REAL_ORDERS_ALLOWED") is not True
    assert overlay_doc.get("AUTO_EXECUTE") is not True
    paper = overlay_doc.get("PAPER_SOAK") or {}
    if isinstance(paper, dict):
        assert paper.get("REAL_ORDERS_ALLOWED") is not True


def test_engine_a_scoring_unchanged_under_default_config():
    from factor_scoring import compute_factor_scores

    snap = {
        "ema21": 110.0,
        "ema50": 100.0,
        "ema200": 90.0,
        "close": 111.0,
        "atr": 1.0,
        "adx": 30.0,
    }
    result = compute_factor_scores(
        d1_snap=snap,
        h4_snap=snap,
        h1_snap=snap,
        pair={"type": "forex", "display": "EUR/USD"},
        d1_candles=[],
        h4_candles=[],
        h1_candles=[],
        volume_ratio=1.0,
    )
    assert result["final_score"] > 0.0
    assert CONFIG.get("FACTOR_MIN_DIRECTIONAL") == 0.25


def test_engine_b_min_score_gate_unchanged_under_default_config():
    from config import CONFIG as cfg

    profiles = (cfg.get("NAKED_ENGINE") or {}).get("style_profiles") or {}
    assert profiles["intraday"]["min_score"] == 4.0
    assert profiles["scalp"]["min_score"] == 4.0
    assert cfg.get("ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP") is True


def test_production_config_not_mutated_by_overlay_read():
    before = copy.deepcopy(CONFIG.get("CALIBRATION_DIAGNOSTICS_ENABLED"))
    _ = yaml.safe_load(OVERLAY_PATH.read_text(encoding="utf-8"))
    assert CONFIG.get("CALIBRATION_DIAGNOSTICS_ENABLED") == before
