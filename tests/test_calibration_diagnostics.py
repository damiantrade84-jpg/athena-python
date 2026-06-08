import json

import pytest

import scoring
from config import CONFIG
from market_structure import engine_b_confidence_passes


def _engine_a_snaps():
    snap = {
        "close": 100.0,
        "atr": 1.0,
        "adx": 30.0,
        "rsi": 55.0,
        "ema50": 99.0,
        "ema200": 95.0,
        "macdHist": 0.1,
    }
    return {"snap": snap}, {"snap": snap}, {"snap": snap}


def _fake_factor_result(**overrides):
    out = {
        "final_score": 2.2,
        "direction": "LONG",
        "factor_scores": {"trend": 1.4, "momentum": 0.7, "addon": 0.0},
        "weights": {"trend": 1.0, "momentum": 0.7, "addon": 0.0, "base": 0.3},
        "regime": "TRENDING",
        "directional_score": 1.4,
        "nondirectional_score": 0.7,
        "directional_confidence_multiplier": 0.8,
        "min_directional_threshold": 0.25,
        "effective_min_directional": 0.45,
        "active_directional_factors": ["trend"],
        "active_nondirectional_factors": ["momentum"],
        "feed_status": {},
        "adx_multiplier": 1.0,
        "directional_ramp_multiplier": 1.0,
        "engine_a_asset_diagnostics": {
            "volatility": {"atr_pct_scaler": 0.95},
        },
    }
    out.update(overrides)
    return out


def test_calibration_diagnostics_disabled_by_default(tmp_path, monkeypatch):
    from calibration_diagnostics import (
        calibration_diagnostics_enabled,
        record_calibration_diagnostic,
    )

    monkeypatch.setitem(CONFIG, "CALIBRATION_DIAGNOSTICS_ENABLED", False)
    out = tmp_path / "calibration.jsonl"

    assert calibration_diagnostics_enabled() is False
    assert record_calibration_diagnostic({"engine": "engine_a"}, path=out) is False
    assert not out.exists()


