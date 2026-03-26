# Sentinel Pro v4.0 — Claude Code Instructions

## Recent Changes (2026-03-26) — MT5 Data Migration & Pipeline Hardening

**Goal:** Finalize the migration of all traditional assets to direct MT5 broker integration, ensuring real-time data persistence and NY Close grid alignment.

**Full MT5 Asset Migration (`athena.py`, `config.yaml`):**
- Moved all **Forex, Commodities, Indices, US Stocks, and ETFs** to `"source": "mt5"`.
- Bypassed EODHD/Polygon/yfinance for these assets to eliminate REST API latency and data staleness.
- Refined `athena.py` startup to filter out EODHD WebSocket and seeding processes for MT5-sourced pairs.

**Live Price Bridging (`athena.py`):**
- Updated `fetch_mt5()` to populate the global `_live_prices` cache.
- Ensures that real-time execution levels for MT5-migrated assets are synchronized with terminal prices during scans and manual execution.

**H4/D1 Resample Alignment (`candles_cache.py`, `config.yaml`):**
- Implemented **`FOREX_H4_RESAMPLE_OFFSET_HOURS: 1.0`** to align H1 -> H4/D1 buckets with NY Close (5pm EST) brokers.
- This fixes the "disjoint candles" issue where local H4 buckets didn't match TradingView/Broker charts.

**Backtest Pipeline Hardening (`backtest_runner.py`):**
- Added native `mt5` source support to the backtest engine.
- MT5 assets now pull high-quality historical data directly from the broker terminal instead of falling back to EODHD REST, ensuring backtest/live parity.

**1000-Candle Depth Audit:**
- Confirmed that all MT5 requests use a 1000-candle depth to ensure EMA200 stability.

## Recent Changes (2026-03-25) — EMA200 Display Fix & Forex Data Source Testing

**EMA200 Chart Display Fix (`static/index.html`):**
- Fixed EMA200 line rendering bug that caused undefined values and gaps
- Added proper bounds checking to prevent array overflow when chart index exceeds EMA array length
- Improved EMA calculation accuracy with proper SMA seeding and null handling for initial periods

**Forex Data Source Testing (`athena.py`, `candles_cache.py`):**
- Enabled Polygon as primary source for GBP/USD test pair to compare native H4/D1 data vs resampled H1
- Fixed CandleBuilder routing to skip H4/D1 for Polygon sources
- Implemented deterministic H1 resampling for forex chart consistency

**Factor Scoring Enhancements (`factor_scoring.py`):**
- Added **`volume_momentum_spread` (VMS)** to microstructure directional factors.
- Measures the acceleration of volume-weighted directional conviction (Swift Algo X concept).
- Removed correlation filter complexity; simplified function to return all indicators with full weights.

**Scalp Trading Engine (Engine D) Implementation (`scalp_engine.py`, `execution.py`, `static/index.html`):**
- **Core Logic**: High-probability M15 structural zone detection + M5 tactical rejection/engulfing triggers.
- **AI Quality Grade**: Instant rule-based scoring (0-100, A-D grades) based on session, zone strength, and momentum.
- **UI Integration**: Added a dedicated **⚡ SCALP** tab to the dashboard for high-frequency setups.
- **Execution Safety**: Integrated with `risk_engine.risk_check()` for drawdown and sizing protection before hitting MT5.

**Desktop Control & Background Mode (`start_sentinel.bat`, `stop_sentinel.bat`, `run_background.vbs`):**
- **`run_background.vbs`**: Launches Sentinel Pro in windowless mode to prevent laptop sleep/minimization throttling.
- **`stop_sentinel.bat`**: Safely terminates background Python and Flask processes.
- Generated desktop shortcuts for **Start Background** and **Stop** for easy environment management.

**AI Vision Tuning (Engine C):**
- Simplified AI Vision confirmation to use **H4-only screenshots** (removed redundant D1/H1 captures).
- Reduces screenshot capture time and AI analysis latency while focusing on the primary execution timeframe.

## Debugging & audit playbook — cross-layer bugs (scoring, UI, candles)

**Why deep “scoring only” audits can miss real user-visible bugs**

- **Backend math** (`factor_scoring.py`, `scoring.py`, `calc_confluence`, thresholds) can be **correct** while **another layer** lies: **`analyze_pair` JSON fields**, **dashboard meters/labels** (`static/index.html`), or **a different API** (`/api/candles` vs scan fetch). Audits that only re-read scoring modules never touch the **mapping** from score → bar % → “WEAK / BUILDING / STRONG”.
- **Engine A vs Engine B** will often **disagree by design** (different inputs, gates, scales). Treat “huge gap” as **hypothesis**: part **real**, part **presentation** (e.g. confluence % denominator), part **data parity** (candle windows). Do **not** rationalize the full gap as architecture without **tracing one concrete signal** through every layer.

**When fixes in “the obvious file” do not resolve the symptom — pivot (do not loop the same audit)**

1. **Enumerate layers** for that symptom: data feed → cache → `fetch_candles` / `/api/candles` → `analyze_pair` → **response fields** → **UI formula** → TradingView/vendor reference.
2. **Pick one golden pair + TF** and trace **numbers**: raw `confluenceScore`, `confluencePct`, `maxScore`, `get_min_confluence_threshold(pair)`, scan tier from `_classify_signal`, and (if relevant) factor `final_score`. They must tell one coherent story; if not, the bug is in **wiring or display**, not necessarily in the core formula.
3. **Candle / EMA mismatches**: compare **`scan_candle_limits()`** vs chart **`limit`**, **forming-bar drop** in `analyze_pair`, **venue** (crypto: spot REST vs futures WS for H1), **forex**: canonical H1 + `resample_from_h1` in `candles_cache.py` (`utc=True` parse, **`origin="epoch"`** for stable H4/D1 buckets unless explicitly changing session policy). Check **TTL cache key** includes **`limit`** so chart and scan do not share the wrong series.
4. **Add a guardrail** after root cause: small **unit test** or **logged assert** on the invariant (e.g. “at scan threshold, UI % crosses strong band”; “closed bar count after drop ≥ X”).

**Mandatory checks when the user asks to “verify scoring / confluence”**

- [ ] **Display**: how `confluencePct` is computed vs **trade gate** — use **`get_min_confluence_threshold(pair)`** for scan-relevant display scaling (see commit **`20fd03a`** — fixes the **theoretical-max / optical illusion** bar where strong scores looked weak).
- [ ] **Do not conflate**: `MIN_CONFLUENCE_CLASS`, per-pair / subgroup overrides, **`get_min_confluence_threshold`**, and **`AUTO_TRADE_MIN_SCORE`** (auto-trader uses **`max`** of class and auto floor — see `auto_trader.py` / `CLAUDE.md` Signal Flow).
- [ ] **Crypto**: factor path vs vote/confluence path — **scales differ**; compare like with like.
- [ ] **Charts vs engine**: same symbol, TF, **bar count**, and **forming-bar rule** before blaming “EMA vs TradingView”.

**Lessons (2026-03)**

- **UI confluence “optical illusion”**: Bar used **`score / max_score`** (theoretical ceiling ~3); elite setups sat mid-bar. **Fix**: anchor display to **pair threshold** so ~**67%** aligns with “passing” intent — **`20fd03a`**.
- **Candle depth**: Low `*_CANDLES` + chart pulling more bars → **EMA / TV drift**; unify with **`scan_candle_limits()`** and documented chart **`limit`**.

## Recent Changes (2026-03-26) — Crypto H1 OHLCV fix, fetch routing, chart EMA, EMA200 slope clarity

**Goal:** Correct crypto H1 charts/indicators (full kline OHLCV, not close-only ticks); keep H4/D1 on native Binance REST; improve dashboard EMA vs TradingView; document EMA200 slope fields.

**Binance kline → CandleBuilder (`candle_feeds.py`):**
- `BinanceCandleWS` (`fstream.binance.com`, combined `@kline_1h`) calls **`CandleBuilder.on_kline(display, o, h, l, c, vol, t_ms, is_closed)`** — uses full Binance `k` fields (`o/h/l/c/v/x`). **Do not** feed klines through `on_tick(close)` only; that corrupted H/L, broke volume, and `INSERT OR REPLACE` overwrote good seed bars.
- `on_kline` maintains **H1 only** inside CandleBuilder; completed bars flush with **`tick_count=0`** (kline path).

**`fetch_candles` (`candles_cache.py`):**
- **`use_candle_builder = (tf == "H1" and source != "polygon")`** for all pairs (including crypto). **H4/D1** for crypto come from **`fetch_binance`** native intervals, not SQLite rollups.

**Crypto seed (`candle_feeds.py` `_seed_crypto`):**
- Seeds **H1 only** (500 × `1h` from Binance REST). Before count guard: **`DELETE FROM candle_cache WHERE pair=? AND timeframe='H1' AND tick_count > 0`** to purge old WS-corrupted rows, then re-seed if `COUNT < 100`.

**Forex/non-crypto H1:** unchanged — EODHD WS ticks still use `CandleBuilder.on_tick()` for price/volume.

**`BinanceLivePriceWS` (`!ticker@arr`):** execution/live header prices. **`BinanceCandleWS`:** H1 OHLCV for crypto cache. Both started/stopped with app lifecycle in `athena.py`.

**Dashboard chart (`static/index.html` — ACM / `_acmFetchAndRender`):**
- `/api/candles` **`limit=1000`** (server max) for chart loads — **EMA 200** starts further left vs 300–500 bars; TV may still have more off-screen history.
- **`_acmEmaLineData(candles, emaArr)`** — EMA 21/50/200 line series get only **finite** `{time, value}` points (no `null`s); avoids Lightweight Charts glitches. Legacy `Math.min(i, length-1)` on EMA arrays removed.

**EMA200 slope — two different metrics (`athena.py`, `indicators.py`):**
- **`signal["ema200Slope"]`** (UI meta chip): **D1** closes → `calc_ema(..., 200)` → **percent change over 20 daily bars**: `(e[-1]-e[-21])/e[-21]*100`. **Not** passed into `calc_confluence` (see F11 comment in `analyze_pair`).
- **`snap["ema200Slope10"]`** from `calc_indicators(candles)`: **10 bars** on **whatever timeframe** those candles are (10× H4 on H4 series, 10× D1 on D1 series). **Forex** trend gate uses **`d1_snap["ema200Slope10"]`**, not the chip value.
- Comparing the chip to **H4** TradingView EMA200 slope is misleading — different TF and lookback.

