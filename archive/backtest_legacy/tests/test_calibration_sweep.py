import copy
import json

import pytest

from config import CONFIG


def test_calibration_sweep_dry_run_outputs_required_metadata(tmp_path):
    from tools import calibration_sweep

    out_dir = tmp_path / "sweep"
    result = calibration_sweep.run(
        [
            "--engine",
            "both",
            "--preset",
            "tiny",
            "--dry-run",
            "--output-dir",
            str(out_dir),
            "--sample-period",
            "2026-01-01:2026-03-31",
            "--split-label",
            "walk_forward_fold_1",
        ]
    )

    json_path = out_dir / "calibration_sweep.json"
    csv_path = out_dir / "calibration_sweep.csv"
    assert result["mode"] == "dry_run"
    assert json_path.exists()
    assert csv_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["metadata"]["research_only"] is True
    assert payload["metadata"]["sample_period"] == "2026-01-01:2026-03-31"
    assert payload["metadata"]["split_label"] == "walk_forward_fold_1"
    assert payload["metadata"]["parameter_variants_tested"] == len(payload["rows"])
    assert payload["metadata"]["ranking_policy"] == "robust_metrics_not_raw_pnl"
    assert payload["rows"]
    required = {
        "engine",
        "asset_class",
        "score_group",
        "symbol",
        "regime",
        "timeframe_style",
        "parameter_set",
        "trade_count",
        "pass_count",
        "fail_count",
        "win_rate",
        "expectancy",
        "max_drawdown",
        "sqn",
        "average_rr_achieved",
        "level_mode",
        "fallback_tp_applied",
    }
    assert required <= set(payload["rows"][0])
    assert payload["metadata"]["warning"]
    row0 = payload["rows"][0]
    assert row0["sample_period"] == "2026-01-01:2026-03-31"
    assert row0["split_label"] == "walk_forward_fold_1"
    assert row0["parameter_variants_tested"] == payload["metadata"]["parameter_variants_tested"]


def test_calibration_sweep_does_not_mutate_config_permanently(tmp_path):
    from tools import calibration_sweep

    before = copy.deepcopy(CONFIG)
    calibration_sweep.run(
        [
            "--engine",
            "both",
            "--preset",
            "tiny",
            "--dry-run",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert CONFIG == before


def test_calibration_sweep_invalid_parameter_ranges_fail_clearly():
    from tools import calibration_sweep

    with pytest.raises(ValueError, match="ENGINE_A directional ramp"):
        calibration_sweep.build_sweep_space(
            "engine_a",
            preset="custom",
            directional_ramp_min=[0.8],
            directional_ramp_full=[0.4],
        )

    with pytest.raises(ValueError, match="Engine B min_rr"):
        calibration_sweep.build_sweep_space(
            "engine_b",
            preset="custom",
            engine_b_min_rr=[0.0],
        )


def test_calibration_sweep_engine_spaces_remain_separate():
    from tools import calibration_sweep

    engine_a = calibration_sweep.build_sweep_space("engine_a", preset="tiny")
    engine_b = calibration_sweep.build_sweep_space("engine_b", preset="tiny")

    assert engine_a
    assert engine_b
    assert all(row["engine"] == "ENGINE_A" for row in engine_a)
    assert all(row["engine"] == "ENGINE_B" for row in engine_b)
    assert all("ENGINE_B_REGIME_MULTIPLIERS" not in row["overrides"] for row in engine_a)
    assert all("ENGINE_A_SCORE_GROUP_THRESHOLDS" not in row["overrides"] for row in engine_b)


def test_calibration_sweep_engine_b_fallback_tp_uses_synthetic_flag():
    from tools import calibration_sweep

    enabled = calibration_sweep.build_sweep_space(
        "engine_b",
        preset="custom",
        engine_b_min_score=[4.0],
        engine_b_min_rr=[1.5],
        min_room_atr=[0.25],
        regime_multipliers=[1.0],
        fallback_tp_enabled=[True],
    )
    disabled = calibration_sweep.build_sweep_space(
        "engine_b",
        preset="custom",
        engine_b_min_score=[4.0],
        engine_b_min_rr=[1.5],
        min_room_atr=[0.25],
        regime_multipliers=[1.0],
        fallback_tp_enabled=[False],
    )

    assert enabled[0]["overrides"]["ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP"] is True
    assert disabled[0]["overrides"]["ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP"] is False


def test_named_research_presets_cover_requested_families():
    from tools import calibration_sweep

    expected = {
        "engine_a_thresholds": "score_group_threshold_default",
        "engine_a_adx": "adx_trend_min",
        "engine_a_directional_ramp": "directional_ramp_min",
        "engine_a_volatility": "volatility_scaler_low",
        "engine_b_min_score": "style_min_score",
        "engine_b_rr": "style_min_rr",
        "engine_b_room": "min_room_atr",
        "engine_b_regime_multiplier": "regime_multiplier",
        "engine_b_fallback_tp": "fallback_tp_enabled",
    }

    for preset, parameter in expected.items():
        engine = "engine_a" if preset.startswith("engine_a") else "engine_b"
        rows = calibration_sweep.build_sweep_space(engine, preset=preset)
        values = {row["parameters"].get(parameter) for row in rows}
        assert len(values) >= 2, (preset, parameter, values)


def test_calibration_sweep_requires_input_json_without_dry_run():
    from tools import calibration_sweep

    with pytest.raises(ValueError, match="input-json"):
        calibration_sweep.run(["--engine", "engine_a", "--preset", "tiny"])


def test_calibration_sweep_applies_threshold_filter_when_scores_exist(tmp_path):
    from tools import calibration_sweep

    input_json = tmp_path / "rows.json"
    input_json.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "engine": "ENGINE_A",
                        "symbol": "EUR/USD",
                        "asset_class": "forex",
                        "score_group": "forex_majors",
                        "regime": "TRENDING",
                        "timeframe_style": "intraday",
                        "score": 1.4,
                        "win": True,
                        "r_multiple": 1.0,
                    },
                    {
                        "engine": "ENGINE_A",
                        "symbol": "EUR/USD",
                        "asset_class": "forex",
                        "score_group": "forex_majors",
                        "regime": "TRENDING",
                        "timeframe_style": "intraday",
                        "score": 1.6,
                        "win": False,
                        "r_multiple": -1.0,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = calibration_sweep.run(
        [
            "--engine",
            "engine_a",
            "--preset",
            "custom",
            "--engine-a-thresholds",
            "1.5",
            "--engine-a-directional-min",
            "0.25",
            "--engine-a-directional-full",
            "0.45",
            "--engine-a-adx-trend-min",
            "25",
            "--engine-a-adx-hard-fail",
            "15",
            "--engine-a-volatility-low",
            "0.005",
            "--engine-a-volatility-high",
            "0.025",
            "--input-json",
            str(input_json),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["threshold_filter_applied"] is True
    assert row["threshold_candidate"] == pytest.approx(1.5)
    assert row["raw_source_count"] == 2
    assert row["admitted_count"] == 1
    assert row["excluded_by_threshold_count"] == 1
    assert row["trade_count"] == 1
    assert row["expectancy"] == pytest.approx(-1.0)
