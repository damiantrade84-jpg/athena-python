# Engine D / Scalp Lab Diagnostic Audit Report

**Date:** 2026-04-24
**Auditor:** Kimi Code CLI (evidence-only)
**Status:** DIAGNOSTIC / REPORT-ONLY - no thresholds changed, no live orders placed
**Safety:** PAPER_SOAK.ENABLED=true, REAL_ORDERS_ALLOWED=false verified

---

## 1. Executive Summary

Engine D (Scalp Lab) is **not broken** at the code level, but it is **extremely strict** and the **UI hides all blocked/no-setup rows**, making it appear as if "nothing is passing."

The most likely reason no setups appear is the combination of:
- `MIN_GRADE: "B"` (only A/B signals pass; C/D are filtered)
- `WITH_TREND_ONLY: true` (counter-trend setups blocked)
- `MIN_RR: 2.0` (natural structural TP must be >=2R)
- `VP_PROXIMITY_PCT: 0.30` (+-0.30% of price to be "at" a level)
- Missing/absent CVD or absorption on many symbols -> grade drops to C or D
- UI `renderScalpSignals()` only renders `data.signals`, not `data.skipped`

**Recommended first change:** Enable Diagnostic Mode in Scalp Lab UI (`DIAGNOSTIC` button added) to see WHY each symbol is skipped. Then decide whether to fix data sources or adjust gates based on evidence.

---

## 2. Code Path Map (Phase 1)

### 2.1 Route/API
- **Scalp Lab UI panel:** `static/index.html` -> `#panel-scalp`
- **Scan button:** calls `POST /api/scalp-scan`
- **Backend route:** `athena.py:api_scalp_scan()` (line ~8202)
- **Backend function:** `scalp_engine.py:run_scalp_scan(pairs_or_symbols)` (line ~1907)
- **Execute route:** `POST /api/scalp-execute` -> `athena.py:api_scalp_execute()`

### 2.2 Symbols Sent to Engine D
- Source: `scalp_engine.py:get_scalp_pairs()` (line ~2472)
- Returns enabled pairs with `source=mt5` (forex/commodity/index/stock) OR `source=binance` + `type=crypto`
- Override: `SCALP_ENGINE.SCALP_PAIRS` config list
- Default universe: ~54 symbols (14 forex majors + 3 exotics + 8 commodities + 8 indices + 12 stocks + 11 crypto)

### 2.3 Timeframes Required
| Purpose | TF | Config Key | Default |
|---------|-----|------------|---------|
| Structure/VP | M15 | `M15_CANDLES` | 500 |
| Context | M5 | `M5_CANDLES` | 1000 |
| Execution | M1 | `M1_CANDLES` | 300 |
| HTF Bias | H1 | `H1_CANDLES` | 300 |

### 2.4 Data Sources
| Asset Type | Source | Volume | CVD | Notes |
|------------|--------|--------|-----|-------|
| Crypto | Binance futures klines + aggTrade buckets | Real volume (Binance) | Binance aggTrade delta | `TRADE_BUCKET_VP_ENABLED=true` |
| Forex | MT5 `copy_rates_from_pos` | MT5 tick volume (noisy) | Synthetic candle-based | EODHD overlay optional |
| Commodity | MT5 | MT5 tick volume | Synthetic candle-based | EODHD overlay optional |
| Index | MT5 | MT5 tick volume | Synthetic candle-based | EODHD overlay optional |
| Stock | MT5 | MT5 tick volume or WS | Synthetic candle-based | Pre-market proxy only |

### 2.5 Engine D Run Context
- **NOT** part of normal `run_full_scan()` (Engine A/B/C consensus)
- **ONLY** called via Scalp Lab API or `api_scalp_execute()`
- Separate scan loop, separate risk engine call

### 2.6 UI Visibility Issue (Confirmed Bug)
- `api_scalp_scan` returns BOTH `signals` and `skipped`
- `renderScalpSignals(data.signals)` in `static/index.html` **ignores `data.skipped`**
- When no signals pass, UI shows: "No Scalps Found - Try again later or check MT5 connection."
- **Fix applied:** Diagnostic mode now returns and renders skipped rows with fail reasons.

