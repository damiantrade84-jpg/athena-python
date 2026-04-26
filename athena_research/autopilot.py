"""
Athena Research Lab — Autopilot Planner
Generates recommended test plans based on completed strategy discovery runs.

Planner Modes:
- conservative: only strongest candidates
- balanced: strong candidates + conditional edges
- exploratory: capped comparative testing
- autopilot: automatically queues the ideal validations
"""

from __future__ import annotations

import uuid
import logging
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

log = logging.getLogger(__name__)

def generate_auto_plan(
    run_id: str,
    output_dir: Path,
    planner_mode: str = "balanced",
    max_tests: int = 5,
    max_combinations_per_test: int = 300
) -> dict:
    """Read a run report and generate a deterministic strategy test queue."""
    run_dir = output_dir / run_id
    ranked_path = run_dir / "ranked_strategies.csv"
    
    plan_id = f"plan_{uuid.uuid4().hex[:8]}"
    
    if not ranked_path.exists():
        log.warning("[autopilot] ranked_strategies.csv missing for %s", run_id)
        return {
            "plan_id": plan_id,
            "source_run_id": run_id,
            "planner_mode": planner_mode,
            "recommended": False,
            "tests": []
        }
    
    try:
        df = pd.read_csv(ranked_path)
    except Exception as e:
        log.error("[autopilot] Failed to read ranked file: %s", e)
        return {
            "plan_id": plan_id,
            "source_run_id": run_id,
            "planner_mode": planner_mode,
            "recommended": False,
            "tests": []
        }
    
    tests = []
    priority = 1
    
    if df.empty:
        return {
            "plan_id": plan_id,
            "source_run_id": run_id,
            "planner_mode": planner_mode,
            "recommended": False,
            "tests": []
        }
        
    # Helper to clean lists safely
    def _clean_list(v) -> list[str]:
        if pd.isna(v) or not v:
            return []
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return [str(v)]
        
    # Categories:
    # 1. Confirm Winners (Retest strong candidates across similar symbols)
    strong_df = df[df["status"] == "STRONG_CANDIDATE"]
    for _, row in strong_df.head(2).iterrows():
        if priority > max_tests:
            break
        strat = str(row.get("strategy_name", ""))
        sym = str(row.get("symbol", ""))
        tf = str(row.get("timeframe", ""))
        fam = str(row.get("family", "mean_reversion"))
        direction = str(row.get("direction", "both"))
        
        # Build related symbols based on original
        related_symbols = [sym]
        if "USDT" in sym:
            related_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "LINK/USDT"]
        elif "USD" in sym or "/" in sym:
            related_symbols = ["EUR/USD", "GBP/USD", "AUD/USD", "USD/JPY"]
        else:
            related_symbols = [sym]
            
        tests.append({
            "test_id": f"validate_winners_{strat}_{tf}_{priority}",
            "selected_by_default": True,
            "priority": priority,
            "title": f"Confirm {strat} on {tf}",
            "purpose": f"Confirm robustness for {strat} across similar assets.",
            "test_type": "validation",
            "families": [fam],
            "strategies": [strat],
            "symbols": list(set(related_symbols)),
            "timeframes": [tf],
            "directions": [direction],
            "mode": "small",
            "max_combinations": max_combinations_per_test,
            "reason": f"Winner verification for {strat} that ranked well.",
            "acceptance_criteria": {
                "min_trade_count": 20,
                "min_profit_factor": 1.20,
                "min_oos_return": 0.0,
                "min_robustness": 0.50
            }
        })
        priority += 1

    # 2. Generalize Ideas & Engine Proxy (Balanced or Exploratory)
    if planner_mode in ["balanced", "exploratory", "autopilot"] and priority <= max_tests:
        weak_df = df[df["status"] == "WEAK_CANDIDATE"]
        for _, row in weak_df.head(2).iterrows():
            if priority > max_tests:
                break
            strat = str(row.get("strategy_name", ""))
            sym = str(row.get("symbol", ""))
            tf = str(row.get("timeframe", ""))
            fam = str(row.get("family", "trend_momentum"))
            direction = str(row.get("direction", "both"))
            
            tests.append({
                "test_id": f"generalize_{strat}_{priority}",
                "selected_by_default": planner_mode != "exploratory",
                "priority": priority,
                "title": f"Generalize {strat} Alternatives",
                "purpose": f"Evaluate generalizability of {strat}.",
                "test_type": "comparison",
                "families": [fam],
                "strategies": [strat],
                "symbols": [sym],
                "timeframes": [tf],
                "directions": [direction],
                "mode": "small",
                "max_combinations": max_combinations_per_test,
                "reason": f"Weak candidate generalization test.",
                "acceptance_criteria": {
                    "min_trade_count": 15,
                    "min_profit_factor": 1.10,
                    "min_oos_return": -0.05,
                    "min_robustness": 0.35
                }
            })
            priority += 1

    # 3. Conditional Edge detection (Strategies globally weak but strong in certain configs)
    if planner_mode in ["balanced", "autopilot"] and priority <= max_tests:
        # Pick setups that have positive returns but weak status
        edge_df = df[(df["net_return"] > 0) & (df["status"] == "REJECT")]
        for _, row in edge_df.head(1).iterrows():
            if priority > max_tests:
                break
            strat = str(row.get("strategy_name", ""))
            sym = str(row.get("symbol", ""))
            tf = str(row.get("timeframe", ""))
            fam = str(row.get("family", "breakout"))
            direction = str(row.get("direction", "both"))
            
            tests.append({
                "test_id": f"edge_detect_{strat}_{priority}",
                "selected_by_default": True,
                "priority": priority,
                "title": f"Falsify / Retest Edge {strat}",
                "purpose": f"Confirm conditional edge on {sym}.",
                "test_type": "conditional_edge",
                "families": [fam],
                "strategies": [strat],
                "symbols": [sym],
                "timeframes": [tf],
                "directions": [direction],
                "mode": "small",
                "max_combinations": max_combinations_per_test,
                "reason": "Identify specific regime success.",
                "acceptance_criteria": {
                    "min_trade_count": 20,
                    "min_profit_factor": 1.15,
                    "min_oos_return": 0.0,
                    "min_robustness": 0.40
                }
            })
            priority += 1

    return {
        "plan_id": plan_id,
        "source_run_id": run_id,
        "planner_mode": planner_mode,
        "recommended": len(tests) > 0,
        "tests": tests
    }