**Pair cleanup:** `FET/USDT` removed from pair lists, feeds, `static/index.html` TV map, `legacy/ccxt_executor.py`, `MANUAL.md`.

**Scoring vs chart candle depth (`config.py` / `config.yaml` + `CLAUDE.md` Signal Flow):**
- **`D1_CANDLES` / `H4_CANDLES` / `H1_CANDLES`** defaults **1001 / 1000 / 1000** so Engine A `analyze_pair` uses ~1000 closed D1 bars after drop and matches ACM **`/api/candles?limit=1000`** depth on H4/H1 (EMA values align at the right edge). **`analyze_pair`** still **drops the last (forming) bar** per TF before indicators.
- See **Signal Flow → “Scoring vs dashboard chart — candle windows”** for the full table and tradeoffs.

## Recent Changes (2026-03-25) — Crypto Engine A Simplification

**Goal:** Reduce crypto factor overload — fewer redundant/correlated indicators, live H1 candle data from Binance kline WS, fix phantom "missing data" penalty.

**Live crypto H1 + routing (superseded detail 2026-03-26):** `BinanceCandleWS` + `on_kline`, `fetch_candles` H1-only CandleBuilder for crypto, H4/D1 REST, `_seed_crypto` H1-only + `tick_count` purge — see **Recent Changes (2026-03-26)** above.

**Factor Weight Simplification (`config.yaml`):**
- `FACTOR_WEIGHTS.crypto`: microstructure zeroed (0 — candle proxies redundant with momentum/volume), carry zeroed (0 — never fires), structure reduced (0.5 — fib noisy on perps), derivatives tuned (1.2 — funding primary), volatility reduced (0.8), trend_strength explicit (1.0).
- `CRYPTO_FACTOR_WEIGHT_CAPS`: microstructure cap 0, derivatives cap 1.2.
- `INDICATOR_WEIGHTS.derivatives.crypto`: funding_rate 1.0 (up from 0.75), cot_z 0.2 (down from 0.25).
- Subgroup multipliers (`FACTOR_SCORE_GROUP_MULTIPLIERS`): removed microstructure boosts for all crypto subgroups.
- Effective active factor groups reduced from 7 to 6 for crypto.

**Code Guards (already existed in `factor_scoring.py` from prior work, now aligned with config):**
- `_crypto_supports_cot(pair)` — returns True only for BTC/ETH; all other crypto alts skip COT lookup entirely.
- `_optional_directional_keys("crypto", pair)` — returns `("funding_rate",)` for alts, `("funding_rate", "cot_z")` for BTC/ETH. No `carry_z` for any crypto.
- Carry factor excluded from crypto's `nondirectional_factors` mapping (line 640).
- Microstructure: crypto without live WS orderbook data gets all micro indicators as `None` (no candle-proxy fallback).
- `optional_coverage` = 1.0 when funding present for crypto alts (was 0.33 due to phantom missing carry/COT).

## Recent Changes (2026-03-24) — Monolith extraction (`candles_cache`, `candle_feeds`, `athena_runtime`)

**Goal:** Move candle cache, live feeds / WebSockets / `CandleBuilder`, execution routes, and scan/backtest entrypoints out of `athena.py` without changing runtime behavior.

**`candles_cache.py`**
- In-memory TTL candle cache (`_candle_cache` / lock), `extract_candles`, `candle_time_epoch_utc`, `merge_forex_forming_ws`, and `fetch_candles(...)` routing (live path + REST sources; injects `fetch_candles_live`, `fetch_binance`, `fetch_eodhd`, etc. from the monolith).
- `/api/flush-candle-cache` and scan paths use the **same** cache dict imported from this module.

**`candle_feeds.py`**
- `_live_prices` / lock, EODHD REST price poller for non-WS pairs, `BinanceLivePriceWS`, `EODHDWebSocketManager`, `CandleBuilder` (SQLite `candle_cache.db` next to project root), `fetch_candles_live`, `get_candle_builder` / `set_candle_builder`.
- Pair-dependent logic uses **`athena_runtime.rt()`** at **call time** (threads and seed): `ALL_PAIRS`, `CRYPTO_PAIRS`, `NON_WS_EODHD`, `eodhd_ticker_for_pair`, `get_eodhd_client` — all bound via `set_runtime(...)` at the end of `athena.py`.

**`athena_runtime.py`**
- `set_runtime(deps)` / `rt()` for split modules; **`executed_signals`** set shared with `/api/webhook` duplicate guard and execute path.

**Other split modules**
- `execution.py` — `register_execution_routes(app)` (quick execute, engine C scan/confirm, main execute, healthcheck).
- `scanner.py` — `run_full_scan` and scan helpers; `analyze_pair` still resolves via `rt()` / monolith.
- `backtest_runner.py` / `backtest.py` — Engine A/B backtest loops moved from the monolith; wired through `rt()`.
- `data_feeds.py` — shared `http_requests`, `_get_eodhd_client`, funding/OI helpers; feed **start** helpers may still delegate to the monolith where noted in code.
- `candle_manager.py` — thin facade over `athena_legacy.load()` for tools that should not import `athena.py` directly.
- `athena_legacy.py` — loads `athena.py` as a **file** module (`athena_monolith`) so imports never resolve to the `athena/` **package** by mistake.
- `app.py` — `create_app()` returns the Flask app from the legacy loader + optional `/healthz`.

**`athena.py`**
- Imports the above; **`set_runtime(SimpleNamespace(...))`** must include `NON_WS_EODHD`, `CRYPTO_PAIRS`, `eodhd_ticker_for_pair`, `get_eodhd_client` so `candle_feeds` works after import. Startup under `if __name__ == "__main__"` uses `set_candle_builder(CandleBuilder())`.

## Recent Changes (2026-03-24) — H1 Triple-Screen Upgrade

**Change 1 — H1 EMA alignment in forex entry quality (`forex_scoring.py`):**
- `_entry_quality(h1_snap, direction, rsi_history=None)` now applies an H1 EMA alignment modifier after the RSI quality score is computed.
- Uses existing H1 snapshot fields from `calc_indicators()` (`close`, `ema21`, `ema50`) — no new data fetch and no new indicator calculation.
- LONG logic:  
  - `close < ema21` → entry-quality penalty (pullback still in progress)  
  - `close > ema21 > ema50` → entry-quality confirmation bonus (anchor reclaimed)
- SHORT logic mirrors LONG:
  - `close > ema21` → penalty
  - `close < ema21 < ema50` → bonus
- Modifier is controlled by `FOREX_ENGINE.h1_ema_entry_filter` in `config.yaml` (default `true`) so the behavior can be disabled without code edits.

**Change 2 — Triple-screen AI Vision (`static/index.html` + `athena.py`):**
- `runECVisionConfirm` now captures three chart timeframes in sequence: D1 → H4 → H1.
- Frontend sends all three screenshots to `/api/chart-analysis`:
  - `image_d1` (D1)
  - `image` (H4, backward-compatible field)
  - `image_h1` (H1)
- Vision status text updated to reflect the D1+H4+H1 capture pipeline.

**`/api/chart-analysis` triple-screen mode (`athena.py`):**
- Endpoint now reads `img_h1 = data.get("image_h1")` and strips any data-URL prefix the same way as other image fields.
- `triple_mode = bool(img_d1 and img_h1)`:
  - If `true`, prompt follows Elder triple-screen structure:
    1) D1 strategic bias  
    2) H4 structure confirmation (momentum/EMA/MACD context)  
    3) H1 immediate entry quality (RSI pullback depth, EMA21 reclaim, trigger candle)  
    4) Three-way timeframe alignment (D1/H4/H1)  
    5) Per-style ratings mapped to TF intent (scalp=H1, intraday=H4, swing=D1)
  - If `false`, dual-timeframe behavior remains unchanged (backward compatible).
- Vision `max_tokens` increased from 800 to 950 for triple-screen analysis.
- Triple-screen responses include `tf: "D1+H4+H1"` and `triple_tf: True`.

## Recent Changes — Deep Engine Audit Fixes

**CRITICAL — Backtest calc_levels missing style (ATR1):**
- All 4 backtest `calc_levels` calls in `athena.py` now pass `style=effective_style` (or `style=resolved_style` for Engine B BT). Previously all backtests defaulted to swing ATR multipliers regardless of actual style, making scalp/intraday backtest SL/TP 2-3x too wide.

**HIGH — FVG label inversion (B1):**
- `_detect_fvg` in `market_structure.py`: swapped labels. `prev_low > next_high` (gap down) now correctly labelled "bearish". `prev_high < next_low` (gap up) now correctly labelled "bullish". Mitigation checks also corrected: bearish FVG mitigation checks `high >= midpoint` (rally into gap), bullish checks `low <= midpoint` (retrace into gap).

**HIGH — trend_strength weight key separation (A1):**
- `trend_strength` (ADX magnitude, non-directional) now has its own key in `FACTOR_WEIGHTS` and `REGIME_WEIGHTS` in `config.py`, independent from directional `trend` (EMA alignment). Updated `_weight_key_map` in `factor_scoring.py`. Regime weights: TRENDING=1.5, RANGING=0.5, HIGH_VOL=1.0, LOW_VOL=0.8.

**MEDIUM — Sweep label inversion (B3):**
- `_detect_sweep` in `market_structure.py`: wick below + close above now correctly labelled `bull_sweep` (was `bear_sweep`). Wick above + close below now `bear_sweep` (was `bull_sweep`). SL override consumer updated to match: LONG uses `bull_sweep`, SHORT uses `bear_sweep`.

**MEDIUM — DynamicForexWeights sum-to-1.0 (F2):**
- `forex_scoring.py`: all regimes now sum to exactly 1.0 (MEAN_REVERTING: base=0.20+rsi=0.60+cot=0.20, TRENDING: 0.55+0.20+0.25, NEUTRAL: 0.40+0.40+0.20). Removed backtest base split that broke weight sums.

**MEDIUM — Hurst exponent math (F3):**
- `forex_scoring.py`: changed `np.sqrt(np.std(...))` to `np.std(...)` and removed `* 2.0` correction. Standard variance-of-lagged-differences estimator: `poly[0]` from log-log fit gives H directly.

