import sys

with open('c:/Users/damia/OneDrive/Desktop/athena-python/engine_c.py', 'r', encoding='utf-8') as f:
    text = f.read()

import re

# 1. Update _build_result signature
old_sig = '''def _build_result(
    trade: bool,
    verdict: str,
    direction: Optional[str],
    conviction: float,
    tier: str,
    sizing: float,
    a_norm: dict = None,
    b_norm: dict = None,
    entry: float = 0.0,
    sl: float = None,
    sl_method: str = "",
    tp: float = None,
    tp_method: str = "",
    rr: float = 0.0,
    weights: dict = None,
    regime: str = "",
    opposing_high_confidence: bool = False,
    **kwargs,
) -> dict:'''

new_sig = '''def _build_result(
    trade: bool,
    verdict: str,
    direction: Optional[str],
    conviction: float,
    tier: str,
    sizing: float,
    a_norm: dict = None,
    b_norm: dict = None,
    entry: float = 0.0,
    sl: float = None,
    sl_method: str = "",
    tp: float = None,
    tp_method: str = "",
    rr: float = 0.0,
    weights: dict = None,
    regime: str = "",
    opposing_high_confidence: bool = False,
    decision_state: str = "blocked",
    a_reliability: float = 0.0,
    b_reliability: float = 0.0,
    c_reliability: float = 0.0,
    **kwargs,
) -> dict:'''

if old_sig in text:
    text = text.replace(old_sig, new_sig)

# 2. Update _build_result return dict to include decision states
old_ret = '''        "vision_style_ratings": {},
        "vision_level_suggestions": {},
    }'''

new_ret = '''        "vision_style_ratings": {},
        "vision_level_suggestions": {},
        "decision_state": decision_state,
        "a_reliability": round(a_reliability, 4),
        "b_reliability": round(b_reliability, 4),
        "c_reliability": round(c_reliability, 4),
    }'''
if old_ret in text:
    text = text.replace(old_ret, new_ret)

# Now we need a function to wrap the compute_consensus with reliability
# At the end of compute_consensus before returning, right before "if ai_vision":
old_comp = '''    result = _build_result(
        trade=tier != "SKIP",
        verdict="ALIGNED",
        direction=direction,
        conviction=conviction,
        tier=tier,
        sizing=sizing,
        a_norm=a, b_norm=b, entry=entry,
        sl=sl_resolved["sl"], sl_method=sl_resolved["method"],
        tp=tp_resolved["tp"], tp_method=tp_resolved["method"],
        rr=tp_resolved["rr"],
        weights=weights,
        regime=regime,
        meta_policy=meta_policy,
        meta_base_weights=base_weights,
    )'''

new_comp = '''    # --- Reliability Layer ---
    a_quality = meta_policy.get("engineQualities", {}).get("engine_a", {}).get("quality_score", 0.50)
    b_quality = meta_policy.get("engineQualities", {}).get("engine_b", {}).get("quality_score", 0.50)
    
    a_reliability = (a.get("confidence", 0.5) * 0.5) + (a_quality * 0.5)
    
    b_struct_rel = 0.5
    if b.get("structure_ok"): b_struct_rel += 0.2
    if b.get("zone_ok"): b_struct_rel += 0.15
    if b.get("trigger_ok"): b_struct_rel += 0.15
    b_reliability = (b_struct_rel * 0.5) + (b_quality * 0.5)
    
    c_reliability = (a_reliability * weights["A"]) + (b_reliability * weights["B"])

    decision_state = "blocked"
    if tier != "SKIP":
        if c_reliability >= 0.60 and conviction >= 0.65:
            decision_state = "execute"
        elif c_reliability >= 0.45 and conviction >= 0.50:
            decision_state = "reduced_risk"
            sizing = max(0.0, sizing - 0.25)
        elif conviction >= 0.50:
            decision_state = "watchlist"

    if decision_state in ("blocked", "watchlist"):
        tier = "SKIP"
        sizing = 0.0

    result = _build_result(
        trade=decision_state in ("execute", "reduced_risk"),
        verdict="ALIGNED",
        direction=direction,
        conviction=conviction,
        tier=tier,
        sizing=sizing,
        a_norm=a, b_norm=b, entry=entry,
        sl=sl_resolved["sl"], sl_method=sl_resolved["method"],
        tp=tp_resolved["tp"], tp_method=tp_resolved["method"],
        rr=tp_resolved["rr"],
        weights=weights,
        regime=regime,
        meta_policy=meta_policy,
        meta_base_weights=base_weights,
        decision_state=decision_state,
        a_reliability=a_reliability,
        b_reliability=b_reliability,
        c_reliability=c_reliability,
    )'''

if old_comp in text:
    text = text.replace(old_comp, new_comp)

# Handle vision contradiction to adjust decision_state
old_vis = '''    if action in ("override", "contradict"):
        updated["trade"] = False
        updated["verdict"] = f"VISION_{action.upper()}"
        updated["tier"] = "SKIP"
        updated["sizing_override"] = 0.0'''

new_vis = '''    if action in ("override", "contradict"):
        updated["trade"] = False
        updated["verdict"] = f"VISION_{action.upper()}"
        updated["tier"] = "SKIP"
        updated["sizing_override"] = 0.0
        updated["decision_state"] = "blocked"'''

if old_vis in text:
    text = text.replace(old_vis, new_vis)


with open('c:/Users/damia/OneDrive/Desktop/athena-python/engine_c.py', 'w', encoding='utf-8') as f:
    f.write(text)
