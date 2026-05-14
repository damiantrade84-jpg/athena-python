"""Evidence tests for Engine B full-scan semantics and gate-funnel diagnostics.

Does not import athena.py. Does not mutate execution or Engine B thresholds.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from config import CONFIG
from market_structure import resolve_engine_b_execution_levels
from scanner import _attach_engine_b_scan_gate_funnel, _patch_engine_b_funnel_final_tier

ENGINE_B_SCAN_FUNNEL_SHAPE_KEYS = frozenset(
    {
        "symbol",
        "asset_type",
        "score_group",
        "style",
        "sig_a_present",
        "engine_b_evaluated",
        "engine_b_skip_stage",
        "engine_b_error",
        "candles_ok",
        "atr",
        "atr_source",
        "bybit_atr_available",
        "fallback_allowed",
        "structure_verdict",
        "direction_a",
        "direction_b",
        "structure_ok",
        "location_ok",
        "entry_ok",
        "room_ok",
        "space_gate_ok",
        "rr_ok",
        "score",
        "min_score_scaled",
        "min_rr",
        "entry",
        "sl",
        "tp",
        "structural_sl",
        "structural_tp",
        "execution_sl",
        "execution_tp",
        "rr",
        "rr_source",
        "execution_level_reject_reason",
        "tp_structural_limited",
        "failed_gate_names",
        "final_tier",
        "final_reason",
        "structure_executed",
        "synthetic_fallback_rr_tp_enabled",
        "rr_can_satisfy_space_gate_crypto_config",
        "rr_space_gate_enabled_overlay",
        "engine_b_confidence_passed",
    }
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_scan_source_engine_b_only_after_sig_a():
    text = (REPO_ROOT / "scanner.py").read_text(encoding="utf-8")
    idx = text.find("if not sig_a:")
    assert idx >= 0
    assert "return pair, None, None" in text[idx : idx + 500]


def test_scan_source_crypto_bybit_atr_unavailable_diagnostic_constant():
    text = (REPO_ROOT / "scanner.py").read_text(encoding="utf-8")
    assert "bybit_atr_unavailable" in text


def test_yaml_crypto_rr_space_sat_false():
    raw = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    gate = raw.get("ENGINE_B_RR_CAN_SATISFY_SPACE_GATE") or {}
    assert isinstance(gate, dict)
    assert gate.get("crypto") is False


def test_loaded_config_matches_crypto_rr_space_false():
    cfg_rr = CONFIG.get("ENGINE_B_RR_CAN_SATISFY_SPACE_GATE")
    assert isinstance(cfg_rr, dict), "expected dict-scoped ENGINE_B_RR_CAN_SATISFY_SPACE_GATE"
    assert cfg_rr.get("crypto") is False


def test_resolve_engine_b_structural_tp_below_min_rr_rejects_when_fallback_off():
    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=95.0,
        structural_tp=102.0,
        atr=1.0,
        style="intraday",
        asset_class="crypto",
        min_rr=99.0,
        fallback_rr=2.0,
    )
    assert out["execution_level_reject_reason"] == "structural_tp_below_min_rr"
    assert out["execution_levels_valid"] is False


def test_attach_engine_b_scan_gate_funnel_stable_shape(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_B_SCAN_GATE_FUNNEL_ENABLED", True)

    pair = {"display": "TESTUSDT", "symbol": "TESTUSDT", "type": "crypto"}
    sig: dict = {"direction": "LONG", "engine_b_evaluated": True}
    conf_b = {
        "structure_ok": True,
        "passed": False,
        "failed_gate_names": ["space"],
        "score": 2.5,
        "rr_space_gate_enabled": False,
    }
    res_b = {"structural_verdict": "UNCLEAR", "tp_structural_limited": False}
    extras = {
        "candles_tf_ok": True,
        "atr_value": 12.34,
        "atr_source": "bybit_levels",
        "bybit_atr_available": True,
        "engine_b_skip_stage": None,
        "entry_price": 1000.11,
        "structure_executed": False,
    }
    sp = {"min_rr": 1.5}

    _attach_engine_b_scan_gate_funnel(
        sig=sig,
        pair=pair,
        score_group="test_group",
        resolved_style="scalp",
        style_profile_b=sp,
        conf_b=conf_b,
        res_b=res_b,
        extras=extras,
    )
    funnel = sig.get("engine_b_scan_gate_funnel")
    assert isinstance(funnel, dict)
    assert ENGINE_B_SCAN_FUNNEL_SHAPE_KEYS <= set(funnel.keys())
    assert funnel["rr_can_satisfy_space_gate_crypto_config"] is False

    _patch_engine_b_funnel_final_tier(sig, "skip", "Below discovery threshold")
    assert funnel["final_tier"] == "skip"
    assert funnel["final_reason"] == "Below discovery threshold"


def test_attach_respects_engine_b_scan_gate_funnel_disabled(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_B_SCAN_GATE_FUNNEL_ENABLED", False)
    pair = {"display": "X", "type": "forex"}
    sig: dict = {}
    _attach_engine_b_scan_gate_funnel(
        sig=sig,
        pair=pair,
        score_group=None,
        resolved_style="intraday",
        style_profile_b={},
        conf_b=None,
        res_b=None,
        extras={"candles_tf_ok": True},
    )
    assert "engine_b_scan_gate_funnel" not in sig
