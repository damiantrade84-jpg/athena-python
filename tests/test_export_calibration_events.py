"""Tests for tools/export_calibration_events.py (JSONL diagnostics export only)."""

from __future__ import annotations

import json

import pytest

import calibration_diagnostics
from calibration_diagnostics import build_engine_a_calibration_row, build_engine_b_calibration_row


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def test_engine_b_quality_floor_is_recorded_as_shadow_when_total_basis_is_active(
    monkeypatch,
):
    monkeypatch.setitem(
        calibration_diagnostics.CONFIG,
        "ENGINE_B_MIN_SCORE_BASIS",
        "total",
    )
    monkeypatch.setitem(
        calibration_diagnostics.CONFIG,
        "ENGINE_B_MIN_QUALITY_RATIO_BY_STYLE",
        {"intraday": 0.35},
    )
    monkeypatch.setattr(
        calibration_diagnostics,
        "_engine_b_regime_multiplier",
        lambda *_args, **_kwargs: 1.0,
    )
    monkeypatch.setattr(
        calibration_diagnostics,
        "_engine_b_level_cohort",
        lambda value: str(value or "unknown"),
    )

    row = build_engine_b_calibration_row(
        conf={
            "score": 5.2,
            "passed": True,
            "quality_score": 0.34,
            "quality_max_possible": 1.0,
            "quality_pct": 34.0,
        },
        style_profile={"style": "intraday", "min_score": 4.5},
        regime_label="RANGING",
        asset_type="forex",
        scaled_min_score=4.5,
        passed=True,
    )

    assert row["passed"] is True
    assert row["min_score_basis"] == "total"
    assert row["quality_ratio"] == pytest.approx(0.34)
    assert row["quality_ratio_threshold"] == pytest.approx(0.35)
    assert row["quality_ratio_would_pass"] is False
    assert row["quality_ratio_gate_active"] is False