**MEDIUM — Forex bonus saturation (F1):**
- `forex_scoring.py`: SMC bonuses (FVG, liquidity sweep, volume) changed from additive to multiplicative. Prevents any base score ≥0.10 from immediately hitting the 1.0 clamp. New multipliers: FVG=1.20x, liquidity=1.15x, volume=up to 1.10x.

**MEDIUM — Timeframe alignment (CE1):**
- `scoring.py`: added `_tf_score_proxy()` to build lightweight per-TF directional scores from each timeframe's indicator snapshot. `compute_confidence` now receives distinct D1/H4/H1 proxies instead of the same factor_result duplicated.

**MEDIUM — Dead code removal (A2/A3):**
- Removed `_dynamic_regime_weights()` and `_bayesian_blend()` from `factor_scoring.py` — both defined but never called.

**MEDIUM — Regime classification (R1):**
- `regime.py`: low ADX + collapsing momentum now classifies as RANGING (trend loss) instead of HIGH_VOLATILITY. Actual HIGH_VOLATILITY now requires BB width confirmation (bb_width_pct ≥ 75 while ranging). Removed dead `h4_snap.get("adxSlope", 0)` statement.

**MEDIUM — Correlation filter decay (accepted suggestion):**
- `factor_scoring.py`: removed `* 0.94` static decay in `_apply_correlation_filter`. The decay created a dead zone where correlations 0.80-0.85 never triggered the filter.

**LOW — Signal classification wired (A6/A7):**
- `scoring.py`: `classify_signal_setup()` now called in `calc_confluence()`, populating `signalClass` and `entryMode` dynamically instead of hardcoding to `"trend_continuation"` / `"trend"`. The derived signal type feeds into confidence engine's regime_fit scoring.

**LOW — Unused tp1_mult statements (ATR4):**
- Fixed 3 no-op `lvl["mults"]["tp1"]` statements in backtest loops to proper `tp1_mult = lvl["mults"]["tp1"]` assignments.

**MEDIUM — COT z-score clamp escape (A4):**
- `factor_scoring.py`: `cot_z` after fade-the-herd logic (`-_cot * 1.5`) now clamped to ±3.0. Previously could reach ±4.5, exceeding the system-wide z-score bound enforced by `feature_normalizer.py` on all other indicators.

**MEDIUM — Sweep reference refactored to swing levels (B2):**
- `market_structure.py`: `_detect_sweep()` now accepts `swing_high`/`swing_low` parameters from `find_peaks` (same prominence/distance as BOS detection). Reference levels are now structural swing highs/lows where retail stops cluster per SMC methodology, replacing the arbitrary `closes[-6]`. Falls back to `closes[-6]` only when `find_peaks` fails.

**MEDIUM — RSI divergence detection (X2):**
- `scoring.py`: `detect_div()` now compares RSI at the exact bar of the prior price peak/trough (per Wilder 1978 / Murphy definition). Previously used `max(rm)` — the highest RSI in a window, not the RSI at the actual price extreme.

**LOW — London breakout UTC fallback (F4):**
- `forex_scoring.py`: Asian candle detection now applies `timezone.utc` to naive timestamps before hour comparison. Prevents incorrect session classification when candle timestamps lack timezone info.

**NOTE — X1 (AUTO_TRADE_MIN_SCORE) is NOT an issue:**
- `auto_trader.py` uses `AUTO_TRADE_MIN_CONVICTION` (already a per-class dict at line 392-396), not `AUTO_TRADE_MIN_SCORE`. The flat 0.70 value in config.py is a legacy key not consumed by the auto-trader.

## Recent Changes (2026-03-23) — Scoring Group Audit (Engine A + B + C)

**Scoring subgroup routing (separate from ATR style):**
- Added `get_pair_score_group(pair)` in `scoring.py` to resolve subgroup identity used for confluence/scoring routing.
- Added `get_min_confluence_threshold(pair)` in `scoring.py` with priority:
  1) `PAIR_PROFILES[...].min_confluence`
  2) `MIN_CONFLUENCE_GROUP[type][score_group]`
  3) `MIN_CONFLUENCE_CLASS[type]`
- `run_full_scan()` now uses `get_min_confluence_threshold(pair)` (instead of class-only fallback), and `_classify_signal()` uses the same resolver when `scanThreshold` is missing.

**Engine A subgroup alignment:**
- `analyze_pair()` now computes `scoreGroup` per pair and includes it in returned signals.
- Forex path now passes `score_group` into `compute_forex_score(...)`.
- Forex scoring failure no longer falls back to factor scoring; the pair is skipped to avoid cross-engine contamination.
- Forex regime for TP/SL is now detected from H4 snapshots (`detect_regime`) and used in `res["regime"]["state"]` so style ATR widening/tightening is not hardcoded to ranging.

**Factor engine subgroup controls:**
- Added `_apply_pair_profile_weight_rules(...)` in `factor_scoring.py`:
  - Maps legacy pair-profile vote overrides (`h4_fib`, `h1_bb`, etc.) into factor-group weight adjustments.
  - Applies optional `FACTOR_SCORE_GROUP_MULTIPLIERS[score_group]`.
- This restores practical influence of pair profile weighting on the current factor engine.

**Engine B subgroup controls:**
- `_naked_scan_style_profile(style, score_group=None)` now supports subgroup-specific strictness overrides via:
  - `CONFIG["NAKED_ENGINE"]["score_group_overrides"][score_group][style]`.
- Major Engine B call paths now pass subgroup context (scan, compare, naked analysis, backtest, overlay, Engine C scan).
- Engine B scalp remains a distinct backend profile (not auto-promoted to intraday in the generic style resolver).

**New config defaults (config.py):**
- Added `MIN_CONFLUENCE_GROUP` for subgroup-specific scan gates.
- Added `FACTOR_SCORE_GROUP_MULTIPLIERS` for subgroup-specific factor weighting.
- Added `FOREX_ENGINE.score_group_adjustments` for subgroup multipliers on forex scoring components.
- Added `NAKED_ENGINE.score_group_overrides` for subgroup/style-specific Engine B checklist strictness.

**Tests added:**
- `tests/test_scoring_group_routing.py` — subgroup mapping + threshold resolution + forex scorer source hooks.
- `tests/test_factor_group_overrides.py` — pair-profile and subgroup factor-weight adjustments.
- `tests/test_style_level_consistency.py` — style-level recompute parity and Engine C style metadata.
- `tests/test_market_specific_contracts.py` — TP1/TP2 ordering and subgroup guardrails.

**Migration checklist (config-only rollout):**
- **Step 1 — Assign subgroups:** In `PAIR_PROFILES`, set `score_group` for pairs that need explicit routing (or rely on built-in defaults from `get_pair_score_group`).
- **Step 2 — Gate thresholds first:** Populate `MIN_CONFLUENCE_GROUP` by asset class + subgroup; keep values close to existing `MIN_CONFLUENCE_CLASS` on day one, then tune incrementally.
- **Step 3 — Tune Engine A weights:** Use `FACTOR_SCORE_GROUP_MULTIPLIERS` to adjust factor groups per subgroup before changing core factor logic.
- **Step 4 — Tune forex subgroup behavior:** Use `FOREX_ENGINE.score_group_adjustments` for majors/crosses/exotics rather than hardcoding pair-level branches.
- **Step 5 — Tune Engine B strictness:** Use `NAKED_ENGINE.score_group_overrides` per subgroup and style (`scalp`/`intraday`/`swing`) for `min_score`, `min_rr`, `min_room_atr`.
- **Step 6 — Keep ATR logic separate:** Style ATR (`STYLE_ATR_MULTS` / `LEVEL_ATR_PRIORITY`) should be tuned independently from scoring subgroup thresholds.
- **Step 7 — Validate before widening:** Run targeted tests (`test_scoring_group_routing.py`, `test_factor_group_overrides.py`, style/contract suites) before enabling broad live changes.
- **Step 8 — Observe and iterate:** Start with stricter gates for noisy subgroups (exotics, DOGE, nat gas), then relax only with evidence from audit/backtest logs.

## Recent Changes (2026-03-23) — ATR Recalibration & Per-Style AI Ratings

**ATR TP/SL Benchmark Recalibration:**
- `STYLE_ATR_MULTS` recalibrated against industry research (quantstock.org, bestmt4ea.com, atrindicator.com, luxalgo.com, fxnx.com, cryptotrading-guide.com 2026, fxpremiere.com for XAU/USD, tapbit.com 2026).
- Previous scalp/intraday values (sl=0.16-0.27x for scalp, sl=0.20-0.33x for intraday) were 2-5x below industry minimum, causing noise stop-outs on every trade.
- New tiered TP1/TP2 approach: TP1 = quick partial exit (slightly below industry floor), TP2 = industry standard runner.
- Scalp (H1 ATR): sl=0.50, tp1=0.75, tp2=1.25 (commodity: sl=0.65, tp1=1.00, tp2=1.50).
- Intraday (H4 ATR): sl=0.75, tp1=1.50, tp2=2.50 (commodity: sl=1.00, tp1=2.00, tp2=3.00).
- Swing now explicit in STYLE_ATR_MULTS (mirrors ATR_CLASS; ATR_CLASS demoted to fallback-only).
- `analyze_pair` `calc_levels` call now passes `style=_style` (was missing — always used ATR_CLASS swing defaults even for scalp/intraday scans). This was the core bug causing "TP too far" on all engines.

**Per-Style AI Ratings (Scalp / Intraday / Swing):**
- `StyleRating` sub-model added to `ai_schemas.py` with grade/edgeProbability/riskLevel.
- `EngineAResponse` and `EngineBResponse` now include `style_ratings` (Optional dict) for per-style breakdown.
- Engine A prompt (`EXPERT_PROMPT` in athena.py) now requests independent ratings for all 3 styles. Top-level grade/edgeProbability/riskLevel reflects the AI's best-rated style.
- Engine B prompt (`get_engine_b_ai_verdict` in engine_b_ai.py) same per-style rating request.
- AI Vision prompt (`/api/chart-analysis`) now asks for `SCALP RATING:`, `INTRADAY RATING:`, `SWING RATING:` independently. Max tokens bumped to 700.
- `apply_vision` in `engine_c.py`: new `_parse_style_ratings_from_text()` extracts per-style ratings via regex. Stored as `vision_style_ratings` on the updated consensus dict. Fallback: if no per-style found, all styles get the single overall rating.
- Engine C execute buttons (SCALP/INTRADAY/SWING) now individually gated: if Vision rates a style as AVOID/CONTRADICTS, that button is disabled.
- `buildAIContent` (Engine A + B AI panels): new `_buildStyleRatingStrip()` renders a 3-column rating strip showing grade + probability + risk per style.
- `buildECVisionResult`: shows per-style vision rating badges when available.
- Engine C tab: added style selector (Scalp/Intraday/Swing) next to asset class selector. Scan passes selected style to `/api/engine-c-scan` so card SL/TP/RR matches the intended trade style.

