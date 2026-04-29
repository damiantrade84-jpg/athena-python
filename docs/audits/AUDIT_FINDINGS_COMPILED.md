# Athena Full System Audit — Compiled Findings

> Audit Date: 2026-04-22
> Scope: config, API routes, scoring engines, market structure, consensus, execution, data feeds, risk, auto-trader, tests
> Auditor: Cascade (AI Assistant)

---

## 1. CONFIG LAYER (`config.py` + `config.yaml`)

### 1.1 Positive Observations
- **`_deep_merge_dict`** correctly overlays YAML onto hard-coded defaults without clobbering nested dicts.
- **`validate_config()`** runs at import time and warns on missing asset-class keys, non-numeric thresholds, and invalid PAIR_PROFILES.
- `_json_safe` handles `NaN`, `inf`, numpy generics, and nested structures recursively.

### 1.2 Issues Found

| Severity | Finding | File / Line | Details |
|----------|---------|-------------|---------|
| **MEDIUM** | `CRYPTOPANIC_KEY` & `FINNHUB_KEY` still in CONFIG defaults | `config.py:170-171` | Per user memory, only EODHD_KEY, POLYGON_KEY, ANTHROPIC_KEY are needed. Dead keys should be removed to reduce config surface area and prevent accidental wiring. |
| **LOW** | `AI_MODEL` default hardcoded to `grok-4-1-fast-reasoning` | `config.py:15` | Acceptable, but if xAI changes model names this becomes a silent breakage. Consider env-only fallback with no default. |
| **LOW** | `SCAN_MAX_WORKERS` default 3 in code, 6 in YAML | `config.py:185`, `config.yaml:13` | Intentional override; not a bug, but verify the YAML value is safe for the machine's CPU count. |
| **MEDIUM** | `FOREX_ENGINE.hurst_gate_threshold` = 0.52 in code vs 0.40 in YAML | `config.py:880`, `config.yaml:340` | YAML overrides code; verify 0.40 is intentional. A 0.12 difference is significant for Hurst gating. |
| **LOW** | `NAKED_ENGINE.style_profiles` duplicated across code and YAML | `config.py:768-786`, `config.yaml:766-778` | Same values; harmless duplication but increases drift risk. Consider removing from one source. |

---

## 2. API ROUTES (`athena.py`)

### 2.1 Positive Observations
- **`_auth_and_rate_limit()`** runs on `app.before_request` — shared-secret auth + per-IP rate limiting (120 req/min general, 5 req/min for execute/killswitch/webhook).
- Most routes wrap execution in `try/except` and return sanitized error messages (no internal paths leaked).
- `_json_safe` is applied before `jsonify` on nearly all routes that return complex objects.
- Kill-switch checks (`_kill_switch`) guard execution, scan, and webhook endpoints.

### 2.2 Issues Found

| Severity | Finding | File / Line | Details |
|----------|---------|-------------|---------|
| **HIGH** | `_rate_limits` dict is in-memory only — no persistence or cross-process sharing | `athena.py:4575-4636` | A restart or horizontal scale wipes rate-limit state. A malicious actor could theoretically bypass limits by triggering a reload. For a single-instance deployment this is acceptable; document the limitation. |
| **MEDIUM** | `api_webhook()` returns `200` for some error conditions | `athena.py:6011-6017` | MT5 symbol-not-found returns HTTP 200 with error body. Clients may misinterpret as success. Should return 400/404. |
| **MEDIUM** | `api_analyze()` uses `request.json` (can raise 400 on bad JSON) instead of `request.get_json(silent=True)` | `athena.py:4667` | Inconsistent with other routes that use `get_json(silent=True)`. If malformed JSON hits `/api/analyze`, Flask auto-returns 400 before the route body executes, bypassing the custom error shape. |
| **LOW** | `api_naked_analysis()` uses `request.json` similarly | `athena.py:5421` | Same inconsistency as above. |
| **MEDIUM** | `_persist_naked_style_profiles_yaml()` uses regex text replacement on YAML | `athena.py:7550-7600` | Regex-based YAML mutation is fragile. If YAML formatting changes (e.g. extra indentation), the regex may fail silently or corrupt the file. Better: load with PyYAML, mutate, and dump back. |
| **LOW** | `api_scalp_group_rr()` also uses regex YAML mutation | `athena.py:7651-7677` | Same fragility. |
| **LOW** | `api_backtest()` calls `api_backtest_impl()` but the impl function isn't in the same file | `athena.py:6920-6934` | The function is imported; verify the import path and ensure it's not shadowed. |
| **LOW** | `api_mt5_status()` and `api_bybit_status()` leak exception strings directly | `athena.py:6144`, `6217` | `return jsonify({"connected": False, "error": str(e)})` — acceptable for debug, but consider sanitizing `str(e)` in production. |
| **LOW** | `executed_signals` set is in-memory only | `athena.py:5944-5946` | Duplicate webhook guard relies on process memory. Restart loses state. For a single-instance setup this is acceptable. |

