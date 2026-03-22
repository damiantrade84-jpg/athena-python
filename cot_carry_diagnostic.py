"""Quick diagnostic: test COT/Carry for every pair formula."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cot_feed import get_cot_z, _PAIR_FORMULA, _asset_z
from carry_feed import get_carry_z

print("=" * 70)
print("COT/CARRY DIAGNOSTIC — ALL MAPPED PAIRS")
print("=" * 70)

# First test raw currency legs
print("\n── RAW CURRENCY LEG DATA ──")
legs = ["EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "MXN", 
        "BTC", "ETH", "SP500", "NQ100", "XAU", "XAG", "OIL"]
for leg in legs:
    z = _asset_z(leg)
    status = f"{z:+.3f}" if z is not None else "❌ NONE (not seeded)"
    print(f"  {leg:8s}: {status}")

print("\n── PAIR COT + CARRY RESULTS ──")
print(f"{'Pair':<16} {'COT':>8} {'Carry':>8} {'Formula Legs':>15} {'Status'}")
print("-" * 65)

working = 0
partial = 0
dead = 0

for pair, formula in sorted(_PAIR_FORMULA.items()):
    cot = get_cot_z(pair)
    carry = get_carry_z(pair)
    legs_str = "+".join([k for _, k in formula]) if formula else "EMPTY"
    
    if not formula:
        status = "⬜ No formula"
        dead += 1
    elif cot != 0.0:
        status = "✅ Active"
        working += 1
    else:
        # Check which legs failed
        resolved = 0
        for sign, key in formula:
            if _asset_z(key) is not None:
                resolved += 1
        if resolved == 0:
            status = f"❌ 0/{len(formula)} legs resolved"
            dead += 1
        elif resolved < len(formula):
            status = f"⚠️ {resolved}/{len(formula)} legs (partial)"
            partial += 1
        else:
            status = f"🔸 All legs OK but z=0.0 (net cancel)"
            partial += 1
    
    cot_str = f"{cot:+.2f}" if cot != 0.0 else "0.00"
    carry_str = f"{carry:+.2f}" if carry != 0.0 else "0.00"
    print(f"  {pair:<14s} {cot_str:>8} {carry_str:>8} {legs_str:>15} {status}")

print(f"\n── SUMMARY ──")
print(f"  ✅ Active:  {working}")
print(f"  ⚠️ Partial: {partial}")
print(f"  ❌ Dead:    {dead}")
print(f"  Total:     {working + partial + dead}")
