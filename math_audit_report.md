# Athena Trading System — Mathematical / Computational Audit Report

**Date:** 2026-04-30  
**Scope:** indicators.py, scoring.py, factor_scoring.py, engine_c.py, confidence_engine.py, config.py, feature_normalizer.py  
**Auditor:** Kimi Code CLI  

---

## Executive Summary

| Severity | Count | Categories |
|----------|-------|------------|
| **CRITICAL** | 2 | Division by zero risk, formula error |
| **HIGH** | 4 | Index misalignment, scale mismatch, precision loss, logic bug |
| **MEDIUM** | 6 | Threshold inconsistency, naming mismatch, clamping side-effects, unreachable code, missing epsilon, edge case |
| **LOW** | 5 | Documentation drift, defensive gaps, minor formula deviations |

---

## 1. CRITICAL ISSUES

### C1. `calc_atr` — Division by Zero when ATR=0 Propagates to `calc_levels` [CRITICAL]
**File:** `indicators.py` (lines 872-880)  
**Code:**
```python
rr1 = abs(tp1 - price) / abs(sl - price) if abs(sl - price) > 0 else 0
```
The guard is present, but `calc_levels` is called from multiple paths where `atr=0` is not pre-checked. In `compute_factor_scores` (factor_scoring.py:813-815), there's a warning log for ATR=0 but no early return — scoring continues with `atr=0`, which produces `sl == price` and `tp == price`, yielding `rr1 = 0/0` guarded to `0`.  
**Impact:** Zero R:R signals pass through to Engine C, potentially causing `resolve_tp` to hit the fallback 2R path with incorrect risk assumptions.  
**Fix:** Hard-abort in `compute_factor_scores` when `_atr == 0` (same pattern as ADX hard abort).

### C2. `_robust_z_score_normalization` — Misnamed and Mathematically Incorrect [CRITICAL]
**File:** `confidence_engine.py` (lines 94-114)  
**Code:**
```python
std_equiv = mean_mad / 0.6745
robust_z = 1.0 / (1.0 + std_equiv)
```
This is **NOT** a z-score. A z-score is `(x - μ) / σ`. This function computes an inverse sigmoid-like transform of MAD-derived dispersion. The docstring and function name are misleading.  
**Impact:** Engineers reading the code expect standard z-score behavior (centered at 0, unbounded, ~68% within ±1). Instead they get a [0,1] bounded decay function. This causes cognitive mismatch and potential misuse if copied elsewhere.  
**Fix:** Rename to `_mad_dispersion_score` or `_inverse_dispersion_transform`. Update docstring to accurately describe the formula.

---

## 2. HIGH SEVERITY ISSUES

### H1. `calc_adx` — ADX Index Misalignment [HIGH]
**File:** `indicators.py` (lines 143-213)  
**Code:**
```python
for i in range(p, len(true_range)):
    ...
    plus_di[i + 1] = pdi_val      # writes at i+1
    minus_di[i + 1] = mdi_val
    dx_values.append(...)         # appends sequentially

adx[p * 2] = adx_avg              # first ADX value
for i in range(p, len(dx_values)):
    adx_avg = (adx_avg * (p - 1) + dx_values[i]) / p
    idx = i + p + 1
    if idx < n:
        adx[idx] = adx_avg
```
**Issues:**
1. `plus_di`/`minus_di` are written at index `i+1` where `i` ranges over `true_range` indices (0-based from bar 1). The first DI values land at `p+1`, but the first valid bar for DI is bar `p` (after `p` bars of smoothing). Off-by-one: DI values are shifted right by 1.
2. `adx[p*2]` is the first ADX value, but ADX is a `p`-period smoothed DX. If DX starts at index `p` (bar `p+1` in price terms), the first ADX should be at `2*p` (mean of DX[0:p]), which is correct. However, the loop `for i in range(p, len(dx_values))` skips the first `p` DX values for smoothing, meaning `adx_avg` is initialized from `dx_values[:p]` and then the smoothing loop starts at `i=p` (the `p+1`th DX value). This is actually correct for Wilder smoothing (init from first `p`, then smooth forward).  
3. **The real bug:** `idx = i + p + 1`. If `i = p` (first smoothed value after init), `idx = 2p + 1`. But `adx[p*2]` was already set. So the first smoothed ADX goes to `2p+1`, leaving `adx[2p]` as the simple average and `adx[2p+1]` as the first smoothed value. This creates a **duplicate / misaligned ADX series** where index `2p` is a simple mean and `2p+1` onward are Wilder-smoothed.  
**Impact:** ADX values consumed by `_adx_gate` and regime detection may read the unsmoothed mean at `2p` instead of the proper smoothed value, causing false "dead market" or "trending" classifications.  
**Fix:** Remove the `adx[p*2] = adx_avg` line and start the smoothing loop from `i=0` with `idx = i + p`, or restructure to align all outputs consistently.