def test_enabling_diagnostics_does_not_change_engine_a_score_output(tmp_path, monkeypatch):
    import factor_scoring

    monkeypatch.setattr(
        factor_scoring,
        "compute_factor_scores",
        lambda *args, **kwargs: _fake_factor_result(),
    )
    monkeypatch.setattr(
        scoring,
        "compute_confidence",
        lambda **kwargs: {"confidence": 0.72, "components": {}, "available_count": 1},
        raising=False,
    )
    monkeypatch.setitem(CONFIG, "CALIBRATION_DIAGNOSTICS_PATH", str(tmp_path / "calibration.jsonl"))
    monkeypatch.setitem(CONFIG, "CALIBRATION_DIAGNOSTICS_ENABLED", False)

    d1, h4, h1 = _engine_a_snaps()
    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}
    disabled = scoring.calc_confluence(d1, h4, h1, 1.0, {}, pair, "neutral")

    monkeypatch.setitem(CONFIG, "CALIBRATION_DIAGNOSTICS_ENABLED", True)
    enabled = scoring.calc_confluence(d1, h4, h1, 1.0, {}, pair, "neutral")

    assert enabled == disabled
    rows = [
        json.loads(line)
        for line in (tmp_path / "calibration.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["engine"] == "engine_a"
    assert rows[-1]["threshold"] == pytest.approx(scoring.get_score_threshold(pair, regime="TRENDING"))
    assert rows[-1]["trend_score"] == 1.4
    assert rows[-1]["adx_mult"] == 1.0
    assert rows[-1]["dir_ramp_mult"] == 1.0
    assert rows[-1]["volatility_scaler"] == 0.95
    assert rows[-1]["final_score"] == 2.2


def test_enabling_diagnostics_does_not_change_engine_b_confidence_pass_output(tmp_path, monkeypatch):
    monkeypatch.setitem(CONFIG, "CALIBRATION_DIAGNOSTICS_PATH", str(tmp_path / "calibration.jsonl"))
    style_profile = {"style": "intraday", "min_score": 4.0, "min_rr": 1.5}
    conf = {
        "score": 4.2,
        "passed": True,
        "rr": 1.7,
        "level_mode": "atr_sl_structural_tp",
        "fallback_tp_applied": False,
        "structure_ok": True,
        "location_ok": True,
        "entry_ok": True,
        "space_gate_ok": True,
        "rr_ok": True,
        "macro_ok": True,
        "bonus_points": 1.0,
    }

    monkeypatch.setitem(CONFIG, "CALIBRATION_DIAGNOSTICS_ENABLED", False)
    disabled = engine_b_confidence_passes(conf, style_profile, "TRENDING", "forex")

    monkeypatch.setitem(CONFIG, "CALIBRATION_DIAGNOSTICS_ENABLED", True)
    enabled = engine_b_confidence_passes(
        conf,
        style_profile,
        "TRENDING",
        "forex",
        diagnostics_context={
            "pair": "EUR/USD",
            "symbol": "EURUSD",
            "asset_class": "forex",
            "score_group": "forex_majors",
        },
    )

    assert enabled == disabled
    rows = [
        json.loads(line)
        for line in (tmp_path / "calibration.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["engine"] == "engine_b"
    assert rows[-1]["style"] == "intraday"
    assert rows[-1]["min_score"] == 4.0
    assert rows[-1]["base_min_score"] == 4.0
    assert rows[-1]["regime_multiplier"] == 0.9
    assert rows[-1]["scaled_min_score"] == enabled[1]
    assert rows[-1]["multipliers_enabled"] is True
    assert rows[-1]["passed"] is True


def test_engine_b_level_mode_and_fallback_tp_are_captured(tmp_path, monkeypatch):
    monkeypatch.setitem(CONFIG, "CALIBRATION_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setitem(CONFIG, "CALIBRATION_DIAGNOSTICS_PATH", str(tmp_path / "calibration.jsonl"))

    engine_b_confidence_passes(
        {
            "score": 3.5,
            "passed": False,
            "rr_used_for_gate": 1.2,
            "level_mode": "atr_sl_fallback_rr_tp",
            "fallback_tp_applied": True,
            "failed_gate_names": ["min_score"],
        },
        {"style": "scalp", "min_score": 4.0, "min_rr": 1.5},
        "RANGING",
        "crypto",
        diagnostics_context={"pair": "BTC/USDT", "score_group": "crypto_btc"},
    )

    row = json.loads((tmp_path / "calibration.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["level_mode"] == "atr_sl_fallback_rr_tp"
    assert row["fallback_tp_applied"] is True
    assert row["failure_reason"] == "min_score"


def test_missing_optional_diagnostic_fields_do_not_crash_reporting():
    from calibration_diagnostics import build_calibration_summary

    summary = build_calibration_summary(
        [
            {"engine": "engine_a", "asset_class": "crypto", "passed": False},
            {
                "engine": "engine_b",
                "style": "swing",
                "passed": True,
                "level_cohort": "atr_sl_fallback_rr_tp",
            },
        ]
    )

    assert summary["total_rows"] == 2
    assert summary["by_engine"]["engine_a"] == 1
    assert summary["by_engine"]["engine_b"] == 1
    assert summary["by_engine_b_level_cohort"]["atr_sl_fallback_rr_tp"] == 1


def test_calibration_summary_includes_engine_b_cohort_performance():
    from calibration_diagnostics import build_calibration_summary

    summary = build_calibration_summary(
        [
            {
                "engine": "engine_b",
                "level_cohort": "structural_sl_structural_tp",
                "passed": True,
                "rr_actual": 2.0,
            },
            {
                "engine": "engine_b",
                "level_mode": "atr_sl_fallback_rr_tp",
                "passed": False,
                "rr_actual": 1.0,
            },
            {"engine": "engine_a", "passed": True},
        ]
    )

    perf = summary["engine_b_level_cohort_performance"]
    assert perf["structural_sl_structural_tp"]["count"] == 1
    assert perf["structural_sl_structural_tp"]["passed"] == 1
    assert perf["atr_sl_fallback_rr_tp"]["count"] == 1
    assert perf["atr_sl_fallback_rr_tp"]["failed"] == 1
    assert "engine_a" not in perf


def test_engine_a_calibration_row_reads_directional_ramp_mult_key():
    from calibration_diagnostics import build_engine_a_calibration_row

    row = build_engine_a_calibration_row(
        pair={"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"},
        result={
            "score": 2.0,
            "factorDiagnostics": {"directionalRampMult": 0.85},
        },
        factor_result={},
        threshold=2.5,
        passed=False,
        failure_reason="below_threshold",
    )

    assert row["dir_ramp_mult"] == pytest.approx(0.85)
