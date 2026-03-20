# SENTINEL PRO v4.0 — Implementation Changes from Research Audit
# Upload this file to Claude Code for review and implementation.
# Generated: March 20, 2026

# ═══════════════════════════════════════════════════════════════════
# IMPORTANT: This document contains TWO separate sections.
#   Section A: Engine A (Z-Score Factor) — changes involve z-score
#              math, weights, and config thresholds.
#   Section B: Engine B (Naked Structure) — changes are PURELY
#              structural price-action logic. NO z-scores, NO
#              complex math scoring. Engine B's purpose is to
#              replicate naked chart trading using structure,
#              zones, triggers, and pass/fail rules only.
#
# DO NOT mix Engine A math concepts into Engine B.
# DO NOT add scoring formulas or z-score normalization to Engine B.
# Engine B = price action checklist. That's it.
# ═══════════════════════════════════════════════════════════════════

---

## SECTION A: ENGINE A CHANGES (factor_scoring.py, config.yaml, forex_scoring.py)

All changes in this section are z-score/factor/math related. These
apply ONLY to Engine A files and do not touch market_structure.py.

### A1. Enable carry for forex [HIGH PRIORITY]
**File:** config.yaml
**Line:** ~196 (FACTOR_WEIGHTS.forex)
**Current:**
```yaml
forex: { trend: 2.0, momentum: 1.0, volatility: 1.0, volume: 0.5, structure: 1.5, derivatives: 0.3, microstructure: 0.5, carry: 0.0 }
```
**Change to:**
```yaml
forex: { trend: 2.0, momentum: 1.0, volatility: 1.0, volume: 0.5, structure: 1.5, derivatives: 0.3, microstructure: 0.5, carry: 0.5 }
```
**Why:** Carry trade is academically the most proven FX strategy (Quantpedia: 7.27% annualized, Sharpe 0.29 on DB Currency Carry Index). Currently contributing zero signal to forex scoring despite carry_feed.py being fully functional with FRED data.
**References:** R17 Quantpedia, R18 Lustig/Roussanov/Verdelhan SSRN, R19 Hsu & Wang 2024 ScienceDirect
**Risk:** Low. Carry is a directional factor already plumbed through factor_scoring.py via `carry_z`. Weight 0.5 is conservative — below trend (2.0) and structure (1.5).

---

### A2. Raise LEARNING_MIN_TRADES from 5 to 30 [HIGH PRIORITY]
**File:** config.yaml
**Line:** ~156
**Current:**
```yaml
LEARNING_MIN_TRADES: 5
```
**Change to:**
```yaml
LEARNING_MIN_TRADES: 30
```
**Why:** Bayesian factor blending (adaptive_weights.py) needs statistically meaningful trade history. Below 30 trades per factor per asset class, empirical win rates are noise, not signal. The Bayesian prior (0.5) should dominate until sufficient data exists. Monte Carlo research (Carta & Conversano 2020) showed Kelly-style estimates need 40,000+ trades for stability — 30 is already aggressive for factor-level learning.
**References:** R25 Bocconi BSIC Monte Carlo analysis, R26 Rotando & Thorp 1992
**Risk:** None. This makes the system MORE conservative by requiring more evidence before adapting weights.

---

### A3. Revert testing parameters before live [HIGH PRIORITY]
**File:** config.yaml
**Lines:** ~129-130
**Current:**
```yaml
MAX_OPEN_POSITIONS: 20         # Raised for testing week — revert to 5 before live account
MAX_CORRELATED_POSITIONS: 10   # Raised for testing week — revert to 2 before live account
```
**Change to:**
```yaml
MAX_OPEN_POSITIONS: 5          # Production setting
MAX_CORRELATED_POSITIONS: 2    # Production setting — prevents concentration risk
```
**Why:** These were explicitly raised for testing per CLAUDE.md line 70-71. Must revert before any live capital deployment.
**Risk:** Critical if not reverted — 20 open positions with 10 correlated can blow through MAX_PORTFOLIO_HEAT.

---

### A4. Test nondir_norm denominator [MEDIUM PRIORITY — BACKTEST FIRST]
**File:** factor_scoring.py
**Line:** ~624
**Current:**
```python
_nondir_norm = min(nondir_score / 3.0, 1.0)  # normalize quality to [0, 1]
```
**Investigation:** Before changing, run a diagnostic across all 90 pairs to check the actual distribution of `nondir_score`. If the 95th percentile is below 1.5, the quality amplifier range is compressed to 0.6-0.8, limiting its discriminating power.
**Potential change (only if data confirms compression):**
```python
_nondir_norm = min(nondir_score / 2.0, 1.0)  # wider effective range if scores cluster low
```
**Why:** The multiplicative formula `|dir| * (0.6 + nondir/3 * 0.4)` is proven, but the denominator needs to match actual score distribution. If max observed nondir is ~1.5, dividing by 3.0 means the amplifier only ranges 0.6–0.8 instead of the intended 0.6–1.0.
**References:** R1 arXiv:2507.07107 cross-sectional optimization, R3 PointAlgo composite z-score
**DO NOT CHANGE without first logging actual nondir_score values across 50+ scans.**

