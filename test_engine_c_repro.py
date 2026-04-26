import sys
sys.path.insert(0, '.')

from engine_c import compute_consensus, normalise_engine_a, normalise_engine_b, _build_disagreement_diagnosis

# Test: passed=False -> has_signal=False -> b_has=False -> enters else branch
# In else branch: not b_struct_ok -> (signal_b.get("engine_b_diagnostics") or {}).get(...)
# If engine_b_diagnostics is a string -> CRASH

signal_a = {
    "confluenceScore": 1.5, "maxScore": 3.0, "direction": "LONG",
    "regime": {"label": "TRENDING"}, "sl": 100, "tp1": 110, "tp2": 115,
    "rr1": 1.5, "factor_scores": {"trend": 1.0}
}
signal_b = {
    "structural_verdict": "CLEAR", "direction": "LONG",
    "score": 3.0, "pct": 60.0, "passed": False,  # passed=False -> b_has=False
    "recommended_stop_loss": 99, "recommended_take_profit": 111,
    "structure_ok": False,
    "zone_ok": False,
    "trigger_ok": False,
    "entry_ok": True, "rr_ok": True, "rr": 1.5,
    "bos_mtf_confirmed": True, "ob_at_zone": True,
    "engine_b_diagnostics": "some_string",  # string instead of dict
}
conf_b = {"score": 3, "max_possible": 5, "pct": 60, "passed": False,
          "structure_ok": False, "zone_ok": False, "trigger_ok": False}

print("Test 1: _build_disagreement_diagnosis with b_has=False, b_struct_ok=False, string engine_b_diagnostics")
try:
    a = normalise_engine_a(signal_a)
    b = normalise_engine_b(signal_b, conf_b)
    print(f"  a_has={a['has_signal']}, b_has={b['has_signal']}")
    diag = _build_disagreement_diagnosis("B_ONLY", a, b, signal_a, signal_b, "TRENDING")
    print("  OK")
except Exception as e:
    import traceback
    traceback.print_exc()
    print("  FAIL:", type(e).__name__, e)

print("\nTest 2: compute_consensus with B_ONLY where b_has=False (should not crash at disagreement)")
try:
    # For B_ONLY: a_has=False, b_has=True... but we need the scenario where
    # the function is called with b_has=False. This happens when verdict is B_ONLY
    # but b_has is actually True in normalise. Let me just test NO_SIGNAL.
    result = compute_consensus(signal_a, signal_b, conf_b, asset_type="crypto", regime="TRENDING")
    print("  OK:", result["verdict"])
except Exception as e:
    import traceback
    traceback.print_exc()
    print("  FAIL:", type(e).__name__, e)
