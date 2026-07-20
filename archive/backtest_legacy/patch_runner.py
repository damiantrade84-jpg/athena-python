import os
import re

def patch_scalp_engine():
    with open('scalp_engine.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # Find where levels are evaluated in run_scalp_scan
    # we know it looks like:
    #             levels = calculate_scalp_levels(direction, current_price, vp, setup["setup_type"], sym_info, asset_type, min_rr_override=_min_rr)
    #             if levels.get("rr_below_min"):
    
    fee_guard_code = """
            # --- ENGINE D FEE GUARD ---
            if cfg.get("ENGINE_D_FEE_GUARD_ENABLED", True):
                risk_distance_abs = abs(levels["entry"] - levels["sl"])
                risk_distance_pct = risk_distance_abs / levels["entry"] if levels["entry"] > 0 else 0
                
                estimated_fee_pct = float(cfg.get("ESTIMATED_FEE_PCT", 0.0006))
                estimated_slippage_pct = float(cfg.get("ESTIMATED_SLIPPAGE_PCT", 0.0002))
                estimated_total_cost_pct = estimated_fee_pct + estimated_slippage_pct
                
                cost_as_R = estimated_total_cost_pct / risk_distance_pct if risk_distance_pct > 0 else float('inf')
                
                max_cost_R = float(cfg.get("ENGINE_D_MAX_COST_R", 0.20))
                min_stop_pct = float(cfg.get("ENGINE_D_MIN_STOP_PCT", 0.0005))
                
                if risk_distance_abs <= 0 or risk_distance_pct < min_stop_pct or cost_as_R > max_cost_R:
                    _fee_reason = "fee_guard_micro_stop"
                    log.warning(f"[SCALP] {_fee_reason} on {display}: cost_as_R={cost_as_R:.2f} > {max_cost_R} or stop_pct={risk_distance_pct:.5f} < {min_stop_pct}")
                    _record_stability_sample(display, asset_type, False, reason=_fee_reason)
                    skipped.append({"pair": display, "reason": _fee_reason})
                    _funnel["gate_result"] = "BLOCKED"
                    _funnel["fail_reasons"].append(_fee_reason)
                    _funnel["diagnostic_notes"].update({
                        "engine_d_reject_reason": _fee_reason,
                        "risk_distance_pct": risk_distance_pct,
                        "estimated_total_cost_pct": estimated_total_cost_pct,
                        "cost_as_R": cost_as_R,
                        "min_required_stop_pct": min_stop_pct
                    })
                    continue
"""

    search_str = """            if levels.get("rr_below_min"):
                log.warning(
                    f"[SCALP] {display}: {setup['setup_type']} RR {levels['rr']:.2f} < MIN_RR "
                    f"— skipping (natural TP too close)"
                )
                _record_stability_sample(display, asset_type, False, reason="rr_below_min")
                skipped.append({"pair": display, "reason": "rr_below_min"})
                _funnel["gate_result"] = "BLOCKED"
                _funnel["fail_reasons"].append("rr_below_min")
                continue"""

    if fee_guard_code.strip() not in content and search_str in content:
        content = content.replace(search_str, search_str + "\n" + fee_guard_code)
        with open('scalp_engine.py', 'w', encoding='utf-8') as f:
            f.write(content)
        print("Patched scalp_engine.py")
    else:
        print("Could not find anchor in scalp_engine.py or already patched.")


def patch_backtest_runner():
    with open('backtest_runner.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # Find the trade recording part in backtest_pair_scalp
    # We need to insert logic for tracking partials and fee_R
    
    # We will search for: tp1_hit = (direction == "LONG" and high >= tp1) or (direction == "SHORT" and low <= tp1)
    # But it's better to just do regex to replace the whole execution loop for ENGINE_D if we can.
    print("Finding backtest_pair_scalp boundaries...")
    
    start_idx = content.find("def backtest_pair_scalp")
    end_idx = content.find("def run_full_backtest", start_idx)
    if start_idx == -1 or end_idx == -1:
        print("Could not find backtest_pair_scalp")
        return

    bt_code = content[start_idx:end_idx]

    # Modify exit loop inside backtest_pair_scalp
    # To be extremely precise, I will just dump it to a file so we can view it and modify it directly using python.
    with open('bt_scalp_dump.py', 'w', encoding='utf-8') as f:
        f.write(bt_code)
    print("Dumped backtest_pair_scalp to bt_scalp_dump.py")

patch_scalp_engine()
patch_backtest_runner()