## Recent Changes (2026-03-23)

**`/api/candles` (dashboard chart widget — `static/index.html` ACM):**
- **Forex `H1` / `H4`:** Loads **`fetch_eodhd`** first (intraday H1 → pandas `4h` resample). Avoids serving the old **`candle_cache` + WS** mix that produced disjoint OHLC vs TradingView on pairs like **USD/ZAR**. Falls back to `fetch_candles` if EODHD returns nothing.
- **`_merge_forex_forming_ws`:** After EODHD, merges **only** the **forming bar** from **`CandleBuilder`** (EODHD WebSocket ticks): same bar time → update H/L/C/vol, keep REST **open**; newer WS bar → append one bar (trim to `limit`). Scans and non-forex paths unchanged.
- **Debug:** `GET ?source=live` uses **`fetch_candles` only** (legacy mixed series) for A/B comparison.
- **Response metadata:** `pairType`, `display` (for `_latestPrices` key, e.g. `USD/ZAR` vs `USDZAR=X`), **`candlesSource`**: `eodhd` | `eodhd+ws` | `live` | `cache`.
- **Timestamps:** Naive ISO datetimes from storage get **`Z`** appended so the browser parses them as UTC.

**Athena chart modal (ACM) & Engine C Vision chart:**
- **`_acmNormalizeSig`:** Maps Engine C **`entry` / `tp`** onto **`price` / `tp1`** so ENTRY/SL/TP price lines draw correctly.
- **`openAcm`:** If fallback has **`sl_method`** (Engine C consensus), **prefer that fallback** over a same-symbol row from `allSignals` so levels match the EC snapshot, not Engine A.
- **`fmtPriceForSig`:** Quote decimals by **`pairType` / signal type** — e.g. JPY crosses **3** dp, other forex **5** dp; crypto/stock/commodity scaled by magnitude. **Scalp vs intraday vs swing** affects typical stop **distance**, not pip **display** decimals.
- **Forex sanitize:** `_acmSanitizeCandlesForChart` **does not** wick-clip forex (vendor OHLC trusted); clipping remains for other assets to damp bad prints.
- **`_acm.priceDisplay`:** Set from API **`display`**; live header / last-bar poll uses it so WS prices resolve. Forex: ignore live tick if **>12%** from last candle close (glitch guard).
- **Legend (EC):** When `sl_method` present, note that **SL/TP/entry are Engine C snapshot** while **H4 is refetched on modal open**.

**`/api/engine-c-scan` (forex Engine C entry vs chart):**
- For **`type == forex`**, H4/H1 passed into Engine B + **`current_price` / consensus `entry`** use **`fetch_eodhd` + `_merge_forex_forming_ws`**, same as **`/api/candles`**. Previously **`fetch_candles`** (mixed cache) last close could differ from the chart’s last H4 close, so the **ENTRY** price line looked misaligned on the modal.

## Recent Changes (2026-03-22)

**Engine C — Consensus Layer (New):**
- Added `engine_c.py`: combines Engine A (quantitative factor scoring) and Engine B (naked price action) into a unified signal with conviction-based position sizing.
- Architecture: Layer 1 normalises both engine scores to 0–1; Layer 2 applies regime-weighted conviction scoring; Layer 3 resolves SL/TP and sizing multiplier.
- AI Vision is NOT a voter — it modifies conviction after consensus is established (CONFIRM/WEAKEN/CONTRADICT/AVOID).
- **Engine B `passed` / naked `min_score` are NOT hard gates for Engine C.** Gating on `calculate_confidence["passed"]` was removed: it blocked essentially all Engine C signals. Engine C uses structural `CLEAR`, normalised checklist score, and its own RR/tier logic instead.
- **`combinedConviction` (main scan / auto-trader) ≠ Engine C `conviction`.** Full scan attaches `combinedConviction = 0.6×A_norm + 0.4×B_norm` when engines align (`run_full_scan`). Engine C uses `ENGINE_C_AB_WEIGHTS` in `engine_c.py` (regime-dependent, e.g. trending 0.65/0.35) plus optional BOS/OB multipliers. Do not treat the two numbers as interchangeable when ranking or explaining signals.
- **Two different `REGIME_WEIGHTS` concepts:** `config.yaml` / `CONFIG["REGIME_WEIGHTS"]` adjusts **factor** weights inside Engine A. `ENGINE_C_AB_WEIGHTS` in `engine_c.py` is the **Engine A vs Engine B blend** for consensus only — same regime *names*, different purpose.
- Regime-conditional A/B blend (`ENGINE_C_AB_WEIGHTS`): `TRENDING={A:0.65, B:0.35}`, `RANGING={A:0.35, B:0.65}`, `HIGH_VOLATILITY={A:0.50, B:0.50}`, `LOW_VOLATILITY={A:0.45, B:0.55}`.
- **BOS / OB at zone:** `calculate_confidence` can add extra checklist rows for `bos_mtf` and `ob_at_zone` (raising B_norm). Engine C may also apply small conviction multipliers (×1.08 / ×1.05) when those flags are set — deliberate emphasis on structural alignment, not an accidental duplicate path.
- Conviction tiers: `HIGH≥0.70→full size`, `MEDIUM≥0.50→0.65x`, `LOW≥0.35→0.35x`, `SKIP→no trade`.
- Vision CONFIRM with conviction ≥ 0.35 upgrades LOW-tier signals to tradeable; AVOID/CONTRADICT hard-veto regardless.
- SL priority: Engine B structural → ATR validate (clamp at 2.5x ATR) → pick tighter. Minimum RR is enforced in `resolve_tp` and post-resolution checks (not inside `resolve_sl`).
- TP priority: Engine B structural if RR ≥ 1.5, else Engine A ATR-based.
- Verdict **`OPPOSING_HIGH_CONFIDENCE`**: both engines strongly disagree on direction (high normalised scores, opposite bias) — **not** a proven regime change; name encodes the actual condition.

**New API Endpoints (athena.py):**
- `/api/engine-c-scan` (POST): runs Engine A + B on all pairs for a given `assetClass`, returns `{aligned, a_only, b_only, conflict, skipped}` buckets sorted by conviction.
- `/api/engine-c-confirm` (POST): applies AI Vision result to a consensus dict, returns updated consensus. Imports `apply_vision` locally to avoid circular imports. Always returns JSON (wrapped in try/except).

**Dashboard — ENGINE C Tab:**
- New ENGINE C tab added to the dashboard tab bar.
- Scan controls: asset class selector + SCAN CONSENSUS button.
- Signal cards show: two-column Engine A/B comparison, conviction bar, unified SL/TP/RR/sizing, AI CONFIRM button, and SCALP/INTRADAY/SWING execute buttons.
- Execute buttons always rendered (hidden until Vision confirms); shown for any tier Vision approves.
- `runECVisionConfirm`: opens H4 chart modal → html2canvas → `/api/chart-analysis` → `/api/engine-c-confirm` → updates card in-place.
- Response guards: `closeAcm()` runs before `.json()` parse; both fetch calls check `.ok` and surface readable errors instead of "Unexpected token '<'".

**Bug Fix — chart-analysis regime crash:**
- `api_chart_analysis` crashed when Engine C passed `regime` as a plain string (`"RANGING"`) because line called `.get('label')` assuming a dict. Fixed to handle both dict (Engine A) and string (Engine C) regime values. This was outside the try/except so Flask returned HTML, causing the "Unexpected token '<'" error.

**Sizing Override — quick-execute:**
- `/api/quick-execute` now reads `sizing_override` from POST body (default 1.0) and passes it to `risk_check`, replacing the hardcoded `CONFIG.get("AUTO_TRADE_SIZING_OVERRIDE")`. Engine C HIGH/MEDIUM/LOW conviction tiers now directly control position size.

## Recent Changes (2026-03-20)

**Engine B (Naked) Simplification & Parity:**
- Engine B now uses a shared naked price-action checklist across live scan, single-pair analysis, compare, and backtest.
- Core Engine B pass/fail is now rule-based only: structure, zone/breakout location, trigger candle, room, RR, and style profile.
- Engine B allows both retest-style and continuation-style naked entries where appropriate.
- Engine B live scan, compare, and naked-analysis API responses are routed through `_json_safe()` before `jsonify()`.
- Engine B backtests now persist to `backtest_results` with `engine="naked_engine"`.
- Engine B backtest now enforces the same explicit `min_rr` gate as live scan and applies a real 2-bar cooldown after the resolved exit bar.
- Engine B backtest `auto` style resolves to `intraday`, and hold windows are style-specific: `scalp=12`, `intraday=24`, `swing=60` H4 bars.
- Engine B now resolves one shared regime label via `_engine_b_regime_label()` across live naked scan, naked analysis, Engine A overlay, and backtest.
- Engine B backtest trade records now store the actual regime label (`TRENDING` / `RANGING` / `HIGH_VOLATILITY` / `LOW_VOLATILITY`) instead of the macro swing sequence.
- Engine B backtest regime lookup was optimized to use lightweight H4 indicator snapshots (`calc_indicators`) instead of the normalized factor-scoring path, avoiding multi-minute single-pair backtest regressions.

**Engine B AI Role (Current):**
- AI is advisory for Engine B — it reviews generated structure data, writes narrative/warnings, and supports compare output.
- AI does **not** currently decide whether an Engine B signal passes or fails.
- Any historic weighted/AI-adjusted Engine B scoring guidance should be treated as obsolete.

