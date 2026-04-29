# SENTINEL PRO v4.0 — FULL SYSTEM AUDIT
# Paste this into Claude Code. It will perform a complete audit.
# Generated: March 20, 2026
#
# INSTRUCTIONS FOR CLAUDE CODE:
# 1. Read CLAUDE.md first for system context and hard rules.
# 2. Read CLAUDE_CODE_IMPLEMENTATION.md for pending research-backed changes.
# 3. Then work through each audit section below sequentially.
# 4. For EVERY issue found: state the file, line number, current code,
#    what's wrong, and the exact fix. No assumptions. No hallucinations.
#    If you're unsure, say so and explain what you'd need to verify.
# 5. Do NOT skip sections. Do NOT summarize without checking code.
# 6. Use `grep`, `cat`, file reads to verify every claim.

# ═══════════════════════════════════════════════════════════════════
# AUDIT SCOPE: Every file in the project that contains logic.
# Time estimate: This is a large audit. Work methodically.
# ═══════════════════════════════════════════════════════════════════

---

## SECTION 1: NUMBER INPUTS & MATHEMATICAL CORRECTNESS

### 1.1 Factor scoring math (factor_scoring.py)
- [ ] Verify the multiplicative formula: `abs(dir_score) * (0.6 + nondir_norm * 0.4)`
  - Check that `nondir_norm = min(nondir_score / 3.0, 1.0)` cannot produce NaN or negative values
  - Check that `dir_score` division by `dir_w_sum` handles zero denominator
  - Check that `nondir_score` division by `nondir_w_sum` handles zero denominator
  - Verify output range: final_score should always be >= 0.0
- [ ] Verify FACTOR_MIN_DIRECTIONAL gate: `abs(dir_score) < _min_dir` → score=0
  - Check that _min_dir reads correctly from config (should be 0.25)
- [ ] Verify `_weighted_factor_score()`: weighted mean calculation
  - Check that `w_sum <= 0` returns None (not 0 or NaN)
  - Check that `use_abs=True` correctly takes absolute values for non-directional factors
- [ ] Verify `_candle_microstructure()`: check all divisions have guards against zero
  - `(close - open) / (high - low)` — what happens when high == low?
  - All outputs clamped to [-3.0, 3.0]?
- [ ] Verify z-score scaling for each indicator:
  - `volume_ratio`: `(volume_ratio - 1.0) * 3.0` — check clamping to [-3.0, 3.0]
  - `funding_rate`: `-funding_rate * 3000` — check clamping
  - `fib_proximity`: check that 0 maps to None (excluded, not zero)
- [ ] Verify correlation filter math:
  - `_pearson()`: check for division by zero when `den == 0`
  - `_apply_correlation_filter()`: verify weight reduction caps at 0.5 minimum
- [ ] Check that `_dynamic_regime_weights()` clamps between 0.3 and 2.5
- [ ] Check that `_bayesian_blend()` formula: `score * (0.7 + 0.3 * hist_wr)` — verify hist_wr defaults to 0.5 when missing

### 1.2 Forex scoring math (forex_scoring.py)
- [ ] Verify `_entry_quality()` RSI zones:
  - LONG: 35-55 = 1.0, 55-65 = 0.5, <35 = 0.2, >65 = 0.0
  - SHORT: 45-65 = 1.0, 35-45 = 0.5, >65 = 0.2, <35 = 0.0
  - Check that MAD z-score variant handles len(history) < window
- [ ] Verify `_hurst_exponent()` math:
  - Check for division by zero in `np.polyfit`
  - Check output is clipped to [0.0, 1.0]
  - Check that `max_lag * 2` guard prevents insufficient data errors
- [ ] Verify `_mad_zscore()`: check MAD < 1e-8 returns 0.0 (not NaN)
- [ ] Verify `DynamicForexWeights.score()`: `self.base + eq * self.rsi_w + cot * self.cot_w`
  - Check that output range is reasonable (should be 0.0-2.0 for forex)
- [ ] Verify `_check_trend_gate()` ADX filter:
  - `adx < trend_gate_adx_min` returns False — check float conversion safety
  - `margin = (d1_close - d1_ema200) / d1_ema200` — division by zero if ema200 == 0?
