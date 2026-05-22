import ast
from pathlib import Path
from typing import Any

import pytest

from ai_review.engine_a_panel_adapter import FIELD_SPECS, build_engine_a_visual_audit_payload


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IMPORT_ROOTS = frozenset({
    "factor_scoring",
    "scoring",
    "scanner",
    "engine_c",
    "execution",
    "risk_engine",
    "guardian",
    "auto_trader",
    "mt5_executor",
    "bybit_executor",
})


def _synthetic_engine_a_result() -> dict:
    return {
        "final_score": 2.14,
        "direction": "LONG",
        "regime": "TRENDING",
        "factor_scores": {
            "trend": 1.72,
            "momentum": 0.64,
            "addon": 0.2,
            "research_lab": 0.05,
            "mean_reversion": -0.01,
        },
        "weights": {"trend": 1.0, "momentum": 0.55, "addon": 0.2, "base": 0.25},
        "engine_a_asset_diagnostics": {
            "directional_ramp": {
                "min_directional": 0.35,
                "soft_span": 0.15,
                "multiplier": 0.9,
            },
            "volatility": {
                "atr_pct_scaler": 0.97,
                "regime_multiplier": {"enabled": True, "multiplier": 0.88},
            },
        },
        "trend_coherence": {"coherence_ratio": 0.67, "d1": "LONG", "h4": "LONG"},
        "directional_score": 1.72,
        "unweighted_directional_sum": 1.72,
        "adx_value": 24.5,
        "adx_source": "H4",
        "adx_multiplier": 0.8,
        "momentum_quality": 0.64,
        "addon_type": "funding",
        "addon_value": 0.2,
        "research_lab_value": 0.05,
        "research_lab_detail": {"enabled": True, "strategy": "pullback"},
        "mean_reversion_value": -0.01,
        "mean_reversion_detail": {"enabled": True, "state": "extended"},
        "addon_unsupported": False,
        "session_multiplier": 1.0,
        "equity_session_multiplier": 1.0,
        "volatility_regime_multiplier": 0.88,
        "directional_ramp_multiplier": 0.9,
        "effective_min_directional": 0.5,
        "min_directional_threshold": 0.35,
        "intermarket_confirmation": {"verdict": "confirming", "engineADelta": 0.02},
        "intermarket_engine_a_delta": 0.02,
        "structure_context_adjustment": {"enabled": True, "applied": False},
        "engine_a_correlated_overlay_guard": {"enabled": True, "warning": False},
        "feed_status": {
            "addon": "funding:ok",
            "adx": "H4",
            "di_align": "1.00",
            "session": "1.00",
            "vol_scaler": "0.97",
            "vol_regime": "0.88",
            "vwap_filter": "0.92",
        },
        "filtered_indicators": {
            "h4_rsi": 58.0,
            "h4_macd_hist": 0.12,
            "volume_ratio": 1.7,
            "microstructure_exchange": "bybit",
        },
    }


def _synthetic_signal() -> dict:
    return {
        "symbol": "BTCUSDT",
        "display": "BTC/USDT",
        "direction": "LONG",
        "confluenceScore": 2.14,
        "maxScore": 3.0,
        "threshold": 2.0,
        "price": 65000.0,
        "sl": 64200.0,
        "tp1": 66600.0,
        "rr1": 2.0,
        "atr": 800.0,
        "atrDiagnostics": {"atr_tf": "H4", "atr_source": "bybit"},
        "factorScores": {
            "trend": 1.72,
            "momentum": 0.64,
            "addon": 0.2,
            "research_lab": 0.05,
            "mean_reversion": -0.01,
        },
        "factorDiagnostics": {
            "momentumQuality": 0.64,
            "adxMultiplier": 0.8,
            "directionalRampMult": 0.9,
            "diAlignMult": 1.0,
            "feedStatus": {"di_align": "1.00"},
        },
    }


def _value_at_path(payload: dict, path: str) -> Any:
    cur: Any = payload
    for part in path.split("."):
        cur = cur[part]
    return cur