---

## 3. Funnel Diagnostics (Phase 2)

### 3.1 Infrastructure Added
- **Module:** `scalp_audit.py`
- **Log path:** `logs/scalp_audit/engine_d_funnel.jsonl`
- **Fields:** 48 fields per row (timestamp, symbol, asset_type, all gate states, VP levels, CVD, absorption, VWAP, setup score/grade, RR, fail reasons, shadow proximity, etc.)
- **Shadow simulation:** `shadow_proximity_simulations()` tests 5 proximity variants without affecting live decisions

### 3.2 Integration
- `scalp_engine.py` now initializes `_funnel` dict per symbol
- `try/finally` wrapper guarantees every symbol emits a funnel row
- Major gate points update `_funnel["gate_result"]` and `_funnel["fail_reasons"]`
- Shadow proximity computed after `_locate_price_vs_vp()` and stored in `diagnostic_notes`

---

## 4. Is Engine D Being Called? (Phase 3)

**YES - confirmed by code inspection.**

- `POST /api/scalp-scan` -> `run_scalp_scan()` is invoked for every symbol in `get_scalp_pairs()`
- `api_scalp_execute()` also calls `run_scalp_scan([symbol])` before execution
- The `skipped` array in the response proves Engine D is evaluating each symbol and rejecting most

**Tests added:**
- `test_scalp_scan_route_exists` (skipped in CI due to Flask import, passes locally)
- `test_scalp_scan_returns_diagnostic_field`
- `test_scalp_scan_diagnostic_includes_fail_reasons`

---

## 5. Data Requirements Check (Phase 4)

### 5.1 Crypto
- Binance M1/M5/M15 klines available via `_scalp_fetch_candles()`
- Binance aggTrade buckets require `athena/microstructure/trade_bucket_store.py` DB to be warmed
- If no WS trades are feeding, CVD falls back to candle-based synthetic CVD (less reliable)
- Volume profile can build from real volume if buckets are fresh, else candle fallback

### 5.2 Forex
- MT5 lower timeframe candles available if MT5 connected
- MT5 tick volume is noisy; EODHD overlay is cache-only and may lag
- Real volume unavailable (tick volume proxy)
- CVD is synthetic only (`indicators.calc_cvd` candle approximation)
- Engine D runs on forex but absorption detection is explicitly downgraded (`MT5_ABSORPTION_MIN_COUNT: 2`)

### 5.3 Stocks/Indices/Commodities
- MT5 candles available
- Volume is tick volume or EODHD overlay (stocks: near-real-time WS; others: delayed)
- No true footprint/CVD source
- Engine D runs on these but data quality is lower than crypto

### 5.4 Provider Limitations
| Asset | Real Volume | True CVD | Footprint | Limitation |
|-------|-------------|----------|-----------|------------|
| Crypto | Yes | Yes (aggTrade) | Yes (buckets) | Requires active WS feed |
| Forex | No (tick proxy) | No (synthetic) | No | MT5 tick volume noisy |
| Stock | Partial (WS/EODHD) | No (synthetic) | No | Pre-market proxy only |
| Commodity | No (tick proxy) | No (synthetic) | No | No external overlay yet |
| Index | No (tick proxy) | No (synthetic) | No | No external overlay yet |

---

## 6. Engine D Gates (Phase 5)

