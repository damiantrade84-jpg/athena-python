"""Purge/embargo leakage tests."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd

import ase_cli
from athena_research.ase.train import (
    ASE_TOTAL_FAMILY_HORIZON_TRIALS,
    MODEL_FAMILIES,
    MODEL_HORIZONS,
)
from athena_research.ase.training_report import write_training_report
from athena_research.ase.walkforward import expanding_folds, labels_leak_into_train, purge_embargo_mask


def test_fold_test_labels_not_in_training():
    df = pd.DataFrame(
        {
            "instrument": ["EURUSD"] * 8,
            "decision_time_ms": list(range(8)),
            "label_start_ms": list(range(8)),
            "label_end_ms": [x + 2 for x in range(8)],
        }
    )
    splits = expanding_folds(df, n_folds=2)
    assert splits
    split = splits[-1]
    purged = purge_embargo_mask(df, train_idx=split.train_idx, test_idx=split.test_idx, max_hold_bars=2)
    assert not labels_leak_into_train(df, train_idx=purged, test_idx=split.test_idx)


def test_training_report_surfaces_dsr_and_bootstrap_metrics():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = write_training_report(
            {
                "version": "v1",
                "trained": [
                    {
                        "family": "forex",
                        "horizon": "intraday",
                        "metrics": {
                            "eval_trades": 40,
                            "eval_expectancy": 0.11,
                            "eval_win_rate": 0.55,
                            "eval_brier": 0.24,
                            "dsr": float("nan"),
                            "bootstrap_lb": 0.03,
                            "thr_family": 0.10,
                            "threshold_fallback": False,
                            "enriched": {"usable": False, "reason": "insufficient"},
                        },
                    },
                ],
                "failed": [],
            },
            Path(temp_dir) / "report.md",
        )

        report = path.read_text(encoding="utf-8")
        assert "| Brier | DSR | Bootstrap LB | threshold |" in report
        assert "| 0.2400 | n/a | 0.0300 | 0.1000 |" in report


def test_dsr_trial_count_matches_family_horizon_grid():
    assert ASE_TOTAL_FAMILY_HORIZON_TRIALS == len(MODEL_FAMILIES) * len(MODEL_HORIZONS)


def test_export_holdout_metrics_writes_gate_json(monkeypatch, capsys):
    def fake_train_family_horizon(family, horizon):
        return {
            "metrics": {
                "holdout_gate_metrics": {
                    "oos_trades": 40,
                    "instruments": 6,
                    "folds_nonneg": 3,
                    "max_instrument_profit_share": 0.35,
                    "max_dd_R": 4.0,
                    "brier_skill": 0.02,
                }
            }
        }

    monkeypatch.setattr(
        "athena_research.ase.train.train_family_horizon",
        fake_train_family_horizon,
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = Path(temp_dir) / "holdout.json"
        args = ase_cli.build_parser().parse_args(
            [
                "export-holdout-metrics",
                "--family",
                "forex",
                "--horizon",
                "intraday",
                "--out",
                str(output_path),
            ]
        )

        assert args.func(args) == 0
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        assert payload["family"] == "forex"
        assert payload["horizon"] == "intraday"
        assert payload["oos_trades"] == 40
        assert json.loads(capsys.readouterr().out)["written"] == str(output_path)