**Dashboard/UI:**
- Confluence display on cards now favors a visual meter/bar plus qualitative labels instead of raw score fractions like `4.00/3`.
- Engine B backtest metric cards now show naked-rule metrics such as zone touch, breakout/rejection mix, and dominant trigger rather than stale FVG/volume placeholders.

## Recent Changes (2026-03-19)

**EODHD WebSocket & Data Feed Optimization:**
- **Complete Crypto Migration**: All 19 crypto pairs now use Binance Futures WebSocket (`fstream.binance.com/ws/!ticker@arr`) exclusively - removed from EODHD WebSocket and REST poller
- **EODHD WebSocket Expansion**: Added TSLA, NVDA, META to EODHD WebSocket (removed `"ws":False`) - now ~45 pairs with live data
- **REST Poller Optimization**: Reduced frequency from 60s to 21 minutes for delayed stock data (15-20min exchange delays) - 95% API call reduction
- **Smart Batch Processing**: Enhanced REST poller with type-based grouping (forex/stock/other) and optimal 15-symbol batches
- **Pair Cleanup**: Removed MT5-specific instruments (Euro Stoxx 50, XLF) and replaced Nasdaq Composite with NASDAQ-100
- **Copper WebSocket**: Moved Copper from REST to EODHD WebSocket for real-time pricing

**Pair Configuration Updates:**
- **Total Active Pairs**: 90 (removed 2 MT5 pairs, added 1 NAS100)
- **EODHD WebSocket**: ~45 pairs (TSLA/NVDA/META added)
- **Binance WebSocket**: 19 crypto pairs (all live, no REST dependency)
- **EODHD REST**: 15 pairs only (21min polling, optimized for delays)
- **Removed**: Euro Stoxx 50, XLF, Nasdaq Composite
- **Added**: NASDAQ-100 (via EODHD REST)

**Data Source Summary:**
- Crypto: Binance Futures WebSocket (≤1s latency)
- Forex/Stocks/Commodities: EODHD WebSocket (real-time) + REST fallback (21min)
- No TwelveData/Polygon usage (fallback only)
- All 92 active trading pairs have live price access for both Engine A & Engine B

## Recent Changes (2026-03-17)



**Engine B (Naked Market Structure):**
- Added `market_structure.py` housing `NakedEngine` for pure price-action evaluation (no indicators).
- Engine looks at D1/H4 swings (HH/HL, LH/LL) and checks H1 for nearest structural Resistance/Support zones using wick/body clusters.
- Added `/api/scan-naked` to run Naked Engine against all active candidate pairs.
- Added `/api/naked-analysis` to run deep Engine B analysis on a single selected pair.

**Dashboard Performance & UI:**
- **Optimization A:** Throttled `run_full_scan` `ThreadPoolExecutor` from 6 workers to 3 to prevent CPU lock-ups.
- **Optimization B:** De-synced the Naked Scan execution loop by intentionally yielding checking blocks with `time.sleep(0.1)` so the Flask web thread remains responsive.
- **Optimization C:** Removed automatic `setTimeout` chart opening payloads from `index.html` after a scan finishes. Charts now Lazy-Load strictly on button clicks, preventing LightweightCharts from seizing the GPU/DOM.
- Added "NAKED GLOBAL SCAN" manual button to UI.
- Added "👁 NAKED SCALP" button to individual signal cards, producing a structural verdict window with a "⚡ QUICK SCALP EXECUTE" button that executes based on Engine B's proprietary SL/TP levels.

**Data Feeds & Infrastructure:**
- **Crypto Live Pricing Migration**: Complete migration to dedicated `BinanceLivePriceWS` thread consuming `fstream.binance.com/ws/!ticker@arr` stream (19 pairs, real-time ≤1s)
- **EODHD WebSocket Optimization**: ~45 pairs for live pricing (US stocks, forex, commodities, indices). Recent additions: TSLA, NVDA, META now live via WebSocket
- **EODHD REST Poller**: 15 pairs only, 21-minute intervals (optimized for 15-20min stock exchange delays). 95% API call reduction from previous 60s polling
- **Pair Cleanup**: Removed MT5 instruments (Euro Stoxx 50, XLF), replaced Nasdaq Composite with NASDAQ-100 via REST
- **Live Price Access**: All 92 active pairs have live data for both Engine A (_live_prices dict) and Engine B (H1 candle close)

## Previous Changes (2026-03-16)

**Testing Week Adjustments:**
- `MAX_OPEN_POSITIONS: 20` (was 5) — raised for testing with multiple open trades
- `MAX_CORRELATED_POSITIONS: 10` (was 2) — allow multiple correlated pairs during testing
- Revert both to 5 and 2 before live account activation

**Forex Session & Timezone Fixes:**
- Added `SERVER_TZ_OFFSET_HOURS: 2` config (for GMT+2/SAST) — derives UTC from local system time to fix Windows timezone misconfiguration
- Added Asian session window (00:00–08:00 UTC) to `forex_scoring.py` — forex now trades 24h (was London+NY only)
- Added `FOREX_SESSION_FILTER: true` config to disable session gate if needed

**Scoring & Auto-Trade Threshold Fixes:**
- `AUTO_TRADE_MIN_SCORE` changed from flat 0.75 to **per-class dict** (crypto: 0.80, forex: 0.65, stock: 0.85, commodity: 0.80, index: 0.80)
  - **Why:** factor engine scores 0–3.0, forex engine scores 0–1.0 — same 0.75 threshold was incompatible
- `MIN_CONFLUENCE_CLASS.forex: 0.70` → **0.60** (matches forex_scoring.py scale; 0.70 was over-filtering)
- `BT_MIN.forex: 0.60` kept in sync with MIN_CONFLUENCE_CLASS.forex
- Removed dead config `MIN_FOREX_CONFLUENCE: 0.60` and `MIN_FOREX_BREAKOUT: 0.50` — replaced with comments

**Dashboard Enhancements:**
- Added "Failed Executions" section in Performance tab (shows manual & auto rejections with error reasons)
- Failed manual executions now logged to audit_log with `grade="MANUAL-ERR"` and `error_tag=<reason>`
- New `/api/failed-executions` endpoint returns last 50 failed attempts

---

## ⚠️ DATA PROTECTION
audit.db and candle_cache.db contain all live trading history.
NEVER delete, overwrite, or zip these files during updates.
Run `python backup_db.py` before any major code changes.
Backups stored in db_backups/ folder — keep last 7 days automatically.
Hardcoded DB/reset/restore safeguards must remain untouched unless the user explicitly asks for that exact change.
Do not silently change backup, restore, reset, or destructive maintenance behavior.

## Project Overview
Multi-asset algorithmic trading system: Flask dashboard, Engine A (MFQS: Multi-Factor Quantitative Scoring), Engine B (Naked price-action), Engine C (Consensus), AI review, live execution on MT5 and Bybit.

---

## File Map

| File | Purpose | Size |
|------|---------|------|
| `athena.py` | Flask app, most API routes, `analyze_pair`, pair lists, core orchestration | ~8500 lines |
| `candles_cache.py` | TTL candle cache, `fetch_candles` (H1→`fetch_candles_live` first; crypto H4/D1→Binance REST), `extract_candles`, forex WS bar merge | |
| `candle_feeds.py` | Live prices, EODHD/Binance WS, `CandleBuilder` (`on_tick` forex H1; `on_kline` crypto H1 from `BinanceCandleWS`), `fetch_candles_live` | |
| `athena_runtime.py` | `set_runtime` / `rt()` bindings; `executed_signals` dedupe set | |
| `execution.py` | Execution-related Flask routes (`register_execution_routes`) | |
| `scanner.py` | `run_full_scan` and scan pipeline wiring | |
| `backtest_runner.py` | Engine A/B backtest implementations (pulled from monolith) | |
| `backtest.py` | Re-exports backtest entrypoints from `backtest_runner` | |
| `data_feeds.py` | HTTP session, EODHD client, funding/OI fetch helpers | |
| `candle_manager.py` | Facade → `athena_legacy` for external candle access | |
| `athena_legacy.py` | Loads monolith file module as `athena_monolith` | |
| `app.py` | `create_app()` Flask factory | |
| `indicators.py` | Pure indicator functions (EMA, RSI, MACD, ATR, ADX, BB, Stochastic, Weinstein, Fib, OBV, Squeeze) | |
| `scoring.py` | Confluence engine, vote weights, session, pair profiles, signal classification | |
| `factor_scoring.py` | Z-score factor engine — directional (trend, momentum, microstructure, derivatives) + non-directional (trend_strength, volatility, volume, structure). Includes `volume_momentum_spread` (VMS). | |
| `scalp_engine.py` | Engine D: Independent M15/M5 scalping module focusing on structural rejection and high-frequency execution. | |
| `confidence_engine.py` | 4-component confidence scoring (indicator agreement, timeframe alignment, regime fit, liquidity) | |
| `feature_normalizer.py` | Rolling z-score, percentile rank, min-max normalization | |
| `market_structure.py` | Engine B naked price-action engine: structure, zones, trigger patterns, and shared checklist pass/fail logic | |
| `engine_b_ai.py` | Engine B review layer — advisory AI verdict/narrative only, not primary signal generation | |
| `engine_c.py` | Engine C consensus — `ENGINE_C_AB_WEIGHTS` (A vs B blend, not CONFIG `REGIME_WEIGHTS`), normalises A+B, conviction + SL/TP, Vision modifier, sizing | |
| `config.py` | Hard-coded CONFIG defaults + YAML loader + validation | |
| `config.yaml` | All tunable thresholds — edit this, not config.py | |
| `risk_engine.py` | Risk gateway: kill switch, drawdown, position sizing, portfolio heat | |
| `mt5_executor.py` | MetaTrader 5 execution | |
| `bybit_executor.py` | Bybit Linear Futures execution | |
| `auto_trader.py` | Autonomous scheduler: scan every 30 min, auto-execute | |
| `ai_learning.py` | Outcome extraction → learning_log in audit.db; factor-level analysis for AI calibration | |
| `backup_db.py` | Safe SQLite backup/restore helper for `audit.db` and `candle_cache.db` | |
| `stop_sentinel.bat` | Background process killer for all Sentinel Pro Python and Flask threads. | |
| `regime.py` | Market regime detection (TRENDING/DEVELOPING/RANGING/DEAD RANGING) | |
| `carry_feed.py` | Interest rate carry data — FRED static fallback, 1h cooldown on failure, non-blocking | |
| `cot_feed.py` | CFTC Commitment of Traders z-scores — non-blocking, cache-first during scans | |
| `duka_volume.py` | Dukascopy tick volume for forex — non-blocking during scans (returns 1.0 if cache not ready) | |
| `forex_scoring.py` | Dedicated forex scoring engine (rules-based, 0–1 scale) — trend gate + session filter + RSI pullback + COT boost | |
| `auto_trader.py` | Autonomous trade executor with per-class auto-trade thresholds (dict-based, per CONFIG) | |
| `static/index.html` | Dashboard UI: signals, backtest, screener; ACM charts (`/api/candles` `limit=1000`, `_acmEmaLineData` for EMAs); pair list from `/api/pairs` | ~2550 lines |
| `tests/test_athena.py` | Main test suite (56+ tests) | |
| `tests/test_factor_scoring.py` | Factor scoring unit tests | |
| `test_indicators.py` | Pure indicator unit tests (imports from `indicators` only) | |
| `audit.db` | SQLite runtime DB — **never commit** (gitignored) | |
| `candle_cache.db` | SQLite candle cache — **never commit** (gitignored) | |
| `*.db` | All `.db` files are gitignored — too large / runtime data | |

