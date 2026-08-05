"""Focused Phase 2 Engine B quality/adaptation contracts."""

from __future__ import annotations

import pytest

import config
from engine_b_phase2 import (
    build_invalidation_context,
    build_phase2_context,
    compute_confluence_density,
    resolve_adaptive_thresholds,
)
from engine_b_quality import compute_confluence_subscores
from market_structure import NakedEngine


def _candles() -> list[dict]:
    rows = []
    for i in range(8):
        rows.append(
            {
                "open": 100.0 - i * 0.05,
                "high": 100.35 - i * 0.03,
                "low": 99.65 - i * 0.04,
                "close": 99.90 - i * 0.02,
                "volume": 100.0,
                "time": f"2026-08-05T10:{i:02d}:00+00:00",
            }
        )
    rows[-1].update({"open": 99.65, "high": 100.35, "low": 99.20, "close": 100.20, "volume": 180.0})
    return rows


def _res() -> dict:
    return {
        "asset_type": "crypto",
        "regime": "TRENDING",
        "style": "intraday",
        "bos_confirmed": True,
        "choch_confirmed": False,
        "bos_data": {
            "last_broken_high": 100.0,
            "bos_volume_available": True,
            "bos_volume_ratio": 1.2,
            "bos_volume_threshold": 1.3,
        },
        "liquidity_sweep": True,
        "sweep_direction": "LONG",
        "ob_at_zone": True,
        "fvg_overlap": True,
        "fvg_context": {"reaction_confirmed": True},
        "breaker_block": False,
        "current_swing_sequence": "HH_HL",
        "recommended_stop_loss": 98.0,
    }


def test_phase2_context_grades_pullback_sweep_and_moderate_volume():
    res = _res()
    out = build_phase2_context(
        res=res,
        candles=_candles(),
        direction="LONG",
        atr=1.0,
        active_zone={"center": 100.0, "scan_count": 2},
        session_quality="high",
        asset_type="crypto",
    )

    assert out["version"] == "engine_b.phase2.v1"
    assert out["pullback_quality"]["available"] is True
    assert 0.0 < out["pullback_quality"]["score"] <= 1.0
    assert out["sweep_quality"]["grade"] in {"moderate", "strong"}
    assert out["volume_confirmation"]["grade"] == "moderate"
    assert 0.0 < out["volume_confirmation"]["score"] < 1.0
    assert out["volume_confirmation"]["hard_rejection"] is False


def test_graded_volume_reaches_weighted_quality_component():
    res = _res()
    res["bos_volume_source_applicable"] = True
    res["phase2_quality"] = build_phase2_context(
        res=res,
        candles=_candles(),
        direction="LONG",
        atr=1.0,
        active_zone={"center": 100.0, "scan_count": 1},
        session_quality="medium",
        asset_type="crypto",
    )
    scores = compute_confluence_subscores(res, "LONG", 1.0, volume_ok=False, asset_type="crypto")
    assert scores["volume_confirmation"] == pytest.approx(
        res["phase2_quality"]["volume_confirmation"]["score"]
    )
    assert scores["pullback_quality"] > 0.0
    assert scores["sweep_quality"] > 0.0


def test_confluence_density_is_weighted_and_soft():
    rich = _res()
    rich["phase2_quality"] = build_phase2_context(
        res=rich,
        candles=_candles(),
        direction="LONG",
        atr=1.0,
        active_zone={"center": 100.0, "scan_count": 1},
        session_quality="high",
        asset_type="crypto",
    )
    sparse = {"current_swing_sequence": "RANGING", "phase2_quality": {}}
    rich_score = compute_confluence_density(rich, "LONG")
    sparse_score = compute_confluence_density(sparse, "LONG")
    assert rich_score["score"] > sparse_score["score"]
    assert rich_score["hard_gate"] is False
    assert set(rich_score["components"]) == set(rich_score["weights"])