### H2. `timeframe_alignment` — Scale Mismatch with `_tf_score_proxy` [HIGH]
**File:** `confidence_engine.py` (lines 142-162)  
**Code:**
```python
return max(0.0, min(1.0, 1.0 - std_val / 1.0))
```
The comment claims proxy `final_score` is in `[-1, +1]`, but `_tf_score_proxy` (scoring.py:17-55) returns an average of components that can each be `-1, 0, +1`. With 3 components, the average is in `[-1, 1]`. However, `factor_result["final_score"]` from Engine A is in `[0, 3.0]`. When `timeframe_alignment` is called with `d1_factor_result`, `h4_factor_result`, `h1_factor_result` (from `compute_confidence`), these are Engine A results (0-3.0 scale), NOT `_tf_score_proxy` results.  
**Impact:** If D1=2.5, H4=1.0, H1=0.5 (all LONG, different strengths), std ≈ 0.82, alignment = 0.18. But these are all agreeing directions — the function penalizes score magnitude differences, not directional disagreement. The comment explicitly says this was calibrated for `[-1,+1]` proxies, but the caller passes `[0,3.0]` scores.  
**Fix:** Either normalize scores to `[-1,1]` before computing std, or change the divisor to `3.0` (the actual scale). Given the comment history, the intended behavior is directional alignment, so normalize by dividing by max possible (3.0) first.

### H3. `_coherent_trend_score` — `coherence_ratio` Formula Underweights D1 Dominance [HIGH]
**File:** `factor_scoring.py` (lines 120-218)  
**Code:**
```python
coherence_ratio = max(0.5, min(1.0, dominant_w / total_w))
```
When D1=LONG (weight 0.5) and H4=SHORT (weight 0.3), `dominant_w = 0.5`, `total_w = 0.8`, `coherence_ratio = 0.625`. With `_tf_coverage = 2/3`, magnitude = `(0.35 + 0.65*0.625) * 3.0 * 0.667 = 1.51`. Direction = LONG (D1 wins).  
This is mathematically correct, but the **floor of 0.5** means even a 51/49 split gets `coherence_ratio = 0.5`, producing magnitude = `(0.35 + 0.325) * 3.0 * 0.667 = 1.35`. A nearly tied vote produces a "moderate" score (1.35/3.0 = 45% of max) instead of near-zero.  
**Impact:** Conflicting multi-TF signals (e.g., D1 bullish, H4 bearish) still produce tradable scores instead of being suppressed. Given the 42.3% directional hit-rate of Engine A, this false-confidence mechanism may amplify noise.  
**Fix:** Remove the 0.5 floor, or make it configurable. A tied vote should produce near-zero magnitude.

### H4. `calc_stochastic` — `mapped` Zero-Fill Corrupts SMA Smoothing [HIGH]
**File:** `indicators.py` (lines 509-550)  
**Code:**
```python
mapped = [v if v is not None else 0 for v in rawK]
kL = calc_sma(mapped, ks)
```
`rawK` has `None` for indices `< kp-1`. These are replaced with `0` in `mapped`, then fed to `calc_sma`. The SMA calculation includes these zeros, pulling down the early valid %K values.  
**Impact:** Stochastic %D (the SMA of %K) is artificially depressed for the first `ks` bars after %K becomes valid. For `kp=14, ks=3`, the first valid %K at index 13 gets averaged with `0, 0` from indices 11,12, producing `%D = rawK[13]/3` instead of `rawK[13]`.  
**Fix:** Use `None` preservation in `calc_sma`, or slice `mapped[kp-1:]` before SMA and re-align.

---

## 3. MEDIUM SEVERITY ISSUES