---

### A5. Correlation decay sensitivity [LOW PRIORITY — BACKTEST]
**File:** factor_scoring.py
**Line:** ~160
**Current:**
```python
decay_factor = 0.94  # 6% decay per bar (standard in 2026 quant guides)
```
**Investigation:** Run backtest with 3 values: 0.90, 0.94, 1.0 (no decay). Compare SQN across asset classes. The filter is disabled in backtest by default (INDICATOR_CORRELATION_ENABLED: false), so this requires temporarily enabling it for a controlled test.
**Why:** The 0.94 value is cited as standard but lacks specific empirical validation in this codebase. Research confirms the 0.8 threshold is correct; the decay factor determines how recent the correlation must be.
**References:** R1 arXiv correlation filtering discussion

---

## SECTION B: ENGINE B CHANGES (market_structure.py ONLY)

═══════════════════════════════════════════════════════════════════
ENGINE B IS PURE PRICE-ACTION. These changes add structural
detection capabilities using OHLC data, swing analysis, and
candle patterns. NO z-scores. NO indicator normalization. NO
weighted scoring formulas. Engine B's pass/fail checklist stays
as-is — these changes improve what it DETECTS, not how it SCORES.
═══════════════════════════════════════════════════════════════════

### B1. Add CHoCH (Change of Character) detection [MEDIUM PRIORITY]
**File:** market_structure.py
**Add new method to NakedEngine class (after _detect_bos, ~line 199)**

CHoCH = when price breaks the swing that produced the last BOS, signaling
a potential reversal. This is purely structural — it looks at swing
highs/lows and candle closes, nothing else.

```python
def _detect_choch(self, highs: np.ndarray, lows: np.ndarray, atr: float) -> dict:
    """
    Detect Change of Character (CHoCH) — structural reversal signal.
    CHoCH occurs when price breaks the swing that produced the last BOS,
    indicating the trend structure has changed direction.
    
    This is a pure price-action detection — no indicators, no z-scores.
    
    Returns:
        choch_bull: True if bearish structure broke bullish (reversal up)
        choch_bear: True if bullish structure broke bearish (reversal down)
        choch_level: The price level that was broken
    """
    try:
        from scipy.signal import find_peaks
        
        peak_idx, _ = find_peaks(highs, prominence=atr * 0.8, distance=3)
        trough_idx, _ = find_peaks(-lows, prominence=atr * 0.8, distance=3)
        
        last_peaks = [highs[i] for i in peak_idx[-4:]]
        last_troughs = [lows[i] for i in trough_idx[-4:]]
        
        if len(last_peaks) < 3 or len(last_troughs) < 3:
            return {"choch_bull": False, "choch_bear": False, "choch_level": None}
        
        # Bullish CHoCH: price was making LH/LL (downtrend), then breaks
        # above the most recent Lower High — structure shifts bullish.
        # The key swing is the LH that preceded the last LL.
        was_bearish = (last_peaks[-2] < last_peaks[-3] and 
                       last_troughs[-2] < last_troughs[-3])
        choch_bull = was_bearish and highs[-1] > last_peaks[-2]
        
        # Bearish CHoCH: price was making HH/HL (uptrend), then breaks
        # below the most recent Higher Low — structure shifts bearish.
        was_bullish = (last_peaks[-2] > last_peaks[-3] and 
                       last_troughs[-2] > last_troughs[-3])
        choch_bear = was_bullish and lows[-1] < last_troughs[-2]
        
        choch_level = None
        if choch_bull:
            choch_level = float(last_peaks[-2])
        elif choch_bear:
            choch_level = float(last_troughs[-2])
        
        return {
            "choch_bull": choch_bull,
            "choch_bear": choch_bear,
            "choch_level": choch_level,
        }
    except Exception:
        return {"choch_bull": False, "choch_bear": False, "choch_level": None}
```

**Then wire into analyze_structure() — after BOS detection (~line 491):**
```python
# 3b. CHoCH Detection (structural reversal)
choch_data = self._detect_choch(h1_highs, h1_lows, atr)
```

**Add to the return dict (~line 614):**
```python
"choch_data": choch_data,
"choch_confirmed": (direction == "LONG" and choch_data["choch_bull"]) or
                   (direction == "SHORT" and choch_data["choch_bear"]),
```