- [ ] Verify `_momentum_confirm()` handles None values safely
- [ ] Verify `_local_to_utc_hour()` timezone math:
  - `utc_hour = (local_hour - offset) % 24` — check negative hour wrap
  - Check SERVER_TZ_OFFSET_HOURS reads correctly from config (should be 2 for SAST)

### 1.3 Risk engine math (risk_engine.py)
- [ ] Verify position sizing formula:
  - `base * score_factor * sizing_override * dd_factor`
  - Check that volume never exceeds MAX_RISK_PER_TRADE (0.03)
  - Check that volume is always > 0 (never negative or zero)
- [ ] Verify drawdown calculations:
  - `drawdown_pct = (peak_equity - current_equity) / peak_equity`
  - Check for peak_equity == 0 division
  - Verify DRAWDOWN_REDUCE_THRESHOLD (0.10) halves size
  - Verify DRAWDOWN_STOP_THRESHOLD (0.15) stops all trading
- [ ] Verify Kelly criterion sizing if ADAPTIVE_KELLY_ENABLED:
  - Check that KELLY_FRACTION (0.5) is applied correctly
  - Check that win_rate and payoff_ratio inputs are validated
- [ ] Verify portfolio heat calculation:
  - Total risk across all positions should not exceed MAX_PORTFOLIO_HEAT (0.06)
- [ ] Check all tick value calculations:
  - Commodity: tick=0.01, contract=100, tick_val=1.0
  - Stock/crypto: tick=0.01, contract=1, tick_val=0.01
  - Forex: tick=0.00001, tick_val=1.0

### 1.4 Market structure math (market_structure.py)
- [ ] Verify `_find_zones()` prominence threshold: `atr * 1.5` — correct?
- [ ] Verify `_determine_sequence()` prominence: `atr * 0.8` — consistent with BOS?
- [ ] Verify `_detect_bos()` peak comparison logic:
  - `last_peaks[-1] > last_peaks[-2]` for bull BOS — correct directional check?
  - Check array bounds: what if peak_idx has only 1 element?
- [ ] Verify `_detect_sweep()`:
  - Reference level `closes[-6]` — why -6? Is this correct for 5-candle analysis?
  - Check ATR buffer: `0.3 * atr` — is this appropriate?
- [ ] Verify `_detect_fvg()`:
  - Bullish FVG: `prev_low > next_high` — this is correct (gap between candle 1 low and candle 3 high)
  - Bearish FVG: `prev_high < next_low` — verify
  - Note: line 274 has `float(candles[i]["high"])` result unused — is this a bug?
- [ ] Verify `calculate_confidence()`:
  - All checklist items produce boolean correctly
  - `pct = min(100, int((total_score / max_possible) * 100))` — check max_possible > 0
  - Verify strict vs flexible checklist mode logic
- [ ] Verify SL/TP calculations in `analyze_structure()`:
  - SL: `anchored_low - (atr * sl_mult)` for LONG — correct?
  - TP fallback: `current_price + (sl_dist * 2.0)` for LONG when no opposing zone — correct?
  - Check that sl_dist == 0 fallback to `atr * sl_mult` works

---

## SECTION 2: MECHANICAL FAILURE POINTS

### 2.1 Threading and concurrency (athena.py)
- [ ] Check that `run_full_scan()` ThreadPoolExecutor uses max_workers=3 (not higher)
- [ ] Check that all SQLite writes use WAL mode and timeout=15.0
- [ ] Check that `_regime_lock` in factor_scoring.py prevents race conditions
- [ ] Verify that `carry_feed`, `cot_feed`, `duka_volume` are non-blocking during scans
  - Each must return cached/neutral values if data not ready
  - Check for any `time.sleep()` calls inside these that could block

### 2.2 Data feed failures (athena.py)
- [ ] Verify fetch_candles() fallback chain: EODHD → YFinance
  - What happens when ALL sources fail? Does it return None or empty list?
  - Does the caller handle None/empty gracefully?
- [ ] Check cache TTL keys are uppercase: "H1", "H4", "D1" (not lowercase)
  - Search entire codebase for any lowercase cache key references
- [ ] Verify Binance WebSocket reconnection logic
- [ ] Verify EODHD WebSocket reconnection logic
- [ ] Check what happens when `_live_prices` dict has stale/missing data for a pair

### 2.3 Executor safety (mt5_executor.py, bybit_executor.py)
- [ ] Verify `_validate_exit_levels()` catches inverted SL/TP
  - LONG: SL must be below entry, TP must be above entry
  - SHORT: SL must be above entry, TP must be below entry