### 6.1 Current Gate Values
| Gate | Value | Notes |
|------|-------|-------|
| `enabled` | `true` | Engine D active |
| `MIN_GRADE` | `"B"` | **A/B only; C/D filtered** (raised 2026-04-17 from C) |
| `MIN_GRADE_AUTO_EXECUTE` | `"B"` | Auto-trader same gate |
| `MIN_RR` | `2.0` | Structural TP must be >=2R |
| `WITH_TREND_ONLY` | `true` | Counter-trend blocked |
| `BIAS_REQUIRE_CONFIRMATION` | `false` | Allow if H1 bars insufficient |
| `SESSION_FILTER` | `false` | Session gate disabled |
| `VP_ENABLED` | `true` | Volume Profile required |
| `VP_PROXIMITY_PCT` | `0.30` | +-0.30% of price |
| `TRADE_BUCKET_VP_ENABLED` | `true` | Crypto prefers aggTrade VP |
| `TRADE_BUCKET_CVD_ENABLED` | `true` | Crypto prefers aggTrade CVD |
| `VWAP_ENABLED` | `true` | VWAP lean used |
| `AAA_ENABLED` | `true` | Triple-A sequence enabled |
| `ABSORPTION_VOL_MULT` | `2.0` | Volume > 2x SMA(20) |
| `MARKET_CANDLE_MAX_AGE_SEC` | `900` | 15 min stale threshold |
| `MAX_SPREAD_PIPS` | forex: 4, commodity: 8, index: 6, stock: 5 | Crypto disabled |
| `MAX_DAILY_LOSSES` | `3` | Hard daily stop |
| `CONSECUTIVE_LOSS_HALVE` | `true` | Halve after 2 losses |
| `SIZE_CUT_AFTER_2R` | `true` | Cut size after +2R day |

### 6.2 Grade Scoring Breakdown
| Component | Max Points | Notes |
|-----------|-----------|-------|
| Location (at VAH/VAL/LVN/POC) | 25 | at_vah/at_val = 25 pts |
| Absorption | 20 | Needs >=2 bars for MT5 |
| CVD confirmation | 15 | Missing on forex -> 0 or 5 pts |
| AAA sequence | 15 | Rare; usually 0 |
| VWAP alignment | 5 | Usually available |
| Session | 10 | London/NY overlap = 10 |
| HTF bias | 5 | Needs 200 H1 bars |
| Spread | +-5 | Wide spread = -5 |
| **Total** | **100** | **B grade = 60+ pts** |

**Key insight:** A setup with location (25) + neutral CVD (5) + session (7) + VWAP (5) = 42 points = **Grade D**.
To reach B (60), you need: location (25) + strong absorption (20) + CVD confirms (15) = 60.
That is a very high bar. Missing any one of these drops you to C or D.

### 6.3 Top Fail Reasons (Expected Distribution)
Based on code analysis, the most common skip reasons should be:
1. `grade_C_below_min` or `grade_D_below_min` - grade too low
2. `no_setup:balance_inside_va` or `no_setup:imbalance_outside_va` - price not near a valid level
3. `no_absorption_at_va_extreme` or `no_absorption_outside_va` - no volume confirmation
4. `cvd_against_reversion:...` - CVD opposes direction
5. `counter_trend:...` - direction vs HTF bias mismatch
6. `rr_below_min` - natural TP too close
7. `htf_bias_unavailable: insufficient H1 bars` - cannot confirm trend
8. `insufficient_m15_candles` - data missing
9. `spread_too_wide_...` - spread gate
10. `vp_invalid:...` - volume profile failed to build

---

## 7. Profile Proximity Special Check (Phase 6)

### 7.1 Current Proximity Logic
```python
proximity_pct = float(cfg.get("VP_PROXIMITY_PCT", 0.15)) / 100.0  # = 0.0030
_near(level) = abs(price - level) / level < proximity_pct
```

### 7.2 Proximity by Asset Class
| Asset | Price | 0.30% | In Pips/Points | Assessment |
|-------|-------|-------|----------------|------------|
| BTC | $80,000 | $240 | N/A | Reasonable for M15 |
| ETH | $3,000 | $9 | N/A | Reasonable |
| EUR/USD | 1.0800 | 0.00324 | 32.4 pips | **Very wide** - almost always "near" |
| XAU/USD | $3,300 | $9.90 | 990 pts | Wide but OK for gold M15 |
| S&P 500 | $5,500 | $16.50 | 16.5 pts | Reasonable |

### 7.3 Shadow Proximity Variants
The funnel now logs shadow simulations for:
- `current` (0.30%)
- `atr_10pct`
- `atr_15pct`
- `atr_25pct`
- `max(tick_size * 4, ATR * 0.15)`

**Expected result:** For forex, narrowing proximity to ATR-based values will likely REDUCE "near" hits (since 0.30% is already very wide). For crypto, ATR-based may be wider or narrower depending on volatility.

---

## 8. Volume/CVD Special Check (Phase 7)