**Then add CHoCH as an alternative entry qualifier in calculate_confidence() (~line 693):**
In the `entry_ok` line, add `choch_confirmed` as another valid entry reason:
```python
entry_ok = (
    trigger_ok
    or breakout_ok
    or bool(res.get("bos_confirmed"))
    or bool(res.get("liquidity_sweep"))
    or bool(res.get("choch_confirmed"))  # NEW: CHoCH as valid entry catalyst
)
```

**Why:** CHoCH captures early reversal entries that BOS continuation alone cannot. Research (DailyPriceAction 2025): "A CHoCH often comes right before price returns to fill Fair Value Gaps or sweep liquidity zones." This is a structural detection — it only looks at whether swing highs/lows broke the prior trend's key level.
**References:** R12 DailyPriceAction SMC, R23 SMC Market Structure
**What this does NOT do:** It does not add any score, weight, or z-score. It adds a boolean flag (`choch_confirmed`) that feeds into the existing pass/fail checklist as an alternative entry catalyst alongside BOS, trigger, and sweep.

---

### B2. Add volume confirmation on BOS for crypto/stocks [MEDIUM PRIORITY]
**File:** market_structure.py
**Modify _detect_bos() (~line 145)**

Volume confirmation checks whether the BOS candle had above-average volume.
This is a simple ratio check — not a z-score, not normalized. Just: was
the bar's volume higher than the recent 20-bar average?

**Add parameter and logic:**
```python
def _detect_bos(self, highs: np.ndarray, lows: np.ndarray, atr: float,
                volumes: np.ndarray = None) -> dict:
    """
    Detect Break of Structure (BOS) patterns using peak/trough analysis.
    Optional volume confirmation: BOS candle should have above-average volume
    to distinguish genuine breaks from low-conviction wick breaks.
    
    volumes: numpy array of bar volumes. None = skip volume check (forex).
    """
    try:
        from scipy.signal import find_peaks
        
        peak_idx, _ = find_peaks(highs, prominence=atr * 0.8, distance=3)
        trough_idx, _ = find_peaks(-lows, prominence=atr * 0.8, distance=3)
        
        last_peaks = [highs[i] for i in peak_idx[-3:]]
        last_troughs = [lows[i] for i in trough_idx[-3:]]
        
        if len(last_peaks) < 2 or len(last_troughs) < 2:
            return {
                "bos_bull": False, "bos_bear": False,
                "last_broken_high": None, "last_broken_low": None,
                "bos_volume_confirmed": False,
            }
        
        bos_bull = False
        last_broken_high = None
        if last_peaks[-1] > last_peaks[-2] and highs[-1] > last_peaks[-2]:
            bos_bull = True
            last_broken_high = last_peaks[-2]
        
        bos_bear = False
        last_broken_low = None
        if last_troughs[-1] < last_troughs[-2] and lows[-1] < last_troughs[-2]:
            bos_bear = True
            last_broken_low = last_troughs[-2]
        
        # Volume confirmation (only when volume data available)
        bos_volume_confirmed = True  # default True when no volume data
        if volumes is not None and len(volumes) >= 20 and (bos_bull or bos_bear):
            avg_vol_20 = float(np.mean(volumes[-20:]))
            last_vol = float(volumes[-1])
            if avg_vol_20 > 0:
                bos_volume_confirmed = last_vol >= avg_vol_20 * 1.0  # at or above average
            # If volume data exists but is all zeros (forex with no real vol), skip
            if avg_vol_20 == 0:
                bos_volume_confirmed = True
        
        return {
            "bos_bull": bos_bull,
            "bos_bear": bos_bear,
            "last_broken_high": last_broken_high,
            "last_broken_low": last_broken_low,
            "bos_volume_confirmed": bos_volume_confirmed,
        }
    except Exception:
        return {
            "bos_bull": False, "bos_bear": False,
            "last_broken_high": None, "last_broken_low": None,
            "bos_volume_confirmed": False,
        }
```

**Update the call in analyze_structure() (~line 491):**
```python
# Extract volumes for BOS volume confirmation (None for forex — no centralized volume)
h1_volumes = None
_has_vol = any(float(c.get("vol", 0)) > 0 for c in h1_candles[-5:])
if _has_vol:
    h1_volumes = np.array([float(c.get("vol", 0)) for c in h1_candles])

bos_data = self._detect_bos(h1_highs, h1_lows, atr, volumes=h1_volumes)
```

**Add to return dict:**
```python
"bos_volume_confirmed": bos_data.get("bos_volume_confirmed", True),
```

**Why:** Research (Alchemy Markets 2026): "A break without volume is like a promise without proof." For crypto and stocks where real volume exists, genuine BOS should show at least average volume. For forex (no centralized volume), the check is skipped (returns True by default).
**References:** R14 EBC Financial, R15 Alchemy Markets BOS Trading Guide
**What this does NOT do:** This is NOT a z-score or normalized volume indicator. It's a simple binary check: was the BOS candle's volume at or above the 20-bar average? Yes/No.