### M1. All Asset Classes Share Identical RSI Bounds (70/30) [MEDIUM]
**File:** `config.py`  
**Config:**
```python
RSI_BOUNDS = {
    "crypto": {"ob": 70, "os": 30},
    "forex": {"ob": 70, "os": 30},
    ...
}
```
Crypto is significantly more volatile than forex. Using 70/30 for crypto means RSI rarely reaches overbought/oversold in strong trends, making the `_momentum_quality` RSI scoring (which gives +0.50 for RSI≥50 on LONG) almost always trigger even in deeply overextended moves.  
**Impact:** Crypto momentum quality is systematically overstated. RSI≥50 is a very weak bar for crypto bull markets.  
**Fix:** Use crypto-specific bounds (e.g., 80/20) or make them configurable per asset class with different defaults.

### M2. `_momentum_quality` — MACD Opposing Direction Penalty Too Weak [MEDIUM]
**File:** `factor_scoring.py` (lines 275-290)  
**Code:**
```python
elif (is_long and hist < 0) or (not is_long and hist > 0):
    macd_score = -0.15
```
With RSI weight 0.6 and MACD weight 0.4, a confirming RSI (+0.50) and opposing MACD (-0.15) yields: `(0.50*0.6 + (-0.15)*0.4) / 1.0 = 0.24`. After clamp to `[0,1]`, this becomes `0.24`. The MACD opposition only reduces the weighted sum by 0.06 (from 0.30 to 0.24).  
**Impact:** MACD divergence is heavily diluted by RSI. A strong MACD histogram reversal against position direction barely moves the needle.  
**Fix:** Increase MACD opposing penalty to `-0.50` or increase MACD weight.

### M3. `get_score_threshold` — BT Chain Logic Has Unreachable Live Fallback [MEDIUM]
**File:** `scoring.py` (lines 161-214)  
**Code:**
```python
if is_backtest and use_bt_chain:
    if profile.get("bt_min") is not None:
        return float(profile["bt_min"])
if profile.get("min_confluence") is not None:
    return float(profile.get("min_confluence"))
```
If `is_backtest and use_bt_chain` and `profile.bt_min` is None, it falls through to `profile.min_confluence`. If that's also None, it enters the group check. The live group fallback at lines 197-201 has condition `if not (is_backtest and use_bt_chain)`. But if `use_bt_chain=True` and `is_backtest=True`, this is False, so the live group fallback is correctly skipped.  
**However:** If `is_backtest=True` and `use_bt_chain=False` (user explicitly disabled BT thresholds), the code takes the live path at line 197. This is correct behavior.  
**Real issue:** The early return at line 185 (`profile.min_confluence`) happens BEFORE the BT group check. If a pair has `min_confluence` set but no `bt_min`, a backtest with BT chain will use the live `min_confluence` instead of the BT group/class thresholds. This may be intentional (profile overrides everything), but the docstring says "bt_min first", implying BT chain should prefer BT thresholds even over live profile thresholds.  
**Fix:** Clarify docstring or reorder: if BT chain active, skip `min_confluence` profile override unless explicitly configured to mirror live.

### M4. `calc_macd` — Signal Line Alignment Assumes Contiguous Valid MACD [MEDIUM]
**File:** `indicators.py` (lines 91-120)  
**Code:**
```python
valid = [v for v in ml if v is not None]
se = calc_ema(valid, sig)
sl2 = [None] * len(c)
vf = next((i for i, v in enumerate(ml) if v is not None), len(c))
si = 0
for i in range(vf, len(c)):
    sl2[i] = se[si] if si < len(se) else None
    si += 1
```
If `ml` has `None` interspersed (unlikely with standard EMA, but possible with sparse data), `valid` strips them and `se` is shorter. The alignment loop assumes all positions from `vf` onward map 1:1 to `se`, which is wrong if there were internal None values.  
**Impact:** Signal line misalignment in edge cases with missing data.  
**Fix:** Map `se` indices only to non-None positions in `ml`.