def test_adapter_maps_current_compute_factor_scores_fields() -> None:
    payload = build_engine_a_visual_audit_payload(
        _synthetic_engine_a_result(),
        _synthetic_signal(),
    )

    assert payload["engine"] == "A"
    assert payload["symbol"] == "BTCUSDT"
    assert payload["direction"] == "LONG"
    assert payload["final_score"] == 2.14
    assert payload["max_score"] == 3.0
    assert payload["threshold"] == 2.0

    assert payload["trend_block"]["trend_score"] == 1.72
    assert payload["trend_block"]["trend_coherence"]["coherence_ratio"] == 0.67
    assert payload["trend_block"]["directional_ramp_multiplier"] == 0.9
    assert payload["trend_block"]["min_directional_threshold"] == 0.35
    assert payload["trend_block"]["effective_min_directional"] == 0.5

    assert payload["momentum_block"]["momentum_quality"] == 0.64
    assert payload["momentum_block"]["filtered_indicators"]["volume_ratio"] == 1.7
    assert payload["adx_di_block"]["adx_value"] == 24.5
    assert payload["adx_di_block"]["adx_source"] == "H4"
    assert payload["adx_di_block"]["adx_multiplier"] == 0.8
    assert payload["adx_di_block"]["di_alignment_multiplier"] == 1.0

    assert payload["addon_block"]["addon_type"] == "funding"
    assert payload["addon_block"]["addon_value"] == 0.2
    assert payload["addon_block"]["addon_unsupported"] is False
    assert payload["addon_block"]["research_lab_value"] == 0.05
    assert payload["addon_block"]["status"] == "CONFIRMING"

    assert payload["multiplier_chain"]["session_multiplier"] == 1.0
    assert payload["multiplier_chain"]["equity_session_multiplier"] == 1.0
    assert payload["multiplier_chain"]["volatility_regime_multiplier"] == 0.88
    assert payload["multiplier_chain"]["directional_ramp_multiplier"] == 0.9
    assert payload["multiplier_chain"]["vwap_filter"] == 0.92
    assert payload["multiplier_chain"]["volatility_scaler"] == 0.97

    assert payload["adjustments"]["research_lab_detail"]["enabled"] is True
    assert payload["adjustments"]["mean_reversion_value"] == -0.01
    assert payload["adjustments"]["intermarket_engine_a_delta"] == 0.02
    assert payload["risk_block"]["entry"] == 65000.0
    assert payload["risk_block"]["tp"] == 66600.0
    assert payload["risk_block"]["atr_timeframe"] == "H4"
    assert payload["source_field_map"]["trend_block.trend_score"] == "engine_a_result.factor_scores.trend"


def test_directional_ramp_multiplier_uses_single_source_for_both_blocks() -> None:
    payload = build_engine_a_visual_audit_payload(
        _synthetic_engine_a_result(),
        _synthetic_signal(),
    )

    trend_source = payload["source_field_map"]["trend_block.directional_ramp_multiplier"]
    chain_source = payload["source_field_map"]["multiplier_chain.directional_ramp_multiplier"]
    assert trend_source == chain_source == "engine_a_result.directional_ramp_multiplier"
    assert payload["trend_block"]["directional_ramp_multiplier"] == payload["multiplier_chain"]["directional_ramp_multiplier"]


def test_populated_fields_have_source_field_map_entries() -> None:
    payload = build_engine_a_visual_audit_payload(
        _synthetic_engine_a_result(),
        _synthetic_signal(),
    )
    source_map = payload["source_field_map"]
    tracked_paths = {spec.output_path for spec in FIELD_SPECS} | {
        "engine",
        "max_score",
        "addon_block.feed_status",
        "addon_block.status",
        "adx_di_block.di_alignment_multiplier",
        "multiplier_chain.directional_ramp_multiplier",
        "multiplier_chain.vwap_filter",
        "multiplier_chain.volatility_scaler",
    }

    for path in sorted(tracked_paths):
        value = _value_at_path(payload, path)
        if value is None or value == {}:
            continue
        assert path in source_map, f"missing source_field_map entry for populated field {path}"
        assert source_map[path], f"empty source for populated field {path}"


def test_unavailable_fields_match_null_outputs() -> None:
    payload = build_engine_a_visual_audit_payload({"final_score": 0.0, "direction": None})
    unavailable = set(payload["unavailable_fields"])

    assert payload["timeframe"] is None
    assert "timeframe" in unavailable
    assert "addon_block.addon_value" in unavailable
    assert "risk_block.entry" in unavailable
    assert "timeframe" not in payload["source_field_map"]