---

### B3. Upgrade FVG overlap to quality grade [LOW PRIORITY]
**File:** market_structure.py
**Modify _detect_fvg() and zone FVG overlap logic**

Currently FVG overlap is binary (True/False). Upgrade to include
size relative to ATR and fill percentage — still pure price-action
measurements, no scoring formulas.

**Replace the FVG zone overlap logic in analyze_structure() (~line 479-484):**
```python
# Determine FVG overlap with zones — graded by quality
fvgs = self._detect_fvg(h4_candles)
for zone in res_zones + sup_zones:
    overlapping_fvgs = [
        fvg for fvg in fvgs
        if not (zone["upper"] < fvg["bottom"] or zone["lower"] > fvg["top"])
    ]
    zone["fvg_overlap"] = len(overlapping_fvgs) > 0
    # FVG quality: size relative to ATR (larger = more significant)
    if overlapping_fvgs and atr > 0:
        largest_fvg = max(overlapping_fvgs, key=lambda f: abs(f["top"] - f["bottom"]))
        zone["fvg_size_atr"] = round(abs(largest_fvg["top"] - largest_fvg["bottom"]) / atr, 2)
    else:
        zone["fvg_size_atr"] = 0.0
```

**Why:** A large unfilled FVG at a zone is a stronger confluence than a tiny one. The size/ATR ratio is a direct measurement, not a score — it tells you how many ATRs wide the gap is.
**References:** R12 DailyPriceAction FVG fills, R13 Equiti sweep+FVG confluence
**What this does NOT do:** No weighting, no scoring, no normalization. Just attaches a measurement to the existing boolean flag for richer UI display and Claude Code advisory context.

---

## SECTION C: SHARED / CONFIG CHANGES

### C1. Fix COT/carry historical backtest [HIGH PRIORITY — ENGINE A]
**File:** factor_scoring.py
**Issue:** `derivatives=None` in all backtests. The `bar_time` parameter is correctly passed for historical COT/carry lookup, but backtest mode is not providing the necessary data through the pipeline.
**Action for Claude Code:** Trace the backtest call path from `backtest_pair()` in athena.py through to `compute_factor_scores()`. Verify that `bar_time` is being passed correctly and that `cot_feed.get_cot_z()` and `carry_feed.get_carry_z()` can resolve historical data when given an `as_of_date`. This is an existing bug noted in CLAUDE.md and user preferences.

### C2. Add bos_volume_confirmed and choch_confirmed to Engine B backtest results [MEDIUM]
**File:** athena.py (backtest_pair for engine="naked_engine")
**Action:** Ensure the new fields from B1 and B2 are persisted to `backtest_results` table so backtest analysis can evaluate their impact. Add columns to `_init_audit_db()` migration list if storing in audit.db.
**Remember:** `audit_log` schema auto-migrates on startup; new columns must be added to BOTH `CREATE TABLE` and the migration list in `_init_audit_db()`.

---

## RESEARCH REFERENCES (for Claude Code context)

These are the primary sources backing each change. Claude Code does NOT
need to verify these — they have been verified by web search and web fetch.

| Ref | Source | Validates |
|-----|--------|-----------|
| R1 | arXiv:2507.07107 (2025) ML Multi-Factor Trading, Sharpe > 2.0 | A1, A4, A5 |
| R2 | ACM (2025) ETH Multi-Factor, Z-score ±1.0, Sharpe 2.5 | A1 |
| R3 | PointAlgo (2025) TradingView Quant Engine | A1, A4 |
| R12 | DailyPriceAction (2025) SMC BoS and CHoCH | B1, B3 |
| R13 | Equiti (2025) Liquidity Sweeps Explained | B3 |
| R14 | EBC Financial (2025) SMC Strategy, $9.6T daily FX | B2 |
| R15 | Alchemy Markets (2026) BOS Trading Guide | B2 |
| R17 | Quantpedia FX Carry Trade, 7.27% annualized | A1 |
| R18 | Lustig/Roussanov/Verdelhan SSRN Currency Risk Factors | A1 |
| R25 | Carta & Conversano (2020) via Bocconi, Monte Carlo Kelly | A2 |
| R26 | Rotando & Thorp (1992) Half-Kelly sizing | A2 |

---

## HARD RULES REMINDER (from CLAUDE.md)

1. Never bypass risk_check() for any execution
2. Never hardcode thresholds in Python — use config.yaml
3. Cache TTL dict keys are uppercase "H1"/"H4"/"D1"
4. audit_log schema auto-migrates; new columns → both CREATE TABLE and migration list
5. Engine B AI is review-only — do NOT reintroduce AI as pass/fail gate
6. Non-blocking I/O: carry_feed, cot_feed, duka_volume must never block scan thread
7. _json_safe() applied to all API responses before jsonify()