- [ ] Verify emergency close logic in bybit_executor:
  - Post-fill validation failure → emergency market close with `reduceOnly: True`
  - Check that `_set_trading_stop()` retries once (2s sleep) before emergency
- [ ] Verify CCXT retry loop: 3 attempts on `NetworkError`/`RequestTimeout` only
  - Check that other exceptions are NOT retried
- [ ] Verify MT5 TP2: placed as separate pending limit order at half volume
- [ ] Check signal freshness: `SIGNAL_MAX_AGE_SEC` (300s) — is it enforced?
- [ ] Check price drift rebase: >1% drift triggers SL/TP recalculation

### 2.4 Database integrity
- [ ] Verify `_init_audit_db()` creates all columns in both CREATE TABLE and migration list
- [ ] Check that `factors_json` column exists and is populated on every execution
- [ ] Check that backtest_results persists `engine` column correctly:
  - "forex_scoring" for forex Engine A
  - "factor_scoring" for non-forex Engine A
  - "naked_engine" for Engine B
- [ ] Verify `PRAGMA journal_mode=WAL` is set on both audit.db and candle_cache.db

---

## SECTION 3: ENGINE A LOGIC VERIFICATION

### 3.1 Confluence scoring (scoring.py)
- [ ] Verify vote slot count: should be 12 votes per CLAUDE.md
- [ ] Verify each vote's weight range matches config.yaml VOTE_WEIGHTS
- [ ] Verify session vote: adds `W_SESS * 0.5` to BOTH bull and bear
  - `_base_max` should subtract `W_SESS * 0.5` so confluencePct isn't overstated
- [ ] Verify tie-break: `bull >= bear → LONG` (intentional long bias)
- [ ] Verify `classify_signal_setup()` uses structured boolean flags, NOT string matching
- [ ] Verify `get_pair_vote_weights()` merges class weights + pair profile correctly
- [ ] Verify ranging penalties from config.yaml RANGING section:
  - dead ADX threshold per class
  - choppy ADX threshold per class

### 3.2 Factor scoring (factor_scoring.py)
- [ ] Verify directional factors: trend, momentum, derivatives, microstructure
  - Each produces a signed value (positive = bullish)
- [ ] Verify non-directional factors: trend_strength, volatility, volume, structure, carry
  - Each uses `abs()` values (always positive contribution)
- [ ] Verify regime weights from config.yaml map correctly to factor names
  - Check `_weight_key_map` dict maps factor names to config keys
- [ ] Verify that disabled factors (weight=0 in base) cannot be overridden by regime weights
  - Line: `weights[factor] = 0.0 if base_w == 0 else regime_weights.get(wk, base_w)`
- [ ] Verify COT contrarian logic:
  - forex/commodity with |z| >= 2.0 → reverse signal * 1.5
  - |z| < 1.0 → set to 0.0 (ignore)
  - Check that `as_of_date` for historical backtest lookup works correctly

### 3.3 Forex scoring (forex_scoring.py)
- [ ] Verify trend gate requires BOTH D1 EMA alignment AND ADX above minimum
- [ ] Verify session windows match CLAUDE.md:
  - London: 07:00-17:00 UTC
  - NY: 12:00-21:00 UTC
  - Asian: 00:00-08:00 UTC (JPY/AUD/NZD only)
- [ ] Verify `_ASIAN_SESSION_PAIRS` set contains exactly the right pairs
- [ ] Verify London breakout detection logic (if implemented)
- [ ] Verify Hurst exponent integration:
  - H < 0.45 → RSI pullback dominates (0.60 weight)
  - H > 0.55 → trend + COT dominate
  - H 0.45-0.55 → balanced

---

## SECTION 4: ENGINE B LOGIC VERIFICATION

### 4.1 Structure analysis (market_structure.py)
- [ ] Verify swing sequence detection: HH_HL, LH_LL, CONTRACTION, EXPANSION
  - Check that 3+ peaks and 3+ troughs are required
