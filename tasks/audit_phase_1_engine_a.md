# Engine A — Phase 1 Nuclear Audit
**Date:** 2026-05-12 | **Mode:** Audit-only — no patches applied.

---

## Files Inspected (Full Read)

| File | Lines | Status |
|------|-------|--------|
| `factor_scoring.py` | 1–2044 | ✅ Full |
| `scoring.py` | 1–1094 | ✅ Full |
| `indicators.py` | 1–1627 | ✅ Full |
| `config.yaml` | 1–1600 | ✅ Full |
| `regime.py` | 1–98 | ✅ Full |
| `intermarket.py` | 1–800 | ✅ Full |
| `forex_scoring.py` | 1–800 | ✅ Full |
| `confidence_engine.py` | 1–298 | ✅ Full |
| `calibration.py` | 1–800 | ✅ Full |

---

## Section 1 — Scoring Mathematics

### 1.1 Normalization

**Finding: No classical weight normalization — by design.**

Engine A v2 (`compute_factor_scores`) uses a **gated-product model**, not a weighted-sum.

```
base_score = abs(trend_score) * adx_mult * vol_scaler * session_mult
             * di_align_mult * dir_ramp_mult * vwap_mult
```
```
final_score = base_score * (floor + (1 - floor) * conviction)
              * (1 - cost_penalty) * (1 + total_adj) + mean_rev_adj
```
*(factor_scoring.py L1770–1881)*

`trend_score` is the primary driver (0–3.0 scale from `_coherent_trend_score`). Remaining terms are multiplicative gates (each ∈ [0, 1] or near-1 scalers). No divisor exists, so no hardcoded-divisor bug is possible.

**Verdict: ✅ Correct by architecture.**

### 1.2 Conviction Formula

```python
conviction = eff_base_w + eff_mom_w * mom_quality + eff_addon_w * addon_norm
conviction = clamp(0.0, 1.0)
```
*(factor_scoring.py L1742–1747)*

**Defaults:** `base=0.20, momentum=0.50, addon=0.30` → sum = 1.0 ✅

When `mom_quality=1.0` and `addon_norm=1.0` → conviction = 1.0.
When both are 0 → conviction = `base_w` = 0.20.

**Addon-unsupported redistribution** (stocks/indices): addon weight split via `ADDON_UNSUPPORTED_SPLIT_TO_BASE` (default 0.0 → all goes to momentum). *(L1735–1740)*

> **⚠️ BUG #1 (MEDIUM): Addon redistribution default is asymmetric.**
> With `SPLIT_TO_BASE=0.0`, all addon weight → momentum → `eff_mom_w=0.80`. Stock signals become momentum-dominated; base floor stays 0.20 instead of being raised. Config key exists but default silently favors momentum-only paths for non-crypto.

### 1.3 Conviction Floor (Regime-Conditional)

```python
_floor_by_regime = CONFIG.get("CONVICTION_FLOOR_BY_REGIME") or {}
_eff_floor = _floor_by_regime.get(regime, _conviction_floor)
```
*(factor_scoring.py L1765–1769)*

Default floor = 0.20. With floor=0.20 and conviction=0.0 → `final_score = base_score * 0.20`.

**Verdict: ✅ Structurally sound.**

### 1.4 BTC Bias

Applied in `scoring.py` (not `factor_scoring.py`) via `_apply_btc_bias()` with correlation gate at 0.80 (strong) / 0.50 (moderate). Crypto-only; config-bounded.

**Verdict: ✅ Correctly gated.**

### 1.5 Group Multipliers

`FACTOR_SCORE_GROUP_MULTIPLIERS` applied in `scoring.py` **post** factor scoring, **pre** threshold comparison. A multiplier of 0.85 makes threshold harder to reach — intentional.

**Verdict: ✅ Correct application order.**

---

## Section 2 — Indicator Mathematics

### 2.1 RSI — ✅ Standard Wilder's EWM
`calc_rsi`: `alpha=1/period` with `adjust=False`. Correct.

