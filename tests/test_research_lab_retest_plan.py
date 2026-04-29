"""Retest-plan API: SELECTED keyword and candidate narrowing (no execution imports)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pandas as pd
import pytest
from flask import Flask


@pytest.fixture
def research_output_dir(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="athena_retest_plan_"))
    import athena_research.research_lab_routes as rlr

    monkeypatch.setattr(rlr, "_DEFAULT_OUTPUT", d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _write_sample_recommendations_csv(run_dir: Path) -> None:
    df = pd.DataFrame(
        [
            {
                "recommendation": "RETEST",
                "engine": "ENGINE_B",
                "engine_component": "entry_trigger",
                "strategy_name": "micro_breakout",
                "source_indicator": "x",
                "family": "engine_d_proxy",
                "market_group": "commodity",
                "pair_group": "commodity_other",
                "symbol": "Nat Gas",
                "timeframe_zone": "intra",
                "timeframe": "H4",
                "structure_context": "micro_structure_break",
                "direction": "both",
                "status": "WEAK_CANDIDATE",
                "trade_count": 10,
                "win_rate": 0.5,
                "profit_factor": 1.2,
                "oos_return": 0.01,
                "baseline_delta_pf": 0,
                "baseline_delta_oos": 0,
                "robustness_score": 0.6,
            }
        ]
    )
    df.to_csv(run_dir / "add_remove_retest_recommendations.csv", index=False)


def test_retest_plan_selected_keyword_with_candidates(research_output_dir):
    run_id = "run_retest_plan_fixture"
    run_dir = research_output_dir / run_id
    run_dir.mkdir(parents=True)
    _write_sample_recommendations_csv(run_dir)

    import athena_research.research_lab_routes as rlr

    app = Flask(__name__)
    rlr.register_research_lab_routes(app)
    client = app.test_client()

    resp = client.post(
        "/api/research-lab/retest-plan",
        json={
            "run_id": run_id,
            "recommendations": ["SELECTED"],
            "max_tests": 5,
            "candidates": [
                {
                    "recommendation": "RETEST",
                    "engine": "ENGINE_B",
                    "engine_component": "entry_trigger",
                    "strategy_name": "micro_breakout",
                    "pair_group": "commodity_other",
                    "timeframe_zone": "intra",
                    "structure_context": "micro_structure_break",
                    "direction": "both",
                    "timeframe": "H4",
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data.get("tests"), data
    assert data["tests"][0]["strategies"] == ["micro_breakout"]
    assert data["tests"][0]["symbols"] == ["Nat Gas"]
