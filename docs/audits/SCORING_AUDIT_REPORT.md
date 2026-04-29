# Athena Scoring Engine Audit Report

**Date:** 2026-04-08  
**Scope:** Full audit of Engine A, Engine B, and Forex Scoring Engine  
**Focus:** Signal direction logic (buy/sell) accuracy and scoring discrepancies

---

## Executive Summary

This audit analyzed the complete scoring structure across all three engines:
1. **Engine A** (`factor_scoring.py`) — Factor-based scoring with regime-aware weights
2. **Engine B** (`market_structure.py`) — Naked price action / market structure analysis  
3. **Forex Engine** (`forex_scoring.py`) — Dedicated rules-based forex scoring

**Overall Finding:** The direction logic across all engines is **correctly implemented** with no critical signal-generation errors identified. However, several **potential edge cases** and **minor discrepancies** were found that could affect signal quality in specific scenarios.

---

## 1. Engine A — Factor Scoring Analysis

### 1.1 Direction Determination Logic

**Location:** `factor_scoring.py:compute_factor_scores()` (lines ~1100-1130)

```python
dir_score = 0.0
if active_dir:
    dir_w_sum = sum(weights.get(f, 1.0) for f in active_dir)
    for f, s in active_dir.items():
        dir_score += (weights.get(f, 1.0) / dir_w_sum) * s

# Direction from WEIGHTED dir_score
if dir_score > 0:
    direction = "LONG"
elif dir_score < 0:
    direction = "SHORT"
else:
    # Tie-break: unweighted sum sign, then default LONG
    if dir_sum > 0:
        direction = "LONG"
    elif dir_sum < 0:
        direction = "SHORT"
    else:
        direction = "LONG"
```

### 1.2 Findings

| ID | Severity | Finding | Impact |
|----|----------|---------|--------|
| A1 | **LOW** | Default direction is LONG when `dir_score == 0` and `dir_sum == 0` | In rare edge cases with perfectly balanced indicators, system defaults to LONG. This is intentional but could produce false signals in ranging markets. |
| A2 | **INFO** | Correlation filtering is currently **disabled** | No impact on direction, but may affect score magnitude. Code comment indicates this was intentional. |
| A3 | **INFO** | `_directional_confidence_multiplier` smooths threshold transitions | Correctly implemented — prevents abrupt direction flips near zero. |

### 1.3 Indicator-to-Direction Mapping

The following directional factors contribute to `dir_score`:

| Factor | Positive (+) | Negative (-) |
|--------|--------------|--------------|
| `trend` | Bullish EMA alignment | Bearish EMA alignment |
| `momentum` | RSI > 50, MACD hist > 0 | RSI < 50, MACD hist < 0 |
| `derivatives` | Funding rate aligned | Funding rate adverse |
| `microstructure` | Order book imbalance bullish | Order book imbalance bearish |
| `carry` | Carry direction aligned | Carry direction adverse |

**Verdict:** ✅ All directional factors correctly map to LONG/SHORT signals.

---

## 2. Engine B — Naked Market Structure Analysis

### 2.1 Direction Handling

**Critical Note:** Engine B does **NOT** determine direction independently. It receives direction from Engine A and validates structural alignment.

**Location:** `market_structure.py:analyze_structure()` (line 799)

```python
def analyze_structure(
    self,
    d1_candles: list,
    h4_candles: list,
    h1_candles: list,
    current_price: float,
    direction: str,  # <-- Passed IN from Engine A
    atr: float,
    regime: str = "RANGING",
    ...
)
```

### 2.2 BOS (Break of Structure) Validation

**Location:** `market_structure.py:_detect_bos()` (lines 276-354)

```python
# BOS Bull: recent peak > previous peak AND current CLOSE > previous peak
bos_bull = False
if last_peaks[-1] > last_peaks[-2] and _last_close > last_peaks[-2]:
    bos_bull = True

# BOS Bear: recent trough < previous trough AND current CLOSE < previous trough  
bos_bear = False
if last_troughs[-1] < last_troughs[-2] and _last_close_bear < last_troughs[-2]:
    bos_bear = True
```

### 2.3 Direction Alignment Check

**Location:** `market_structure.py` (lines 1103-1105)