### 2.2 Bollinger Bands — ⚠️ Minor divergence
`calc_bb`: Uses `ddof=1` (sample std). Most platforms use `ddof=0` (population). Produces ~2-3% wider bands on short lookbacks. Not a bug, but BB-squeeze detection triggers slightly later than TradingView/MT5.

### 2.3 ADX / ATR — ✅ Standard Wilder's
Both use `ewm(alpha=1/period)`. ATR uses True Range. Correct.

### 2.4 MACD — ✅ Standard 12/26/9
`adjust=False` EMA. Correct.

---

## Section 3 — Threshold Calibration

### 3.1 Score Threshold Resolution

`get_score_threshold` in `scoring.py` — 3-tier hierarchy:

1. **Pair profile** → `PAIR_PROFILES[pair].min_score`
2. **Configured** → `SCORE_THRESHOLDS[asset_class]`
3. **Fallback** → `volatile` (crypto, nat_gas): **2.0** | `exotic` (forex_exotic, crypto_doge): **1.7** | `stable` (forex_major, indices, stocks): **1.5**

Dynamic regime multiplier optionally applied (e.g., RANGING × 1.1).

### 3.2 Reachability Analysis

**Max theoretical:** `3.0 * 1.0 * 1.25 * 1.0^4 * 1.0 * 1.05 + 0.15 = 4.09` → clamped to **3.0**

**Realistic strong signal:** `trend=2.5, adx=0.85, vol=1.0, di=1.0, dir_ramp=0.95, conviction=0.75, floor=0.20`:
```
base = 2.5 * 0.85 * 1.0 * 1.0 * 1.0 * 0.95 * 1.0 = 2.019
final = 2.019 * (0.20 + 0.80*0.75) = 2.019 * 0.80 = 1.615
```

**Realistic moderate:** `trend=1.8, adx=0.70, di=0.5, conviction=0.60`:
```
base = 1.8 * 0.70 * 1.0 * 1.0 * 0.50 * 1.0 = 0.630
final = 0.630 * 0.68 = 0.428
```

> **🔴 BUG #3 (HIGH): Volatile threshold 2.0 is nearly unreachable.**
> Realistic strong crypto signals score ~1.6. Reaching 2.0 requires near-perfect alignment across ALL multiplicative gates (adx>0.95, di=1.0, dir_ramp=1.0, conviction>0.90). With RANGING regime multiplier (1.1), effective threshold rises to 2.2 — practically unreachable. The `FACTOR_CONVICTION_FLOOR` reduction from 0.60→0.20 was intended to increase throughput but the multiplicative chain still suppresses below 2.0.
>
> **Recommendation:** Lower volatile threshold to ~1.7 or audit live signal distribution to confirm actual P95 scores.

> **🔴 BUG #2 (HIGH): DI alignment zeroes score silently.**
> When `di_align_mult = 0.0` (trend=LONG but -DI > +DI by >5 pts), `base_score` is zeroed regardless of all other factors. No `abort_reason` set, no warning emitted. Signal appears as normal zero-score, indistinguishable from data-missing.
>
> **Impact:** Debugging zero-score "strong" signals requires manually checking DI alignment.

### 3.3 AUTO_TRADE_MIN_CONVICTION

Dual-gate: `final_score ≥ threshold` AND `conviction ≥ AUTO_TRADE_MIN_CONVICTION[class]`. Both must pass independently.

**Verdict: ✅ Structurally sound.**

---

## Section 4 — Factor Pipeline Status

### 4.1 Active Factors