### 8.1 CVD Availability
| Asset | CVD Source | Freshness | Quality |
|-------|-----------|-----------|---------|
| Crypto | Binance aggTrade buckets | 5 min (`TRADE_BUCKET_MAX_AGE_SEC: 300`) | High (real delta) |
| Forex/others | `indicators.calc_cvd()` candle approximation | Always available | Low (proxy) |

### 8.2 CVD Handling in Setup Logic
- `_check_trade_bucket_cvd()` returns `{"direction": None, "source": "unavailable"}` if buckets missing
- Falls back to `_check_cvd(candles_exec)` which uses candle close/low/high approximation
- Fallback CVD is always available but less reliable
- **No silent pass:** Missing CVD does not silently pass; it just gives neutral direction (reduces grade score by 10 pts)

### 8.3 Volume Profile Availability
- Crypto: trade buckets OR candle-based fallback
- Non-crypto: candle-based only (tick volume or EODHD overlay)
- If `vp_invalid` -> explicit skip reason logged

---

## 9. UI/API Contract Fix (Phase 8)

### 9.1 Changes Made
- `api_scalp_scan()` now accepts `"diagnostic": true` in POST body
- When diagnostic mode is ON, skipped rows are enriched with `_diagnostic` flag
- Response includes `pass_count`, `skip_count`, `diagnostic` fields
- UI added `DIAGNOSTIC` toggle button
- `renderScalpSignals()` now accepts `skipped` and `diagnostic` params
- `buildScalpCard()` renders skipped rows with red border and skip reason banner
- Execution button hidden for skipped rows

### 9.2 Diagnostic Mode Behavior
- Shows ALL symbols: PASS + BLOCKED + NO_SETUP + DATA_MISSING
- Each skipped card shows: "DIAGNOSTIC - SKIPPED" + reason text
- Status bar shows: `Scanned N | X pass | Y skip`

---

## 10. Test Results (Phase 10)

```
pytest tests/test_engine_d_audit.py
==============================
test_build_funnel_row_has_all_required_fields PASSED
test_funnel_log_write_and_read PASSED
test_shadow_proximity_does_not_mutate_live PASSED
test_shadow_proximity_variants PASSED
test_scalp_scan_route_exists SKIPPED (Flask import in CI)
test_scalp_scan_returns_diagnostic_field SKIPPED (Flask import in CI)
test_scalp_scan_diagnostic_includes_fail_reasons SKIPPED (Flask import in CI)
test_scalp_scan_non_diagnostic_does_not_enrich_skipped SKIPPED (Flask import in CI)
test_missing_cvd_creates_explicit_fail_reason PASSED
test_paper_mode_prevents_real_orders PASSED
test_unsupported_asset_type_classified PASSED
test_funnel_gate_result_values PASSED

Full suite: 665 passed, 4 skipped
```

**py_compile:** `athena.py`, `scalp_engine.py`, `scalp_audit.py` - all clean.

---

## 11. Final Report and Recommendations (Phase 11)

### 11.1 Is Engine D Being Called?
**YES.** Every symbol in `get_scalp_pairs()` is passed to `run_scalp_scan()`. The `skipped` array proves evaluation is happening.

### 11.2 Does UI Hide Non-Pass Rows?
**YES - in normal mode.** The original `renderScalpSignals()` only rendered `data.signals`.
**FIXED:** Diagnostic mode now shows skipped rows with fail reasons.

### 11.3 Supported Asset Types
- Crypto: Full support (best data quality)
- Forex: Supported but data-limited (tick volume, synthetic CVD)
- Commodity: Supported but data-limited
- Index: Supported but data-limited
- Stock: Supported but data-limited

### 11.4 Required Data Sources and Availability
| Data | Crypto | Forex | Other | Status |
|------|--------|-------|-------|--------|
| Lower TF candles | Yes Binance | Yes MT5 | Yes MT5 | OK |
| Volume Profile | Yes aggTrade / candles | Partial candles only | Partial candles only | Weak for non-crypto |
| CVD | Yes aggTrade delta | No synthetic | No synthetic | Weak for non-crypto |
| Absorption | Yes real volume | Partial tick proxy | Partial tick proxy | Weak for non-crypto |
| Spread | N/A | Yes MT5 | Yes MT5 | OK |
| HTF Bias | Yes Binance H1 | Yes MT5 H1 | Yes MT5 H1 | OK if 200 bars |