```python
bos_confirmed = (direction == "LONG" and bos_data["bos_bull"]) or (
    direction == "SHORT" and bos_data["bos_bear"]
)
```

### 2.4 Findings

| ID | Severity | Finding | Impact |
|----|----------|---------|--------|
| B1 | **INFO** | Engine B is a **validator**, not a direction generator | Correct design — prevents Engine B from overriding Engine A direction. |
| B2 | **LOW** | CHoCH (Change of Character) detection could conflict with BOS | If CHoCH signals reversal but Engine A direction is opposite, signal may be blocked. This is **intentional** conservative behavior. |
| B3 | **INFO** | Forex ADX gate (`ENGINE_B_FOREX_ADX_MIN`) can block valid signals | Default 25.0 ADX minimum may filter out valid ranging forex setups. Configurable via `config.yaml`. |
| B4 | **LOW** | Sweep detection uses swing highs/lows as reference | Falls back to `closes[-6]` when swing data unavailable — could produce false positives in choppy markets. |

### 2.5 Lifecycle State Machine

Engine B correctly tracks signal lifecycle:
- `invalidated` — Price breached SL level
- `expired` — Target already reached
- `candidate` — Awaiting structural confirmation
- `triggered` — Valid trigger at entry zone
- `armed` — Price testing entry zone
- `retracing` — Pullback in progress
- `confirmed` — Structural break confirmed

**Verdict:** ✅ No direction errors. Engine B correctly validates Engine A signals.

---

## 3. Forex Scoring Engine Analysis

### 3.1 Direction Determination Logic

**Location:** `forex_scoring.py:compute_forex_score()` (lines ~700-750)

```python
# Direction from higher base score wins
if trend_score >= bo_final:
    base_score = trend_score
    result.direction = trend_dir  # From trend gate
    result.signal_type = "TREND_PULLBACK" if trend_score > 0 else "NONE"
else:
    base_score = bo_final
    result.direction = bo_dir  # From London breakout
    result.signal_type = "LONDON_BREAKOUT"
```

### 3.2 Trend Direction Logic

**Location:** `forex_scoring.py:_check_trend_gate()` (lines ~200-280)

```python
# D1/H4 EMA alignment determines trend direction
if d1_ema21 > d1_ema50 and h4_ema21 > h4_ema50:
    trend_dir = "LONG"
elif d1_ema21 < d1_ema50 and h4_ema21 < h4_ema50:
    trend_dir = "SHORT"
else:
    trend_dir = None  # Mixed alignment = no trend signal
```

### 3.3 London Breakout Direction

**Location:** `forex_scoring.py:_london_breakout_score()` (lines ~450-520)

```python
# Breakout direction from Asian range break
if close > asian_high + (atr * 0.3):
    bo_dir = "LONG"
elif close < asian_low - (atr * 0.3):
    bo_dir = "SHORT"
```

### 3.4 Findings

| ID | Severity | Finding | Impact |
|----|----------|---------|--------|
| F1 | **MEDIUM** | When `trend_score == bo_final`, trend direction wins | If London breakout has equal score but opposite direction, trend direction is used. This could miss valid breakout reversals. |
| F2 | **LOW** | Hurst Exponent veto can block valid signals | `hurst_veto_trend` blocks trend signals in mean-reverting regimes. May be too aggressive for some pairs. |
| F3 | **INFO** | Session filtering (`_in_session`) affects signal availability | Signals outside London/NY sessions may be blocked even if technically valid. |
| F4 | **LOW** | COT boost direction check uses `trend_dir` before it's finalized | COT is computed before final direction is determined, but this is correct as COT should align with the intended trend direction. |

### 3.5 SMC (Smart Money Concepts) Integration

The forex engine includes three SMC upgrades:
1. **FVG Confirmation** — Fair Value Gap overlap bonus
2. **Liquidity Sweep Detection** — Stop hunt reversal bonus
3. **Volume Strength at Levels** — Asian range / Fib level volume bonus

**Verdict:** ✅ Direction logic is correct. F1 is a design choice, not a bug.

---

## 4. Cross-Engine Integration Analysis

### 4.1 Signal Flow