- [ ] Verify BOS detection is close-based (not wick-based)
- [ ] Verify sweep detection: wick beyond + close inside + directional candle
- [ ] Verify zone detection uses `find_peaks` with ATR-based prominence
- [ ] Verify FVG detection produces correct bullish/bearish gaps
- [ ] Verify checklist in `calculate_confidence()`:
  - structure_ok: not hard_counter AND (micro_aligned OR macro_aligned OR bos OR sweep)
  - location_ok: zone_ok OR (allow_breakout AND breakout_ok)
  - entry_ok: trigger OR breakout OR bos OR sweep
  - room_ok: distance >= ATR * min_room_atr
  - rr_ok: RR >= min_rr
  - space_ok: room_ok OR rr_ok
  - Flexible mode: structure AND location AND entry AND space AND macro
  - Strict mode: structure AND zone AND trigger AND room AND rr AND macro

### 4.2 Engine B AI (engine_b_ai.py)
- [ ] Verify AI is advisory only — does NOT affect pass/fail
- [ ] Verify `build_engine_b_signal_message()` does not contain any scoring logic
- [ ] Verify `get_engine_b_ai_verdict()` result is stored in `res["ai_analysis"]` only
  - It must NOT modify `res["passed"]`, `res["score"]`, or any checklist fields

---

## SECTION 5: BACKTEST VERIFICATION

### 5.1 Backtest parameters (athena.py — backtest_pair)
- [ ] Verify BT_MIN per class matches MIN_CONFLUENCE_CLASS per class:
  - crypto: 0.70 = 0.70
  - forex: 0.60 = 0.60
  - stock: 0.70 = 0.70
  - commodity: 0.70 = 0.70
  - index: 0.70 = 0.70
- [ ] Verify swing backtest: walks D1 bars, max hold 20 bars → TIMEOUT
- [ ] Verify intraday backtest: walks H4 bars, max hold 12 bars → TIMEOUT
- [ ] Verify TIMEOUT handling:
  - Force-closed at last bar's close
  - P&L calculated as actual R-multiple (capped ±5R)
  - Labelled "TIMEOUT" (not counted as loss or win)
- [ ] Verify `open_positions` counter:
  - Incremented on entry, decremented on exit
  - MAX_OPEN=3 concurrent positions in backtest
- [ ] Verify fee deduction:
  - FEE_PCT per asset class applied correctly
  - crypto: 0.0011, forex: 0.0004, commodity: 0.0004, stock: 0.0006, index: 0.0004
- [ ] Verify that `e200s` is NOT computed in backtest loops (variable was removed)
- [ ] Verify ATR source per asset class:
  - Non-crypto: D1_ATR
  - Crypto: H4_ATR

### 5.2 Backtest persistence
- [ ] Verify results write to `backtest_results` table with correct `engine` value
- [ ] Verify `atr_source` is recorded
- [ ] Verify SQN, Sharpe, Sortino, profit_factor calculations are correct
- [ ] Check for any look-ahead bias: does the backtest use future data at any point?

### 5.3 Known backtest bug
- [ ] Verify that COT/carry data is still `None` in backtests (known bug)
  - Trace: backtest_pair() → compute_factor_scores() → cot_feed.get_cot_z(as_of_date=?)
  - Is `bar_time` being passed correctly through the backtest pipeline?
  - Document where the pipeline breaks

---

## SECTION 6: UI ↔ SERVER DATA FLOW

### 6.1 Signal display (static/index.html)
- [ ] Verify `buildCard()` reads all fields from the signal object correctly:
  - pair, direction, confluenceScore, maxScore, confluencePct
  - price, sl, tp1, rr1
  - votes (bull/bear/neutral for each)
  - trendState, regime, signalClass
  - warnings array
- [ ] Verify `getConfluencePct()` handles both Engine A (0-3 scale) and forex (0-2 scale)
- [ ] Verify `formatConfluenceText()` produces correct qualitative labels
- [ ] Verify filter buttons work: all, trade, forex, crypto, commodity, index, LONG, SHORT, high
- [ ] Verify asset class filter dropdown filters correctly

### 6.2 Live price updates
- [ ] Verify `pollPrices()` calls `/api/prices` and updates ticker strip
- [ ] Verify price flash animation (green up, red down) triggers on change
- [ ] Verify WebSocket price data reaches `_live_prices` dict in athena.py
- [ ] Verify that `getSignalLivePrice()` returns current price for execution