---

## Signal Flow (end-to-end)

```
run_full_scan(style, asset_class)
  └─ for each active pair (ThreadPoolExecutor, 3 workers)
       └─ analyze_pair(pair, btc_bias, style)
            ├─ fetch_candles(pair, "D1"/"H4"/"H1", limit)   ← H1: CandleBuilder if enough bars; crypto H4/D1: Binance REST; else TTL + REST fallbacks
            ├─ calc_indicators(candles)                       ← returns {snap: {...}} dict
            ├─ calc_confluence(d1i, h4i, h1i, vr, stoch, pair, btc_bias, ...)
            │    ├─ get_pair_vote_weights(pair)               ← merges class weights + pair profile
            │    ├─ votes tallied → bull/bear score
            │    ├─ ranging/counter-trend penalties applied
            │    ├─ classify_signal_setup(direction, entry_mode, squeeze_bonus, atr_breakout, votes)
            │    └─ returns {score, direction, votes, warnings, signalClass, regime, maxScoreOverride}
            ├─ calc_levels(entry, atr, direction, ptype, regime_state)
            └─ returns full signal dict

  └─ _annotate_signal_for_scan(sig, pair, threshold, ...)
  └─ _classify_signal(sig, pair) → tier: "trade" | "watchlist" | "skip"
  └─ apply_correlation_cap(results)
  └─ _json_safe(result)                                       ← strips NaN/Inf before jsonify
```

### Scoring vs dashboard chart — candle windows (EMA reference)

**Engine A (`analyze_pair` in `athena.py`):** loads D1/H4/H1 via **`scan_candle_limits()`** (same keys as **`CONFIG["D1_CANDLES"]`**, **`H4_CANDLES`**, **`H1_CANDLES`**) and **`fetch_candles`** (routes by pair: crypto → Binance, EODHD pairs → EODHD/live cache, etc.), then **drops the last bar** on each series (forming candle) before `calc_indicators_with_normalized`. Defaults are in **`config.py`** and overridable in **`config.yaml`** — **D1=1001**, **H4=1000**, **H1=1000** (after drop: ~1000 / ~999 / ~999 closed bars). **Minimums after drop:** `len(d1) >= 220`, `len(h4) >= 50`, `len(h1) >= 50` or the pair is skipped.

**Engine B (live paths):** `/api/scan-naked`, `/api/naked-analysis`, compare-engines, and Engine C’s own `analyze_structure` fetch use the **same limits** (`scan_candle_limits` + CONFIG keys) and the **same forming-bar drop** where they fetch their own series, so naked structure sees the **same closed-bar windows as Engine A** per asset class. **`fetch_candles`** remains the single routing layer to Binance vs EODHD vs cache for all pair types (forex, crypto, stocks, indices, ETFs, commodities).

All votes, `h4.snap` / `d1.snap` / `h1.snap` EMAs, forex engine inputs, and factor snapshots are computed from **these series only** — not from the chart’s internal array.

**Dashboard (`GET /api/candles`):** max **`limit=1000`**; ACM client requests **1000**. Candlestick + client-drawn EMA 21/50/200 use that payload.

**Why this matters:** EMA is recursive over the **whole** series; different lengths → different **last-bar EMA values**. After alignment, **H4/H1 `snap.ema200`** should match the **right-edge** ACM line for the same symbol/TF (same forming-bar rule: chart may still include a forming bar depending on path — minor edge case). **Lowering** `H4_CANDLES` / `H1_CANDLES` in yaml saves API/CPU per scan but diverges from the chart again.

**Tradeoffs of 1000-bar intraday windows:** larger REST payloads, more memory, TTL cache entries keyed by `(symbol, tf, limit)` (see `candles_cache.py`). **D1 at 1001** (≈1000 closed after drop) gives D1 EMA200 + margin; larger D1 windows increase REST payload per pair.

**Engine B flow:**
```
/api/scan-naked OR /api/naked-analysis OR /api/compare-engines
  └─ market_structure.NakedEngine.analyze_structure(...)
       ├─ derive swing state, BOS, sweeps, active zone, trigger pattern
       └─ returns raw price-action structure result
  └─ market_structure.NakedEngine.calculate_confidence(...)
       ├─ checklist only: structure, location, trigger/entry, room, RR, macro if required
       └─ returns pass/fail + score + pct for UI
  └─ Engine B AI review (optional/advisory only)
  └─ _json_safe(response)
```

**Engine C flow:**
```
/api/engine-c-scan
  └─ for each enabled pair in asset class (time.sleep(0.1) yield per pair)
       ├─ analyze_pair(pair, btc_bias, style)          ← Engine A
       ├─ regime = _engine_b_regime_label(h4, type, sig_a.get("regime"))  ← H4 detect + Engine A hint; forex signal_type mapped to zone regimes
       ├─ NakedEngine.analyze_structure(...)           ← Engine B (best of LONG/SHORT)
       ├─ NakedEngine.calculate_confidence(...)
       └─ compute_consensus(signal_a, signal_b, confidence_b, regime, entry, atr)
            ├─ normalise_engine_a() → score_norm 0–1
            ├─ normalise_engine_b() → score_norm 0–1
            ├─ direction gate (conflict → SKIP; opposing strong both → OPPOSING_HIGH_CONFIDENCE)
            ├─ ENGINE_C_AB_WEIGHTS regime blend: A*wA + B*wB (+ BOS/OB multipliers)
            ├─ resolve_sl() — structural → ATR-clamped → tighter candidate
            ├─ resolve_tp() — structural if RR≥1.5, else ATR
            └─ returns {trade, verdict, direction, conviction, tier, sizing_override, sl, tp, rr, ...}

/api/engine-c-confirm
  └─ apply_vision(consensus, vision_result)
       ├─ parse Vision rating: STRONG/MODERATE/WEAK/AVOID/CONTRADICTS
       ├─ apply conviction multiplier
       ├─ CONFIRM + conviction≥0.35 → trade=True
       ├─ AVOID/CONTRADICT → trade=False, tier=SKIP
       └─ returns updated consensus dict
```

**Execution path:**
```
api_execute()
  ├─ signal freshness check (SIGNAL_MAX_AGE_SEC)
  ├─ live re-analyze if stale → HTTP 409 if direction flipped
  ├─ price drift >1% → rebase SL/TP offsets
  ├─ _validate_exit_levels(direction, price, sl, tp)
  ├─ risk_check(signal, balance, equity, positions, symbol_info, ...)
  │    ├─ kill switch / drawdown stop check
  │    ├─ portfolio heat check (MAX_PORTFOLIO_HEAT)
  │    ├─ position sizing: base * score_factor * sizing_override * dd_factor
  │    └─ returns RiskApproval(approved, volume, risk_amount, risk_pct, reason)
  └─ mt5_execute(signal, approval) OR bybit_execute(signal, approval)
```

---

## Key Functions

### `calc_confluence(d1, h4, h1, vr, stoch, pair, btc_bias, ...)` — scoring.py
Signature (no `e200s` — removed intentionally):
```python
calc_confluence(d1: dict, h4: dict, h1: dict, vr: float, stoch: dict,
                pair: dict, btc_bias: str,
                d1_candles=None, h4_candles=None, h1_candles=None,
                funding_rate=None, volume_threshold=None, bar_time=None) -> dict
```
- Reads `get_pair_vote_weights(pair)` — merges class-level VOTE_WEIGHTS with pair profile overrides
- 12 vote slots: d1_trend(2.0), h1_ema(1.0), d1_adx(1.0), h4_macd(1.0), h4_oscillator(0.75–1.0), volume(0–1.0), funding(0–1.0), session(0–1.0), h4_fib(0.5–1.0), h1_bb(0.5–1.0), weinstein(0–1.0), divergence(1.0 bonus)
- Session vote adds W_SESS×0.5 to BOTH bull and bear — net directional max is 0.5, not full W_SESS
- `_base_max` subtracts `W_SESS×0.5` so confluencePct is not overstated
- Tie-break: `bull >= bear → LONG` (intentional long bias)
- Returns: `{score, votes, direction, bull, bear, spread, warnings, trendState, weinsteinStage, weinsteinLabel, entryMode, signalClass, regime, fundingRate, maxScoreOverride, adxMomentum, adxSlope}`

### `classify_signal_setup(direction, entry_mode, squeeze_bonus, atr_breakout, votes)` — scoring.py
Uses **structured boolean flags** — NOT string matching on warning text:
```python
# squeeze_bonus and atr_breakout are set at source inside calc_confluence
signal_class = classify_signal_setup(direction, _entry_mode,
                                     squeeze_bonus=_squeeze_bonus,
                                     atr_breakout=_atr_breakout,
                                     votes=v)
```
Returns: `"mean_reversion"` | `"breakout"` | `"trend_pullback"` | `"trend_continuation"`

### `get_pair_profile(pair)` — scoring.py
```python
profiles = CONFIG.get("PAIR_PROFILES", {})
return profiles.get(pair["display"]) or profiles.get(pair["symbol"]) or {}
```

### `get_pair_vote_weights(pair)` — scoring.py
Merges class-level VOTE_WEIGHTS with `disabled_votes` and `weight_overrides` from pair profile.

