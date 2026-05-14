# Engine A Phase 1 — Audit Report

**Scope:** Audit-only (no code changes). Evidence cites repo-relative paths and line ranges / functions as observed during review.

**Required files verified readable:** `AGENTS.md`, `factor_scoring.py`, `scoring.py`, `indicators.py`, `regime.py`, `intermarket.py` (consumed via `from intermarket import apply_confirmation_to_score` inside `factor_scoring.compute_factor_scores`), `confidence_engine.py`, `calibration.py`, `athena_app/services/structure_context.py`, `config.yaml` (repository root).

**Related execution surfaces:** `athena.py` (`analyze_pair`), `scanner.py` (combined conviction / Engine B), `backtest_runner.py` (point-in-time Engine A + optional structure-first).

---

## Engine A Audit — Detailed Findings

### 1. Core Scoring Logic

- **Normalization / scaling (trend):** `_coherent_trend_score` uses coherence × structural magnitude `(0.35 + 0.65 * coherence_ratio) * 3.0 * tf_coverage` (`factor_scoring.py`, `_coherent_trend_score`, ~L407–432). The **3.0** factor encodes the three-TF model scale; it is **not** `sum(active INDICATOR_WEIGHTS.trend)` for magnitude.

- **Normalization / scaling (momentum):** `_momentum_quality` builds `total_w = rsi_w + macd_w (+ volume_w)` (`factor_scoring.py`, ~L738–752), computes weighted raw contributions, then rescales using fixed per-indicator theoretical caps `_rsi_cap = _macd_cap = _volume_cap = 0.50` in `_max_raw` (~L764–772). Denominator for weights is **active sum** (`total_w`); caps are **fixed 0.50**, not derived from config maxima.

- **combinedConviction (scanner):** When Engine B direction aligns, `_engine_b_scan_combined_conviction` uses regime weights `w_a * a_norm + w_b * b_norm` (`scanner.py`, ~L155–184). When **not** direction-aligned, returns `a_norm * 0.60` (~L172–173). When Engine B path falls through / error, A-only uses `a_norm * _a_only_auto_weight(pair)` (~L1334–1337).

- **scoreNorm:** `score_norm = min(1.0, float(res["score"]) / float(max_score))` (`athena.py`, ~L11819–11822). Scanner uses this as `a_norm`.

- **Forming-bar / confirmed-bar:** Engine A scores **confirmed candles only**; forming bar kept diagnostic (`athena.py`, ~L11251–11252). EMA hysteresis uses `_previous_indicator_snap`: `calc_indicators(candles[:-1])` (`factor_scoring.py`, ~L447–455), invoked from `compute_factor_scores` (~L1776–1778).

- **INDICATOR_WEIGHTS:** Trend keys `d1_ema_trend`, `h4_ema_trend`, `ema_trend` resolved via `_resolve_class_keyed(tf_weights_raw, score_group, asset_type, {})` (`factor_scoring.py`, ~L346–357). Momentum uses `rsi_z`, `macdLine_z`; `volume_momentum_spread` participates only when `ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED`, indicator weights branch applies, and `volume_momentum_score` is non-zero (~L722–751).

- **FACTOR_SCORE_GROUP_MULTIPLIERS:** No reads located in `scoring.py` / `factor_scoring.py`; **not applied** in Engine A v2. Class overrides use `ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED` and maps such as `ENGINE_A_FACTOR_WEIGHTS_BY_CLASS` (`factor_scoring.py`, ~L72–126; `config.yaml`, ~L257–264).

- **Directional vs nondirectional:** Returned as `directional_score` (trend) and `nondirectional_score` (momentum quality) (`factor_scoring.py`, ~L2275–2277).

- **BTC bias guard:** Applied only for `pair.type == "crypto"`, non-neutral `btc_bias`, defined `_dir`, and `"BTC" not in pair_display`; uses `_get_30d_correlation` from asset/benchmark series or heuristic (`scoring.py`, `calc_confluence`, ~L686–717).

---

### 2. Indicators and Weighting

| Indicator | Formula / parameter source | Evidence |
|-----------|---------------------------|----------|
| **RSI** | Wilder smoothing | `indicators.py`, `calc_rsi`, ~L60–91 |
| **Bollinger Bands σ** | Sample variance uses `/ p` (population std, **ddof=0**) | `indicators.py`, `calc_bb`, ~L237–241 |
| **ATR** | Wilder-style smoothed true range; series bundle passes **period 14** | `calc_atr` ~L127–144; `_calc_indicator_bundle` ~L1072 |
| **ADX / +DI / −DI** | Wilder DM/TR, DX series, smoothed ADX | `indicators.py`, `calc_adx`, ~L147–221 |
| **Snap indexing** | Latest bar index `L = len(cl)-1` for `snap` fields | `indicators.py`, `calc_indicators`, ~L1005–1054 |
| **Normalized snapshots** | `get_normalization_lookback(asset_type)` drives normalization pipeline | `indicators.py`, `calc_indicators_with_normalized`, ~L1086+ |

