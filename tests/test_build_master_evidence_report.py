"""Tests for tools/build_master_evidence_report.py."""

import csv
import json

from tools.build_master_evidence_report import build_group_matrix, build_master_evidence


def test_live_change_allowed_false_on_all_matrix_rows(tmp_path):
    accepted = [
        {
            "engine": "ENGINE_A",
            "asset_class": "forex",
            "score_group": "forex_majors",
            "regime": "TRENDING",
            "style": "intraday",
            "win": True,
            "r_multiple": 1.0,
        }
    ]
    rejected = [
        {
            "engine": "ENGINE_A",
            "asset_class": "forex",
            "score_group": "forex_majors",
            "regime": "TRENDING",
            "style": "intraday",
            "passed": False,
            "failure_reason": "below_threshold",
            "distance_to_threshold": -0.2,
        }
    ]
    matrix = build_group_matrix(accepted, rejected)
    assert matrix
    assert all(row["live_change_allowed"] is False for row in matrix)


def test_build_master_evidence_writes_outputs(tmp_path):
    accepted_json = tmp_path / "accepted.json"
    accepted_json.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "engine": "ENGINE_A",
                        "asset_class": "forex",
                        "score_group": "x",
                        "regime": "TRENDING",
                        "timeframe_style": "intraday",
                        "win": True,
                        "r_multiple": 0.5,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    diag_dir = tmp_path / "diag"
    diag_dir.mkdir()
    (diag_dir / "diagnostic_events_normalized.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "engine": "engine_a",
                        "asset_class": "forex",
                        "score_group": "x",
                        "regime": "TRENDING",
                        "timeframe_style": "intraday",
                        "passed": False,
                        "failure_reason": "below_threshold",
                        "raw_score": 1.0,
                        "threshold": 2.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    out_md = tmp_path / "master.md"
    out_csv = tmp_path / "matrix.csv"
    result = build_master_evidence(
        baseline_dir=tmp_path / "baseline",
        diagnostics_dir=diag_dir,
        accepted_json=accepted_json,
        audit_db=tmp_path / "missing.db",
        output_md=out_md,
        output_csv=out_csv,
    )
    assert result["accepted_count"] == 1
    assert result["rejected_count"] == 1
    assert out_md.exists()
    assert out_csv.exists()
    with out_csv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["live_change_allowed"] == "False"


def test_distance_only_when_score_and_threshold_exist():
    from tools.export_calibration_events import compute_distance_to_threshold

    assert compute_distance_to_threshold(2.0, 2.5) == -0.5
    assert compute_distance_to_threshold(None, 2.0) is None