### `pair_filter_enabled(pair, filter_name)` — scoring.py
Returns `False` if `filter_name` in pair profile's `disable_filters` list.
Filter names: `weinstein`, `session`, `regime_transition`, `obv`, `funding`, `squeeze`, `mean_revert`, `btc_bias`, `divergence_warning`

### `_json_safe(value)` — config.py
Recursively replaces `float("nan")` / `float("inf")` / `float("-inf")` with `None`, and normalizes numpy scalars/arrays into native JSON-safe Python values.
Applied to scan, backtest, analyze, naked scan, naked analysis, and compare API responses before `jsonify()`.
Imported in `athena.py` via `from config import _json_safe`.

### `NakedEngine.calculate_confidence(...)` — market_structure.py
- Shared Engine B checklist evaluation for live scan, single analysis, compare, and backtest
- Score is now a checklist count / UI measure, not a weighted AI-driven decision model
- `passed` is determined by naked price-action rules, not AI interpretation

### `_naked_scan_style_profile(style)` — athena.py
- Resolves Engine B style profile for `scalp`, `intraday`, or `swing`
- Controls checklist strictness, room, RR, ATR timeframe, and macro-alignment requirements

### `_engine_b_regime_label(...)` — athena.py
- Shared Engine B regime resolver used by live scan, naked analysis, Engine A overlay, and backtest
- Uses `regime.detect_regime(...)` on lightweight H4 indicator snapshots so Engine B zone multipliers adapt by asset/regime without backtest slowdown
- Returns config-compatible regime labels: `TRENDING`, `RANGING`, `HIGH_VOLATILITY`, `LOW_VOLATILITY`

### `_build_event_risk(pair, ds_ctx, earnings_ctx, closed_exchanges)` — scoring.py
Builds `{hardBlock, reasons, earnings?, dividend?, split?}` risk dict for a pair.

### `_local_to_utc_hour()` — forex_scoring.py
Derives UTC hour from local system time + `SERVER_TZ_OFFSET_HOURS` config. Fixes Windows timezone misconfiguration where `datetime.now(timezone.utc)` returns wrong UTC. User sets offset to match their timezone (SAST = 2, UTC+0 = 0, etc.).

### `_classify_signal(signal, pair)` — scoring.py
Returns `(tier, reason)` where `tier` is `"trade"` | `"watchlist"` | `"skip"`.

### `_validate_exit_levels(direction, entry_price, sl, tp)` — bybit_executor.py
Returns error string or `None`. Called twice: before order placement and after fill.
Post-fill failure triggers emergency market close (1 retry on SL/TP set before emergency close).

### `_max_score_for_pair(pair)` — athena.py
Computes theoretical max score using `get_pair_vote_weights(pair)`, subtracts `W_SESS×0.5` to match `calc_confluence` accounting.

### `fetch_candles(pair, tf, limit)` — `candles_cache.py` (injected from monolith)
- **H1 (all pairs, not polygon):** Tries `fetch_candles_live()` / `CandleBuilder.get_candles` first if bar count ≥ min (crypto H1: Binance `@kline_1h` + seed); else TTL then REST.
- **Crypto H4/D1:** Binance REST native intervals (not rolled up from CandleBuilder).
- **Non-crypto H4/D1:** Source-specific REST / cache (EODHD, etc.) per `pair["source"]`.
- **Cache TTL keys**: Uppercase `"H1"`, `"H4"`, `"D1"` (not lowercase)
- **TTL**: H1=55 min, H4=3h55m, D1=23h
- **Fallback chain**: EODHD → YFinance (TwelveData removed due to rate limits)
- **Pairs with `"ws": False`**: Use REST cache only (15 pairs, 21min polling)

### `_build_ticker_map(pairs)` — athena.py (EODHDWSManager)
Routes pairs to EODHD WS endpoints (`us`, `forex`). Skips any pair with `"ws": False`. Crypto pairs are entirely excluded from EODHD and handled natively by `BinanceLivePriceWS` without connection limits.
- EODHD plan cap: **50 tickers total** across all endpoints
- **Current WS allocation (~45)**: US stocks/ETFs ~17, Forex/Commodities/Indices ~28
- Recent additions: TSLA, NVDA, META moved from REST to WebSocket
- Pairs with `"ws": False` (15 pairs) still scan, backtest, and execute normally via REST cache (21min polling)
- To re-allocate WS slots: run backtests → query `backtest_results` by SQN → update `"ws"` flags in pair lists

### `/api/chart-analysis` endpoint — athena.py
POST endpoint. Sends a chart screenshot (base64 PNG) to Claude Vision (`claude-opus-4-6`) for professional TA review.

**Request body:**
```json
{
  "image": "<base64 PNG>",
  "symbol": "BTC/USDT",
  "tf": "H4",
  "signal": { "direction": "LONG", "confluenceScore": 1.2, "price": ..., "sl": ..., "tp1": ..., "regime": "RANGING" },
  "engineB": { "current_swing_sequence": "HH-HL", "bos_confirmed": true, ... }
}
```
- `signal` and `engineB` are optional — endpoint prefers POST body over scan cache (`_last_scan_results` / `_engine_b_cache`)
- `regime` in `signal` can be a **dict** `{"label": "TRENDING"}` (Engine A) or a **plain string** `"TRENDING"` (Engine C) — both handled
- Context builder is **outside** the try/except — any crash there returns HTML 500 (known risk area; keep context code simple)
- Returns `{analysis: "<text>", structured: {...}, model: "claude-opus-4-6", symbol, tf}` or `{error: "<msg>"}`
- Requires `ANTHROPIC_API_KEY` env var; returns 500 if missing

### `/api/pairs` endpoint — athena.py
Returns ALL_PAIRS grouped by asset class for the frontend pair selector. Response: `{groups: {label: [{sym, label, enabled}]}, total, active}`. The backtest dropdown in index.html fetches this on page load — do NOT hardcode pair lists in HTML.

### `_can_execute(signal, cfg)` — auto_trader.py
Gate for auto-trade execution. Reads `AUTO_TRADE_MIN_SCORE` as a **per-class dict** from CONFIG:
```yaml
AUTO_TRADE_MIN_SCORE:
  crypto:    0.80   # factor engine (0–3 scale)
  stock:     0.85   # higher bar for single stocks
  commodity: 0.80
  index:     0.80
  forex:     0.65   # forex engine (0–1 scale)
```
Effective min = `max(AUTO_TRADE_MIN_SCORE[asset_type], MIN_CONFLUENCE_CLASS[asset_type])`. Falls back to crypto value if asset_type missing.
**Why per-class:** factor engine outputs 0–3.0; forex outputs 0–1.0 — same flat threshold was incompatible.

### `risk_check(signal, balance, equity, positions, symbol_info, ...)` — risk_engine.py
- Commodity tick defaults: tick=0.01, contract=100, tick_val=1.0 (e.g. gold: 0.01×100)
- Stock/crypto tick defaults: tick=0.01, contract=1, tick_val=0.01
- Forex tick defaults: tick=0.00001, tick_val=1.0
- MT5 `mt5_get_positions()` uses `symbol_info` tick_size/tick_value for accurate risk_amount (not hardcoded)

---

## Pair Profiles (config.yaml)

Per-pair overrides without touching class-level defaults:
```yaml
PAIR_PROFILES:
  XAU/USD:
    disable_filters: [obv, session]
    weight_overrides:
      h4_fib: 1.5
      h1_bb: 0.5
    min_confluence: 5.8
    bt_min: 4.6
  EUR/USD:
    disabled_votes: [volume]
    weight_overrides:
      session: 1.25
```
- `disabled_votes`: zeroes that vote's weight
- `weight_overrides`: sets specific vote weight
- `disable_filters`: disables named filter logic (weinstein warnings, OBV, squeeze detection, etc.)
- `min_confluence`, `bt_min`, `volume_threshold`: per-pair threshold overrides

Valid vote keys: `d1_trend, h1_ema, d1_adx, h4_macd, h4_oscillator, volume, funding, session, h4_fib, h1_bb, weinstein, divergence`
Valid filter keys: `weinstein, session, regime_transition, obv, funding, squeeze, mean_revert, btc_bias, divergence_warning`
`PAIR_PROFILE_VOTES` and `PAIR_PROFILE_FILTERS` constants defined in **config.py**, imported by scoring.py — single source of truth.

---

## Auto-Trader (auto_trader.py)

```
AutoTrader._scheduler_loop() — daemon thread, wakes every 30s
  └─ every AUTO_TRADE_SCAN_INTERVAL_MIN minutes:
       └─ _run_auto_scan()
            ├─ run_scan_fn(style="auto")          ← fresh scan, not cached
            ├─ logs: passed / watchlist / low_score / closed / inactive counts
            ├─ logs: best near-miss if no signals pass
            └─ for each signal in tradeSignals:
                 └─ _can_execute(signal, cfg)
                      ├─ score >= max(AUTO_TRADE_MIN_SCORE, MIN_CONFLUENCE_CLASS[type])
                      ├─ logs: pair, score/maxScore, min threshold, direction
                      └─ session filter (if AUTO_TRADE_SESSIONS configured)
                 └─ _execute_signal(signal, cfg)
                      ├─ risk_check() → RiskApproval
                      ├─ bybit_execute() or mt5_execute()
                      ├─ success → _write_audit() + trades_today++
                      └─ failure → _write_error(signal, error_tag)
```

**Config keys (config.yaml):**
```yaml
AUTO_TRADE_ENABLED: true           # boot-persistent (toggling in UI resets on restart without this)
AUTO_TRADE_MIN_SCORE:
  crypto:    0.80
  forex:     0.65
  stock:     0.85
  commodity: 0.80
  index:     0.80
AUTO_TRADE_MAX_DAILY: 10  # demo mode cap
AUTO_TRADE_MAX_PER_SCAN: 1
AUTO_TRADE_SCAN_INTERVAL_MIN: 30
```

**Diagnosing why no trades fired:**
```sql
SELECT pair, score, direction, error_tag, grade, ts
FROM audit_log WHERE grade LIKE 'AUTO%' ORDER BY ts DESC LIMIT 20;
```
`grade='AUTO-DEMO'` = successful demo trade. `grade='AUTO-ERR-DEMO'` = blocked — see `error_tag`.