---

### 3. Asset Class and Score Group Differentiation

- **`_resolve_class_keyed`:** Prefer `score_group`, then `asset_type`, then `"default"` (`scoring.py`, ~L172–182).

- **PAIR_PROFILES / score_group:** `get_pair_profile`, `get_pair_score_group` (`scoring.py`, ~L96–169); thresholds via `ENGINE_A_SCORE_GROUP_THRESHOLDS` (`config.yaml`, ~L724–744).

- **ADX reachability:** `_adx_gate` resolves `ADX_TREND_MIN_CLASS` / `FACTOR_ADX_HARD_FAIL_CLASS` through `_resolve_class_keyed`; missing entries fall back to **30** / **10** (`factor_scoring.py`, ~L1128–1141).

---

### 4. Addons

| Addon | Status | Stale/missing behavior | Direction / denominator notes |
|-------|--------|------------------------|--------------------------------|
| **Forex carry** | ACTIVE (`_asset_addon` → `_carry_addon_with_status`) | Cost penalty path uses `get_carry_differential`; **`except Exception: pass`** (~L2079–2092) | Separate from `addon_norm` conviction blend |
| **Crypto funding + OI** | ACTIVE | `build_oi_context_for_factor_scoring` returns `None` on missing/stale OI (`~L240–253`, `~L255–266`) | Combo caps `FACTOR_CRYPTO_ADDON_COMBO_*`; `_asset_addon` ~L1366–1388 |
| **COT** | ACTIVE for types in `ENGINE_A_COT_ADDON_ASSET_TYPES` | `_cot_addon_with_status` errors → logged warning path (~L1245) | `_asset_addon` ~L1390–1396; `config.yaml` ~L782–787 |
| **Intermarket** | ACTIVE | Try/except logs debug on failure (`factor_scoring.py`, ~L2184–2186) | `apply_confirmation_to_score` adjusts bounded score (`~L2166–2179`) |
| **Macro / volume / divergence (legacy dict)** | ACTIVE bounded adjustments | Rich dict vs simple dict branching (~L2141–2153) | `_vol_adj`, `_macro_adj`, `_inter_adj` capped by `FACTOR_TOTAL_ADJ_CAP` (~L2152–2153) |
| **Research lab** | PARTIAL / gated | `ENGINE_A_RESEARCH_LAB_FACTORS.ENABLED` | `_research_lab_candidate_addon` ~L790–792 |
| **Structure context** | PARTIAL / gated | Skips if disabled or non-dict / exception (`~L2193–2215`) | `apply_structure_context_to_score` multiplier + cap (`structure_context.py`, ~L88–222) |

**Addon weight when unsupported:** For `addon_status == "unsupported"`, `_eff_base_w` / `_eff_mom_w` redistribute `_eff_addon_w` per `ADDON_UNSUPPORTED_SPLIT_TO_BASE` (`factor_scoring.py`, ~L1994–2005; `config.yaml`, ~L249–255). Conviction denominator is **not** inflated by a phantom addon slot.

---

### 5. Engine B Structure Integration

- **Inside factor score:** When `ENGINE_A_STRUCTURE_CONTEXT_ENABLED`, `apply_structure_context_to_score` may multiply/clamp final score (`factor_scoring.py`, ~L2193–2206).

- **Live overlay:** `analyze_pair` runs naked Engine B overlay when `use_naked_engine` and score clears threshold (~L11825+ in `athena.py`), adjusting levels and warnings.

- **Hard gate vs multiplier:** `structure_context` is a **bounded multiplier** on Engine A score when influence flags are on (`structure_context.py`, ~L159–217). Base Engine A path **does not require** Engine B confirmation.

- **Backtest BOS/CHoCH:** `_engine_a_structure_first_entry_check` / `_engine_a_structure_first_entry_passes` enforce optional `require_bos` / `require_choch` only when `ENGINE_A.structure_first_entry.enabled` (`backtest_runner.py`, ~L106–173, funnel ~L1871–1896).

