"""Engine A and Engine B feature attribution audit.
Read-only — no strategy changes, no threshold changes.
"""
import json
import sys
import csv
import math
from collections import defaultdict
from statistics import mean, stdev

sys.stdout.reconfigure(encoding="utf-8")
MATRIX_FILE = "logs/backtest_matrix/bt_results_strategy_lab.json"
OUT_DIR = "logs/strategy_lab"

import os
os.makedirs(OUT_DIR, exist_ok=True)

# ─── load ────────────────────────────────────────────────────────────────────
with open(MATRIX_FILE) as f:
    ALL = json.load(f)

EA = [r for r in ALL if r.get("engine") == "ENGINE_A"]
EB = [r for r in ALL if r.get("engine") == "ENGINE_B"]


# ─── helpers ─────────────────────────────────────────────────────────────────
def _stats(trades):
    if not trades:
        return dict(n=0, wr=0.0, pf=0.0, avg_r=0.0, avg_win=0.0,
                    avg_loss=0.0, max_dd=0.0, best=0.0, worst=0.0)
    rs = [r["resultR"] for r in trades if r.get("resultR") is not None]
    if not rs:
        return dict(n=0, wr=0.0, pf=0.0, avg_r=0.0, avg_win=0.0,
                    avg_loss=0.0, max_dd=0.0, best=0.0, worst=0.0)
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    pf = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    # running max-dd in R
    peak = cum = 0.0
    max_dd = 0.0
    for x in rs:
        cum += x
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return dict(
        n=len(rs),
        wr=round(len(wins) / len(rs), 4),
        pf=round(pf, 4),
        avg_r=round(mean(rs), 4),
        avg_win=round(mean(wins), 4) if wins else 0.0,
        avg_loss=round(mean(losses), 4) if losses else 0.0,
        max_dd=round(max_dd, 4),
        best=round(max(rs), 4),
        worst=round(min(rs), 4),
    )


def _group(trades, key_fn):
    groups = defaultdict(list)
    for t in trades:
        groups[key_fn(t)].append(t)
    return {k: _stats(v) for k, v in sorted(groups.items())}


def _uplift(present, absent):
    pa = present.get("avg_r", 0)
    aa = absent.get("avg_r", 0)
    pp = present.get("pf", 0)
    ap = absent.get("pf", 0)
    return round(pa - aa, 4), round(pp - ap, 4)


def _classify(present, absent):
    n_p = present.get("n", 0)
    n_a = absent.get("n", 0)
    conf = "HIGH" if min(n_p, n_a) >= 100 else ("MEDIUM" if min(n_p, n_a) >= 30 else "LOW")
    if conf == "LOW" or n_p < 5:
        return "INSUFFICIENT_SAMPLE", conf
    da, dpf = _uplift(present, absent)
    pf_p = present.get("pf", 0)
    ar_p = present.get("avg_r", 0)
    if pf_p >= 1.10 and ar_p > 0 and conf in ("MEDIUM", "HIGH"):
        return "KEEP_STRONG_EDGE", conf
    if pf_p >= 1.00 and ar_p >= 0 and conf in ("MEDIUM", "HIGH"):
        return "KEEP_WEAK_EDGE", conf
    if 0.90 <= pf_p < 1.00 and abs(ar_p) < 0.05:
        return "NEUTRAL", conf
    if pf_p < 0.85 and ar_p < 0 and conf in ("MEDIUM", "HIGH"):
        return "HARMFUL", conf
    return "NEUTRAL", conf


# ═══════════════════════════════════════════════════════════════════════════════
# PART 1 — Field availability map
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("PART 1 — FIELD AVAILABILITY MAP")
print("="*70)

DESIRED_EA = [
    "score","factors","factor_weights","regime","direction","timeframe",
    "asset_group","pair","exit_reason","outcome","resultR",
    "tp1_rr","tp2_rr","max_favorable_excursion_r","max_adverse_excursion_r",
    "bars_to_mfe","bars_to_mae","reached_half_r","reached_one_r","reached_tp1",
    "oos","wf_fold","volAdj",
]
DESIRED_EB = [
    "score","regime","direction","timeframe","asset_group","pair",
    "exit_reason","outcome","resultR","r_multiple","rr_target",
    "liquidity_sweep","fvg_overlap","trigger_pattern","zone_touched",
    "bos_volume_confirmed","choch_confirmed","ob_at_zone","bos_mtf_confirmed",
    "breaker_active","ob_strength","fvg_bonus","volume_strength",
    "selected_tp_source","selected_sl_source","oos",
]
MISSING_EA = ["rsi","macd","ema_alignment","adx","atr_pct","bb_width",
              "volume_ratio","session","carry","cot","funding_rate",
              "hurst","swing_sequence","trend_score_raw"]
MISSING_EB = ["bos_confirmed","bos_direction","fvg_present","fvg_unmitigated",
              "sweep_direction","checklist_structure","checklist_location",
              "checklist_trigger","checklist_room","checklist_macro",
              "checklist_rr","adx","session","d1_conflict"]

availability = []

def _field_avail(records, field, engine):
    present = [r for r in records if r.get(field) is not None]
    missing = len(records) - len(present)
    samples = []
    for r in present[:3]:
        v = r[field]
        samples.append(repr(v)[:40])
    return {
        "field": field, "engine": engine,
        "present_count": len(present), "missing_count": missing,
        "sample_values": samples,
        "usable": "yes" if len(present) > 10 else "no",
    }

for f in DESIRED_EA:
    availability.append(_field_avail(EA, f, "ENGINE_A"))
for f in DESIRED_EB:
    availability.append(_field_avail(EB, f, "ENGINE_B"))

print(f"{'Field':<35} {'Engine':<12} {'Present':>8} {'Missing':>8} {'Usable'}")
for a in availability:
    print(f"  {a['field']:<33} {a['engine']:<12} {a['present_count']:>8} {a['missing_count']:>8}  {a['usable']}")

print("\n⚠️  FIELDS MISSING FROM MATRIX (would need re-run with feature snapshots):")
print("  ENGINE_A missing:", ", ".join(MISSING_EA))
print("  ENGINE_B missing:", ", ".join(MISSING_EB))