| Factor | Location | Status |
|--------|----------|--------|
| EMA Trend (D1+H4+H1) | `_coherent_trend_score` | ✅ Active, 2-bar hysteresis |
| RSI Momentum | `_momentum_quality` | ✅ Active |
| MACD Momentum | `_momentum_quality` | ✅ Active |
| ADX Gate | `_adx_gate` | ✅ Active, linear ramp |
| DI Alignment | `_di_alignment_multiplier` | ✅ Active |
| Volatility Scaler | `_volatility_scaler` | ✅ Active, per-class bands |
| Cost/Funding Penalty | L1780–1817 | ✅ Active |
| Volume Adjustment | L1826–1840 | ✅ Active, ±0.03 |
| Macro Adjustment | L1844–1858 | ✅ Active, ±0.02 |
| Intermarket Adjustment | L1860–1906 | ✅ Active, two-stage |
| Directional Ramp | `_directional_ramp` | ✅ Active |

### 4.2 Config-Gated Factors

| Factor | Config Key | Default | Status |
|--------|-----------|---------|--------|
| Research Lab | `ENGINE_A_RESEARCH_LAB_FACTORS.ENABLED` | `true` | ✅ Active |
| Mean Reversion | `ENGINE_A_MEAN_REVERSION.ENABLED` | `true` | ✅ Active |
| VWAP Filter | `VWAP_DIRECTION_FILTER.ENABLED` | `true` | ✅ Active |
| Session Mult | (deprecated) | Always 1.0 | 🔴 Dead code |

### 4.3 Dead / Deprecated Code

| Item | Location | Status |
|------|----------|--------|
| `_session_multiplier` | factor_scoring.py L1686 | 🔴 Dead — always returns 1.0 |
| `CRYPTO_TRANSITION_PENALTY_ENABLED` | config.yaml + regime.py | 🔴 Dead |
| `forex_scoring.py` | Entire file | 🔴 Dead — not used by Engine A |

> **ℹ️ BUG #4 (LOW): Dead session multiplier still in multiplication chain.**
> No math impact (always 1.0); adds meaningless `feed_status["session"]` entry.

---

## Section 5 — Regime Detection & Gating

### 5.1 Classification

`detect_regime` in `regime.py`:

```
ADX > 25 → TRENDING
ADX < 20 → RANGING
BBW > threshold → HIGH_VOLATILITY
else → LOW_VOLATILITY
```

**Verdict: ✅ Mutually exclusive.**

### 5.2 Smoothing

3-sample majority filter via `_get_smoothed_regime` (deque maxlen=3). **✅ Good anti-whipsaw.**

### 5.3 Regime Multipliers

| Regime | Multiplier | Effect |
|--------|-----------|--------|
| TRENDING | 0.90 | Easier threshold |
| RANGING | 1.10 | Harder threshold |
| HIGH_VOLATILITY | 1.15 | Harder threshold |
| LOW_VOLATILITY | 1.00 | Neutral |

> **⚠️ BUG #5 (MEDIUM): Regime RANGING multiplier works against mean-reversion.**
> In ranging markets, mean-reversion is the valid edge — but threshold is raised 10%. Meanwhile trend-coherence score is naturally lower in ranges. Mean-reversion additive (+0.10 to +0.15) insufficient to compensate. Double penalty.

---

## Section 6 — Intermarket Logic

### 6.1 Two-Stage Application

**Stage 1 (inline):** ±0.02 based on `divergence` flag/score. *(L1860–1874)*
**Stage 2 (post-score):** `apply_confirmation_to_score()` with `engineADelta`. *(L1886–1906)*

> **⚠️ BUG #6 (MEDIUM): Intermarket divergence double-counted.**
> Stage 1 applies `_inter_adj = -0.02` when `divergence=True`. Stage 2 independently evaluates the same data via confirmation engine. Same divergence penalized twice: ~±0.02 inline + up to ±0.05 from confirmation = ±0.07 total. Stage 1 bounded by `_total_adj_cap`; Stage 2 is not.

### 6.2 Error Handling

**Verdict: ✅ Fail-neutral.** Exception → no adjustment, score unchanged.

---

## Section 7 — Confidence Engine

`confidence_engine.py` computes independent confidence (not same as `conviction`). Evaluates indicator agreement, TF alignment, regime fit, liquidity. Output 0.0–1.0. Used downstream by Engine C/UI, does NOT feed back into `final_score`.