- **RANGING:** Engine regime label for diagnostics comes from `detect_regime` (`regime.py`). Separate legacy **`trendState`** string is computed from ADX thresholds in `calc_confluence` (`scoring.py`, ~L802–813).

---

### 6. Threshold Calibration Assessment

- **Scan floors:** `ENGINE_A_SCORE_GROUP_THRESHOLDS` (`config.yaml`, ~L724–744).

- **FACTOR_CONVICTION_FLOOR:** Drives effective floor inside `final_score = base_score * (_eff_floor + (1.0 - _eff_floor) * conviction)` (`factor_scoring.py`, ~L1988–2035, ~L2055). YAML comment at `FACTOR_CONVICTION_FLOOR` (~L238) describes “combined conviction”; actual use is **inside the multiplicative final_score blend** — confusing operationally (see BUG-A-4).

- **RANGING / trend blocking:** `AUTO_TRADE_BLOCKED_TREND_STATES` applies to **`trendState`** (`config.yaml`, ~L952–956), sourced from `calc_confluence` (~L802–813), **not** from `regime.py` labels alone.

- **VOLATILITY_SCALER_BANDS:** `_volatility_scaler` reads `VOLATILITY_SCALER_BANDS` via `_resolve_class_keyed`; falls back to `VOLATILITY_SCALER_ATR_PCT_LOW/HIGH` and multiplier constants (`factor_scoring.py`, `_volatility_scaler`, ~L1576–1612).

- **A-only auto-trade arithmetic (checked-in config):** With `AUTO_TRADE_MIN_CONVICTION.default = 0.50` and `AUTO_TRADE_A_ONLY_WEIGHT` default/crypto **0.60** (`config.yaml`, ~L936–945), need `scoreNorm ≥ 0.50/0.60 ≈ 0.833` ⇒ raw **≥ 2.5** on `maxScore = 3.0`, while many crypto **scan** thresholds are **2.0** (~L724–744). Execution gate can **exceed** scan tier unless combined conviction is raised by aligned Engine B weights.

---

### 7. Dead Code and Dead Config

- **`VOTE_WEIGHTS`:** No production `.py` references found; legacy `votes` built from factor signs (`scoring.py`, ~L778–798).

- **`AUTO_TRADE_MIN_SCORE`:** Documented as informational; execution uses `combinedConviction` vs `AUTO_TRADE_MIN_CONVICTION` (`auto_trader.py`, `get_status`, ~L349–363; `config.yaml`, ~L912–942).

- **`FACTOR_SCORE_GROUP_MULTIPLIERS`:** Not loaded by Engine A v2; regression asserts absence from `CONFIG` (`tests/test_engine_a_confirmed_audit_fixes.py`, ~L336).

- **`CRYPTO_TRANSITION_PENALTY` / `CRYPTO_TRANSITION_PENALTY_ENABLED`:** Asserted absent from `CONFIG` (`tests/test_engine_a_confirmed_audit_fixes.py`, ~L338–339).

- **`INDICATOR_WEIGHTS.momentum.crypto.volume_momentum_spread`:** Often **inactive** unless microstructure populates `h4_snap` and branch conditions hold (`factor_scoring.py`, ~L722–751).

---

### 8. Live vs Backtest Parity

| Dimension | Live (`analyze_pair`) | Backtest (`backtest_runner`) |
|-----------|----------------------|------------------------------|
| Candle series | Fetched / preloaded → confirmed-only lists | Windows end strictly before decision timestamp (e.g. `d1_raw[i-MIN_BARS:i]`, `h4_raw[:h4_idx]`) (~L1663–1680) |
| Forming bar | Excluded from scoring (~L11251–11252) | Same intent via cutoff indexing |
| Indicators | `calc_indicators_with_normalized` (~L11377–11404) | Same + optional `_bt_indicators_from_cache` with `end_idx=i-1` / `h4_idx-1` (~L1688–1699) |
| `calc_confluence` | Same function | Same (~L1795–1815 region) |
| Regime smoothing | Optional `regime_context` passed through | Passed when caller wires it |
| Structure context | Applied inside `compute_factor_scores` when enabled | `_apply_engine_a_structure_context_to_bt_result` (~L276–287) |

**SUSPECTED (not fully traced):** Exact identity of every backtest funnel threshold vs live `get_min_confluence_threshold` / scanner quantile floors — parity beyond shared `calc_confluence` inputs was not line-complete in this audit.

---

### 9. Identified Issues — Ranked by Severity

**BUG-A-1**

**Severity:** MEDIUM  

**File:** `factor_scoring.py`  