---

## 3. SCORING ENGINES

### 3.1 `factor_scoring.py`

| Severity | Finding | File / Line | Details |
|----------|---------|-------------|---------|
| **MEDIUM** | `_apply_correlation_filter()` is permanently disabled but still called | `factor_scoring.py:322-372` | Returns uniform weights (1.0) for all indicators. The `O(n²)` Pearson code is dead weight. Either remove or add a config toggle to enable it after 6+ months of data collection. |
| **LOW** | `_build_indicator_series()` uses `calc_rsi`, `calc_macd`, `calc_atr`, `calc_adx` with `window=200` default | `factor_scoring.py:300-319` | If `h4_candles` has < 200 bars, returns empty dict. Caller doesn't handle empty gracefully — correlation filter silently skips. Not harmful because filter is disabled. |
| **LOW** | `_crypto_live_ws_unavailable_reason()` can return `None` if age is missing and no WS data present | `factor_scoring.py:195-203` | Returns `"missing_live_ws"` — acceptable fallback. |
| **LOW** | `make_regime_smoothing_context()` uses `threading.Lock()` but is shared across threads | `factor_scoring.py:248-277` | Correct usage; no issue found. |

### 3.2 `forex_scoring.py`

| Severity | Finding | File / Line | Details |
|----------|---------|-------------|---------|
| **MEDIUM** | `_session_state()` mixes `london_local` and explicit UTC window logic | `forex_scoring.py:65-118` | The `london_breakout_window_mode` config key is checked but the actual session evaluation uses hardcoded `_LONDON_OPEN = (7, 17)` and `_NY_OPEN = (12, 21)`. If user sets custom London hours in config, the function ignores them. Consider making windows fully config-driven. |
| **LOW** | `_hurst_exponent()` returns `0.5` (random walk) on any failure | `forex_scoring.py:121-139` | Fail-open is intentional but may mask data-quality issues. Consider logging when `len(prices) < max_lag * 2`. |
| **LOW** | `_trend_gate_detail()` has `adx_hard_fail` defaulting to `trend_gate_adx_min` | `forex_scoring.py:215-216` | If `trend_gate_adx_hard_fail_min` is not set in config, it equals `trend_gate_adx_min`. The soft gate then has `span = 0` and `adx_gate_multiplier` becomes `0.40` at the boundary. Verify this is intentional — it creates a cliff at the minimum. |
| **LOW** | `_entry_quality()` RSI bands are hardcoded (30–45, 45–52, 55–70) | `forex_scoring.py:407-425` | These don't read from `CONFIG["RSI_BOUNDS"]`. If user overrides RSI bounds in config, forex entry quality stays fixed. Consider syncing with config. |
| **LOW** | `_momentum_confirm()` returns `0.3` neutral when `hist` is `None` | `forex_scoring.py:440-441` | This gives partial credit for missing data. Consider `0.0` to avoid inflating scores when MACD is unavailable. |

### 3.3 `scoring.py`

