import os

file_path = r"c:\Users\damia\OneDrive\Desktop\athena-python\risk_engine.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# We want to replace around line 748-753:
#
#    if direction == "LONG" and sl >= entry:
#        log.warning(f"{prefix} REJECTED: LONG stop {sl} must be below entry {entry}")
#        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, dd, "INVALID_LEVELS")
#    if direction == "SHORT" and sl <= entry:
#        log.warning(f"{prefix} REJECTED: SHORT stop {sl} must be above entry {entry}")
#        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, dd, "INVALID_LEVELS")

target_long = '    if direction == "LONG" and sl >= entry:'
target_short = '    if direction == "SHORT" and sl <= entry:'

if target_long in content and target_short in content:
    # Let's find where the SHORT check ends.
    idx = content.find(target_short)
    # Move past the return statement of SHORT check
    idx_ret = content.find('return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, dd, "INVALID_LEVELS")', idx)
    idx_end = content.find("\n", idx_ret) + 1
    
    tp_logic = """
    # TP validation
    tp1 = float(signal.get("tp1") or 0)
    if direction == "LONG" and tp1 > 0 and tp1 <= entry:
        log.warning(f"{prefix} REJECTED: LONG tp1 {tp1} must be above entry {entry}")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, dd, "INVALID_LEVELS")
    if direction == "SHORT" and tp1 > 0 and tp1 >= entry:
        log.warning(f"{prefix} REJECTED: SHORT tp1 {tp1} must be below entry {entry}")
        return RiskApproval(False, 0.0, 0.0, 0.0, 0.0, dd, "INVALID_LEVELS")
"""
    
    new_content = content[:idx_end] + tp_logic + content[idx_end:]
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(new_content)
        
    print("risk_engine.py patched successfully.")
else:
    print("Could not find targets in risk_engine.py.")