**Line/function:** `_adx_gate`, ~L1121–1125  

**Evidence:** When both D1 and H4 ADX are missing, returns multiplier **0.5** unless `ADX_MISSING_BOTH_ABORT` forces abort (~L1122–1125).

**What it does:** Supplies mid-scale ADX credit without observing trend strength.

**What it should do:** Either abort (fail-closed) when ADX is required, or document and consistently label missing-ADX scoring as degraded.

**Impact:** Fail-soft scores can clear downstream gates if other factors are strong.

**Fix:** Align policy with risk expectations: default abort when ADX missing for asset classes that always compute ADX, or surface explicit `feed_status` warning promoted to scan diagnostics.

**Confidence:** CONFIRMED  

---

**BUG-A-2**

**Severity:** MEDIUM  

**File:** `scoring.py` ; `regime.py`  

**Line/function:** `calc_confluence` ~L802–813 (`trendState`); `detect_regime` (labels HIGH_VOLATILITY / LOW_VOLATILITY)

**Evidence:** Payload carries **`trendState`** (e.g. DEVELOPING, RANGING, DEAD RANGING) from ADX ladder logic vs **`regime`** dict built from `factor_result['regime']` sourced from `detect_regime` (`scoring.py`, ~L819–827; `factor_scoring.py`, ~L2014–2019).

**What it does:** Exposes two regime-like classifications that can disagree.

**What it should do:** Single canonical regime for execution/UI or explicit documented mapping between fields.

**Impact:** Misaligned automation or diagnostics if consumers mix `trendState` with `regimeName` / confidence_engine inputs.

**Fix:** Consolidate labels or add translator table in signal contract docs + regression asserts on field parity.

**Confidence:** CONFIRMED  

---

**BUG-A-3**

**Severity:** LOW  

**File:** `factor_scoring.py`  

**Line/function:** `compute_factor_scores`, forex branch ~L2079–2092  

**Evidence:** `except Exception: pass` around `get_carry_differential` carry pricing.

**What it does:** Silently drops carry-based `_cost_penalty`.

**What it should do:** Record failure in `feed_status` / logs for observability.

**Impact:** Operators cannot distinguish neutral carry from broken carry feed.

**Fix:** Debug/warn log + `feed_status["carry"] = "error"` branch.

**Confidence:** CONFIRMED  

---

**BUG-A-4**

**Severity:** LOW (documentation / operator hazard)  

**File:** `config.yaml` ; `factor_scoring.py`  

**Line/function:** `FACTOR_CONVICTION_FLOOR` comment ~L238 vs formula ~L1988–2055, ~L2055  

**Evidence:** Comment ties floor to “combined conviction”; implementation scales **`final_score`** via `_eff_floor + (1-_eff_floor)*conviction`.

**What it does:** Misleads readers tuning Engine A.

**What it should do:** Comment must describe the multiplicative final_score role.

**Impact:** Wrong manual tuning assumptions.

**Fix:** Rewrite YAML comment only (no threshold change).

**Confidence:** CONFIRMED  

---

**Operational arithmetic note (CONFIRMED — configuration interaction, not a wiring bug)**

With `AUTO_TRADE_MIN_CONVICTION` / `AUTO_TRADE_A_ONLY_WEIGHT` as in `config.yaml` (~L936–945) and crypto scan thresholds at **2.0** (~L734–744), **A-only** live auto-trades require **confluenceScore ≥ ~2.5** to satisfy `combinedConviction = scoreNorm × weight ≥ 0.50`. Aligned Engine B blending can raise `combinedConviction` without raising this implicit raw-score floor as aggressively.

---

### Overall Assessment

Engine A v2 centralizes quantitative scoring in `factor_scoring.compute_factor_scores`, orchestrated by `scoring.calc_confluence` and fed by confirmed-bar indicator snapshots on live paths plus aligned point-in-time windows in swing backtests. Historical concerns about forming-bar lookahead, unused `VOTE_WEIGHTS`, absent `FACTOR_SCORE_GROUP_MULTIPLIERS`, dead `AUTO_TRADE_MIN_SCORE` execution gate, and removed `CRYPTO_TRANSITION_PENALTY*` keys are **consistent with the current code and config** reviewed here.

Residual risks: **ADX-missing fail-soft**, **dual regime/trend semantics**, **silent carry failures**, and **operator confusion** between scan thresholds, `FACTOR_CONVICTION_FLOOR`, and A-only `combinedConviction` arithmetic.

---

## Verification

No automated test suite was executed as part of this document-only audit deliverable.