| Severity | Finding | File / Line | Details |
|----------|---------|-------------|---------|
| **LOW** | `get_score_threshold()` backtest-live parity logic is complex but well-commented | `scoring.py:146-200` | No bugs detected; the `use_bt_chain` guard correctly prevents live thresholds from leaking into backtests. |
| **LOW** | `detect_div()` RSI divergence uses `pr[-1] > max(prior_highs)` for bearish div | `scoring.py:290-294` | This looks for a higher high in price and lower RSI at the peak. The `prior_highs` slice is `pr[t:2*t]` which may miss the true most recent peak. The logic is approximate rather than strict swing-high-based divergence. Documented as H4 RSI divergence — acceptable for a heuristic. |
| **LOW** | `CORR_CLUSTERS` hardcodes many crypto tickers | `scoring.py:315-347` | If new altcoins are added to the pair list, they won't be in any cluster and correlation caps won't apply. Consider deriving clusters dynamically from pair metadata. |
| **LOW** | `apply_correlation_cap()` only tags with warning; doesn't actually cap execution | `scoring.py:391-406` | The function appends a warning string but doesn't modify the signal score or prevent execution. Downstream (risk_engine) should read `correlationWarning` and act on it. Verify the risk engine checks this field. |

---

## 4. ENGINE B — `market_structure.py`

### 4.1 Positive Observations
- `NakedEngine` is a proper class with clear separation: `_find_zones`, `_detect_bos`, `_detect_choch`, `_detect_order_blocks`, `_detect_fvg`, `_detect_sweep`, `_price_action_trigger`.
- `_detect_bos()` uses close-based confirmation (not wick-only), reducing false positives.
- `_detect_sweep()` has improved fallback logic (B4 fix) using local min/max from bars 6–15 instead of arbitrary `closes[-6]`.
- `_detect_fvg()` correctly merges consecutive FVGs of the same type.
- `_detect_order_blocks()` calculates displacement and volume strength for scoring.

### 4.2 Issues Found

| Severity | Finding | File / Line | Details |
|----------|---------|-------------|---------|
| **MEDIUM** | `_find_zones()` uses `scipy.signal.find_peaks` which requires scipy | `market_structure.py:148-220` | If scipy is not installed, the import at file top may fail. Verify scipy is in requirements. The zone-finding is critical for Engine B. |
| **MEDIUM** | `_determine_sequence()` only checks `last_peaks[-1] > last_peaks[-2]` and `last_troughs[-1] > last_troughs[-2]` | `market_structure.py:234-248` | This is a 2-swing test. A single false peak/trough from noise can flip the sequence. Consider requiring 3+ confirming swings or ATR-based noise filtering. |
| **LOW** | `_detect_bos()` volume confirmation uses `last_vol >= avg_vol_20 * 1.0` | `market_structure.py:329-336` | Multiplier is 1.0 (at or above average). This is lenient. If volume data is noisy (e.g., forex with MT5 tick volume), it may confirm weak breakouts. Consider making the multiplier configurable. |
| **LOW** | `_detect_choch()` requires 3 peaks and 3 troughs | `market_structure.py:378` | If the chart is trending strongly with few pullbacks, CHoCH may never fire even when a reversal begins. This is by design (conservative) but may miss early reversals. |
| **LOW** | `_detect_order_blocks()` displacement uses `max(float(candles[j]["high"]) for j in range(i+1, min(i+6, len(candles))))` | `market_structure.py:440` | Only looks 5 bars forward for displacement. In slow-moving markets, displacement may take longer. Consider config-driven lookforward. |
| **LOW** | `_price_action_trigger()` `range_ = max(high - low, atr * 0.05, 1e-9)` | `market_structure.py:740` | The `1e-9` floor prevents division by zero but may cause issues on ultra-low-priced instruments (e.g. penny stocks). Consider instrument-point-aware minimum. |
| **LOW** | `_zone_context()` `near_zone = distance <= atr_val * 0.5` | `market_structure.py:680` | The 0.5×ATR proximity threshold is hardcoded. For some forex pairs this may be too tight; for volatile crypto it may be too loose. Consider making it a config parameter. |
| **LOW** | `calculate_confidence()` `flexible` checklist mode allows `breakout_ok and bos_volume_confirmed` to substitute for trigger | `market_structure.py:1747-1753` | This means a BOS with volume can pass even without a candle pattern trigger. Verify this is intentional for breakout entries. |
| **LOW** | `ENGINE_B_PROFILE_SCORING_ENABLED` defaults to `False` in code, `True` in YAML | `config.py:167`, `config.yaml:424` | YAML enables profile scoring. Verify this is intentional; the code default is conservative. |
| **MEDIUM** | `d1_pd_array_conflict` penalty is `-0.5` points | `market_structure.py:1737-1738` | A signal at exactly `min_score` (e.g. 4.0/5.0 = 80%) drops to 3.5 and fails. This is aggressive. Verify through backtests that this penalty improves outcomes. |