### 11.5 Pass/Watchlist/Blocked/No-Setup/Data-Missing Counts
**Cannot be determined without running a live scan.**
The funnel diagnostics (`logs/scalp_audit/engine_d_funnel.jsonl`) will populate these counts after the next scan.

To get counts, run:
```bash
python -c "from scalp_audit import read_funnel_rows; rows=read_funnel_rows(); from collections import Counter; print(Counter(r['gate_result'] for r in rows))"
```

### 11.6 Top 10 Expected Fail Reasons
1. `grade_C_below_min` / `grade_D_below_min` - grade gate too high
2. `no_setup:balance_inside_va` - price not near actionable level
3. `no_absorption_at_va_extreme` - no volume confirmation
4. `cvd_against_reversion:...` - CVD opposes setup
5. `counter_trend:...` - HTF bias mismatch
6. `rr_below_min` - natural TP < 2R
7. `htf_bias_unavailable` - insufficient H1 bars
8. `insufficient_m15_candles` - missing data
9. `spread_too_wide_...` - spread gate
10. `vp_invalid:...` - volume profile build failed

### 11.7 Are Thresholds Too High or Is Data the Bottleneck?
**Both.**
- `MIN_GRADE: "B"` is very strict. Grade C setups (40-59 pts) are common when any confirmation is missing.
- Grade scoring gives 20 pts for absorption, but MT5 tick-volume absorption is noisy and requires >=2 bars (`MT5_ABSORPTION_MIN_COUNT: 2`).
- CVD is worth 15 pts. On non-crypto, CVD is synthetic and often neutral -> loses 10 pts vs aligned CVD.
- Forex proximity at 0.30% is 32 pips - this is actually **too wide**, making "at level" easy but natural TP often < 2R (hence `rr_below_min`).

### 11.8 Shadow Proximity Results
**Pending live scan.** Shadow simulation code is in place. After next scan, check `diagnostic_notes.shadow_proximity` in funnel rows.

### 11.9 Recommended First Change
1. **Enable Diagnostic Mode in Scalp Lab UI** - click `DIAGNOSTIC` and run a scan.
2. **Review the skip reasons** that appear.
3. If most skips are `grade_C_below_min` or `grade_D_below_min`, the issue is strict grading, not missing data.
4. If most skips are `vp_invalid`, `insufficient_m15_candles`, or `MARKET_DATA_STALE`, the issue is data quality.
5. **Do not lower MIN_GRADE yet** - gather evidence from diagnostic mode first.

### 11.10 Safety Checklist
| Question | Answer |
|----------|--------|
| Should Engine D threshold be lowered? | **NOT YET** - wait for diagnostic evidence |
| Should Engine D proximity be changed? | **NOT YET** - shadow simulation will tell us |
| Should Engine D be crypto-only? | **Maybe** - non-crypto lacks real volume/CVD; consider a data-quality gate |
| Should Scalp Lab show blocked/no-setup rows? | **YES** - fixed via Diagnostic mode |
| Is paper/demo still safe? | **YES** - `PAPER_SOAK.ENABLED=true`, `REAL_ORDERS_ALLOWED=false` |
| Is real-money still blocked? | **YES** - `REAL_ORDERS_ALLOWED=false` verified |

---

## 12. Files Modified

| File | Change |
|------|--------|
| `scalp_audit.py` | NEW - funnel diagnostics, shadow proximity, JSONL logger |
| `scalp_engine.py` | `_funnel` dict per symbol, `try/finally` wrapper, gate logging, shadow sim |
| `athena.py` | `api_scalp_scan` diagnostic mode, enriched skipped rows |
| `static/index.html` | Diagnostic toggle button, skipped row rendering, `buildScalpCard` updates |
| `tests/test_engine_d_audit.py` | NEW - 12 tests for funnel, shadow, contract, safety |
| `docs/diagnostics/engine_d_scalp_audit.md` | NEW - this report |

---

*End of report. No live orders placed. No thresholds changed. Paper mode remains active.*