@pytest.mark.parametrize(
    ("addon_value", "addon_unsupported", "feed_addon", "expected"),
    [
        (0.2, False, "funding:ok", "CONFIRMING"),
        (-0.1, False, "funding:ok", "OPPOSING"),
        (0.0, False, "funding:ok", "NEUTRAL"),
        (0.0, True, "cot_proxy:unsupported", "UNAVAILABLE"),
        (None, False, "funding:ok", "UNAVAILABLE"),
        (0.1, False, "funding:error", "ERROR"),
        (0.1, False, "cot_proxy:unsupported", "UNAVAILABLE"),
    ],
)
def test_addon_status_matrix(addon_value, addon_unsupported, feed_addon, expected) -> None:
    engine_a_result = {
        "final_score": 1.0,
        "direction": "LONG",
        "addon_value": addon_value,
        "addon_unsupported": addon_unsupported,
        "feed_status": {"addon": feed_addon},
    }
    payload = build_engine_a_visual_audit_payload(engine_a_result)
    assert payload["addon_block"]["status"] == expected


def test_missing_fields_are_unavailable_not_neutral() -> None:
    payload = build_engine_a_visual_audit_payload({"final_score": 0.0, "direction": None})

    assert payload["timeframe"] is None
    assert payload["addon_block"]["status"] == "UNAVAILABLE"
    assert "timeframe" in payload["unavailable_fields"]
    assert "addon_block.addon_value" in payload["unavailable_fields"]
    assert "risk_block.entry" in payload["unavailable_fields"]
    assert payload["addon_block"]["status"] != "NEUTRAL"


def test_addon_unsupported_is_unavailable_not_neutral() -> None:
    engine_a_result = _synthetic_engine_a_result()
    engine_a_result.update(
        {
            "addon_type": "cot_proxy",
            "addon_value": 0.0,
            "addon_unsupported": True,
            "feed_status": {"addon": "cot_proxy:unsupported"},
        }
    )

    payload = build_engine_a_visual_audit_payload(engine_a_result)

    assert payload["addon_block"]["addon_value"] == 0.0
    assert payload["addon_block"]["addon_unsupported"] is True
    assert payload["addon_block"]["status"] == "UNAVAILABLE"


def test_adapter_does_not_create_additive_weighted_contributions() -> None:
    payload = build_engine_a_visual_audit_payload(
        _synthetic_engine_a_result(),
        _synthetic_signal(),
    )
    payload_text = repr(payload)

    assert "weighted_contribution" not in payload_text
    assert "weightedContribution" not in payload_text
    assert "raw_score" not in payload
    assert "rawScore" not in payload


def test_field_specs_cover_contract_output_paths() -> None:
    spec_paths = {spec.output_path for spec in FIELD_SPECS}
    required = {
        "symbol", "timeframe", "direction", "final_score", "threshold",
        "trend_block.trend_score", "trend_block.trend_coherence",
        "trend_block.directional_ramp_multiplier", "trend_block.min_directional_threshold",
        "trend_block.effective_min_directional", "momentum_block.momentum_quality",
        "momentum_block.filtered_indicators", "adx_di_block.adx_value",
        "adx_di_block.adx_source", "adx_di_block.adx_multiplier",
        "addon_block.addon_value", "addon_block.addon_type", "addon_block.addon_unsupported",
        "addon_block.research_lab_value", "multiplier_chain.session_multiplier",
        "multiplier_chain.equity_session_multiplier", "multiplier_chain.volatility_regime_multiplier",
        "adjustments.research_lab_detail", "adjustments.mean_reversion_value",
        "adjustments.mean_reversion_detail", "adjustments.intermarket_confirmation",
        "adjustments.intermarket_engine_a_delta", "adjustments.structure_context_adjustment",
        "adjustments.engine_a_correlated_overlay_guard", "risk_block.entry", "risk_block.sl",
        "risk_block.tp", "risk_block.atr", "risk_block.atr_timeframe", "risk_block.rr",
    }
    assert required.issubset(spec_paths)


def test_adapter_module_has_no_scoring_or_execution_imports() -> None:
    for rel_path in ("engine_a_panel_adapter.py", "payload_mapping.py"):
        tree = ast.parse((ROOT / "ai_review" / rel_path).read_text(encoding="utf-8"))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
        assert imported_roots.isdisjoint(FORBIDDEN_IMPORT_ROOTS), f"{rel_path} imports {imported_roots & FORBIDDEN_IMPORT_ROOTS}"


def test_execution_modules_do_not_import_panel_adapter() -> None:
    forbidden_files = [
        "factor_scoring.py",
        "scoring.py",
        "scanner.py",
        "engine_c.py",
        "execution.py",
        "risk_engine.py",
        "guardian.py",
        "auto_trader.py",
        "mt5_executor.py",
        "bybit_executor.py",
    ]
    needles = ("engine_a_panel_adapter", "build_engine_a_visual_audit_payload")
    for rel_path in forbidden_files:
        text = (ROOT / rel_path).read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text, f"{needle} found in {rel_path}"