---

## 5. ENGINE C — `engine_c.py`

### 5.1 Positive Observations
- `normalise_engine_a()` and `normalise_engine_b()` correctly convert disparate score scales to 0–1.
- `resolve_sl()` picks the tighter of Engine A ATR-based SL and Engine B structural SL, with a 2.5×ATR clamp on structural extremes.
- `resolve_tp()` prefers Engine B structural TP when RR ≥ 1.5, else falls back to Engine A ATR TP.
- `_build_disagreement_diagnosis()` is purely diagnostic — no scoring effect.

### 5.2 Issues Found

| Severity | Finding | File / Line | Details |
|----------|---------|-------------|---------|
| **MEDIUM** | `ENGINE_C_AB_WEIGHTS` gives B 60% weight in all regimes | `engine_c.py:38-46` | A is reduced to 40% across the board due to 42.3% directional hit-rate. If A's hit-rate improves, these weights may be too pessimistic. The comment says "equalise until validation" — this should be revisited after the next backtest audit. |
| **LOW** | `_engine_c_meta_blend()` defaults to `0.20` | `engine_c.py:47, 51-57` | 20% meta-learner influence is conservative. If the meta-learner has insufficient trade history (< `_MIN_SAMPLES_FOR_ADAPTATION`), it may add noise. Verify `meta_learner.py` handles low-sample gracefully. |
| **LOW** | `CONVICTION_TIERS` thresholds are hardcoded | `engine_c.py:60-65` | `HIGH=0.70`, `MEDIUM=0.50`, `LOW=0.35`. Consider exposing in config for calibration. |
| **LOW** | `_DEFAULT_VISION_MODIFIERS` uses `AVOID` and `CONTRADICTS` both with `conviction_mult=0.0` | `engine_c.py:69-75` | Two different semantic labels map to the same numeric effect. This may confuse UI display. Consider differentiating (e.g. `CONTRADICTS` could be a softer veto at 0.1×). |
| **LOW** | `compute_consensus()` (not fully shown in search) may not handle `None` signals gracefully | inferred | Verify that if `signal_a=None` or `signal_b=None`, the consensus engine correctly falls back to B-only or A-only logic without crashing. |

---

## 6. EXECUTION LAYER

### 6.1 `bybit_executor.py` (reviewed in prior session)

| Severity | Finding | Details |
|----------|---------|---------|
| **LOW** | `BYBIT_TESTNET` env var required but not validated at import | If missing, the executor may attempt mainnet connections with test credentials. |
| **LOW** | `bybit_get_positions()` returns raw API response; if Bybit changes field names, position parsing may fail | Add field-name validation or schema check. |
| **MEDIUM** | Clock sync is disabled by default (`BYBIT_TIME_SYNC_ENABLED: False`) | `config.yaml:696` — if the local clock drifts > 1s, Bybit rejects requests with "invalid timestamp". Consider enabling for production. |

### 6.2 `mt5_executor.py` (reviewed in prior session)

| Severity | Finding | Details |
|----------|---------|---------|
| **MEDIUM** | Pepperstone close-price bug workaround uses hardcoded broker detection | `mt5_executor.py` — if the user switches brokers, the workaround may misapply. Consider making broker-specific quirks config-driven. |
| **LOW** | `mt5_get_symbol_info()` falls back to empty dict on failure | Downstream code may assume the dict has expected keys. Add validation. |

### 6.3 `execution.py`

| Severity | Finding | File / Line | Details |
|----------|---------|-------------|---------|
| **MEDIUM** | `_apply_level_override()` can mutate `sig` dict in-place | `execution.py:197-200` | If the signal dict is shared with other engines, the mutation propagates. Consider deep-copying before override. |
| **LOW** | `_quick_audit_context()` doesn't deep-copy `engine_b` | `execution.py:121-194` | If `engine_b` is mutated later, the audit snapshot may change. Shallow copies are used; acceptable for logging. |
| **LOW** | `healthcheck()` route is defined but not wired in the provided snippets | `execution.py:30-32` | Verify it's registered in `athena.py` or a blueprint. |