def test_valid_jsonl_converts(tmp_path):
    from tools import export_calibration_events

    inp = tmp_path / "events.jsonl"
    rows = [
        build_engine_a_calibration_row(
            pair={"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "score_group": "forex_majors"},
            result={"score": 2.0, "regimeName": "TRENDING"},
            factor_result={"final_score": 2.0, "directional_score": 1.0},
            threshold=2.5,
            passed=False,
            failure_reason="below_threshold",
        ),
        build_engine_b_calibration_row(
            conf={
                "score": 3.5,
                "passed": False,
                "rr": 1.2,
                "level_mode": "atr_sl_fallback_rr_tp",
                "fallback_tp_applied": True,
                "structure_ok": False,
                "failed_gate_names": ["structure_ok"],
            },
            style_profile={"style": "intraday", "min_score": 4.0, "min_rr": 1.5, "score_group": "forex_majors"},
            regime_label="RANGING",
            asset_type="forex",
            scaled_min_score=4.0,
            passed=False,
            diagnostics_context={"pair": "EUR/USD", "symbol": "EURUSD", "asset_type": "forex"},
        ),
    ]
    _write_jsonl(inp, rows)
    out = tmp_path / "out"
    result = export_calibration_events.export_calibration_events(
        input_path=inp,
        output_dir=out,
        also_write_calibration_root=False,
    )

    assert result["normalized_row_count"] == 2
    assert result["malformed_line_count"] == 0
    payload = json.loads((out / "diagnostic_events_normalized.json").read_text(encoding="utf-8"))
    assert len(payload["rows"]) == 2

    engine_a = payload["rows"][0]
    assert engine_a["engine"] == "ENGINE_A"
    assert engine_a["distance_to_threshold"] == pytest.approx(-0.5)
    assert engine_a["engine_a_trend_score"] == 1.0
    assert engine_a["engine_b_level_mode"] is None

    engine_b = payload["rows"][1]
    assert engine_b["engine"] == "ENGINE_B"
    assert engine_b["engine_b_level_mode"] == "atr_sl_fallback_rr_tp"
    assert engine_b["engine_b_fallback_tp_applied"] is True
    assert engine_b["engine_b_structure_ok"] is False
    assert engine_b["engine_a_trend_score"] is None


def test_malformed_lines_reported_non_strict(tmp_path):
    from tools import export_calibration_events

    inp = tmp_path / "bad.jsonl"
    inp.write_text(
        '{"engine": "engine_a", "raw_score": 1.0, "threshold": 2.0, "passed": false}\n'
        "not-json\n"
        '{"engine": "engine_b", "raw_score": 4.0, "threshold": 3.5, "passed": true}\n',
        encoding="utf-8",
    )
    result = export_calibration_events.export_calibration_events(
        input_path=inp,
        output_dir=tmp_path / "out",
        strict=False,
        also_write_calibration_root=False,
    )
    assert result["normalized_row_count"] == 2
    assert result["malformed_line_count"] == 1


def test_strict_mode_raises_on_malformed(tmp_path):
    from tools import export_calibration_events

    inp = tmp_path / "bad.jsonl"
    inp.write_text('{"engine": "engine_a"}\n{broken\n', encoding="utf-8")
    with pytest.raises(ValueError, match="malformed JSONL"):
        export_calibration_events.export_calibration_events(
            input_path=inp,
            output_dir=tmp_path / "out",
            strict=True,
            also_write_calibration_root=False,
        )


def test_missing_optional_fields_become_null(tmp_path):
    from tools import export_calibration_events

    inp = tmp_path / "minimal.jsonl"
    _write_jsonl(inp, [{"engine": "engine_a", "passed": True}])
    result = export_calibration_events.export_calibration_events(
        input_path=inp,
        output_dir=tmp_path / "out",
        also_write_calibration_root=False,
    )
    row = json.loads((tmp_path / "out" / "diagnostic_events_normalized.json").read_text(encoding="utf-8"))[
        "rows"
    ][0]
    assert row["max_score"] is None
    assert row["score_pct"] is None
    assert row["distance_to_threshold"] is None
    assert row["failure_reason"] is None


def test_distance_to_threshold_requires_score_and_threshold():
    from tools.export_calibration_events import compute_distance_to_threshold

    assert compute_distance_to_threshold(2.5, 2.0) == pytest.approx(0.5)
    assert compute_distance_to_threshold(None, 2.0) is None
    assert compute_distance_to_threshold(2.0, None) is None


def test_engine_fields_remain_separate(tmp_path):
    from tools import export_calibration_events

    inp = tmp_path / "events.jsonl"
    _write_jsonl(
        inp,
        [
            {"engine": "engine_a", "raw_score": 2.0, "threshold": 1.5, "passed": True, "trend_score": 1.0},
            {
                "engine": "engine_b",
                "raw_score": 4.0,
                "threshold": 3.5,
                "passed": False,
                "structure_ok": False,
                "fallback_tp_applied": True,
            },
        ],
    )
    export_calibration_events.export_calibration_events(
        input_path=inp,
        output_dir=tmp_path / "out",
        also_write_calibration_root=False,
    )
    rows = json.loads((tmp_path / "out" / "diagnostic_events_normalized.json").read_text(encoding="utf-8"))[
        "rows"
    ]
    assert rows[0]["engine_a_trend_score"] == 1.0
    assert rows[0]["engine_b_structure_ok"] is None
    assert rows[1]["engine_b_structure_ok"] is False
    assert rows[1]["engine_a_trend_score"] is None


def test_export_does_not_call_live_execution(monkeypatch, tmp_path):
    from tools import export_calibration_events

    def _boom(*_args, **_kwargs):
        raise AssertionError("live execution path must not run")

    monkeypatch.setattr("execution.execute_trade", _boom, raising=False)
    monkeypatch.setattr("auto_trader.run_auto_trade_cycle", _boom, raising=False)

    inp = tmp_path / "events.jsonl"
    _write_jsonl(inp, [{"engine": "engine_a", "raw_score": 1.0, "threshold": 2.0, "passed": False}])
    export_calibration_events.export_calibration_events(
        input_path=inp,
        output_dir=tmp_path / "out",
        also_write_calibration_root=False,
    )


def test_summary_csv_files_created(tmp_path):
    from tools import export_calibration_events

    inp = tmp_path / "events.jsonl"
    _write_jsonl(
        inp,
        [
            {"engine": "engine_a", "raw_score": 1.0, "threshold": 2.0, "passed": False, "failure_reason": "low_score"},
            {"engine": "engine_b", "raw_score": 4.0, "threshold": 3.5, "passed": True},
        ],
    )
    out = tmp_path / "out"
    export_calibration_events.export_calibration_events(
        input_path=inp,
        output_dir=out,
        also_write_calibration_root=False,
    )
    for name in (
        "by_engine_failure_reason",
        "by_engine_asset_class_failure_reason",
        "engine_a_distance_to_threshold",
        "engine_b_gate_failures",
        "engine_b_fallback_tp_diagnostics",
    ):
        assert (out / f"{name}.csv").exists()