### 6.3 Auto-trade UI
- [ ] Verify auto-trade toggle calls `/api/auto-trade` POST correctly
- [ ] Verify auto-trade banner shows: next scan time, trades today, last execution
- [ ] Verify that auto-trade continues when screen sleeps / browser tab inactive:
  - Auto-trader runs as daemon thread in Python (server-side)
  - UI polling is irrelevant — the scheduler runs independently
  - Confirm: `AutoTrader._scheduler_loop()` is a daemon thread started on server boot
  - Confirm: it does NOT depend on any UI interaction or browser session
- [ ] Verify `_can_execute()` reads per-class thresholds from CONFIG dict

### 6.4 Execution flow
- [ ] Verify `doExecute()` in index.html calls `/api/execute` with correct payload
- [ ] Verify server-side `api_execute()` flow:
  - Signal freshness check
  - Live re-analyze if stale
  - Price drift rebase
  - `_validate_exit_levels()`
  - `risk_check()` — NEVER bypassed
  - Execute via mt5_execute() or bybit_execute()
- [ ] Verify execution confirmation modal shows correct details
- [ ] Verify post-execution: audit_log entry written, trades tab updated

### 6.5 Backtest UI
- [ ] Verify pair selector populated from `/api/pairs` dynamically
- [ ] Verify backtest progress bar updates during run
- [ ] Verify backtest results display: SQN, Sharpe, Sortino, PF, trades, win rate
- [ ] Verify leaderboard loads from `/api/backtest-best`
- [ ] Verify Engine A vs Engine B backtest routing works correctly

### 6.6 Performance tab
- [ ] Verify `/api/performance` returns correct aggregated stats
- [ ] Verify failed executions load from `/api/failed-executions`
- [ ] Verify auto-trade log loads from `/api/auto-trade/log`
- [ ] Verify equity curve chart renders correctly

---

## SECTION 7: CONFIG CONSISTENCY

### 7.1 Threshold alignment
- [ ] BT_MIN must equal MIN_CONFLUENCE_CLASS for every asset class
  - Check both config.yaml values match exactly
- [ ] AUTO_TRADE_MIN_SCORE must be >= MIN_CONFLUENCE_CLASS for each class
  - crypto: 0.80 >= 0.70 ✓
  - forex: 0.65 >= 0.60 ✓
  - stock: 0.85 >= 0.70 ✓
  - commodity: 0.80 >= 0.70 ✓
  - index: 0.80 >= 0.70 ✓

### 7.2 Testing parameters (MUST be reverted before live)
- [ ] MAX_OPEN_POSITIONS: currently 20 — MUST be 5 for live
- [ ] MAX_CORRELATED_POSITIONS: currently 10 — MUST be 2 for live
- [ ] Flag these as CRITICAL if still at testing values

### 7.3 Config loading
- [ ] Verify config.py loads YAML and merges with defaults correctly
- [ ] Verify all CONFIG.get() calls have correct default values
- [ ] Check for any hardcoded thresholds in Python that should be in config.yaml

---

## SECTION 8: EDGE CASES & DEFENSIVE CODING

- [ ] Check all `_json_safe()` calls before `jsonify()`:
  - /api/scan response
  - /api/analyze response
  - /api/naked-analysis response
  - /api/compare-engines response
  - /api/scan-naked response
  - /api/backtest response
- [ ] Check for NaN/Inf leaks in any API response
- [ ] Check that pair lists: `ALL_PAIRS = FOREX + COMMODITY + INDEX + US_STOCK + ETF + JSE + CRYPTO`
  - JSE_PAIRS must be in this concatenation
- [ ] Check that `CandleBuilder.seed()` and `bulk_update_d1()` skip `enabled:False` pairs
- [ ] Check that `_resolve_scan_style()` is used for per-pair style resolution
- [ ] Verify `BybitWS` has `ping_interval=None` in websockets.connect()

---

## OUTPUT FORMAT

For each section, report:
```
## SECTION N: [name]
### [subsection]
- [x] PASS: [description] — verified at [file:line]
- [ ] FAIL: [description] — [file:line] — Issue: [what's wrong] — Fix: [exact code change]
- [ ] WARN: [description] — [file:line] — [concern, not broken but risky]
```

At the end, provide a summary:
```
## AUDIT SUMMARY
- Total checks: N
- Passed: N
- Failed: N (list each with priority: CRITICAL/HIGH/MEDIUM/LOW)
- Warnings: N
- Testing params still at elevated values: YES/NO
```