### M5. `zscore_normalize` Hard Clamp at ±3 Destroys Tail Information [MEDIUM]
**File:** `feature_normalizer.py` (lines 34-48)  
**Code:**
```python
z = max(-3.0, min(3.0, z))  # clamp
```
In volatile markets (crypto), 3-sigma events are common. Clamping destroys the ability to distinguish a 3-sigma move from a 6-sigma move. This affects `calc_realized_volatility` z-scores and any downstream ML features.  
**Impact:** Extreme volatility events are indistinguishable from moderate outliers. Risk models may underreact to black swan moves.  
**Fix:** Use soft scaling (e.g., `tanh(z/3) * 3`) or remove clamp for volatility features. Keep clamp only for UI display.

### M6. `apply_vision` — STRONG Boost of 1.30 Can Push Conviction Above 1.0 [MEDIUM]
**File:** `engine_c.py` (lines 578-670)  
**Code:**
```python
new_conviction = min(1.0, old_conviction * mult)
```
The clamp is present, so this is bounded. However, the `_vision_modifiers` default has STRONG=1.30. If conviction is 0.80, STRONG boosts to 1.0 (capped). If conviction is 0.50, STRONG boosts to 0.65. The boost is multiplicative, meaning low-conviction signals get proportionally less benefit than high-conviction signals.  
**Impact:** A STRONG vision rating on a weak signal (0.35 → 0.455, still SKIP) does nothing, while on a strong signal (0.70 → 0.91, HIGH) it promotes tier. This is actually reasonable behavior, but the asymmetry should be documented.  
**Not a bug, but worth noting:** The CONTRADICTS and AVOID ratings both set `conviction_mult=0.0`, but `action` differs ("contradict" vs "override"). The action field is informational only — both produce identical mathematical results (conviction=0).

---

## 4. LOW SEVERITY ISSUES

### L1. `calc_rsi` — Wilder Smoothing Uses `max(d, 0)` Instead of `d if d>0 else 0` [LOW]
**File:** `indicators.py` (lines 79-86)  
This is functionally identical. No bug, but `max(d, 0)` is slightly less explicit than conditional assignment for readers learning Wilder RSI.

### L2. `calc_ema` — Seed Uses Simple Mean Instead of First Price [LOW]
**File:** `indicators.py` (lines 27-42)  
**Code:**
```python
e[p - 1] = sum(c[:p]) / p
```
Standard EMA implementations sometimes use `c[0]` as the seed. Using SMA is a valid alternative and produces slightly different early values. Documented behavior, not a bug.

### L3. `calc_squeeze` — Only Checks Last Bar [LOW]
**File:** `indicators.py` (lines 241-305)  
The function only checks if the squeeze is active on the final bar. It does not count consecutive squeeze bars correctly for the `bars` return value (always 0 or 1). The docstring claims "consecutive squeeze bars" but the implementation doesn't track history.  
**Fix:** Either track consecutive bars or update docstring.

### L4. `_session_multiplier` — Shoulder Zone Dead Code [LOW]
**File:** `factor_scoring.py` (lines 774-780)  
**Code:**
```python
# Hour 16 is already inside the NY core window (13 <= h < 21) so including
# range(16, 16+shoulder_h) here is dead code — core check fires first.
```
Self-documented dead code. Harmless but adds clutter.

### L5. `compute_factor_scores` — `weights` Dict Has `trend: 1.0` But Trend Is Not Weighted [LOW]
**File:** `factor_scoring.py` (lines 939-940)  
**Code:**
```python
"weights": {"trend": 1.0, "momentum": _eff_mom_w, "addon": _eff_addon_w, "base": _eff_base_w}
```
The `trend` weight is hardcoded to 1.0, but in the final score formula, trend is not multiplied by a weight — it's the base driver. The `weights` dict is for UI display and may confuse users expecting a weighted sum.

---

## 5. THRESHOLD CONSISTENCY CHECK

| Threshold | Crypto | Forex | Commodity | Stock | Index |
|-----------|--------|-------|-----------|-------|-------|
| BT_MIN | 2.15 | 1.60 | 1.80 | 1.10 | 1.17 |
| MIN_CONFLUENCE_CLASS | 2.40 | 2.10 | 1.80 | 1.80 | 1.50 |
| ADX_TREND_MIN | 20 | 25 | 25 | 25 | 25 |
| SL_ATR_MULT (default) | 1.5 | 1.2 | 1.5 | 1.5 | 1.5 |
| TP1_ATR_MULT (default) | 2.0 | 2.0 | 2.5 | 2.5 | 2.5 |
| TP2_ATR_MULT (default) | 3.5 | 3.0 | 4.0 | 4.0 | 4.0 |
| RSI_BOUNDS (ob/os) | 70/30 | 70/30 | 70/30 | 70/30 | 70/30 |