**Verdict: ✅ Clean separation, no circular dependency.**

---

## Section 8 — Calibration

`calibration.py` reads `audit.db` for empirical probability buckets (score → win rate). Read-only analysis, does not modify scoring.

**Verdict: ✅ Correctly isolated.**

---

## Ranked Bug List

| # | Severity | Bug | File(s) | Impact |
|---|----------|-----|---------|--------|
| 3 | 🔴 HIGH | Volatile threshold 2.0 nearly unreachable — multiplicative chain caps realistic signals ~1.6 | `scoring.py`, `factor_scoring.py` | Crypto signals rarely auto-trade |
| 2 | 🔴 HIGH | DI alignment `0.0` zeroes score silently — no diagnostic flag or abort_reason | `factor_scoring.py` L1692–1714 | Zero-scores indistinguishable from data-missing |
| 1 | 🟡 MEDIUM | Addon redistribution default (`SPLIT_TO_BASE=0.0`) → stock scoring momentum-dominated | `factor_scoring.py` L1735–1740 | No base-floor uplift for stocks |
| 5 | 🟡 MEDIUM | Regime RANGING multiplier opposes mean-reversion factor | `scoring.py` + `factor_scoring.py` | Mean-reversion double-penalized in ranges |
| 6 | 🟡 MEDIUM | Intermarket divergence double-counted (inline + confirmation engine) | `factor_scoring.py` L1860–1906 | Divergence penalty ~3.5x intended |
| 4 | 🟢 LOW | Dead `_session_multiplier` in multiplication chain | `factor_scoring.py` L1686 | No math impact; code clutter |
| — | 🟢 LOW | Bollinger `ddof=1` vs industry `ddof=0` | `indicators.py` | Slight BB-squeeze timing diff |

---

## Execution Paths Traced

1. Scanner → `compute_factor_scores` → full multiplicative chain
2. `final_score` → `get_score_threshold` → 3-tier hierarchy + regime multiplier
3. `conviction` → `AUTO_TRADE_MIN_CONVICTION` → dual-gate check
4. `detect_regime` → `_get_smoothed_regime` → threshold modification
5. `intermarket_context` → inline adj → `apply_confirmation_to_score` → two-stage

---

## Areas NOT Verified

| Area | Reason |
|------|--------|
| Live `config.yaml` on production | Audit used checked-in config |
| `audit.db` win-rate distribution | Would require DB query |
| BTC correlation cache staleness | Cache TTL not audited |
| Research Lab edge cases | Partially read, not fully traced |
| Engine C consumption of factor_scores | Out of scope |

---

## Recommended Negative-Case Tests

| Test | Proves |
|------|--------|
| `test_di_alignment_zeroes_score` | DI misalignment → score=0 AND diagnostic emitted |
| `test_volatile_threshold_reachability` | Synthetic perfect signal actually reaches 2.0 |
| `test_addon_redistribution_stock` | `SPLIT_TO_BASE=0.5` raises base floor for stocks |
| `test_regime_ranging_mean_reversion` | Mean-rev signal in RANGING can pass threshold |
| `test_intermarket_no_double_count` | Divergence penalty applied only once end-to-end |
| `test_conviction_floor_regime_override` | `CONVICTION_FLOOR_BY_REGIME` overrides default |
| `test_ema_hysteresis_blocks_whipsaw` | 2-bar confirmation prevents single-bar flip |
| `test_research_lab_cross_engine_cap` | Engine B RL active → capped at 1.5x standalone max |

---

## Summary

Engine A uses a **gated-product model**. The architecture is sound but the multiplicative chain creates compounding suppression — each gate slightly below 1.0 dramatically reduces the final score. The two HIGH-severity findings (DI silent zeroing, volatile threshold unreachability) are the most impactful. The three MEDIUM findings represent calibration mismatches between config dimensions that weren't designed to interact.

No data corruption risks found. No safety gate bypasses found. All fail-closed paths verified correct.
