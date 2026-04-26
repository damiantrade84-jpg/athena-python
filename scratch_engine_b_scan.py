import sys
import os
import runpy

root_path = r"c:\Users\damia\OneDrive\Desktop\athena-python"
if root_path not in sys.path:
    sys.path.insert(0, root_path)

print("Initializing athena runtime via runpy...")
try:
    # Set sys.argv to avoid athena executing --scan or starting servers
    original_argv = sys.argv
    sys.argv = ["athena.py"]
    
    # Run athena.py
    runpy.run_path(os.path.join(root_path, "athena.py"), run_name="__main__")
    
    # Restore sys.argv
    sys.argv = original_argv
    print("athena runtime initialized successfully.")
except Exception as e:
    # Note: Flask app running will block, so we need to make sure we don't start the Flask app
    # Wait, does athena.py start Flask app on import/run? 
    # Let's check athena.py bottom lines.
    pass

import athena_runtime
from scanner import scan_candle_limits
from market_structure import NakedEngine, engine_b_min_score_threshold
from indicators import calc_atr, calc_indicators_with_normalized

def main():
    try:
        r = athena_runtime.rt()
        print(f"rt() initialized! ALL_PAIRS count: {len(r.ALL_PAIRS)}")
    except Exception as e:
        print(f"Still failed to get rt(): {e}")
        return
        
    candidate_pairs = [
        p
        for p in r.ALL_PAIRS
        if p["display"] not in r.disabled_pairs
    ]
    
    _lim = scan_candle_limits()
    _engine_b = NakedEngine()
    
    total_scanned = len(candidate_pairs)
    candidates_above_threshold = 0
    blocked_by_rr = 0
    unblocked_by_execution_rr = 0
    final_signals_count = 0
    top_10_unblocked = []

    print(f"Scanning {total_scanned} pairs with Engine B...")

    for pair in candidate_pairs:
        ptype = pair.get("type", "")
        _pair_style = "auto"
        
        # We need candles
        raw_candles = {
            "D1": r.fetch_candles(pair, "D1", _lim["D1"]),
            "H4": r.fetch_candles(pair, "H4", _lim["H4"]),
            "H1": r.fetch_candles(pair, "H1", _lim["H1"]),
        }
        
        d1 = raw_candles.get("D1")
        h4 = raw_candles.get("H4")
        h1 = raw_candles.get("H1")
        
        if d1 and len(d1) > 1:
            d1 = d1[:-1]
        if h4 and len(h4) > 1:
            h4 = h4[:-1]
        if h1 and len(h1) > 1:
            h1 = h1[:-1]
            
        if not h4 or len(h4) < 20:
            continue
            
        _highs = [float(c["high"]) for c in h4]
        _lows = [float(c["low"]) for c in h4]
        _closes = [float(c["close"]) for c in h4]
        atr_series = calc_atr(_highs, _lows, _closes, 14)
        atr = float(atr_series[-1]) if atr_series else 0.0
        current_price = float(h4[-1]["close"])
        
        if not atr or atr <= 0:
            continue
            
        regime_label = r.engine_b_regime_label(h4, ptype)
        from scoring import get_pair_score_group
        _pair_score_group = get_pair_score_group(pair)
        resolved_style_b, style_profile_b = r.naked_scan_style_profile(
            _pair_style, score_group=_pair_score_group
        )
        min_rr = float(style_profile_b.get("min_rr", 1.0))
        
        min_score_scaled = engine_b_min_score_threshold(
            style_profile_b,
            regime_label,
            ptype,
        )

        for direction in ["LONG", "SHORT"]:
            _sc_d1_snap = {}
            _sc_h4_snap = {}
            try:
                _sc_d1_snap = (
                    calc_indicators_with_normalized(d1 or [], ptype) or {}
                ).get("snap") or {}
                _sc_h4_snap = (
                    calc_indicators_with_normalized(h4, ptype) or {}
                ).get("snap") or {}
            except Exception:
                pass
                
            res_b = _engine_b.set_registry_context(
                pair.get("symbol") or pair.get("display")
            ).analyze_structure(
                d1 or [],
                h4,
                h1 or [],
                current_price,
                direction,
                atr,
                regime_label,
                fallback_rr=style_profile_b.get("fallback_rr", 2.0),
                asset_type=ptype,
                d1_snap=_sc_d1_snap,
                h4_snap=_sc_h4_snap,
            )
            
            if res_b.get("structural_verdict") != "CLEAR":
                continue
                
            conf_b = _engine_b.calculate_confidence(
                res_b,
                current_price,
                direction,
                entry_candles=h1 or h4,
                style_profile=style_profile_b,
            )
            
            score = float(conf_b.get("score", 0))
            if score >= min_score_scaled:
                candidates_above_threshold += 1
                
                struct_rr = float(conf_b.get("structural_rr", 0))
                exec_rr = float(conf_b.get("execution_rr", 0))
                passed = bool(conf_b.get("passed", False))
                rr_ok = bool(conf_b.get("rr_ok", False))
                
                if struct_rr < min_rr:
                    blocked_by_rr += 1
                    if exec_rr >= min_rr and rr_ok:
                        unblocked_by_execution_rr += 1
                        top_10_unblocked.append({
                            "symbol": pair.get("display"),
                            "asset_class": ptype,
                            "direction": direction,
                            "style": resolved_style_b,
                            "entry": current_price,
                            "structural_sl": conf_b.get("structural_sl"),
                            "structural_tp": conf_b.get("structural_tp"),
                            "structural_rr": struct_rr,
                            "execution_sl": conf_b.get("execution_sl"),
                            "execution_tp": conf_b.get("execution_tp"),
                            "execution_rr": exec_rr,
                            "rr_used_for_gate": conf_b.get("rr_used_for_gate"),
                            "rr_source": conf_b.get("rr_source"),
                            "min_rr": min_rr,
                            "rr_ok": rr_ok,
                            "final_engine_b_passed": passed,
                        })
                
                if passed:
                    final_signals_count += 1
                    
    print("\n" + "="*40)
    print("ENGINE B RUNTIME SCAN CHECK")
    print("="*40)
    print(f"Total pairs scanned: {total_scanned}")
    print(f"Candidates with score above threshold: {candidates_above_threshold}")
    print(f"Candidates blocked by structural RR: {blocked_by_rr}")
    print(f"Candidates unblocked by execution_rr: {unblocked_by_execution_rr}")
    print(f"Final signals: {final_signals_count}")
    
    print("\nTOP 10 UNBLOCKED EXAMPLES (structural_rr < min_rr, execution_rr >= min_rr):")
    for i, ex in enumerate(top_10_unblocked[:10]):
        print(f"\nExample {i+1}:")
        for k, v in ex.items():
            print(f"  {k}: {v}")

if __name__ == "__main__":
    main()