with open(f"{OUT_DIR}/strategy_lab_feature_availability.json","w",encoding="utf-8") as f:
    json.dump({"available": availability,
               "missing_engine_a": MISSING_EA,
               "missing_engine_b": MISSING_EB}, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# PART 2 — Engine A factor attribution
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("PART 2 — ENGINE A FACTOR ATTRIBUTION")
print("="*70)

ea_attr = {}

# 2a. Score buckets — derive threshold from bt_min per asset_group if possible
# Use config default ENGINE_A thresholds: we approximate threshold=1.2 for forex,
# 1.1 for stock/commodity, 1.05 for index, 1.2 for crypto. Or simply flag >threshold
# We'll use the conservative floor of 1.05 as a single threshold proxy.
_EA_THRESH = 1.05  # conservative floor — real analysis uses per-class

def _score_margin(r):
    s = r.get("score", 0) or 0
    return s - _EA_THRESH

def _score_bucket(r):
    m = _score_margin(r)
    if m < 0:
        return "below_threshold"
    if m < 0.05:
        return "0-5%_above"
    if m < 0.10:
        return "5-10%_above"
    if m < 0.20:
        return "10-20%_above"
    return ">20%_above"

ea_attr["score_margin_buckets"] = _group(EA, _score_bucket)

def _raw_score_bucket(r):
    s = r.get("score", 0) or 0
    if s < 0.5: return "0.0-0.5"
    if s < 1.0: return "0.5-1.0"
    if s < 1.5: return "1.0-1.5"
    if s < 2.0: return "1.5-2.0"
    if s < 2.5: return "2.0-2.5"
    if s < 3.0: return "2.5-3.0"
    return ">3.0"

ea_attr["raw_score_buckets"] = _group(EA, _raw_score_bucket)

def _norm_score_bucket(r):
    s = r.get("score", 0) or 0
    pct = min(s / 3.0, 1.0) * 100
    if pct < 25: return "0-25%"
    if pct < 40: return "25-40%"
    if pct < 50: return "40-50%"
    if pct < 60: return "50-60%"
    if pct < 70: return "60-70%"
    if pct < 85: return "70-85%"
    return "85-100%"

ea_attr["normalized_score_buckets"] = _group(EA, _norm_score_bucket)

# 2b. Factor scores
def _trend_bucket(r):
    f = (r.get("factors") or {}).get("trend")
    if f is None: return "missing"
    f = float(f)
    if f <= -2.0: return "strong_bear(<=-2)"
    if f < -1.0: return "bear(-2_to_-1)"
    if f < 0: return "weak_bear(-1_to_0)"
    if f == 0: return "neutral(0)"
    if f <= 1.0: return "weak_bull(0_to_1)"
    if f <= 2.0: return "bull(1_to_2)"
    return "strong_bull(>2)"

ea_attr["trend_factor_buckets"] = _group(EA, _trend_bucket)

def _mom_bucket(r):
    f = (r.get("factors") or {}).get("momentum")
    if f is None: return "missing"
    f = float(f)
    if f < 0.2: return "weak(<0.2)"
    if f < 0.4: return "low(0.2-0.4)"
    if f < 0.6: return "medium(0.4-0.6)"
    if f < 0.8: return "high(0.6-0.8)"
    return "very_high(>=0.8)"

ea_attr["momentum_factor_buckets"] = _group(EA, _mom_bucket)

def _addon_bucket(r):
    f = (r.get("factors") or {}).get("addon")
    if f is None: return "missing"
    f = float(f)
    if f < -0.05: return "negative(<-0.05)"
    if f < 0.05: return "neutral"
    if f < 0.20: return "positive(0.05-0.20)"
    return "strong_positive(>=0.20)"

ea_attr["addon_factor_buckets"] = _group(EA, _addon_bucket)

# 2c. Factor alignment (trend direction vs trade direction)
def _trend_align(r):
    d = (r.get("direction") or "").upper()
    f = (r.get("factors") or {}).get("trend")
    if f is None: return "missing"
    f = float(f)
    if (d == "LONG" and f > 0) or (d == "SHORT" and f < 0):
        if abs(f) >= 2.0: return "strong_aligned"
        return "aligned"
    if f == 0: return "neutral"
    return "against"

ea_attr["trend_alignment"] = _group(EA, _trend_align)

# 2d. Regime
ea_attr["regime"] = _group(EA, lambda r: str(r.get("regime") or "unknown").lower())

# 2e. Direction
ea_attr["direction"] = _group(EA, lambda r: str(r.get("direction") or "unknown").upper())

# 2f. Timeframe
ea_attr["timeframe"] = _group(EA, lambda r: str(r.get("timeframe") or "unknown"))

# 2g. Asset group
ea_attr["asset_group"] = _group(EA, lambda r: str(r.get("asset_group") or "unknown"))

# 2h. Exit reason
ea_attr["exit_reason"] = _group(EA, lambda r: str(r.get("exit_reason") or "unknown"))

# 2i. OOS vs IS
ea_attr["oos"] = _group(EA, lambda r: "OOS" if r.get("oos") else "IS")

# 2j. Pair-level (top 20 by count)
pair_groups = defaultdict(list)
for r in EA:
    pair_groups[r.get("pair","?")].append(r)
top_pairs = sorted(pair_groups.items(), key=lambda x: -len(x[1]))[:25]
ea_attr["top_pairs"] = {p: _stats(v) for p, v in top_pairs}

# 2k. Trend × regime interaction
def _trend_x_regime(r):
    ta = _trend_align(r).split("_")[0] if "_" in _trend_align(r) else _trend_align(r)
    re = str(r.get("regime") or "unk").lower()[:8]
    return f"{ta}|{re}"

ea_attr["trend_x_regime"] = _group(EA, _trend_x_regime)

# 2l. Score × momentum interaction
def _score_x_mom(r):
    s = r.get("score", 0) or 0
    sb = "hi_score" if s >= 1.5 else "lo_score"
    f = (r.get("factors") or {}).get("momentum")
    if f is None: return f"{sb}|mom_missing"
    mb = "hi_mom" if float(f) >= 0.5 else "lo_mom"
    return f"{sb}|{mb}"

ea_attr["score_x_momentum"] = _group(EA, _score_x_mom)

# Print summary
print("\nEngine A — Raw Score Buckets:")
for k, v in ea_attr["raw_score_buckets"].items():
    print(f"  {k:<15} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine A — Score Margin vs Threshold:")
for k, v in ea_attr["score_margin_buckets"].items():
    print(f"  {k:<20} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine A — Trend Factor Buckets:")
for k, v in ea_attr["trend_factor_buckets"].items():
    print(f"  {k:<28} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine A — Trend Alignment:")
for k, v in ea_attr["trend_alignment"].items():
    print(f"  {k:<20} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine A — Momentum Factor Buckets:")
for k, v in ea_attr["momentum_factor_buckets"].items():
    print(f"  {k:<22} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine A — Addon Factor Buckets:")
for k, v in ea_attr["addon_factor_buckets"].items():
    print(f"  {k:<30} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine A — Regime:")
for k, v in ea_attr["regime"].items():
    print(f"  {k:<18} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine A — Direction:")
for k, v in ea_attr["direction"].items():
    print(f"  {k:<10} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine A — Timeframe:")
for k, v in ea_attr["timeframe"].items():
    print(f"  {k:<8} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine A — Asset Group:")
for k, v in ea_attr["asset_group"].items():
    print(f"  {k:<20} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine A — OOS vs IS:")
for k, v in ea_attr["oos"].items():
    print(f"  {k:<6} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine A — Top 15 Pairs:")
for p, v in list(ea_attr["top_pairs"].items())[:15]:
    print(f"  {p:<18} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine A — Score × Momentum:")
for k, v in ea_attr["score_x_momentum"].items():
    print(f"  {k:<25} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

with open(f"{OUT_DIR}/strategy_lab_engine_a_feature_attribution.json","w",encoding="utf-8") as f:
    json.dump(ea_attr, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# PART 3 — Engine B feature attribution
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("PART 3 — ENGINE B FEATURE ATTRIBUTION")
print("="*70)

eb_attr = {}

# Binary features
BINARY_FEATURES_B = [
    "liquidity_sweep","fvg_overlap","zone_touched","bos_volume_confirmed",
    "choch_confirmed","ob_at_zone","bos_mtf_confirmed","breaker_active",
]

def _bool_bucket(field):
    def _fn(r):
        v = r.get(field)
        if v is None: return "missing"
        return "present" if bool(v) else "absent"
    return _fn

for feat in BINARY_FEATURES_B:
    eb_attr[feat] = _group(EB, _bool_bucket(feat))

# Trigger pattern
eb_attr["trigger_pattern"] = _group(EB, lambda r: str(r.get("trigger_pattern") or "UNKNOWN"))

# OB strength buckets
def _ob_strength_bucket(r):
    v = r.get("ob_strength")
    if v is None: return "missing"
    v = float(v)
    if v < 10: return "<10"
    if v < 25: return "10-25"
    if v < 50: return "25-50"
    if v < 75: return "50-75"
    return ">=75"

eb_attr["ob_strength_buckets"] = _group(EB, _ob_strength_bucket)

# FVG bonus buckets
def _fvg_bonus_bucket(r):
    v = r.get("fvg_bonus")
    if v is None: return "missing"
    v = float(v)
    if v == 0: return "no_bonus(0)"
    if v < 0.5: return "low(0-0.5)"
    if v < 1.0: return "medium(0.5-1.0)"
    return "high(>=1.0)"

eb_attr["fvg_bonus_buckets"] = _group(EB, _fvg_bonus_bucket)

# Volume strength buckets
def _vs_bucket(r):
    v = r.get("volume_strength")
    if v is None: return "missing"
    v = float(v)
    if v == 0: return "zero"
    if v < 0.5: return "low(0-0.5)"
    if v < 1.0: return "medium(0.5-1.0)"
    if v < 2.0: return "high(1.0-2.0)"
    return "very_high(>=2.0)"

eb_attr["volume_strength_buckets"] = _group(EB, _vs_bucket)

# RR target buckets
def _rr_bucket(r):
    v = r.get("rr_target")
    if v is None:
        v = r.get("tp1_rr")
    if v is None: return "missing"
    v = float(v)
    if v < 1.0: return "<1.0"
    if v < 1.5: return "1.0-1.5"
    if v < 2.0: return "1.5-2.0"
    if v < 3.0: return "2.0-3.0"
    return ">=3.0"

eb_attr["rr_target_buckets"] = _group(EB, _rr_bucket)

# Regime, direction, asset_group, timeframe, exit_reason
eb_attr["regime"] = _group(EB, lambda r: str(r.get("regime") or "unknown").lower())
eb_attr["direction"] = _group(EB, lambda r: str(r.get("direction") or "unknown").upper())
eb_attr["asset_group"] = _group(EB, lambda r: str(r.get("asset_group") or "unknown"))
eb_attr["timeframe"] = _group(EB, lambda r: str(r.get("timeframe") or "unknown"))
eb_attr["exit_reason"] = _group(EB, lambda r: str(r.get("exit_reason") or "unknown"))
eb_attr["oos"] = _group(EB, lambda r: "OOS" if r.get("oos") else "IS")

# Top pairs
pair_groups_b = defaultdict(list)
for r in EB:
    pair_groups_b[r.get("pair","?")].append(r)
top_pairs_b = sorted(pair_groups_b.items(), key=lambda x: -len(x[1]))[:25]
eb_attr["top_pairs"] = {p: _stats(v) for p, v in top_pairs_b}

# Selected TP/SL source
eb_attr["selected_tp_source"] = _group(EB, lambda r: str(r.get("selected_tp_source") or "unknown"))
eb_attr["selected_sl_source"] = _group(EB, lambda r: str(r.get("selected_sl_source") or "unknown"))

# Score (Engine B score = checklist count)
def _b_score_bucket(r):
    s = r.get("score") or 0
    s = float(s)
    if s < 3: return "<3"
    if s < 4: return "3-4"
    if s < 5: return "4-5"
    if s == 5: return "5(max)"
    return f"{s:.0f}"

eb_attr["score_buckets"] = _group(EB, _b_score_bucket)

# Print
print("\nEngine B — Binary Feature Attribution (present vs absent):")
print(f"  {'Feature':<25} {'Group':<10} {'n':>5} {'WR':>6} {'PF':>7} {'avgR':>8}")
for feat in BINARY_FEATURES_B:
    g = eb_attr[feat]
    for grp, v in g.items():
        print(f"  {feat:<25} {grp:<10} {v['n']:>5} {v['wr']:>6.1%} {v['pf']:>7.3f} {v['avg_r']:>+8.3f}")

print("\nEngine B — Trigger Pattern:")
for k, v in eb_attr["trigger_pattern"].items():
    print(f"  {k:<22} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine B — RR Target Buckets:")
for k, v in eb_attr["rr_target_buckets"].items():
    print(f"  {k:<12} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine B — Score (checklist) Buckets:")
for k, v in eb_attr["score_buckets"].items():
    print(f"  {k:<10} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine B — Regime:")
for k, v in eb_attr["regime"].items():
    print(f"  {k:<18} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine B — Direction:")
for k, v in eb_attr["direction"].items():
    print(f"  {k:<10} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine B — Asset Group:")
for k, v in eb_attr["asset_group"].items():
    print(f"  {k:<20} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine B — OOS vs IS:")
for k, v in eb_attr["oos"].items():
    print(f"  {k:<6} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine B — FVG Bonus:")
for k, v in eb_attr["fvg_bonus_buckets"].items():
    print(f"  {k:<22} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine B — Volume Strength:")
for k, v in eb_attr["volume_strength_buckets"].items():
    print(f"  {k:<22} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

print("\nEngine B — Top 15 Pairs:")
for p, v in list(eb_attr["top_pairs"].items())[:15]:
    print(f"  {p:<18} n={v['n']:>4}  WR={v['wr']:.1%}  PF={v['pf']:.3f}  avgR={v['avg_r']:+.3f}")

with open(f"{OUT_DIR}/strategy_lab_engine_b_feature_attribution.json","w",encoding="utf-8") as f:
    json.dump(eb_attr, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# PART 4 — Cross-comparison Engine A vs Engine B
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("PART 4 — ENGINE A vs ENGINE B CROSS-COMPARISON")
print("="*70)

comparison_rows = []

def _compare_dim(ea_records, eb_records, dim_fn, dim_name):
    ea_groups = defaultdict(list)
    eb_groups = defaultdict(list)
    for r in ea_records:
        ea_groups[dim_fn(r)].append(r)
    for r in eb_records:
        eb_groups[dim_fn(r)].append(r)
    keys = sorted(set(list(ea_groups.keys()) + list(eb_groups.keys())))
    rows = []
    for k in keys:
        sa = _stats(ea_groups[k])
        sb = _stats(eb_groups[k])
        rows.append({
            "dimension": dim_name, "value": k,
            "ea_n": sa["n"], "ea_wr": sa["wr"], "ea_pf": sa["pf"], "ea_avg_r": sa["avg_r"],
            "eb_n": sb["n"], "eb_wr": sb["wr"], "eb_pf": sb["pf"], "eb_avg_r": sb["avg_r"],
            "edge_diff_avg_r": round(sa["avg_r"] - sb["avg_r"], 4),
            "winner": "A" if sa["avg_r"] > sb["avg_r"] else ("B" if sb["avg_r"] > sa["avg_r"] else "TIE"),
        })
    return rows

for dim, fn in [
    ("asset_group", lambda r: str(r.get("asset_group") or "?")),
    ("regime", lambda r: str(r.get("regime") or "?").lower()),
    ("direction", lambda r: str(r.get("direction") or "?").upper()),
    ("pair", lambda r: str(r.get("pair") or "?")),
    ("exit_reason", lambda r: str(r.get("exit_reason") or "?")),
]:
    rows = _compare_dim(EA, EB, fn, dim)
    comparison_rows.extend(rows)
    print(f"\n  {dim.upper()} comparison:")
    print(f"  {'Value':<22} {'EA_n':>5} {'EA_WR':>7} {'EA_PF':>7} {'EA_avgR':>8}  {'EB_n':>5} {'EB_WR':>7} {'EB_PF':>7} {'EB_avgR':>8}  Winner")
    for row in rows:
        v = row["value"][:21]
        print(f"  {v:<22} {row['ea_n']:>5} {row['ea_wr']:>7.1%} {row['ea_pf']:>7.3f} {row['ea_avg_r']:>+8.3f}  {row['eb_n']:>5} {row['eb_wr']:>7.1%} {row['eb_pf']:>7.3f} {row['eb_avg_r']:>+8.3f}  {row['winner']}")

with open(f"{OUT_DIR}/strategy_lab_engine_a_vs_b_comparison.csv","w",newline="",encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(comparison_rows[0].keys()))
    w.writeheader()
    w.writerows(comparison_rows)


# ═══════════════════════════════════════════════════════════════════════════════
# PART 5 — Feature ranking
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("PART 5 — FEATURE RANKING")
print("="*70)

ea_ranking = []
eb_ranking = []

# ── Engine A feature ranking ──
def _rank_feature_ea(name, present_fn):
    present = [r for r in EA if present_fn(r) is True]
    absent  = [r for r in EA if present_fn(r) is False]
    s_p = _stats(present)
    s_a = _stats(absent)
    da, dpf = _uplift(s_p, s_a)
    verdict, conf = _classify(s_p, s_a)
    return {
        "feature": name, "engine": "ENGINE_A",
        "present_n": s_p["n"], "absent_n": s_a["n"],
        "present_wr": s_p["wr"], "absent_wr": s_a["wr"],
        "present_pf": s_p["pf"], "absent_pf": s_a["pf"],
        "present_avg_r": s_p["avg_r"], "absent_avg_r": s_a["avg_r"],
        "uplift_avg_r": da, "uplift_pf": dpf,
        "confidence": conf, "verdict": verdict,
    }

# Trend aligned
ea_ranking.append(_rank_feature_ea(
    "trend_aligned",
    lambda r: (
        (r.get("direction","").upper()=="LONG" and (r.get("factors") or {}).get("trend",0) > 0) or
        (r.get("direction","").upper()=="SHORT" and (r.get("factors") or {}).get("trend",0) < 0)
    ) if r.get("factors") else None
))
# Trend strong (>=2)
ea_ranking.append(_rank_feature_ea(
    "trend_strong_aligned(>=2)",
    lambda r: abs((r.get("factors") or {}).get("trend",0)) >= 2.0 if r.get("factors") else None
))
# High momentum
ea_ranking.append(_rank_feature_ea(
    "momentum_high(>=0.6)",
    lambda r: (r.get("factors") or {}).get("momentum",0) >= 0.6 if r.get("factors") else None
))
# Positive addon
ea_ranking.append(_rank_feature_ea(
    "addon_positive(>0.05)",
    lambda r: (r.get("factors") or {}).get("addon",0) > 0.05 if r.get("factors") else None
))
# Score above 1.5
ea_ranking.append(_rank_feature_ea(
    "score>=1.5",
    lambda r: (r.get("score") or 0) >= 1.5
))
# Score above 2.0
ea_ranking.append(_rank_feature_ea(
    "score>=2.0",
    lambda r: (r.get("score") or 0) >= 2.0
))
# Trending regime
ea_ranking.append(_rank_feature_ea(
    "regime_trending",
    lambda r: str(r.get("regime","")).lower() in ("trending","trend")
))
# High score AND trending
ea_ranking.append(_rank_feature_ea(
    "score>=1.5_AND_trending",
    lambda r: (r.get("score") or 0) >= 1.5 and str(r.get("regime","")).lower() in ("trending","trend")
))
# High score AND momentum_high
ea_ranking.append(_rank_feature_ea(
    "score>=1.5_AND_mom>=0.6",
    lambda r: ((r.get("score") or 0) >= 1.5 and
               (r.get("factors") or {}).get("momentum",0) >= 0.6) if r.get("factors") else None
))
# LONG direction
ea_ranking.append(_rank_feature_ea(
    "direction_LONG",
    lambda r: str(r.get("direction","")).upper() == "LONG"
))
# OOS
ea_ranking.append(_rank_feature_ea(
    "out_of_sample",
    lambda r: bool(r.get("oos"))
))

print("\nEngine A Feature Ranking:")
print(f"  {'Feature':<35} {'P_n':>5} {'A_n':>5} {'P_avgR':>8} {'A_avgR':>8} {'upliftR':>8} {'P_PF':>7} {'Conf':<8} Verdict")
ea_ranking.sort(key=lambda x: -x["uplift_avg_r"])
for row in ea_ranking:
    print(f"  {row['feature']:<35} {row['present_n']:>5} {row['absent_n']:>5} "
          f"{row['present_avg_r']:>+8.3f} {row['absent_avg_r']:>+8.3f} "
          f"{row['uplift_avg_r']:>+8.3f} {row['present_pf']:>7.3f} {row['confidence']:<8} {row['verdict']}")

# ── Engine B feature ranking ──
def _rank_feature_eb(name, present_fn):
    present = [r for r in EB if present_fn(r) is True]
    absent  = [r for r in EB if present_fn(r) is False]
    s_p = _stats(present)
    s_a = _stats(absent)
    da, dpf = _uplift(s_p, s_a)
    verdict, conf = _classify(s_p, s_a)
    return {
        "feature": name, "engine": "ENGINE_B",
        "present_n": s_p["n"], "absent_n": s_a["n"],
        "present_wr": s_p["wr"], "absent_wr": s_a["wr"],
        "present_pf": s_p["pf"], "absent_pf": s_a["pf"],
        "present_avg_r": s_p["avg_r"], "absent_avg_r": s_a["avg_r"],
        "uplift_avg_r": da, "uplift_pf": dpf,
        "confidence": conf, "verdict": verdict,
    }

for feat in BINARY_FEATURES_B:
    eb_ranking.append(_rank_feature_eb(
        feat, lambda r, f=feat: bool(r.get(f)) if r.get(f) is not None else None
    ))

# High RR target
eb_ranking.append(_rank_feature_eb(
    "rr_target>=2.0",
    lambda r: float(r.get("rr_target") or 0) >= 2.0
))
# Volume strength >0
eb_ranking.append(_rank_feature_eb(
    "volume_strength>0",
    lambda r: float(r.get("volume_strength") or 0) > 0
))
# FVG bonus >0
eb_ranking.append(_rank_feature_eb(
    "fvg_bonus>0",
    lambda r: float(r.get("fvg_bonus") or 0) > 0
))
# Sweep + BOS interaction
eb_ranking.append(_rank_feature_eb(
    "sweep_AND_bos_vol_confirmed",
    lambda r: bool(r.get("liquidity_sweep")) and bool(r.get("bos_volume_confirmed"))
))
# OB at zone + volume strength
eb_ranking.append(_rank_feature_eb(
    "ob_at_zone_AND_vol_strength>0",
    lambda r: bool(r.get("ob_at_zone")) and float(r.get("volume_strength") or 0) > 0
))
# MTF BOS
eb_ranking.append(_rank_feature_eb(
    "bos_mtf_confirmed",
    lambda r: bool(r.get("bos_mtf_confirmed")) if r.get("bos_mtf_confirmed") is not None else None
))
# Sweep + BOS_MTF
eb_ranking.append(_rank_feature_eb(
    "sweep_AND_bos_mtf",
    lambda r: bool(r.get("liquidity_sweep")) and bool(r.get("bos_mtf_confirmed"))
))
# Trending regime
eb_ranking.append(_rank_feature_eb(
    "regime_trending",
    lambda r: str(r.get("regime","")).lower() in ("trending","trend")
))
# OOS
eb_ranking.append(_rank_feature_eb(
    "out_of_sample",
    lambda r: bool(r.get("oos"))
))

print("\nEngine B Feature Ranking:")
print(f"  {'Feature':<38} {'P_n':>5} {'A_n':>5} {'P_avgR':>8} {'A_avgR':>8} {'upliftR':>8} {'P_PF':>7} {'Conf':<8} Verdict")
eb_ranking.sort(key=lambda x: -x["uplift_avg_r"])
for row in eb_ranking:
    print(f"  {row['feature']:<38} {row['present_n']:>5} {row['absent_n']:>5} "
          f"{row['present_avg_r']:>+8.3f} {row['absent_avg_r']:>+8.3f} "
          f"{row['uplift_avg_r']:>+8.3f} {row['present_pf']:>7.3f} {row['confidence']:<8} {row['verdict']}")

# Write ranking CSVs
for fname, rows in [
    ("strategy_lab_engine_a_feature_ranking.csv", ea_ranking),
    ("strategy_lab_engine_b_feature_ranking.csv", eb_ranking),
]:
    with open(f"{OUT_DIR}/{fname}","w",newline="",encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# PART 6 — Interaction tests
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("PART 6 — INTERACTION TESTS")
print("="*70)

interaction_rows = []

def _interaction(records, label, filter_fn):
    subset = [r for r in records if filter_fn(r)]
    rest   = [r for r in records if not filter_fn(r)]
    s = _stats(subset)
    sr = _stats(rest)
    da, dpf = _uplift(s, sr)
    verdict, conf = _classify(s, sr)
    return {
        "combo": label, "n": s["n"],
        "wr": s["wr"], "pf": s["pf"], "avg_r": s["avg_r"],
        "baseline_avg_r": sr["avg_r"], "uplift": da,
        "confidence": conf, "verdict": verdict,
    }

# Engine A interactions
ea_interactions = [
    _interaction(EA, "A: trend_aligned + score>=1.5",
        lambda r: ((r.get("direction","").upper()=="LONG" and (r.get("factors") or {}).get("trend",0) > 0) or
                   (r.get("direction","").upper()=="SHORT" and (r.get("factors") or {}).get("trend",0) < 0)) and
                  (r.get("score") or 0) >= 1.5 if r.get("factors") else False),
    _interaction(EA, "A: trend_strong(>=2) + score>=1.5",
        lambda r: abs((r.get("factors") or {}).get("trend",0)) >= 2.0 and (r.get("score") or 0) >= 1.5 if r.get("factors") else False),
    _interaction(EA, "A: trend_aligned + mom>=0.6",
        lambda r: ((r.get("direction","").upper()=="LONG" and (r.get("factors") or {}).get("trend",0) > 0) or
                   (r.get("direction","").upper()=="SHORT" and (r.get("factors") or {}).get("trend",0) < 0)) and
                  (r.get("factors") or {}).get("momentum",0) >= 0.6 if r.get("factors") else False),
    _interaction(EA, "A: score>=2.0 + trending",
        lambda r: (r.get("score") or 0) >= 2.0 and str(r.get("regime","")).lower() in ("trending","trend")),
    _interaction(EA, "A: score>=1.5 + addon>0 + trending",
        lambda r: (r.get("score") or 0) >= 1.5 and
                  (r.get("factors") or {}).get("addon",0) > 0 and
                  str(r.get("regime","")).lower() in ("trending","trend") if r.get("factors") else False),
    _interaction(EA, "A: score>=1.5 + trend_strong + mom>=0.5",
        lambda r: (r.get("score") or 0) >= 1.5 and
                  abs((r.get("factors") or {}).get("trend",0)) >= 2.0 and
                  (r.get("factors") or {}).get("momentum",0) >= 0.5 if r.get("factors") else False),
    _interaction(EA, "A: LOW score(1.0-1.5) + trending",
        lambda r: 1.0 <= (r.get("score") or 0) < 1.5 and str(r.get("regime","")).lower() in ("trending","trend")),
    _interaction(EA, "A: ranging + any score",
        lambda r: str(r.get("regime","")).lower() in ("ranging","range")),
]

# Engine B interactions
eb_interactions = [
    _interaction(EB, "B: sweep + bos_vol_confirmed",
        lambda r: bool(r.get("liquidity_sweep")) and bool(r.get("bos_volume_confirmed"))),
    _interaction(EB, "B: sweep + bos_vol + ob_at_zone",
        lambda r: bool(r.get("liquidity_sweep")) and bool(r.get("bos_volume_confirmed")) and bool(r.get("ob_at_zone"))),
    _interaction(EB, "B: ob_at_zone + rr>=2.0",
        lambda r: bool(r.get("ob_at_zone")) and float(r.get("rr_target") or 0) >= 2.0),
    _interaction(EB, "B: bos_mtf + vol_confirmed",
        lambda r: bool(r.get("bos_mtf_confirmed")) and bool(r.get("bos_volume_confirmed"))),
    _interaction(EB, "B: sweep + bos_mtf",
        lambda r: bool(r.get("liquidity_sweep")) and bool(r.get("bos_mtf_confirmed"))),
    _interaction(EB, "B: rr>=2.0 + vol_strength>0",
        lambda r: float(r.get("rr_target") or 0) >= 2.0 and float(r.get("volume_strength") or 0) > 0),
    _interaction(EB, "B: fvg_overlap + zone_touched",
        lambda r: bool(r.get("fvg_overlap")) and bool(r.get("zone_touched"))),
    _interaction(EB, "B: choch_confirmed + zone_touched",
        lambda r: bool(r.get("choch_confirmed")) and bool(r.get("zone_touched"))),
    _interaction(EB, "B: rr>=2.0 + trending",
        lambda r: float(r.get("rr_target") or 0) >= 2.0 and str(r.get("regime","")).lower() in ("trending","trend")),
    _interaction(EB, "B: ALL_PRESENT: sweep+bos_vol+ob+bos_mtf",
        lambda r: bool(r.get("liquidity_sweep")) and bool(r.get("bos_volume_confirmed")) and
                  bool(r.get("ob_at_zone")) and bool(r.get("bos_mtf_confirmed"))),
]

all_interactions = ea_interactions + eb_interactions
interaction_rows = all_interactions

print(f"\n  {'Combo':<42} {'n':>5} {'WR':>6} {'PF':>7} {'avgR':>8} {'baseline':>8} {'uplift':>8} {'Conf':<8} Verdict")
for row in all_interactions:
    print(f"  {row['combo']:<42} {row['n']:>5} {row['wr']:>6.1%} {row['pf']:>7.3f} "
          f"{row['avg_r']:>+8.3f} {row['baseline_avg_r']:>+8.3f} {row['uplift']:>+8.3f} "
          f"{row['confidence']:<8} {row['verdict']}")

with open(f"{OUT_DIR}/strategy_lab_feature_interactions.csv","w",newline="",encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(interaction_rows[0].keys()))
    w.writeheader()
    w.writerows(interaction_rows)


# ═══════════════════════════════════════════════════════════════════════════════
# PART 7 — indicator_verdicts.md
# ═══════════════════════════════════════════════════════════════════════════════

# Gather final conclusions
def _top_features(ranking, verdict_filter=None, n=5):
    rows = sorted(ranking, key=lambda x: -x["uplift_avg_r"])
    if verdict_filter:
        rows = [r for r in rows if r["verdict"] in verdict_filter]
    return rows[:n]

ea_best = _top_features(ea_ranking, ["KEEP_STRONG_EDGE","KEEP_WEAK_EDGE"])
ea_worst = _top_features(sorted(ea_ranking, key=lambda x: x["uplift_avg_r"]),
                          ["HARMFUL","NEUTRAL"])
eb_best = _top_features(eb_ranking, ["KEEP_STRONG_EDGE","KEEP_WEAK_EDGE"])
eb_worst = _top_features(sorted(eb_ranking, key=lambda x: x["uplift_avg_r"]),
                          ["HARMFUL","NEUTRAL"])

ea_overall = _stats(EA)
eb_overall = _stats(EB)

# Which assets / regimes win for each engine from cross-compare
def _best_for_engine(dim, engine_key, rows):
    subset = [r for r in rows if r["dimension"]==dim and r[f"{engine_key}_n"] >= 30]
    return sorted(subset, key=lambda x: -x[f"{engine_key}_avg_r"])[:5]

md_lines = []
md_lines.append("# Sentinel Pro — Engine A / B Feature Attribution Audit\n")
md_lines.append(f"Matrix records: ENGINE_A={len(EA)}, ENGINE_B={len(EB)}\n")
md_lines.append("\n---\n")
md_lines.append("## Engine A — Overall\n")
md_lines.append(f"- n={ea_overall['n']}  WR={ea_overall['wr']:.1%}  PF={ea_overall['pf']:.3f}  avgR={ea_overall['avg_r']:+.3f}\n")

md_lines.append("\n## Engine A — Best Features (uplift positive)\n")
for r in ea_best:
    md_lines.append(f"- **{r['feature']}** — n_present={r['present_n']}, avgR_present={r['present_avg_r']:+.3f}, "
                    f"uplift={r['uplift_avg_r']:+.3f}, PF={r['present_pf']:.3f}, conf={r['confidence']}, verdict={r['verdict']}\n")

md_lines.append("\n## Engine A — Worst Features (uplift negative)\n")
for r in ea_worst[:5]:
    md_lines.append(f"- **{r['feature']}** — n_present={r['present_n']}, avgR_present={r['present_avg_r']:+.3f}, "
                    f"uplift={r['uplift_avg_r']:+.3f}, PF={r['present_pf']:.3f}, conf={r['confidence']}, verdict={r['verdict']}\n")

md_lines.append("\n## Engine A — Score Distribution Summary\n")
for k, v in ea_attr["raw_score_buckets"].items():
    md_lines.append(f"- {k}: n={v['n']}, WR={v['wr']:.1%}, PF={v['pf']:.3f}, avgR={v['avg_r']:+.3f}\n")

md_lines.append("\n## Engine A — Regime Summary\n")
for k, v in ea_attr["regime"].items():
    md_lines.append(f"- {k}: n={v['n']}, WR={v['wr']:.1%}, PF={v['pf']:.3f}, avgR={v['avg_r']:+.3f}\n")

md_lines.append("\n## Engine A — Direction Summary\n")
for k, v in ea_attr["direction"].items():
    md_lines.append(f"- {k}: n={v['n']}, WR={v['wr']:.1%}, PF={v['pf']:.3f}, avgR={v['avg_r']:+.3f}\n")

md_lines.append("\n---\n")
md_lines.append("## Engine B — Overall\n")
md_lines.append(f"- n={eb_overall['n']}  WR={eb_overall['wr']:.1%}  PF={eb_overall['pf']:.3f}  avgR={eb_overall['avg_r']:+.3f}\n")

md_lines.append("\n## Engine B — Best Features\n")
for r in eb_best:
    md_lines.append(f"- **{r['feature']}** — n_present={r['present_n']}, avgR_present={r['present_avg_r']:+.3f}, "
                    f"uplift={r['uplift_avg_r']:+.3f}, PF={r['present_pf']:.3f}, conf={r['confidence']}, verdict={r['verdict']}\n")

md_lines.append("\n## Engine B — Worst Features\n")
for r in eb_worst[:5]:
    md_lines.append(f"- **{r['feature']}** — n_present={r['present_n']}, avgR_present={r['present_avg_r']:+.3f}, "
                    f"uplift={r['uplift_avg_r']:+.3f}, PF={r['present_pf']:.3f}, conf={r['confidence']}, verdict={r['verdict']}\n")

md_lines.append("\n## Engine B — Structure Feature Summary\n")
for feat in BINARY_FEATURES_B:
    g = eb_attr.get(feat, {})
    p = g.get("present", {})
    a = g.get("absent", {})
    if p and a:
        md_lines.append(f"- **{feat}**: present n={p['n']} avgR={p['avg_r']:+.3f} PF={p['pf']:.3f} | absent n={a['n']} avgR={a['avg_r']:+.3f} PF={a['pf']:.3f}\n")

md_lines.append("\n## Engine B — Trigger Pattern Summary\n")
for k, v in eb_attr["trigger_pattern"].items():
    md_lines.append(f"- {k}: n={v['n']}, WR={v['wr']:.1%}, PF={v['pf']:.3f}, avgR={v['avg_r']:+.3f}\n")

md_lines.append("\n---\n")
md_lines.append("## Cross-Comparison — Where Engine A beats Engine B\n")
ea_wins_ag = [(r["value"], r) for r in comparison_rows
              if r["dimension"]=="asset_group" and r["winner"]=="A" and r["ea_n"]>=20 and r["eb_n"]>=10]
for val, r in sorted(ea_wins_ag, key=lambda x: -x[1]["edge_diff_avg_r"]):
    md_lines.append(f"- **{val}**: EA avgR={r['ea_avg_r']:+.3f} vs EB avgR={r['eb_avg_r']:+.3f}  (diff={r['edge_diff_avg_r']:+.3f})\n")

md_lines.append("\n## Cross-Comparison — Where Engine B beats Engine A\n")
eb_wins_ag = [(r["value"], r) for r in comparison_rows
              if r["dimension"]=="asset_group" and r["winner"]=="B" and r["ea_n"]>=10 and r["eb_n"]>=20]
for val, r in sorted(eb_wins_ag, key=lambda x: x[1]["edge_diff_avg_r"]):
    md_lines.append(f"- **{val}**: EB avgR={r['eb_avg_r']:+.3f} vs EA avgR={r['ea_avg_r']:+.3f}  (diff={r['edge_diff_avg_r']:+.3f})\n")

md_lines.append("\n---\n")
md_lines.append("## ⚠️ Missing Data — Fields Not in Current Matrix Output\n")
md_lines.append("\n### ENGINE_A — Missing fields (need feature-snapshot re-run):\n")
for f in MISSING_EA:
    md_lines.append(f"- `{f}`\n")
md_lines.append("\n### ENGINE_B — Missing fields:\n")
for f in MISSING_EB:
    md_lines.append(f"- `{f}`\n")

md_lines.append("\n---\n")
md_lines.append("## Recommended Next Tests\n")
md_lines.append("### Engine A:\n")
md_lines.append("1. Add feature snapshots: RSI, ADX, EMA alignment, ATR percentile, session, BB width\n")
md_lines.append("2. Long/Short split analysis per asset class\n")
md_lines.append("3. Re-run factor-level Pearson(factor, R) with full OOS sample\n")
md_lines.append("4. Test trend_strong(>=2) as a required gate on crypto separately\n")
md_lines.append("5. Regime split: score distribution in TRENDING vs RANGING separately\n")
md_lines.append("### Engine B:\n")
md_lines.append("1. Add checklist item-level booleans (structure/location/trigger/room/macro/rr)\n")
md_lines.append("2. Add BOS boolean, sweep direction, D1 conflict flag\n")
md_lines.append("3. Session field for London/NY overlap filter hypothesis\n")
md_lines.append("4. Re-run with rr_actual (post execution_sl fix) vs rr_target\n")

with open(f"{OUT_DIR}/strategy_lab_indicator_verdicts.md","w",encoding="utf-8") as f:
    f.writelines(md_lines)

print("\n" + "="*70)
print("PART 8 — WARNINGS AND SUMMARY")
print("="*70)

print(f"""
⚠️  CRITICAL DATA GAPS — FIELDS MISSING FROM MATRIX OUTPUT:

ENGINE_A:
  - RSI, MACD values per bar NOT stored — only composite momentum_factor
  - ADX NOT stored individually — only in composite trend/score
  - EMA alignment detail NOT stored (only final trend factor)
  - Session (London/NY/Asian) NOT in matrix records
  - ATR percentile, BB width, volume ratio NOT stored
  - Individual candle indicator values require re-run with feature snapshots

ENGINE_B:
  - bos_confirmed boolean NOT present — only bos_volume_confirmed
  - BOS/CHoCH direction NOT stored (aligned vs conflicting)
  - FVG present/unmitigated/mitigated distinction missing (only fvg_overlap bonus)
  - Checklist item-level pass/fail NOT exported (structure/location/trigger/room/rr/macro)
  - Session NOT in matrix records
  - D1 conflict flag NOT in matrix records
  - sweep_direction (aligned vs counter) NOT stored

  ➜ Recommend: add a 'feature_snapshot' dict to each trade row in the backtester
    capturing these fields at signal-time for the next matrix run.
""")

print("="*70)
print("OVERALL RESULTS:")
print(f"  ENGINE_A: n={ea_overall['n']}  WR={ea_overall['wr']:.1%}  PF={ea_overall['pf']:.3f}  avgR={ea_overall['avg_r']:+.3f}")
print(f"  ENGINE_B: n={eb_overall['n']}  WR={eb_overall['wr']:.1%}  PF={eb_overall['pf']:.3f}  avgR={eb_overall['avg_r']:+.3f}")

best_ea_str = ", ".join(f"{r['feature']}(+{r['uplift_avg_r']:.3f}R)" for r in ea_best[:3])
worst_ea_str = ", ".join(f"{r['feature']}({r['uplift_avg_r']:+.3f}R)" for r in ea_worst[:3])
best_eb_str = ", ".join(f"{r['feature']}(+{r['uplift_avg_r']:.3f}R)" for r in eb_best[:3])
worst_eb_str = ", ".join(f"{r['feature']}({r['uplift_avg_r']:+.3f}R)" for r in eb_worst[:3])

print(f"\n  ENGINE_A best indicators:  {best_ea_str}")
print(f"  ENGINE_A worst indicators: {worst_ea_str}")
print(f"\n  ENGINE_B best features:  {best_eb_str}")
print(f"  ENGINE_B worst features: {worst_eb_str}")

print(f"""
FILES CREATED:
  {OUT_DIR}/strategy_lab_feature_availability.json
  {OUT_DIR}/strategy_lab_engine_a_feature_attribution.json
  {OUT_DIR}/strategy_lab_engine_b_feature_attribution.json
  {OUT_DIR}/strategy_lab_engine_a_feature_ranking.csv
  {OUT_DIR}/strategy_lab_engine_b_feature_ranking.csv
  {OUT_DIR}/strategy_lab_engine_a_vs_b_comparison.csv
  {OUT_DIR}/strategy_lab_feature_interactions.csv
  {OUT_DIR}/strategy_lab_indicator_verdicts.md
""")