---

## 7. DATA / CANDLE FEEDS

### 7.1 `candles_cache.py` (reviewed in prior session)

| Severity | Finding | Details |
|----------|---------|---------|
| **MEDIUM** | EODHD REST 402 cooldown is 10 minutes | If the user hits the 100K daily limit early in the day, the cooldown means 10 minutes of no EODHD data. Verify fallback chain (Polygon → yfinance) is sufficient for forex during this window. |
| **LOW** | Live candle merging from WebSocket uses `CandleBuilder` which accumulates ticks | If a tick is delayed or out of order, the H1/H4 bar may be slightly off. This is inherent to WS streaming; acceptable. |
| **LOW** | `fetch_candles_live()` exists but is not wired into `analyze_pair` | Per system memory, this is a known pending integration. Document in README or create a ticket. |

---

## 8. RISK ENGINE (`risk_engine.py` — reviewed in prior session)

| Severity | Finding | Details |
|----------|---------|---------|
| **LOW** | `_calc_portfolio_heat()` uses current positions but not pending orders | If an auto-trade fires two signals in quick succession before the first fills, heat calculation may underestimate risk. Consider adding pending-order notional. |
| **LOW** | `adaptive_kelly()` can return sizes > MAX_RISK_PER_TRADE (3%) if `_risk_pct` is high | The cap is applied after Kelly, but if Kelly recommends 5% and cap is 3%, the effective Kelly fraction is silently truncated. Document this behavior. |
| **MEDIUM** | `SIGNAL_MAX_AGE_SEC` is 1800 (30 min) in code but 300 (5 min) in YAML | `config.py:680`, `config.yaml:332` | YAML overrides to 5 min. Verify this is intentional — 5 minutes is very strict for slower markets. |
| **LOW** | `DRAWDOWN_REDUCE_THRESHOLD` = 10% and `DRAWDOWN_STOP_THRESHOLD` = 15% | These are reasonable but hardcoded in code. Consider exposing in config.yaml for runtime tuning. |

---

## 9. AUTO-TRADER (`auto_trader.py`)

