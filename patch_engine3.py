import sys

with open('c:/Users/damia/OneDrive/Desktop/athena-python/engine_c.py', 'r', encoding='utf-8') as f:
    text = f.read()

import re

# 1. move reliability calculation to the top of compute_consensus
# We need meta_policy early. Well, meta_policy needs regime, direction.
# Wait, meta_policy uses direction, which is unknown until we evaluate A vs B.
# Then instead of repeating the block, I'll write a helper function inside compute_consensus, or just replace the specific sections.

def process_file():
    global text
    
    # helper for decision_state
    helper = '''        if decision_state in ("blocked", "watchlist"):
            tier = "SKIP"
            sizing = 0.0'''
            
    # For A_ONLY:
    a_only_target = '''        if ai_vision:
            result = apply_vision(result, ai_vision)
        return _finalize_consensus(result, asset_type)'''
    # Actually it's easier to find the A_ONLY _build_result
    
    a_only_old = '''        result = _build_result(
            trade=conviction >= CONVICTION_TIERS["LOW"]["min"],
            verdict="A_ONLY",
            direction=direction,
            conviction=conviction,
            tier=tier,
            sizing=sizing,
            a_norm=a, b_norm=b, entry=entry,
            sl=sl_resolved["sl"], sl_method=sl_resolved["method"],
            tp=tp_resolved["tp"], tp_method=tp_resolved["method"],
            rr=tp_resolved["rr"],
        )'''
    a_only_new = '''        # Reliability Layer for A_ONLY
        a_quality = 0.50 # fallback
        a_reliability = (a.get("confidence", 0.5) * 0.5) + (a_quality * 0.5)
        c_reliability = a_reliability # 100% allocation to A
        
        decision_state = "blocked"
        if tier != "SKIP":
            if c_reliability >= 0.60 and conviction >= 0.65:
                decision_state = "execute"
            elif c_reliability >= 0.45 and conviction >= 0.50:
                decision_state = "reduced_risk"
                sizing = max(0.0, sizing - 0.25)
            elif conviction >= 0.40:
                decision_state = "watchlist"
        
        if decision_state in ("blocked", "watchlist"):
            tier = "SKIP"
            sizing = 0.0
            
        result = _build_result(
            trade=decision_state in ("execute", "reduced_risk"),
            verdict="A_ONLY",
            direction=direction,
            conviction=conviction,
            tier=tier,
            sizing=sizing,
            a_norm=a, b_norm=b, entry=entry,
            sl=sl_resolved["sl"], sl_method=sl_resolved["method"],
            tp=tp_resolved["tp"], tp_method=tp_resolved["method"],
            rr=tp_resolved["rr"],
            decision_state=decision_state,
            a_reliability=a_reliability,
            b_reliability=0.0,
            c_reliability=c_reliability,
        )'''
        

    b_only_old = '''        result = _build_result(
            trade=tier != "SKIP",
            verdict="B_ONLY_SCORED" if tier != "SKIP" else "B_ONLY",
            direction=direction,
            conviction=conviction,
            tier=tier,
            sizing=sizing,
            a_norm=a, b_norm=b, entry=entry,
            sl=sl_resolved["sl"], sl_method=sl_resolved["method"],
            tp=tp_resolved["tp"], tp_method=tp_resolved["method"],
            weights={"A": 0.0, "B": 1.0},
            rr=tp_resolved["rr"],
        )'''
    b_only_new = '''        # Reliability Layer for B_ONLY
        b_quality = 0.50 # fallback
        b_struct_rel = 0.5
        if b.get("structure_ok"): b_struct_rel += 0.2
        if b.get("zone_ok"): b_struct_rel += 0.15
        if b.get("trigger_ok"): b_struct_rel += 0.15
        b_reliability = (b_struct_rel * 0.5) + (b_quality * 0.5)
        c_reliability = b_reliability
        
        decision_state = "blocked"
        if tier != "SKIP":
            if c_reliability >= 0.60 and conviction >= 0.65:
                decision_state = "execute"
            elif c_reliability >= 0.45 and conviction >= 0.50:
                decision_state = "reduced_risk"
                sizing = max(0.0, sizing - 0.25)
            elif conviction >= 0.40:
                decision_state = "watchlist"
                
        if decision_state in ("blocked", "watchlist"):
            tier = "SKIP"
            sizing = 0.0
            
        result = _build_result(
            trade=decision_state in ("execute", "reduced_risk"),
            verdict="B_ONLY_SCORED" if tier != "SKIP" else "B_ONLY",
            direction=direction,
            conviction=conviction,
            tier=tier,
            sizing=sizing,
            a_norm=a, b_norm=b, entry=entry,
            sl=sl_resolved["sl"], sl_method=sl_resolved["method"],
            tp=tp_resolved["tp"], tp_method=tp_resolved["method"],
            weights={"A": 0.0, "B": 1.0},
            rr=tp_resolved["rr"],
            decision_state=decision_state,
            a_reliability=0.0,
            b_reliability=b_reliability,
            c_reliability=c_reliability,
        )'''


    b_over_old = '''        if (
            allow_b_override
            and not opposing_high_confidence
            and b["score_norm"] >= b_override_min
            and a["score_norm"] <= a_override_max
        ):
            direction = b["direction"]
            conviction = min(1.0, b["score_norm"] * b_override_penalty)
            tier, sizing = classify_conviction(conviction)

            sl_resolved = resolve_sl(entry, None, b["sl"], direction, atr)
            tp_resolved = resolve_tp(entry, sl_resolved["sl"], None, b["tp"], direction)
            if tp_resolved["rr"] < 1.0:
                tier = "SKIP"
                sizing = 0.0

            result = _build_result(
                trade=tier != "SKIP",
                verdict="B_OVERRIDE_CONFLICT",
                direction=direction,
                conviction=conviction,
                tier=tier,
                sizing=sizing,
                a_norm=a, b_norm=b, entry=entry,
                sl=sl_resolved["sl"], sl_method=sl_resolved["method"],
                tp=tp_resolved["tp"], tp_method=tp_resolved["method"],
                rr=tp_resolved["rr"],
                weights={"A": 0.0, "B": 1.0},
                regime=regime,
                opposing_high_confidence=opposing_high_confidence,
            )'''

    b_over_new = '''        if (
            allow_b_override
            and not opposing_high_confidence
            and b["score_norm"] >= b_override_min
            and a["score_norm"] <= a_override_max
        ):
            direction = b["direction"]
            conviction = min(1.0, b["score_norm"] * b_override_penalty)
            tier, sizing = classify_conviction(conviction)

            sl_resolved = resolve_sl(entry, None, b["sl"], direction, atr)
            tp_resolved = resolve_tp(entry, sl_resolved["sl"], None, b["tp"], direction)
            if tp_resolved["rr"] < 1.0:
                tier = "SKIP"
                sizing = 0.0

            b_quality = 0.50
            b_struct_rel = 0.5
            if b.get("structure_ok"): b_struct_rel += 0.2
            if b.get("zone_ok"): b_struct_rel += 0.15
            if b.get("trigger_ok"): b_struct_rel += 0.15
            b_reliability = (b_struct_rel * 0.5) + (b_quality * 0.5)
            c_reliability = b_reliability

            decision_state = "blocked"
            if tier != "SKIP":
                if c_reliability >= 0.60 and conviction >= 0.65:
                    decision_state = "execute"
                elif c_reliability >= 0.45 and conviction >= 0.50:
                    decision_state = "reduced_risk"
                    sizing = max(0.0, sizing - 0.25)
                elif conviction >= 0.40:
                    decision_state = "watchlist"
                    
            if decision_state in ("blocked", "watchlist"):
                tier = "SKIP"
                sizing = 0.0

            result = _build_result(
                trade=decision_state in ("execute", "reduced_risk"),
                verdict="B_OVERRIDE_CONFLICT",
                direction=direction,
                conviction=conviction,
                tier=tier,
                sizing=sizing,
                a_norm=a, b_norm=b, entry=entry,
                sl=sl_resolved["sl"], sl_method=sl_resolved["method"],
                tp=tp_resolved["tp"], tp_method=tp_resolved["method"],
                rr=tp_resolved["rr"],
                weights={"A": 0.0, "B": 1.0},
                regime=regime,
                opposing_high_confidence=opposing_high_confidence,
                decision_state=decision_state,
                a_reliability=0.0,
                b_reliability=b_reliability,
                c_reliability=c_reliability,
            )'''

    text = text.replace(a_only_old, a_only_new)
    text = text.replace(b_only_old, b_only_new)
    text = text.replace(b_over_old, b_over_new)
    
process_file()

with open('c:/Users/damia/OneDrive/Desktop/athena-python/engine_c.py', 'w', encoding='utf-8') as f:
    f.write(text)