---

## Bybit Executor (bybit_executor.py)

```
bybit_execute(signal, approval)
  ├─ _get_exchange()               ← singleton; calls load_time_difference() to fix retCode 10002
  ├─ fetch live price (ask/bid)
  ├─ rebase SL/TP if price drift >1% from signal price
  ├─ _validate_exit_levels()       ← pre-fill check, returns error string or None
  ├─ market order placement
  ├─ post-fill _validate_exit_levels() → emergency close if invalid (no retry here)
  ├─ _set_trading_stop()           ← 2 attempts (1 retry, 2s sleep) before emergency close
  └─ returns {success, ticket, volume, entryPrice, direction, sl, tp, ...}
```
- `adjustForTimeDifference: True` and `recvWindow: 10000` set in exchange options
- Emergency close uses `reduceOnly: True, positionIdx: 0`

---

## Backtest (athena.py — backtest_pair)

- Swing: walks D1 bars, max hold 20 bars → TIMEOUT if neither SL nor TP hit
- Intraday: walks H4 bars, max hold 12 bars → TIMEOUT if not hit
- TIMEOUT is force-closed at last forward bar close, P&L calculated as actual R-multiple (capped ±5R) and labelled TIMEOUT (not a loss)
- `open_positions` counter gates `MAX_OPEN=3` concurrent positions — incremented on entry, decremented on exit (not on OPEN/TIMEOUT)
- `bt_min` per class (config.yaml `BT_MIN`), overridable per pair via `PAIR_PROFILES`

## Engine B Backtest (athena.py — backtest_pair_naked)

- Separate Engine B backtest loop; isolated from Engine A `backtest_pair()`
- Uses 730-day EODHD intraday history for non-crypto pairs and paginated Binance history for crypto
- Drops the current in-progress candle on D1/H4/H1 before iterating
- `auto` resolves to `intraday`
- Hold windows by style: `scalp=12`, `intraday=24`, `swing=60` H4 bars
- Enforces Engine B style `min_score`, checklist `passed`, and explicit `min_rr` parity with live scan
- Applies a 2-bar H4 cooldown after the resolved exit bar to prevent overlapping entries
- TIMEOUT exits are closed at the last forward H4 close and keep their signed `R`; positive TIMEOUT trades count as wins in summary metrics because `_format_backtest_results()` groups wins/losses by final `R`
- Uses `_engine_b_regime_label()` so Engine B zone multipliers and stop buffers adapt to actual regime across forex, crypto, and other asset classes
- No `e200s` computed in backtest loops — variable was dead after `calc_confluence` signature change

---

## Scan Funnel (what the numbers mean)

```
{'total': 90, 'active': 28, 'inactive_pair': 62, 'closed_exchange': 15,
 'low_score': 61, 'passed': 1, 'watchlist': 16, 'dead_ranging': 4}
```
- `total` = ALL_PAIRS count (90 after recent cleanup)
- `active` = enabled pairs scanned
- `inactive_pair` = total - active (disabled, never scored)
- `closed_exchange` = pairs with open exchange flagged closed at scan time (JSE / US pre-open)
- `low_score` = diagnostic code count (NOT unique pairs — one pair can have multiple codes)
- `passed` = signals in `tradeSignals` (tier="trade"), ready for auto-execution
- `watchlist` = near-miss signals (score within 1.0 of threshold, not DEAD RANGING)

---

## Audit DB Schema (audit.db)

### `audit_log` table
Key columns: `ts, pair, score, direction, trend, grade, edge_prob, risk, style, asset_class, score_pct, max_score, votes_json, warnings_json, weinstein, trend_state, adx_pct, btc_bias, session_name, regime, entry_price, sl, tp, volume, risk_amount, risk_pct, ticket, exit_price, exit_time, pnl, r_multiple, exit_reason, holding_period_hours, error_tag, fee_cost, factors_json`

`fee_cost` (REAL) — actual exchange commission paid, captured from `bybit_execute()` → `order["fee"]["cost"]`. NULL for MT5 trades (not available via API).

`factors_json` (TEXT) — JSON blob `{scores, weights, disabled, regime}` from `compute_factor_scores()`. Written on every AI analysis, execution, and webhook entry. Used by `ai_learning.py` to analyze factor reliability across winning/losing trades.

### `backtest_results` table
Populated after every `backtest_pair()` run. Columns: `id, run_date, pair, asset_type, engine, trades, win_rate, profit_factor, expectancy, sqn, sharpe, sortino, is_score, oos_score, max_dd_pct, bt_min, atr_source, notes`

- `engine`: `"forex_scoring"` for forex Engine A rows, `"factor_scoring"` for non-forex Engine A rows, `"naked_engine"` for Engine B rows
- `atr_source`: `"D1_ATR"` for non-crypto, `"H4_ATR"` for crypto
- Index: `idx_bt_pair (pair, run_date)`

**Current persistence paths verified in code:**
- `audit_log`: manual analysis writes, manual execution writes/errors, auto-trader execution writes/errors, and trade outcome updates
- `learning_log`: populated from `extract_learning_from_trade()` after `_update_trade_outcome()` commits a closed trade outcome
- `backtest_results`: populated by Engine A backtests and now also by Engine B naked backtests
- `candle_cache`: populated via SQLite candle cache writes and live cache seeding logic

**Backtest history API endpoints:**
- `GET /api/backtest-history` — last 500 results, newest first
- `GET /api/backtest-history/<pair_name>` — last 50 for a specific pair (URL-encode slashes: `XAU%2FUSD`)
- `GET /api/backtest-best` — most recent run per pair, ordered by SQN desc

Schema auto-migrated on startup — adding a new column: add it to both `CREATE TABLE` and the migration list in `_init_audit_db()`.

**Failed Execution Logging (as of 2026-03-16):**
- Manual executions rejected by `risk_check()` now log to `audit_log` with `grade="MANUAL-ERR"` and `error_tag=<reason>`
- Auto-trade failures already logged with `grade="AUTO-ERR-*"`
- Query failed executions: `WHERE grade LIKE '%-ERR%'` — shows both manual and auto rejections with reason
- New `/api/failed-executions` endpoint returns last 50 failed attempts
- Dashboard "Performance" tab now shows "Failed Executions" section with time, pair, direction, score, source, and reason

---

## Python Environment

- **Python**: prefer the project virtualenv on Windows: `.\.venv\Scripts\python.exe`
- **Run tests**: `.\.venv\Scripts\python.exe -m pytest tests/ test_indicators.py -v` (Windows) / `python3 -m pytest tests/ test_indicators.py -v` (Linux)
- **Platform**: Windows 11, bash shell (dev); Linux also supported

---

## Claude Code Usage

This project uses **Claude Code** (CLI) as the primary AI coding assistant. To start a session:

```bash
# From the project directory:
claude
```

**Preferred workflow:**
- Use Claude Code primarily to review code paths, understand logic, inspect persistence, and then make safe, explicit edits from verified callsites
- For **persistent or “impossible” bugs** after a plausible fix: **switch layer** (API JSON → UI → cache → alternate fetch path) per **Debugging & audit playbook** in this file; do not repeat the same module-only review
- Keep DB/reset/restore safeguards untouched unless the user explicitly requests those exact changes
- To paste long logs or output: use a file (`log.txt`) and ask Claude to read it, rather than pasting directly
- To share a server log: save to a temp file and say "read log.txt"
- Claude Code sessions auto-summarize context so long projects are not lost

**Key commands:**
- `/help` — list all slash commands
- `/model` — switch model (Opus 4.6 for complex tasks, Sonnet 4.6 for speed)
- `/clear` — clear context window (does NOT lose CLAUDE.md memory)
- `Ctrl+C` — interrupt a running tool without exiting

---

## Hard Rules

1. Never bypass `risk_check()` for any execution
2. Never hardcode thresholds in Python — use `config.yaml`
3. Never import from `athena.py` in unit tests — use `indicators` or `scoring` directly
4. Never commit `*.db` files — all SQLite databases are gitignored (runtime/market data, too large)
5. Never add `e200s` back to `calc_confluence()` — it was intentionally removed
6. Never use string matching on warning text for classification — use structured flags
7. `PAIR_PROFILE_VOTES` / `PAIR_PROFILE_FILTERS` live in `config.py` only — do not redefine in scoring.py
8. Cache TTL dict keys are uppercase `"H1"/"H4"/"D1"` — lowercase misses the cache
9. `ALL_PAIRS = FOREX_PAIRS + COMMODITY_PAIRS + INDEX_PAIRS + US_STOCK_PAIRS + ETF_PAIRS + JSE_PAIRS + CRYPTO_PAIRS` — JSE_PAIRS must stay in this concatenation
10. Never hardcode pair counts or pair lists in `static/index.html` — the backtest selector fetches `/api/pairs` dynamically
11. `CandleBuilder.seed()` and `bulk_update_d1()` skip `enabled:False` pairs — do not remove these checks
12. `_resolve_scan_style(requested_style, pair)` — use this for per-pair style resolution in `run_full_scan()`; do not rename or replace with ad-hoc functions
13. Non-blocking I/O during scans: `carry_feed`, `cot_feed`, `duka_volume` must never block the scan thread — return cached/neutral values if data not ready
14. Always use `PRAGMA journal_mode=WAL` and keep SQLite writes on explicit commits; current runtime DB code uses `sqlite3.connect(..., timeout=15.0)` to reduce `database is locked` errors
15. `"ws": False` on a pair dict opts it out of WS subscription only — scan/backtest/execute are unaffected. Default is `True` (backward-compatible). EODHD plan cap is 50 tickers total; do not add `ws:True` pairs without removing others first
16. BybitWS (`athena/datafeeds/bybit_ws.py`): `ping_interval=None` in `websockets.connect()` is required — disables library-level keepalive and lets app-level `{"op":"ping"}` handling prevent 1011 keepalive errors
17. Engine B AI is review-only — do not reintroduce AI as a hidden pass/fail gate for naked signals unless the user explicitly requests that design change
18. **Cross-layer verification:** scoring/confluence/candle complaints require tracing **data → engine → API fields → dashboard display** (and chart `/api/candles` vs `analyze_pair`). Never conclude “engine is wrong” from UI alone without matching **thresholds, denominators, bar counts, and forming-bar rules** across layers