def test_adaptive_room_rr_is_bounded_and_requires_phase2_context(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_PHASE2_QUALITY",
        {**config.CONFIG.get("ENGINE_B_PHASE2_QUALITY", {}), "APPLY_ADAPTIVE_ROOM_RR": True},
    )
    res = _res()
    disabled = resolve_adaptive_thresholds(
        {"regime": "TRENDING"}, "LONG", regime="TRENDING", base_min_room=0.5, base_min_rr=1.5
    )
    assert disabled["enabled"] is False
    assert disabled["effective_min_rr"] == pytest.approx(1.5)

    res["phase2_quality"] = {"version": "engine_b.phase2.v1"}
    trending = resolve_adaptive_thresholds(
        res, "LONG", regime="TRENDING", base_min_room=0.5, base_min_rr=1.5
    )
    ranging = resolve_adaptive_thresholds(
        res, "LONG", regime="RANGING", base_min_room=0.5, base_min_rr=1.5
    )
    assert trending["enabled"] is True
    assert trending["effective_min_room_atr"] < 0.5
    assert ranging["effective_min_room_atr"] > 0.5
    assert 1.35 <= trending["effective_min_rr"] <= 1.65
    assert 1.35 <= ranging["effective_min_rr"] <= 1.65

    forex = dict(res, asset_type="forex", d1_adx=12.0)
    adx_ranging = resolve_adaptive_thresholds(
        forex, "LONG", regime="TRENDING", base_min_room=0.5, base_min_rr=1.5
    )
    assert adx_ranging["regime"] == "RANGING"
    assert adx_ranging["effective_min_room_atr"] > 0.5


def test_invalidation_context_warns_on_opposing_structure():
    res = _res()
    res["bos_data"] = {"bos_bull": True, "bos_bear": True}
    out = build_invalidation_context(res, "LONG")
    assert out["h4_invalidation_price"] == 98.0
    assert out["opposing_bos"] is True
    assert out["opposing_structure_warning"] is True
    assert out["time_exit_bars"] == 18

    trigger_warning = build_invalidation_context(
        {**_res(), "bos_data": {"bos_bull": True, "bos_bear": False}, "opposing_trigger_ok": True},
        "LONG",
    )
    assert trigger_warning["opposing_trigger"] is True
    assert trigger_warning["opposing_structure_warning"] is True


def test_phase2_weight_table_remains_normalized():
    weights = config.CONFIG["ENGINE_B_WEIGHTED_SCORING"]["COMPONENT_WEIGHTS"]
    assert sum(float(value) for value in weights.values()) == pytest.approx(1.0)


def test_calculate_confidence_emits_phase2_ranking_without_new_hard_gate():
    res = _res()
    res.update(
        {
            "atr": 1.0,
            "macro_swing_sequence": "HH_HL",
            "bos_mtf_confirmed": False,
            "zone_touched": True,
            "near_active_zone": True,
            "trigger_ok": True,
            "bos_volume_confirmed": True,
            "strong_close": True,
            "inside_break_candle": False,
            "engulfing_candle": False,
            "distance_to_res": 5.0,
            "distance_to_sup": 1.0,
            "recommended_take_profit": 103.0,
            "active_zone_distance": 0.2,
            "bos_volume_source_applicable": False,
        }
    )
    res["phase2_quality"] = build_phase2_context(
        res=res,
        candles=_candles(),
        direction="LONG",
        atr=1.0,
        active_zone={"center": 100.0, "scan_count": 1},
        session_quality="high",
        asset_type="stock",
    )
    res["invalidation_context"] = build_invalidation_context(res, "LONG")

    out = NakedEngine().calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        entry_candles=_candles(),
        style_profile={
            "style": "intraday",
            "score_group": "stock_other",
            "min_score": 0.0,
            "min_room_atr": 0.35,
            "min_rr": 1.3,
            "fallback_rr": 2.0,
            "require_macro_align": False,
            "checklist_mode": "flexible",
        },
    )

    assert out["phase2_adaptive_thresholds"]["enabled"] is True
    assert 0.0 <= out["confluence_density"]["score"] <= 100.0
    assert out["confluence_density"]["hard_gate"] is False
    assert "pullback_quality" in out["quality_components"]
    assert "sweep_quality" in out["quality_components"]
    assert out["invalidation_context"]["h4_invalidation_price"] == 98.0
