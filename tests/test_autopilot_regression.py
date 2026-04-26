"""
Athena Research Lab — Autopilot Regression Tests
Verifies discovery queues, combination counts, and constraints enforcement.
"""

import pytest
import pandas as pd
from pathlib import Path
from athena_research.autopilot import generate_auto_plan

@pytest.fixture
def dummy_run_dir():
    run_dir = Path("logs/research_lab/.tmp_test_parent")
    run_dir.mkdir(parents=True, exist_ok=True)
    
    # Create ranked_strategies.csv
    data = {
        "strategy_name": ["bollinger_touch", "macd_direction", "ema_cross"],
        "symbol": ["BTC/USDT", "ETH/USDT", "EUR/USD"],
        "timeframe": ["H4", "H1", "M15"],
        "family": ["volatility", "trend_momentum", "trend"],
        "direction": ["both", "both", "long"],
        "status": ["STRONG_CANDIDATE", "WEAK_CANDIDATE", "REJECT"],
        "net_return": [0.45, 0.12, 0.05],
        "robustness_score": [0.65, 0.40, 0.20]
    }
    df = pd.DataFrame(data)
    df.to_csv(run_dir / "ranked_strategies.csv", index=False)
    yield run_dir
    
    # Teardown
    try:
        import shutil
        shutil.rmtree(run_dir)
    except Exception:
        pass

def test_auto_plan_generation(dummy_run_dir):
    plan = generate_auto_plan(
        run_id=".tmp_test_parent",
        output_dir=dummy_run_dir.parent,
        planner_mode="balanced",
        max_tests=3,
        max_combinations_per_test=200
    )
    
    assert plan["plan_id"].startswith("plan_")
    assert plan["source_run_id"] == ".tmp_test_parent"
    assert plan["recommended"] is True
    assert len(plan["tests"]) <= 3
    
    # Check default selection
    for t in plan["tests"]:
        assert isinstance(t["selected_by_default"], bool)
        assert t["max_combinations"] <= 200

def test_auto_plan_no_live_leaks(dummy_run_dir):
    plan = generate_auto_plan(
        run_id=".tmp_test_parent",
        output_dir=dummy_run_dir.parent
    )
    plan_str = str(plan)
    # Ensure live config words aren't suggested
    assert "AUTO_TRADE_MIN_SCORE" not in plan_str
    assert "RISK_GATES" not in plan_str

def test_conditional_edges_population():
    # Test internal DataFrame condition logic
    df = pd.DataFrame({
        "strategy_name": ["edge_strat"],
        "symbol": ["BTC/USDT"],
        "timeframe": ["H4"],
        "family": ["volatility"],
        "direction": ["both"],
        "status": ["REJECT"],
        "net_return": [0.45],
        "robustness_score": [0.15]
    })
    edge_cases = df[(df["net_return"] > 0) & (df["status"] == "REJECT")]
    assert not edge_cases.empty
    assert edge_cases.iloc[0]["strategy_name"] == "edge_strat"
def test_backend_duplicate_prevention_flask():
    from flask import Flask
    from athena_research.research_lab_routes import register_research_lab_routes, _running_autopilot_plans
    
    app = Flask(__name__)
    register_research_lab_routes(app)
    
    with app.test_client() as client:
        # Pre-seed the state
        _running_autopilot_plans["mock_parent_default_plan"] = ["child_1", "child_2"]
        
        payload = {
            "source_run_id": "mock_parent",
            "plan_id": "default_plan",
            "tests": []
        }
        
        # Call 1: Intercepted duplicate
        res = client.post("/api/research-lab/run-auto-plan", json=payload)
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "started"
        assert data["child_run_ids"] == ["child_1", "child_2"]
        assert "validation already in progress" in data["message"]

def test_backend_child_ids_returned():
    from flask import Flask
    from athena_research.research_lab_routes import register_research_lab_routes
    
    app = Flask(__name__)
    register_research_lab_routes(app)
    
    with app.test_client() as client:
        payload = {
            "source_run_id": "mock_fresh_parent",
            "plan_id": "fresh_plan",
            "tests": [{"mode": "small", "symbols": ["BTC/USDT"]}]
        }
        
        res = client.post("/api/research-lab/run-auto-plan", json=payload)
        assert res.status_code == 202
        data = res.get_json()
        assert data["status"] == "started"
        assert len(data["child_run_ids"]) == 1
        assert "validation started" in data["message"]
def test_backend_aggregate_autopilot_result():
    from flask import Flask
    from athena_research.research_lab_routes import register_research_lab_routes
    
    app = Flask(__name__)
    register_research_lab_routes(app)
    
    with app.test_client() as client:
        res = client.get("/api/research-lab/autopilot-result?parent_run_id=mock_parent")
        assert res.status_code == 200
        data = res.get_json()
        assert data["parent_run_id"] == "mock_parent"
        assert "child_runs" in data
        assert "aggregate_classification" in data
