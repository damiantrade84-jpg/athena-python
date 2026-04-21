"""Diagnose soft gating impact on forex scores."""
from config import CONFIG

fx = CONFIG.get('FOREX_ENGINE', {})
print('Current FOREX_ENGINE soft gating settings:')
print(f'  session_mode: {fx.get("session_mode")}')
print(f'  session_soft_multiplier: {fx.get("session_soft_multiplier")}')
print(f'  session_shoulder_multiplier: {fx.get("session_shoulder_multiplier")}')
print(f'  trend_gate_adx_soft_enabled: {fx.get("trend_gate_adx_soft_enabled")}')
print(f'  trend_gate_adx_hard_fail_min: {fx.get("trend_gate_adx_hard_fail_min")}')
print(f'  trend_gate_adx_soft_full_at: {fx.get("trend_gate_adx_soft_full_at")}')

# Example: if ADX = 20, what multiplier?
hard = fx.get('trend_gate_adx_hard_fail_min', 15)
full = fx.get('trend_gate_adx_soft_full_at', 25)

print('\nADX soft gate multiplier examples:')
for adx in [15, 18, 20, 22, 25, 30]:
    if adx < hard:
        mult = 0
    elif adx < full:
        mult = 0.40 + 0.60 * ((adx - hard) / (full - hard))
    else:
        mult = 1.0
    print(f'  ADX={adx} -> multiplier={mult:.3f}')

print('\nImpact on a raw score of 1.2:')
for adx in [15, 18, 20, 22, 25]:
    if adx < hard:
        mult = 0
    elif adx < full:
        mult = 0.40 + 0.60 * ((adx - hard) / (full - hard))
    else:
        mult = 1.0
    final = 1.2 * mult
    threshold = 0.85
    status = "PASS" if final >= threshold else "FAIL"
    print(f'  ADX={adx} -> 1.2 * {mult:.3f} = {final:.3f} vs threshold 0.85 -> {status}')

print('\nTo DISABLE soft ADX gating (revert to pre-April-16 behavior):')
print('  Set trend_gate_adx_soft_enabled: false in config.yaml')
print('\nTo DISABLE soft session gating:')
print('  Set session_mode: "strict" in config.yaml')