```
┌─────────────────┐
│   analyze_pair  │
│   (athena.py)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│  pair.type ==   │ YES │  forex_scoring  │
│    "forex"?     │────▶│  compute_forex  │
└────────┬────────┘     │     _score()    │
         │ NO           └────────┬────────┘
         ▼                       │
┌─────────────────┐              │
│  calc_confluence│              │
│  (Engine A)     │              │
└────────┬────────┘              │
         │                       │
         ▼                       ▼
┌─────────────────────────────────────────┐
│           res["direction"]              │
│  (LONG or SHORT determined here)        │
└────────────────────┬────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│         Engine B Validation             │
│  (if use_naked_engine=True)             │
│  - BOS alignment check                  │
│  - Room-to-move validation              │
│  - DXY correlation check                │
│  - SL/TP structural override            │
└─────────────────────────────────────────┘
```

### 4.2 Direction Consistency Check

| Stage | Direction Source | Can Override? |
|-------|------------------|---------------|
| Engine A (crypto/stock) | `dir_score` weighted sum | N/A — primary source |
| Forex Engine | `trend_dir` or `bo_dir` | N/A — primary source for forex |
| Engine B | Validates, does not override | **NO** — can only block signal |
| Scan/Execution | Uses Engine A/Forex direction | **NO** — passthrough only |

**Verdict:** ✅ No direction override conflicts. Engine B correctly acts as a filter, not a direction generator.

---

## 5. Potential Edge Cases & Recommendations

### 5.1 Edge Case: Zero Direction Score

**Scenario:** All directional indicators cancel out (`dir_score == 0`, `dir_sum == 0`)  
**Current Behavior:** Defaults to LONG  
**Risk:** Could produce false LONG signals in ranging markets  
**Recommendation:** Consider returning `None` or adding a "NO_SIGNAL" state when direction is indeterminate.

### 5.2 Edge Case: Forex Trend vs Breakout Tie

**Scenario:** `trend_score == bo_final` with opposite directions  
**Current Behavior:** Trend direction wins  
**Risk:** May miss valid breakout reversals  
**Recommendation:** Add a tie-breaker based on recency or momentum confirmation.

### 5.3 Edge Case: Engine B Blocks Valid Signal

**Scenario:** Engine A produces valid signal, Engine B blocks due to:
- `distance_to_res` < `min_room_atr` (LONG)
- `distance_to_sup` < `min_room_atr` (SHORT)
- Adverse DXY correlation
- Structural SL exceeds `MAX_SL_PCT`

**Current Behavior:** Signal is completely blocked (`return None`)  
**Risk:** May filter out valid signals in tight consolidation  
**Recommendation:** Consider downgrading to watchlist instead of full block.

### 5.4 Edge Case: Forex ADX Gate

**Scenario:** Valid forex setup with ADX < 25 (default `ENGINE_B_FOREX_ADX_MIN`)  
**Current Behavior:** `structure_ok = False`, signal blocked  
**Risk:** May miss valid mean-reversion setups  
**Recommendation:** Make ADX gate configurable per score_group (majors vs exotics).

---

## 6. Summary of Findings

### Critical Issues: **0**

### Medium Issues: **1**
- **F1:** Forex trend/breakout tie-breaker always favors trend direction

### Low Issues: **4**
- **A1:** Default LONG when direction indeterminate
- **B2:** CHoCH/BOS potential conflict (intentional)
- **B4:** Sweep detection fallback to closes[-6]
- **F2:** Hurst veto may be too aggressive

### Informational: **5**
- **A2:** Correlation filtering disabled
- **A3:** Directional confidence multiplier working correctly
- **B1:** Engine B is validator, not generator
- **B3:** Forex ADX gate configurable
- **F3:** Session filtering affects availability

---

## 7. Conclusion

The Athena scoring system demonstrates **correct signal direction logic** across all engines. No critical bugs were found that would cause the system to output incorrect directions (e.g., generating SELL when logic should produce BUY).

The architecture follows sound principles:
1. **Engine A** determines direction via weighted factor aggregation
2. **Forex Engine** determines direction via trend gate or breakout detection
3. **Engine B** validates but never overrides direction

The identified edge cases are **design choices** rather than bugs, and the system includes appropriate safeguards (confidence multipliers, tie-breakers, regime gates) to handle ambiguous scenarios.

---

*Report generated by Athena Scoring Audit Tool*