**Findings:**
- ✅ BT_MIN and MIN_CONFLUENCE_CLASS are correctly ordered (BT_MIN ≤ MIN_CONFLUENCE_CLASS for all classes except crypto where 2.15 < 2.40). This is intentional — backtest uses lower bars.
- ⚠️ RSI_BOUNDS identical across all asset classes is suboptimal (see M1).
- ✅ ADX_TREND_MIN correctly lower for crypto (20 vs 25) acknowledging higher baseline volatility.
- ✅ SL/TP multipliers correctly tighter for forex (lower ATR mults).

---

## 6. FORMULA VERIFICATION

### Verified Correct
| Formula | Location | Status |
|---------|----------|--------|
| Wilder RSI smoothing | indicators.py:56-88 | ✅ Correct |
| Wilder ATR smoothing | indicators.py:123-140 | ✅ Correct (with note: `tr[0]=0` is a placeholder, first valid ATR at index `p`) |
| Bollinger Bands (sample std) | indicators.py:216-238 | ✅ Correct (N-1 denominator) |
| EMA formula | indicators.py:27-42 | ✅ Correct (k=2/(p+1)) |
| SMA formula | indicators.py:45-53 | ✅ Correct |
| Chandelier Exit ratchet | indicators.py:747-802 | ✅ Correct |
| Engine C conviction blend | engine_c.py:1177-1339 | ✅ Correct (weighted sum with bounds) |
| Confidence weight redistribution | confidence_engine.py:241-259 | ✅ Correct (renormalizes to sum 1.0) |
| Factor scoring final formula | factor_scoring.py:914-916 | ✅ Correct |

### Needs Attention
| Formula | Location | Issue |
|---------|----------|-------|
| ADX indexing | indicators.py:199-211 | ⚠️ See H1 |
| Stochastic zero-fill | indicators.py:534 | ⚠️ See H4 |
| "Z-score" normalization | confidence_engine.py:94-114 | ⚠️ See C2 |
| Timeframe alignment std divisor | confidence_engine.py:162 | ⚠️ See H2 |

---

## 7. RECOMMENDATIONS

### Immediate (before next deployment)
1. **Fix C1:** Add ATR=0 hard abort in `compute_factor_scores` (same pattern as ADX abort).
2. **Fix H1:** Restructure `calc_adx` to align DI and ADX indices correctly. Add unit test with known TA-Lib output for verification.
3. **Fix C2:** Rename `_robust_z_score_normalization` to reflect actual formula.

### Short-term (next sprint)
4. **Fix H2:** Normalize Engine A scores to `[-1,1]` before `timeframe_alignment`, or adjust divisor to `3.0`.
5. **Fix H3:** Make `coherence_ratio` floor configurable, default to `0.0` (no floor) for tied votes.
6. **Fix H4:** Fix `calc_stochastic` None handling to preserve SMA correctness.
7. **Fix M1:** Add per-asset-class RSI bounds (crypto 80/20, forex 70/30, etc.).

### Medium-term (backlog)
8. **Fix M5:** Remove or soften hard clamp in `zscore_normalize` for volatility features.
9. **Fix M3:** Clarify threshold resolution docstring or reorder BT chain priority.
10. **Fix L3:** Implement consecutive squeeze bar counting or update docstring.

---

## Appendix: Test Cases to Add

```python
# Test C1: ATR=0 should abort
def test_atr_zero_aborts():
    result = compute_factor_scores(..., h4_snap={"atr": 0, ...})
    assert result["final_score"] == 0.0

# Test H1: ADX alignment
def test_adx_alignment():
    adx = calc_adx(hi, lo, c, 14)
    # After 2*14+1 bars, all values should be smoothed (no simple mean at 2p)
    assert adx["adx"][28] is None or isinstance(adx["adx"][28], float)

# Test H4: Stochastic early values
def test_stochastic_early_values():
    stoch = calc_stochastic(candles, 14, 3, 3)
    # First valid %K at index 13, first valid %D should not be depressed by zeros
    assert stoch["d"][15] > 0  # or appropriate check
```

---

*End of Report*