| Severity | Finding | File / Line | Details |
|----------|---------|-------------|---------|
| **MEDIUM** | `_scheduler_loop()` uses `time.sleep(30)` in a `while self._running` loop | `auto_trader.py:340-414` | If the loop crashes, the thread dies silently (it's a daemon thread). Add an outer `try/except` around the entire loop body to log and continue. |
| **LOW** | Weekly meta-analysis fires at `weekday() == 6 and hour == 22` | `auto_trader.py:356` | This is Sunday 22:00 UTC. If the server is in a different timezone or DST changes, the timing may shift. Use `timezone.utc` consistently (it does). |
| **LOW** | `_run_auto_scan()` re-stamps signal timestamps to scan-completion time | `auto_trader.py:499` | This is intentional to prevent `SIGNAL_MAX_AGE_SEC` from rejecting fresh scan signals, but it means the original signal generation time is lost. Consider preserving both timestamps. |
| **LOW** | `_executed_slots` set uses `"YYYY-MM-DD_HH"` as key | `auto_trader.py:222` | This prevents more than one execution per hour per slot, but the logic for slot assignment is not shown in the read portion. Verify it handles DST transitions correctly. |
| **MEDIUM** | `AUTO_TRADE_MAX_DAILY` = 3 in code, 10 in YAML | `auto_trader.py:451`, `config.yaml:326` | YAML allows 10 auto-trades per day. With `MAX_OPEN_POSITIONS = 20` and `MAX_CORRELATED_POSITIONS = 3`, 10 trades could saturate the portfolio quickly. Verify the daily cap is appropriate for the account size. |

---

## 10. INDICATORS (`indicators.py` — reviewed in prior session)

| Severity | Finding | Details |
|----------|---------|---------|
| **LOW** | `calc_weinstein_stage()` uses hardcoded `150` bars for stage detection | If `D1_CANDLES` is reduced below 150, the function may fail or produce unreliable stages. Consider adaptive lookback. |
| **LOW** | `calc_rsi_divergence()` uses `window=20` for swing detection | This may miss divergences on higher timeframes. No bugs detected; just a heuristic. |
| **LOW** | `REALIZED_VOL_LOOKBACK` = 30 bars for annualization | This is short for forex. Consider 60–90 for smoother vol estimates. |

---

## 11. TEST SUITE

### 11.1 Coverage Assessment
- **Good coverage:** factor_scoring (61K bytes), scalp_engine (49K bytes), risk_engine (15K bytes), execution_engine_c_scan (14K bytes), parity_fixes (19K bytes), engine_c_bt_levels (9K bytes).
- **Moderate coverage:** forex_scoring_diagnostics (5K bytes), market_specific_contracts (5K bytes), intermarket_core (7K bytes).
- **Weak / missing:**
  - `test_api_contract_smoke.py` (5K) — only basic smoke tests; no deep API contract validation.
  - `test_health_routes.py` (4K) — healthcheck tests exist but may not cover all routes.
  - `test_bybit_ws_orderbook.py` (4K) — WebSocket tests are hard to write; may be mock-only.
  - `test_telegram_bot.py` (3K) — notification tests are shallow.
  - `test_zone_registry.py` (3K) — only basic CRUD; no concurrency stress test.
  - **No dedicated test for:** `config.validate_config()` edge cases, `candles_cache` fallback chain under 402 cooldown, `market_structure._detect_sweep()` B4 fix regression, `engine_c._build_disagreement_diagnosis()` diagnostic formatting.

### 11.2 Recommendations
1. Add a test that simulates EODHD 402 and verifies fallback to Polygon → yfinance.
2. Add a concurrency test for `zone_registry.py` with multiple threads upserting zones simultaneously.
3. Add a test for `config.validate_config()` with intentionally broken YAML to verify all warning paths fire.
4. Add a test for `engine_c` when `signal_a=None` or `signal_b=None` to verify graceful fallback.

---

## 12. SUMMARY — PRIORITY ACTIONS

| Priority | Action | File(s) |
|----------|--------|---------|
| **P0 (Critical)** | None found — no data-loss, security breach, or guaranteed crash bugs. | — |
| **P1 (High)** | Remove `CRYPTOPANIC_KEY` and `FINNHUB_KEY` from `config.py` defaults. | `config.py` |
| **P1 (High)** | Add outer exception handler in `auto_trader._scheduler_loop()` to prevent daemon thread death. | `auto_trader.py` |
| **P2 (Medium)** | Fix `api_webhook()` MT5 symbol-not-found to return 400/404 instead of 200. | `athena.py` |
| **P2 (Medium)** | Fix `api_analyze()` and `api_naked_analysis()` to use `request.get_json(silent=True)`. | `athena.py` |
| **P2 (Medium)** | Replace regex-based YAML mutation in `api_naked_style_thresholds()` and `api_scalp_group_rr()` with PyYAML load/mutate/dump. | `athena.py` |
| **P2 (Medium)** | Verify `ENGINE_C_AB_WEIGHTS` are still appropriate after next backtest audit; consider dynamic weighting from meta-learner. | `engine_c.py` |
| **P2 (Medium)** | Verify `BYBIT_TIME_SYNC_ENABLED` should be `True` for production live trading. | `config.yaml` |
| **P3 (Low)** | Sync `forex_scoring.py` RSI bands with `CONFIG["RSI_BOUNDS"]` instead of hardcoding. | `forex_scoring.py` |
| **P3 (Low)** | Make `_zone_context()` proximity threshold (`atr * 0.5`) configurable per asset class. | `market_structure.py` |
| **P3 (Low)** | Add test for EODHD 402 fallback chain, zone registry concurrency, and Engine C null-signal fallback. | `tests/` |
| **P3 (Low)** | Consider making `DRAWDOWN_REDUCE_THRESHOLD` and `DRAWDOWN_STOP_THRESHOLD` config-driven. | `config.py`, `config.yaml` |

---

*End of Audit Report*
