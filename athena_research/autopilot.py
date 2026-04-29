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

RESEARCH_STYLE_PROFILES = {
  "scalp": {
    "timeframes": ["M15", "H1"],
    "zone_set": "scalp_zones",
    "strategy_families": [
      "engine_d_proxy", "breakout", "mean_reversion"
    ],
    "validation_focus": ["fast reaction", "tight stop feasibility", "TP1 hit rate", "fee/slippage sensitivity", "session quality"]
  },
  "intra": {
    "timeframes": ["M15", "H1", "H4"],
    "zone_set": "intra_zones",
    "strategy_families": [
      "trend_momentum", "pullback", "breakout", "mean_reversion", "engine_b_proxy"
    ],
    "validation_focus": ["same-day continuation", "intraday reversal", "liquidity sweep into zone", "session continuation", "target room"]
  },
  "swing": {
    "timeframes": ["H4", "D1"],
    "zone_set": "swing_zones",
    "strategy_families": [
      "trend_momentum", "pullback", "breakout", "mean_reversion", "engine_b_proxy"
    ],
    "validation_focus": ["higher timeframe trend", "value area reaction", "support/resistance reaction", "structural continuation", "wider target room"]
  }
}

# Alternative families to try when a family produces all-reject results
_FAMILY_ALTERNATIVES: dict[str, list[str]] = {
    "trend_momentum":  ["pullback", "mean_reversion"],
    "pullback":        ["trend_momentum", "breakout"],
    "breakout":        ["mean_reversion", "volatility"],
    "mean_reversion":  ["volatility", "pullback"],
    "volatility":      ["engine_b_proxy", "mean_reversion"],
    "engine_b_proxy":  ["engine_d_proxy", "breakout"],
    "engine_d_proxy":  ["mean_reversion", "breakout"],
}


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
        
        # Build related symbols from YAML config (single source of truth)
        related_symbols = [sym]
        try:
            from athena_research.run_manager import load_config, get_group_symbols
            from athena_research.data_loader import asset_class_for
            _ac = asset_class_for(sym)
            _cfg = load_config()
            _group_syms = get_group_symbols(_cfg, _ac)
            if _group_syms:
                related_symbols = _group_syms[:8]  # cap at 8 for validation
            if sym not in related_symbols:
                related_symbols.insert(0, sym)
        except Exception:
            pass
            
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

    # 4. Alternative family tests — when majority of results are REJECT/NEEDS_MORE_DATA
    if priority <= max_tests:
        reject_df = df[df["status"].isin(["REJECT", "NEEDS_MORE_DATA"])]
        if len(reject_df) > 0 and len(reject_df) >= len(df) * 0.6:
            # Most tested configs failed — recommend a pivot to a different family
            top_reject = reject_df.groupby("family").size().idxmax() if "family" in reject_df.columns else None
            if top_reject:
                alts = _FAMILY_ALTERNATIVES.get(top_reject, [])
            else:
                alts = []
            if alts:
                alt_fam = alts[0]
                # Use symbols from the run (first symbol found)
                sym_sample = df["symbol"].dropna().unique()[:3].tolist() if "symbol" in df.columns else []
                tf_sample = df["timeframe"].dropna().unique()[:1].tolist() if "timeframe" in df.columns else ["H1"]
                tests.append({
                    "test_id": f"pivot_alt_{alt_fam}_{priority}",
                    "selected_by_default": True,
                    "priority": priority,
                    "title": f"Pivot: Try {alt_fam.replace('_', ' ').title()}",
                    "purpose": f"Majority of {top_reject} strategies rejected. Testing alternative family {alt_fam}.",
                    "test_type": "alternative_pivot",
                    "families": [alt_fam],
                    "strategies": [],
                    "symbols": sym_sample,
                    "timeframes": tf_sample,
                    "directions": ["both"],
                    "mode": "tiny",
                    "max_combinations": max_combinations_per_test,
                    "reason": f"{top_reject} showed mostly rejects — pivoting to {alt_fam} to find edge.",
                    "acceptance_criteria": {
                        "min_trade_count": 15,
                        "min_profit_factor": 1.10,
                        "min_oos_return": 0.0,
                        "min_robustness": 0.35
                    }
                })
                priority += 1

    # Deduplicate tests
    seen_signatures = set()
    deduped_tests = []
    import json
    
    for t in tests:
        sig_data = {
            "families": sorted(t.get("families", [])),
            "strategies": sorted(t.get("strategies", [])),
            "timeframes": sorted(t.get("timeframes", [])),
            "symbols": sorted(t.get("symbols", [])),
            "directions": sorted(t.get("directions", [])),
            "mode": t.get("mode"),
            "acceptance": t.get("acceptance_criteria")
        }
        sig = json.dumps(sig_data, sort_keys=True)
        if sig not in seen_signatures:
            seen_signatures.add(sig)
            deduped_tests.append(t)
            
    tests = deduped_tests

    return {
        "plan_id": plan_id,
        "source_run_id": run_id,
        "planner_mode": planner_mode,
        "recommended": len(tests) > 0,
        "tests": tests
    }
