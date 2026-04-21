---
description:
alwaysApply: true
---

# Sentinel Pro v4.0 — Claude Code Instructions

---

## ⚠️ Policy — Scoring Gates (STRICT)

**Do not change anything that alters live or backtest scoring unless the user explicitly instructs it.**

- **Engine A live:** `MIN_CONFLUENCE_CLASS`, `MIN_CONFLUENCE_GROUP`, `MIN_CONFLUENCE_CLASS.forex`, `PAIR_PROFILES.min_confluence`, `AUTO_TRADE_MIN_SCORE`, `SCAN_QUANTILE_*`, confluence logic in `scoring.py` / `factor_scoring.py` / `forex_scoring.py`, `analyze_pair` tiering. Includes Soft Gating keys: `FOREX_ENGINE.session_mode`, `FOREX_ENGINE.trend_gate_adx_soft_enabled`, `FACTOR_MIN_DIRECTIONAL`.
- **Engine A backtest:** `BT_MIN`, `PAIR_PROFILES.bt_min`, `get_backtest_min_score_threshold`, backtest score gates in `backtest_runner.py`.
- **Engine B live + backtest:** `NAKED_ENGINE.style_profiles` (`min_score`, `min_rr`), `ENGINE_B_REGIME_MULTIPLIERS` (score scaling — currently neutralized to 1.0), `zone_multipliers` (structural width), naked checklist gates in `market_structure.py`.
- **Engine D (Scalp Lab):** `SCALP_ENGINE` in `config.yaml` (`MIN_RR`, `MAX_SPREAD_PIPS`, `WITH_TREND_ONLY`, `BIAS_TIMEFRAME`, `M1_CANDLES`, `M15_CANDLES`, `M5_CANDLES`, `H1_CANDLES`, `SESSION_MODE`, `NY_OPEN_SKIP_MINUTES`, `EXECUTION_TIMEFRAME`, `MIN_GRADE_AUTO_EXECUTE`, `BT_ENABLED`, `BT_SESSION_MODE`, `BT_NY_OPEN_SKIP_MINUTES`, `BT_WALK_BARS`, `BT_MAX_CONCURRENT`, `BT_SLIPPAGE_TICKS`, `BT_SCRATCH_ENABLED`, `BT_SCRATCH_BARS`, `BT_SCRATCH_MIN_R`, optional `SCALP_PAIRS`) and core pass/fail logic in `scalp_engine.py` (session filter via `scalp_session_window()`, spread filter, VP build, absorption/CVD/AAA, VWAP, setup classification, HTF bias gate, level math, `ai_quality_grade`).

Cosmetic UI copy is fine. **Do not "tune", "align", or "simplify" thresholds** in passing.

---

## ⚠️ Data Protection

`audit.db` and `candle_cache.db` contain all live trading history.
- **NEVER** delete, overwrite, or zip these files during updates.
- Run `python backup_db.py` before any major code changes.
- Hardcoded DB/reset/restore safeguards must remain untouched unless user explicitly requests that exact change.

---

## Project Overview

Multi-asset algorithmic trading system built on Flask. Covers forex, crypto, stocks, commodities, indices.

**Four top-level engines plus a dedicated forex scoring branch:**
- **Engine A** — Multi-Factor Quantitative Scoring (MFQS): uses **Variance Sqrt-Scaling** and **1.5 Z-score normalization** for improved convicton range. Non-forex uses the factor engine; forex routes to the dedicated rules-based forex scorer with **Soft Gating** (ADX/Session).
- **Engine B** — Naked price-action (market structure, zones, BOS/CHoCH, FVG, swing sequence)
- **Engine C** — Consensus layer: blends A + B + AI Vision confirmation into a conviction-tiered signal
- **Engine D (Scalp Lab)** - Fabio Valentini VP+OrderFlow scalping (`scalp_engine.py`, `volume_profile.py`). Pipeline: Volume Profile (POC/VAH/VAL/LVN) -> Absorption/CVD/AAA -> VWAP lean -> setup classification (Mean Reversion / Trend Continuation) -> `ai_quality_grade` (A/B/C/D). Execution on M1 (configurable) with NY open cooldown. Session filtering via `scalp_session_window()`. Not part of A/B/C consensus. Dashboard **Scalp Lab** panel: `POST /api/scalp-scan`, `POST /api/scalp-execute`. Backtest: `POST /api/backtest-scalp` -> **Engine D (Scalp VP)** button in backtest panel.

**Execution:** MT5 for forex/stocks/commodities; Bybit Linear Futures for crypto.

**AI Review:**
- **Marcus Reid (Grok/xAI)** — AI analysis of Engine A + B signals (`EXPERT_PROMPT`), JSON output with grade/verdict/edgeProbability/style_ratings
- **Chart Vision (xAI multimodal)** — chart screenshot analysis (`/api/chart-analysis`) via OpenAI-compatible xAI client (`base_url=https://api.x.ai/v1`) and `VISION_MODEL`. Single/dual/triple TF; structured footer parsed by `_extract_vision_structured()`.
- **Lottery AI** — `POST /api/lottery/ai-analysis`: Grok (xAI) via OpenAI-compatible client; see **Lottery Lab** below.

**API Keys (secrets via env):** `EODHD_KEY`, `XAI_API_KEY` (Marcus Reid, chart vision, signal debate, Engine B AI, Lottery AI), `ANTHROPIC_API_KEY` (news sentiment helpers where configured). xAI-backed routes require the **`openai`** PyPI package (`base_url=https://api.x.ai/v1`).

---

## File Map

| File | Purpose |
|------|---------|
| `athena.py` | Flask app, all API routes, `analyze_pair`, pair lists, `EXPERT_PROMPT`, `_build_signal_message`, `_build_event_risk`, vision prompts | ~8500 lines |
| `vision_prompts.py` | Modular chart-vision prompt builders (`build_system_prompt`, `build_single_prompt`, `build_dual_prompt`, `build_triple_prompt`) with A-E right-edge candle framework |
| `vision_data.py` | Vision dataset schema/helpers (`vision_samples`, `vision_labels`, `vision_predictions`), artifact persistence, metrics aggregation |
| `vision_hybrid.py` | Advisory hybrid chart-vision v2 training/inference (fusion of image + structured features) |
| `chart_renderer.py` | Server-side chart renderer with Engine B overlays (FVG/OB/BOS/CHoCH/SR), pattern labels, configurable `bars_window` + `dpi` |
| `candles_cache.py` | TTL candle cache, `fetch_candles` routing (H1→live first where applicable; crypto H4/D1→Binance REST). MT5 sources bypass CandleBuilder and fetch MT5 directly first. |
| `candle_feeds.py` | Live prices, EODHD/Binance WS, `CandleBuilder` for EODHD/Binance live bars, `fetch_candles_live` |
| `athena_runtime.py` | `set_runtime`/`rt()` bindings; `executed_signals` dedupe set |
| `execution.py` | Execution Flask routes (`register_execution_routes`) |
| `scanner.py` | `run_full_scan` and scan pipeline |
| `backtest_runner.py` | Engine A/B/D backtest loops: `backtest_pair`, `backtest_pair_naked`, `backtest_pair_scalp`, `run_full_backtest` |
| `data_feeds.py` | HTTP session, EODHD client, funding/OI helpers |
| `news_sentiment_feed.py` | EODHD news + Claude sentiment; optional scan blend; TTL cache |
| `scoring.py` | Confluence engine, vote weights, signal classification, pair profiles, `get_min_confluence_threshold` |
| `factor_scoring.py` | Z-score factor engine with **Variance Sqrt-Scaling** to prevent score collapse; directional + non-directional factors (quality normalized to 1.5 Z-score). |
| `forex_scoring.py` | Rules-based forex scorer with **Soft Gating** (ADX hard/soft floors and session shoulders) + boosted organic momentum/ADX weights. Counter-trend SHORT gate: Case A — breakout SHORT vs LONG EMA stack with Hurst trending → `bo_final × 0.70`; Case B — trend-path SHORT with Hurst > 0.65 → `trend_score × 0.80`. Config: `FOREX_ENGINE.counter_trend_short_gate`. |
| `market_structure.py` | Engine B: `NakedEngine`, swing analysis, BOS/CHoCH/FVG/order blocks, shared checklist pass/fail |
| `engine_b_ai.py` | Engine B advisory AI verdict (review only — not a pass/fail gate) |
| `engine_c.py` | Engine C consensus: `ENGINE_C_AB_WEIGHTS` blend, conviction tiers, SL/TP resolution, Vision modifier |
| `confidence_engine.py` | 4-component confidence scoring (indicator agreement, TF alignment, regime fit, liquidity) + `session_quality` post-multiplier (high=1.0, medium=0.9, low=0.7). Off-hours signals are demoted before Engine C's reliability gate. |
| `indicators.py` | Pure indicator functions (EMA, RSI, MACD, ATR, ADX, BB, Stochastic, Fib, OBV, Squeeze) + Engine D helpers: `calc_vwap`, `detect_absorption`, `calc_cvd`, `detect_range_contraction` |
| `regime.py` | Market regime detection: TRENDING / DEVELOPING / RANGING / DEAD_RANGING |
| `risk_engine.py` | Kill switch, drawdown, position sizing, portfolio heat |
| `config.py` | Hardcoded defaults + YAML loader + `_json_safe()` |
| `config.yaml` | All tunable thresholds — edit here, not `config.py` |
| `scalp_engine.py` | Engine D: Fabio Valentini VP+OrderFlow scalping. Functions: `run_scalp_scan`, `get_scalp_pairs`, `scalp_session_window`, `_build_volume_profile`, `_build_trade_bucket_volume_profile`, `_overlay_eodhd_volume_for_scalp`, `_classify_market_state`, `_locate_price_vs_vp`, `_check_absorption`, `_check_cvd`, `_check_trade_bucket_cvd`, `_check_aaa_sequence`, `_check_vwap_lean`, `_classify_setup`, `calculate_scalp_levels`, `ai_quality_grade`. M1/M5/M15/H1 support. MT5 OHLC/live price for non-crypto; EODHD may overlay volume only. Binance/`fetch_candles` for crypto, with Binance aggTrade buckets preferred for VP/CVD when fresh. |
| `volume_profile.py` | Fixed-range Volume Profile computation: POC, VAH, VAL, LVN levels, session splitting, plus `compute_bucketed_volume_profile()` for price-level trade buckets. Used by `scalp_engine.py`. |
| `athena/microstructure/trade_bucket_store.py` | SQLite-backed Binance aggregate-trade price-bucket store used by Engine D crypto VP/CVD. |
| `tools/preprocess_binance_aggtrades.py` | Preprocess local or downloaded Binance Futures aggTrade ZIP/CSV files into trade buckets for crypto backtests. |
| `tools/audit_eodhd_intraday_volume.py` | Read-only EODHD intraday/EOD volume coverage audit for Athena symbols/timeframes. |
| `tools/audit_eodhd_symbol_coverage.py` | Read-only EODHD exchange symbol-list audit for Athena candidate mappings. |
| `ai_schemas.py` | Pydantic schemas for AI JSON output (`EngineAResponse`, `EngineBResponse`, `StyleRating`) |
| `mt5_executor.py` | MetaTrader 5 execution |
| `bybit_executor.py` | Bybit Linear (USDT-M) Futures execution |
| `auto_trader.py` | Autonomous scheduler: scan every 30 min, auto-execute per conviction |
| `ai_learning.py` | Outcome extraction → `learning_log`; factor-level analysis for AI calibration |
| `lottery_engine.py` | Lottery analytics + ticket generation (7 modes) + simulation |
| `lottery_service.py` | DB schema, CSV import, draw history |
| `static/index.html` | Dashboard UI: signals, Pair Browser, Engine C tab, backtest, ACM charts | ~2550 lines |
| `static/js/features/pair_browser.js` | Pair Browser tab logic: single-pair browsing, Engine A/B/compare actions, and news/intermarket/chart/AI vision sections |

---

## Venues (canonical — execution vs data)

- **Crypto — execution:** **Bybit** USDT-M linear futures only (`bybit_executor.py`). All live crypto **orders** go here (including Engine D `/api/scalp-execute` when `signal.type == "crypto"`). **Binance is not a crypto execution broker in this codebase** — it is market data only.
- **Crypto - market data:** **Binance Futures** WebSocket klines (`candle_feeds.BinanceCandleWS`) -> `CandleBuilder.on_kline()` for **M5, M15, H1, H4, D1**, plus REST fallback / merge via `fetch_candles()` when `pair["source"] == "binance"`. Live crypto quote stream: `BinanceLivePriceWS` `!ticker@arr`. Engine D crypto orderflow also subscribes to Binance **`aggTrade`** streams via `athena/datafeeds/binance_ws.py` and stores price-level buckets in `athena/microstructure/trade_bucket_store.py`; VP/CVD prefer fresh buckets and fall back to candle volume when buckets are unavailable or stale.
- **Non-crypto — execution and OHLC:** **MT5** only (`mt5_executor.py`, `fetch_mt5()` for candles). Forex, commodities, indices, stocks as configured with `source: mt5`.
- **EODHD - volume overlay (not primary price):** For whitelisted **forex, stocks, commodities, and indices**, EODHD overlays real volume onto MT5 candles without changing OHLC (`eodhd_volume_overlay.py`, whitelist per symbol/TF). The overlay is applied in `fetch_mt5()` → `_overlay_mt5_candles_with_eodhd_volume()` and cached in the candle TTL cache. This means **Engine A (H1/H4/D1)**, **chart rendering** (`/api/candles`), and **AI Vision** charts all receive real EODHD volume automatically for whitelisted pairs. Engine D live scalp uses the warmed EODHD volume cache only (`cache_only=live`). Two background warmers: fast loop (M1/M5/M15 for scalp pairs, 60s) and slow loop (H1/H4/D1 for all non-crypto pairs, 900s — uses `scan_candle_limits()` limits to match `analyze_pair()` cache keys). Engine D backtests may fetch EODHD intraday volume directly. Crypto volume/orderflow comes from Binance bars and aggTrade buckets, not EODHD.

---

## Live Data Feed Routing (LOCKED — do not change without explicit approval)

| Asset class | Candles | Live price |
|---|---|---|
| Forex / Stocks / Commodities / Indices | `fetch_mt5()` primary; Engine D may overlay cached EODHD volume only on M1/M5/M15/H1/H4/D1 without changing OHLC | `symbol_info_tick()` bid/ask mid |
| Crypto M5/M15/H1/H4/D1 | `BinanceCandleWS` futures `@kline_*` → `CandleBuilder.on_kline()`; REST merge/fallback `binance_futures` | `BinanceLivePriceWS` `!ticker@arr` |
| Crypto (other TFs) | Binance REST via `fetch_binance` / `tf_b` map when `source == binance` | — |
| EODHD WS | EODHD-sourced pairs only (JSE disabled pairs etc.) | — |

**Candle depth:** `D1_CANDLES: 1001`, `H4_CANDLES: 1001`, `H1_CANDLES: 1001` → ~1000 closed bars after forming bar is dropped. `fetch_mt5()` requests `limit + 100`.

**Never** route MT5-sourced pairs through CandleBuilder. **Never** replace MT5 OHLC with EODHD OHLC on MT5-sourced pairs; EODHD is volume-only overlay. Live Engine D EODHD volume lookup must remain cache-only to avoid delaying scalping; cold cache falls back to MT5 volume. **Never** write a stale bar close into `_live_prices` for any MT5 pair.

---

## AI Components

### Marcus Reid (Engine A + B analysis) — `EXPERT_PROMPT` in `athena.py`
- System prompt for Grok/xAI; JSON-only output
- **6 absolute rules:** cite specific data or don't claim it; no "will/guaranteed/definitely"; every claim must reference a factor name/score/weight/level from input
- **Analysis order:** factor diagnostics → trend coherence → regime → nondirectional quality → levels → Engine B cross-check → context color only
- **Critical cross-checks (all mandatory):** momentum divergence, direction flip bug, confidence multiplier < 0.5, trendCoherence < 0.7, nondirectionalScore < 0.5, SL > 2%
- **edgeProbability:** anchored formula `base = score_pct × 0.8` + modifiers
- Outputs: `grade`, `verdict`, `narrative`, `edgeProbability`, `riskLevel`, `style_ratings` (scalp/intraday/swing)

### `_build_signal_message` — `athena.py`
Builds the text input sent to Marcus Reid. Sections emitted:
- `=== SIGNAL ===` — pair, direction, score, conviction, regime, style
- `=== ENGINE B (NAKED MARKET STRUCTURE) ===` — reads `signal.get("engine_b") or signal.get("naked_data")` (Engine A uses `"engine_b"`, Engine B scan uses `"naked_data"`)
- `=== FACTOR DIAGNOSTICS ===` — Engine A: per-factor scores/weights (`factorScores`, `factorWeights` camelCase), directional/nondirectional scores, confidence multiplier, trendCoherence, active factors. Engine B: `=== ENGINE B SCORING DIAGNOSTICS ===` (score, bonuses, actionable flag)
- `=== CONFIDENCE ENGINE ===` — `confidenceDetail` components (Engine A only)
- `=== TECHNICALS ===`, `=== LEVELS ===`, `=== WARNINGS ===`, `=== CONTEXT ===`, `=== PORTFOLIO ===`

**Key signal dict keys (camelCase — do not use snake_case):**
- `factorScores`, `factorWeights`, `factorDiagnostics`, `confidenceDetail` (all set in `analyze_pair()`)
- `naked_data` (Engine B scan signals only — contains full naked result)

### Chart Vision — `/api/chart-analysis` in `athena.py`
- Transport: OpenAI-compatible xAI chat completions client (`https://api.x.ai/v1`).
- Model id: **`VISION_MODEL`** in `config.yaml` (current runtime typically Grok reasoning multimodal model id).
- Temperature: **`AI_VISION_TEMPERATURE`** — default **`0.2`** in `config.py` / yaml (factual observation mode).
- Three modes: single-TF (H4), dual-TF (D1+H4), triple-TF (D1+H4+H1)
- Prompt architecture is modularized in `vision_prompts.py` and used directly by `/api/chart-analysis` (single active assignment per system/single/dual/triple prompt).
- RIGHT EDGE instructions enforce A-E candle reading:
  - A Pattern ID + structure location
  - B Wick analysis (direction/ratio/meaning)
  - C Body conviction
  - D Sequence narrative as first RIGHT EDGE line
  - E Candle rules (engulfing requires volume confirmation; counter-trend + rising volume => `POTENTIAL REVERSAL`)
- **Read order (2026-03-31):** Prompts enforce **image-first** for structure and prices; **right edge** = last 5 candles on authoritative TF (single/H4, dual/H4, triple/H1); **then** algorithmic context for cross-check. If chart and context conflict on **structure/direction**, **the image wins**.
- **TRADE SNAPSHOT instrument/TF:** Prefer any **visible chart UI** (watermark, symbol strip, title bar — not only top-left). If the label is still illegible, state exactly *chart label not legible — from request* and use **only** the chart-analysis request metadata for identity: `symbol`, `tf` (single-TF), or the request symbol plus each image’s stated TF role (D1/H4 or D1/H4/H1). Do **not** substitute “algo context” wording for identity, guess alternatives, list candidate pairs, or hedge with likely / maybe / appears / possibly / or. Do not infer instrument from ENGINE A/B text. Fallback is **metadata only**; it does not override visible price action.
- **RIGHT EDGE (2026-03-31):** Model must lead with **interpretation** (momentum, control of last closes, continuation vs pullback vs reversal risk, confirm vs threaten algorithmic LONG/SHORT). Avoid long candle-by-colour play-by-play without meaning; optional one compact oldest→newest fact sentence as evidence.
- **Entry quality rules (2026-04-01):** System prompt rules 9–12 enforce entry positioning assessment (tactical vs chasing into congestion), volatility-regime interaction (low-vol breakdowns flagged), move maturity (late/exhausted entries), and independent RR verification. (Rule 8 is instrument/timeframe metadata-only when chart labels are unreadable.) All three user prompts (single/dual/triple) include an **ENTRY QUALITY** body section between **KEY RISKS** and **FINAL VERDICT**.
- **Body structure:** Concise sections (e.g. TRADE SNAPSHOT, MARKET STRUCTURE, RIGHT EDGE, factors, verdict) plus required machine footer below.
- **STRUCTURED FOOTER (required — do not remove):** Line `RIGHT EDGE: CONFIRMS | REVIEW | POTENTIAL REVERSAL` immediately before `TF ALIGNMENT` + three `*_RATING` + three `*_LEVELS` — parsed by `_extract_vision_structured()` for Engine C conviction modification.
- **Anti-hallucination rules:** ONLY describe what you can ACTUALLY SEE; never invent patterns; cross-reference algo context **after** the visual read; full annotation legend for Engine B elements.
- **Footer parsing:** Parsed by `_extract_vision_structured()` and used by `apply_vision()` to modify Engine C conviction. Removing or rewording parser tokens breaks Engine C Vision integration and UI level extraction.
- **EODHD news (same file, `fetch_news_context`):** Per-pair `/api/news` and `/api/news-word-weights` use `timeout=15` (was 8s) to reduce read timeouts on slow EODHD responses; failures stay non-fatal.
- Advisory hybrid v2 sidecar is available via:
  - `POST /api/vision-sample`
  - `POST /api/vision-label`
  - `POST /api/vision-infer-v2`
  - `GET /api/vision-metrics`
  - `POST /api/vision-train-v2`

---

## Signal Flow

```
run_full_scan(style, asset_class)
  └─ for each active pair (ThreadPoolExecutor, SCAN_MAX_WORKERS workers)
       └─ analyze_pair(pair, btc_bias, style)
            ├─ fetch_candles(pair, "D1"/"H4"/"H1", limit)
            ├─ calc_indicators(candles)  → {snap: {...}}
            ├─ calc_confluence(d1i, h4i, h1i, vr, stoch, pair, ...)
            │    ├─ get_pair_vote_weights(pair)
            │    ├─ votes tallied → bull/bear score
            │    ├─ ranging/counter-trend penalties applied
            │    ├─ classify_signal_setup(...)
            │    └─ returns {score, direction, votes, warnings, signalClass, regime, ...}
            ├─ factor_scoring → {factorScores, factorWeights, factorDiagnostics, confidenceDetail}
            ├─ calc_levels(entry, atr, direction, ptype, style)
            └─ returns full signal dict (camelCase keys)
  └─ _classify_signal → tier: "trade" | "watchlist" | "skip"
  └─ apply_correlation_cap(results)
  └─ _json_safe(result)
```

**Engine B flow:**
```
/api/scan-naked | /api/naked-analysis | /api/compare-engines
  └─ NakedEngine.analyze_structure(d1, h4, h1, price, direction, atr, regime)
  └─ NakedEngine.calculate_confidence(res, price, direction, atr, entry_candles, style_profile)
       └─ checklist: structure, location, trigger/entry, room, RR, macro
  └─ Engine B AI review (advisory only — not a pass/fail gate)
  └─ result stored as "naked_data" on Engine B scan signals
```

**Engine C flow:**
```
POST /api/engine-c-scan {assetClass, style}
  └─ Fetches candles ONCE per pair, shares between Engine A and Engine B via confirmed/forming market-state splits
  └─ analyze_pair(preloaded_candles={D1,H4,H1})   ← Engine A (uses same data as Engine B)
  └─ NakedEngine.analyze_structure() + calculate_confidence()   ← Engine B
  └─ compute_consensus() → ALIGNED / A_ONLY / B_ONLY / CONFLICT / SKIPPED
  └─ ALIGNED results insert into shadow ledger (SHADOW_LEDGER_ENABLED must be true)

Other Engine C paths:
  - /api/compare-engines — manual per-pair A+B+C comparison on demand
  - /api/backtest-consensus — Engine C backtest for a specific pair

In each path the consensus logic is:
  └─ compute_consensus(signal_a, signal_b, confidence_b, regime, entry, atr)
       ├─ normalise_engine_a() → 0–1  (max_score 2.0 for forex, 3.0 for non-forex)
       ├─ normalise_engine_b() → 0–1  (max_possible from calculate_confidence, default 5.0)
       ├─ ENGINE_C_AB_WEIGHTS regime blend: TRENDING={A:0.65,B:0.35}, RANGING={A:0.35,B:0.65}
       ├─ resolve_sl() — structural → ATR-clamped → tighter
       ├─ resolve_tp() — structural if RR≥1.5, else ATR
       └─ returns {conviction, tier, sizing_override, sl, tp, rr, ...}

Note: analyze_pair() logs [ANALYZE] warnings when returning None due to empty/insufficient candles
```

/api/engine-c-confirm (Vision overlay):
  └─ apply_vision(consensus, vision_result)
       ├─ CONFIRM + conviction≥0.35 → trade=True
       └─ AVOID/CONTRADICT → trade=False, tier=SKIP
```

**Pair Browser workflow:**
```
Dashboard Pair Browser tab (panel-pair-browser)
  └─ loadPairBrowserPairs() → GET /api/pairs
  └─ selectPairBrowserPair(symbol)
  └─ Engine A button → POST /api/pair-scan
       └─ Runs analyze_pair() for one selected symbol without requiring a full-universe scan first
  └─ Engine B button → POST /api/naked-analysis
       └─ Uses Engine A direction when Pair Browser direction mode is AUTO; otherwise uses operator-selected LONG/SHORT
  └─ Compare button → POST /api/compare-engines
  └─ News section → POST /api/news-sentiment
  └─ Intermarket section → GET /api/intermarket-matrix
       └─ Read-only context when the selected Engine A signal has no attached intermarketConfirmation payload
  └─ AI Vision button → POST /api/chart-analysis (server_render=true)
  └─ Full Chart button → openAcm(symbol, tf, fallbackSig)
  └─ TV Chart button → openTVChart(display, "240")
```

`POST /api/pair-scan` is the dedicated single-pair Engine A browse route for the Pair Browser tab. Keep it single-pair only; do not repurpose it for full scan orchestration or scoring changes.

**Scalp Lab / Engine D flow:**
```
Dashboard Scalp Lab (panel-scalp) → runScalpScan()
  POST /api/scalp-scan
    └─ get_scalp_pairs()  ← CONFIG SCALP_ENGINE.SCALP_PAIRS or active pairs (MT5 + Binance crypto)
    └─ scalp_engine.run_scalp_scan(pairs)
         ├─ Session filter: scalp_session_window() - supports NY open cooldown (NY_OPEN_SKIP_MINUTES)
         ├─ Spread filter: check_spread() per MAX_SPREAD_PIPS - skipped for crypto
         ├─ MT5 candles: mt5_fetch_scalp_candles(M15, M5, M1, bias_tf)
         │  Crypto candles: _scalp_fetch_candles via fetch_candles/Binance
         ├─ Execution TF: M1 by default (EXECUTION_TIMEFRAME), M5 fallback
         ├─ Pillar 1 - Volume Profile: _build_volume_profile(M15) → POC/VAH/VAL/LVN
         │    _classify_market_state() → "balance" | "imbalance"
         │    _locate_price_vs_vp() → location vs levels
         ├─ Pillar 2 - Aggression (execution TF candles):
         │    _check_absorption() → detected, count, bars (uses indicators.detect_absorption)
         │    _check_cvd() → direction, cvd_slope (uses indicators.calc_cvd)
         │    _check_aaa_sequence() → complete (Absorption→Accumulation→Aggression)
         ├─ Pillar 3 - VWAP: _check_vwap_lean(M15) → lean direction (uses indicators.calc_vwap)
         ├─ HTF bias: infer_bias_from_ema_stack(bias_tf candles) — EMA 21/50/200 stack
         ├─ _classify_setup() → direction, setup_type, reasons, valid
         ├─ HTF bias gate: skip if direction != htf_bias (when WITH_TREND_ONLY)
         ├─ calculate_scalp_levels() → SL (VP-based), TP1 (MIN_RR), TP2, sl_method, rr
         └─ ai_quality_grade() → score 0–100, grade A/B/C/D, size_multiplier, reasons
              Grade gate: skip grade D (MIN_GRADE=C) or C+D (MIN_GRADE=B)

Signal dict keys: pair, display, mt5_symbol, direction, price (entry), sl, tp1, tp2, rr1,
  zone_type (setup_type), zone_conditions, trigger_type, momentum_method,
  vp_poc, vp_vah, vp_val, vp_lvn_count, market_state,
  absorption_count, cvd_direction, cvd_slope, aaa_complete,
  vwap, htf_bias, htf_bias_tf, spread_pips, session,
  execution_tf, context_tf, structure_tf,
  ai_score, ai_grade, ai_reasons, size_multiplier,
  confluenceScore (=ai_score/100 for risk sizing), engine="SCALP"

POST /api/scalp-execute  (pair + signal)
  └─ risk_check() → mt5_execute() OR bybit_execute() (crypto when signal.type=="crypto")

POST /api/backtest-scalp  (pair)  ← Engine D backtest
  - backtest_pair_scalp() in backtest_runner.py
       |- Fetches M15 bars (BT_WALK_BARS), walks forward in chunks
       |- Session filter: scalp_session_window(backtest=True) - respects BT_SESSION_MODE/BT_NY_OPEN_SKIP_MINUTES
       |- Builds VP per window, runs full pipeline per bar
       |- Tracks best_favorable_r for early scratch decisions
       |- Resolves SL/TP1 hits intrabar (_resolve_barrier_exit)
       |- Scratch exit: if BT_SCRATCH_ENABLED and no early follow-through after BT_SCRATCH_BARS
       |- Saves to backtest_results (engine="scalp_vp", bt_min=min_rr)
       - Returns standard result dict + scalp_analysis breakdown
            (trigger/setup/grade counts with WR and avg_r per bucket)
  UI: renderScalpBtSingle() - grade-coloured stats + VP analysis block + trade table
```

UI card fields used: `display||pair`, `market_state`, `zone_type`, `ai_grade`, `ai_score`, `size_multiplier`, `vp_vah`, `vp_poc`, `vp_val`, `aaa_complete`, `absorption_count`, `cvd_direction`, `htf_bias`, `htf_bias_tf`, `session`, `price`, `sl`, `tp1`, `rr1`, `spread_pips`, `sl_method`, `ai_reasons`.

**Execution path:**
```
api_execute()
  ├─ signal freshness check (SIGNAL_MAX_AGE_SEC)
  ├─ live re-analyze if stale → HTTP 409 if direction flipped
  ├─ price drift >1% → rebase SL/TP
  ├─ _validate_exit_levels()
  ├─ risk_check() → RiskApproval (kill switch, heat, sizing)
  └─ mt5_execute() OR bybit_execute()
```

**Risk engine scope:** Peak equity, drawdown, and daily realized P&L are tracked **per** `signal["type"]` (asset class: `crypto`, `forex`, `stock`, etc.) so Bybit vs MT5 equity is not mixed. `risk_check` enforces `MAX_SL_PCT` per class from the config dict (`config.yaml` / `config.py`).

---

## Key Functions

### `calc_confluence(d1, h4, h1, vr, stoch, pair, btc_bias, ...)` — `scoring.py`
12 vote slots: `d1_trend(2.0)`, `h1_ema(1.0)`, `d1_adx(1.0)`, `h4_macd(1.0)`, `h4_oscillator(0.75–1.0)`, `volume(0–1.0)`, `funding(0–1.0)`, `session(0–1.0)`, `h4_fib(0.5–1.0)`, `h1_bb(0.5–1.0)`, `weinstein(0–1.0)`, `divergence(1.0 bonus)`
- Session vote adds `W_SESS×0.5` to BOTH sides; `_base_max` subtracts `W_SESS×0.5` to prevent overstatement
- Tie-break: `bull >= bear → LONG` (intentional long bias)
- Returns: `{score, votes, direction, signalClass, regime, factor_scores, factor_weights, factorDiagnostics, confidenceDetail, ...}`

### `get_min_confluence_threshold(pair)` — `scoring.py`
Priority: `PAIR_PROFILES[pair].min_confluence` → `MIN_CONFLUENCE_GROUP[type][score_group]` → `MIN_CONFLUENCE_CLASS[type]`

### `_max_score_for_pair(pair)` — `athena.py`
Theoretical max score using `get_pair_vote_weights(pair)`, minus `W_SESS×0.5`.

### `fetch_candles(pair, tf, limit)` — `candles_cache.py`
- H1 (eligible live feeds only): `CandleBuilder` first if bars ≥ min, else TTL then REST
- Crypto H4/D1: Binance REST native intervals
- MT5 sources: `fetch_mt5()` for all TFs
- Cache TTL keys: **uppercase** `"H1"/"H4"/"D1"` — lowercase misses cache
- TTL: H1=55 min, H4=3h55m, D1=23h

### `NakedEngine.calculate_confidence(...)` — `market_structure.py`
Shared Engine B checklist for live scan, analysis, compare, backtest. Score = checklist count / UI measure. `passed` = naked price-action rules only — not AI-driven. **Note:** `ENGINE_B_REGIME_MULTIPLIERS` (set to 1.0) ensures the `min_score` from UI or `score_group_overrides` is used directly without hidden regime inflation. **`ENGINE_B_PROFILE_SCORING_ENABLED: true`** — volume profile (POC/VAH/VAL) bonus scoring is active. Crypto uses Binance kline bar volume (`vol = k[5]`); non-crypto uses EODHD real volume overlaid on MT5 candles. `compute_fixed_range_volume_profile()` has a range-proxy fallback for any pair with zero-volume bars.

### `_naked_scan_style_profile(style, score_group)` — `athena.py`
Resolves effective Engine B thresholds. Logic: Hardcoded defaults → `style_profiles` (global UI) → `score_group_overrides` (per-pair BT MIN). Per-group overrides take priority and are displayed in the **"Per-Group BT MIN Overrides"** table in the dashboard's Engine B panel.

### `_engine_b_regime_label(h4_candles, pair_type, regime_hint)` — `athena.py`
Shared regime resolver for all Engine B paths. Returns: `TRENDING`, `RANGING`, `HIGH_VOLATILITY`, `LOW_VOLATILITY`.

### `_json_safe(value)` — `config.py`
Recursively replaces `NaN`/`Inf` with `None`, normalizes numpy types. Applied before every `jsonify()`.

### `_can_execute(signal, cfg)` — `auto_trader.py`
Live execute gate is **`combinedConviction`** vs `AUTO_TRADE_MIN_CONVICTION` (with alignment discount and meta delta). `AUTO_TRADE_MIN_SCORE` is **informational** in `get_status()` only; keep forex on the **0–2.0** scale alongside `MIN_CONFLUENCE_CLASS.forex` / `MIN_CONFLUENCE_CLASS.forex`.

### `/api/chart-analysis` — `athena.py`
Requires `XAI_API_KEY` env var and uses OpenAI-compatible xAI client (`base_url=https://api.x.ai/v1`). `regime` in signal can be dict `{"label":"TRENDING"}` (Engine A) or string `"TRENDING"` (Engine C) — both handled. Context builder is **outside** try/except — keep it simple.

### `/api/scalp-scan` / `/api/scalp-execute` — `athena.py`
See **Scalp Lab / Engine D flow** above. Execute path: **`signal.type == "crypto"` → Bybit**; **else → MT5**. Crypto candles for Engine D come from Binance futures (`fetch_candles` / `CandleBuilder`); orders are **never** sent to Binance.

---

## Scoring Architecture Summary

| Engine | Scorer | Scale | Gate key |
|--------|--------|-------|----------|
| Engine A — non-forex | `factor_scoring.py` z-score factor engine | 0–3.0 | `MIN_CONFLUENCE_CLASS[type]` |
| Engine A — forex | `forex_scoring.py` rules-based | 0–2.0 | `MIN_CONFLUENCE_CLASS.forex` (1.0) + `MIN_CONFLUENCE_CLASS.forex` (1.0) |
| Engine B | `market_structure.py` naked checklist | 0–100 pct | `NAKED_ENGINE.style_profiles.min_score` |
| Engine C | `engine_c.py` A+B blend | 0–1 conviction | `ENGINE_C_AB_WEIGHTS` |
| Engine D (Scalp Lab) | `scalp_engine.py` VP+OrderFlow: VP build → absorption/CVD/AAA → VWAP → `_classify_setup` → `ai_quality_grade` | 0–100 (`ai_score`), letter `ai_grade` A/B/C/D, `size_multiplier` (1.0/0.5/0.25) | `SCALP_ENGINE` (`MIN_RR`, `MAX_SPREAD_PIPS`, `MIN_GRADE`, `WITH_TREND_ONLY`, `BIAS_TIMEFRAME`) |

**Two different REGIME_WEIGHTS exist — do not confuse:**
- `CONFIG["REGIME_WEIGHTS"]` — adjusts **factor group weights** inside Engine A
- `ENGINE_C_AB_WEIGHTS` in `engine_c.py` — controls **A vs B blend ratio** in Engine C

**`confluencePct` display scaling:** anchored to `get_min_confluence_threshold(pair)` so ~67% = "passing" intent. Engine C uses raw `confluenceScore / maxScore` (not `confluencePct`) for normalization.

---

## 2026-04-08: Forex London breakout / news / Engine B reality (current active guidance)

### Code-verified London breakout behavior

- The forex engine is London-breakout capable, but it is **not** a London-breakout-only bot.
- `forex_scoring.py` contains a dedicated `_london_breakout_score()` path.
- The breakout path measures the Asian range from **00:00-07:00 UTC** using H1 candles.
- Breakout scoring is only active during the first 3 London hours: **07:00-09:00 UTC**.
- The Hurst/trend gate can veto the trend-following path while the London breakout path still runs.
- The forex engine remains a hybrid:
  - trend pullback path
  - London breakout path
  - final score uses the stronger valid path

Use this mental model when reasoning about forex:
- **Engine A = breakout / timing detector**
- **Engine B = structural validator**
- **Engine C = alignment / consensus layer**
- **news / event risk = final safety layer**

Do not describe the current forex engine as a dedicated London-breakout-only system.

### London breakout operator guidance (not a single code-enforced switch)

Treat this as the intended review / execution flow for breakout trades:
1. Build the Asian range (**00:00-07:00 UTC**).
2. Let **Engine A** detect breakout quality around London open.
3. Use **Engine B** to validate whether the breakout is structurally tradable.
4. Let **Engine C** confirm A/B alignment.
5. Apply event-risk / sentiment safety filters last.

Interpret timing like this:
- too early = first wick poke through the range
- best = first confirmed H1 close outside the Asian range, ideally during **07:00-09:00 UTC**
- too late = extended chase after multiple London impulse candles

If the move is already stretched, treat it as continuation / pullback logic, not a fresh London breakout.

### Engine B role in London breakout

Engine B is **not** the London-session clock and should not be documented as the primary breakout detector.

Use Engine B as a structure / execution-quality filter:
- did price break structure or only wick the range?
- is the breakout or continuation candle strong enough?
- is there room to target?
- is RR still valid after SL placement?
- is price already too extended from the break area?

Correct role split:
- **Engine A finds the breakout event**
- **Engine B checks whether the breakout is structurally tradable**
- **Engine C checks whether A and B align**

### News toggle and news architecture

The UI news toggle only controls `NEWS_SENTIMENT_CONFLUENCE_ENABLED`.

It does **not** automatically disable:
- `SENTIMENT_GATE_ENABLED`
- `EVENT_RISK_ENABLED`

So current behavior is:
- news toggle ON = news sentiment can nudge scan / confluence score
- news toggle OFF = no news score blend
- sentiment / event safety blockers can still remain active for auto-trading

Current repo reality has **3 separate news / event layers**:
1. background news context for UI / AI / narrative use
2. execution safety gates (`SENTIMENT_GATE_ENABLED`, `EVENT_RISK_ENABLED`)
3. optional scan / confluence blend via `NEWS_SENTIMENT_CONFLUENCE_ENABLED`

Do not describe the current repo as having one unified news engine.

### News blend scoring contract

When news sentiment confluence is enabled, score blending must respect the Engine A score contract for the current path:
- forex Engine A = **0-2.0**
- non-forex Engine A = **0-3.0**

Any news delta must scale from the correct `maxScoreOverride`. Do not assume all Engine A paths share one raw max.

### Current active forex thresholds

- Engine A forex scale = **0-2.0**
- `MIN_CONFLUENCE_CLASS.forex` = **1.0**
- `MIN_CONFLUENCE_CLASS.forex` = **1.0**
- `AUTO_TRADE_MIN_SCORE.forex` remains informational / status-only and should stay aligned to the active forex class floor

Historical threshold notes may remain below, but they must stay clearly labeled historical / superseded.

---

## Pair Profiles (`config.yaml`)

```yaml
PAIR_PROFILES:
  XAU/USD:
    disable_filters: [obv, session]
    weight_overrides: {h4_fib: 1.5, h1_bb: 0.5}
    min_confluence: 5.8
    bt_min: 4.6
  EUR/USD:
    disabled_votes: [volume]
    weight_overrides: {session: 1.25}
```
Valid vote keys: `d1_trend, h1_ema, d1_adx, h4_macd, h4_oscillator, volume, funding, session, h4_fib, h1_bb, weinstein, divergence`
Valid filter keys: `weinstein, session, regime_transition, obv, funding, squeeze, mean_revert, btc_bias, divergence_warning`
`PAIR_PROFILE_VOTES` and `PAIR_PROFILE_FILTERS` constants live in `config.py` only.

---

## Auto-Trader (`auto_trader.py`)

- Daemon thread wakes every 30s, scans every `AUTO_TRADE_SCAN_INTERVAL_MIN` minutes
- Execute gate: **`combinedConviction`** ≥ `AUTO_TRADE_MIN_CONVICTION` (adjusted for alignment / meta). `AUTO_TRADE_MIN_SCORE` is status-only; align per-class scales (forex **0–2.0**).
- Diagnosing: `SELECT pair, score, direction, error_tag, grade FROM audit_log WHERE grade LIKE 'AUTO%' ORDER BY ts DESC LIMIT 20`
- `grade='AUTO-DEMO'` = success. `grade='AUTO-ERR-DEMO'` = blocked (see `error_tag`)

---

## Bybit Executor (`bybit_executor.py`)

Bybit Linear (USDT-M) Futures, 1x leverage, ISOLATED margin. LONG = market buy, SHORT = market sell.
- SL = `stop_market`, TP = `take_profit_market`
- `adjustForTimeDifference: True`, `recvWindow: 10000` (fixes retCode 10002)
- Post-fill `_validate_exit_levels()` → emergency close if invalid (no retry)
- `_set_trading_stop()` — 2 attempts, 2s sleep, then emergency close
- Env: `BYBIT_API_KEY`, `BYBIT_API_SECRET`, `BYBIT_TESTNET=true`

---

## Audit DB Schema (`audit.db`)

### `audit_log`
Key columns: `ts, pair, score, direction, grade, edge_prob, risk, style, asset_class, score_pct, max_score, votes_json, warnings_json, regime, entry_price, sl, tp, volume, risk_amount, ticket, exit_price, pnl, r_multiple, exit_reason, holding_period_hours, error_tag, fee_cost, factors_json`

- `factors_json` — `{scores, weights, disabled, regime}` from `compute_factor_scores()`. Used by `ai_learning.py`.
- `fee_cost` — Bybit commission. NULL for MT5.
- `exit_reason` — outcome classification written by `_update_trade_outcome()` in `athena.py`. Current live values include `SL_HIT`, `TP_HIT`, `MANUAL_CLOSE`, and `TIMED_CLOSE`.
- Timed closes are pre-marked by `timed_exit_monitor.py` and preserved by `_update_trade_outcome()` so completed timed exits remain attributable in `audit_log` and `/api/performance`.
- `/api/open-trades-timed` and `timed_exit_monitor.py` match live positions to audit rows by exact ticket first, then by `pair + direction + entry_price` fallback. This covers split MT5 legs and Bybit positions where the live position identifier does not match the audit ticket 1:1.

### `backtest_results`
Columns: `id, run_date, pair, asset_type, engine, trades, win_rate, profit_factor, expectancy, sqn, sharpe, sortino, is_score, oos_score, max_dd_pct, bt_min, atr_source, notes`
- `engine`: `"forex_scoring"` | `"factor_scoring"` | `"naked_engine"` | `"scalp_vp"`
- `bt_min`: the effective minimum threshold used for that run (Engine D stores `min_rr`)
- Endpoints: `GET /api/backtest-history`, `/api/backtest-history/<pair>`, `/api/backtest-best`

Schema auto-migrated on startup. To add a column: add to both `CREATE TABLE` and migration list in `_init_audit_db()`.

---

## Debugging Playbook

**Scoring/confluence complaints require tracing ALL layers:**
1. Data feed → candle cache → `fetch_candles` → `analyze_pair` → response JSON fields → UI formula → `/api/candles` chart
2. Use `confluenceScore`, `maxScore`, `get_min_confluence_threshold(pair)` together — never `confluencePct` alone
3. EMA mismatch: verify same `limit`, same forming-bar drop, same venue (crypto: futures WS for H1)
4. Engine A vs Engine B disagreement is **by design** — different inputs, gates, scales

**Common traps:**
- `confluencePct` optical illusion: display is threshold-anchored, NOT `score/maxScore` — `20fd03a`
- `_json_safe()` must be called before every `jsonify()` (NaN/Inf crash)
- `cache TTL keys must be uppercase "H1"/"H4"/"D1"`
- Engine C `regime` can be string or dict — handle both
- `confidenceDetail` lives on the signal dict (`analyze_pair` sets it); `naked_data` holds the Engine B naked result for Engine B scan signals
- `[NAKED-DBG]` logs in `athena.py` show specific checklist failures (e.g., `fails=[struct,loc,trigger,rr=0.8]`) for immediate diagnostic visibility.
- `[ANALYZE]` warnings in `athena.py` log when `analyze_pair()` returns None due to empty/insufficient candles — check console for D1/H4/H1 bar counts.

---

## Candle Windows

- **Engine A scan:** `D1=1001`, `H4=1001`, `H1=1001` → ~1000 closed bars after forming-bar drop
- **Dashboard `/api/candles`:** `limit=1000` — EMA 21/50/200 right-edge values match scan when limits align
- **Minimums after drop:** `len(d1) >= 220`, `len(h4) >= 50`, `len(h1) >= 50` or pair is skipped

---

## Backtest

### Engine A (`backtest_pair`)
- Swing: D1 bars, max hold 20 bars → TIMEOUT (force-closed at last bar close, signed R, labelled TIMEOUT)
- Intraday: H4 bars, max hold 12 bars
- `MAX_OPEN=3` concurrent positions

### Engine B (`backtest_pair_naked`)
- Hold: `scalp=12`, `intraday=24`, `swing=60` H4 bars
- Enforces same `min_score`, `passed`, `min_rr` as live scan
- 2-bar H4 cooldown after exit bar
- Uses `_engine_b_regime_label()` for zone multipliers

### Engine D (`backtest_pair_scalp`) — `POST /api/backtest-scalp`
- Walks forward over M15 bars (`BT_WALK_BARS` from config, default 2000)
- Session filter: `scalp_session_window(backtest=True)` respects `BT_SESSION_MODE`/`BT_NY_OPEN_SKIP_MINUTES`
- Per bar: builds VP over a rolling lookback window, runs full VP→Absorption/CVD/AAA→VWAP→classify pipeline
- Grade gate applied (skips D, or C+D if MIN_GRADE=B); HTF bias gate applied
- Tracks `best_favorable_r` for early scratch decisions
- Exit: `_resolve_barrier_exit()` intrabar SL/TP1 resolution; scratch exit if `BT_SCRATCH_ENABLED` and no follow-through after `BT_SCRATCH_BARS`
- Slippage: `BT_SLIPPAGE_TICKS` applied to entry
- DB: saved as `engine="scalp_vp"`, `bt_min=min_rr`
- Response includes `scalp_analysis` dict: per-trigger (absorption/cvd_shift/rejection), per-setup (mean_reversion/trend), per-grade (grade_A/B/C) breakdowns with `count`, `wr`, `avg_r`
- UI: **⚡ Engine D (Scalp VP)** button in backtest panel → `renderScalpBtSingle()` renderer

---

## Advisory thresholds (backtest + live suggestions)

- **Module:** [advisory_thresholds.py](advisory_thresholds.py) — builds suggestions from latest `backtest_results` per (pair, engine) and closed `audit_log` trades.
- **No auto-apply:** `BT_MIN`, `MIN_CONFLUENCE_CLASS`, and `NAKED_ENGINE.style_profiles.min_score` change **only** after **POST** `/api/advisory-thresholds/<rec_id>/approve` (human action). GET `/api/advisory-thresholds` is read-only for gates.
- **Audit:** Approvals/rejections stored in `advisory_threshold_actions` in `audit.db`.

## Lottery Lab

**Games:** `lotto` (6/58+bonus), `powerball` (5/50+bonus), `daily_lotto` (5/36)
**Generator modes:** `pure_random · hot_bias · cold_bias · overdue_bias · balanced_mix · pair_bias · anti_crowd`

**Lottery AI:** `POST /api/lottery/ai-analysis` uses **Grok (xAI)** only — not Anthropic. Set **`XAI_API_KEY`** in env; **`openai`** package with **`base_url=https://api.x.ai/v1`**; model from **`LOTTERY_AI_MODEL`** (optional) or **`XAI_MODEL`** in `config.yaml`. Response JSON includes **`model`** echo.

**Critical invariants:**
- `simulate_generator()` uses incremental counters — never revert to full-history rescan per draw (quadratic regression)
- `generate_tickets()` requires `rules`, `universe`, `main_numbers` initialised before `if draw_context is not None`
- Per-ticket retry cap: `max(25, min(400, len(main_numbers) * 4))` — never remove
- New games must be added to both `LOTTERY_GAME_RULES` (`lottery_engine.py`) AND `LOTTERY_GAME_SPECS` (`lottery_service.py`)
- `LOTTERY_GAME_RULES["lotto"]` uses `main_max=58, bonus_max=58` (SA Lotto 1–58 pool since 21 Sep 2025) — do not revert to 52 or 55

---

## EODHD WebSocket Allocation

- Plan cap: **50 tickers total** across all endpoints
- Current WS: ~17 US stocks/ETFs + ~28 forex/commodities/indices = ~45
- `"ws": False` on a pair = opts out of WS only; scan/backtest/execute unaffected
- To reallocate WS slots: run backtests → query `backtest_results` by SQN → update `"ws"` flags

---

## Python Environment

- Virtualenv: `.\.venv\Scripts\python.exe`
- Tests: `.\.venv\Scripts\python.exe -m pytest tests/ test_indicators.py -v`
- Platform: Windows 11 (dev); Linux also supported

---

## DECISIONS

## 2026-04-21: Confidence session multiplier + forex counter-trend SHORT gate + BE fix

**`confidence_engine.py` — session quality multiplier:**
`compute_confidence()` now accepts `session_quality: Optional[str]` and post-multiplies confidence:
- `"high"` (London Open, London-NY overlap) → **1.00** (no change)
- `"medium"` (London solo, NY solo) → **0.90**
- `"low"` (Asian, off-hours) → **0.70**

Called from `scoring.py` via `get_session(bar_time)["quality"]`. Off-hours Engine A signals have lower confidence → Engine C's reliability gate (`confidence >= 0.60`) naturally demotes them. Does not affect scoring gates directly.

**`forex_scoring.py` — counter-trend SHORT gate:**
Two config-driven penalty cases under `FOREX_ENGINE.counter_trend_short_gate`:
- **Case A** (`bo_counter_trend_mult: 0.70`): London breakout SHORT (`bo_dir="SHORT"`) while EMA stack is LONG (`trend_dir="LONG"`) and Hurst is trending → `bo_final × 0.70`. Prevents fading a confirmed bullish trend on the first London reversal wick.
- **Case B** (`trend_high_hurst_short_mult: 0.80`): Trend-path SHORT with `_hurst > high_hurst_threshold (0.65)` → `trend_score × 0.80`. Mature downtrend is already extended; penalise late-SHORT entries.
Both penalties are additive (can both apply). Result stored in `result.components["counter_trend_short_applied"]` and `"counter_trend_case"`. Config key `enabled: true`; set `false` to bypass.

**`timed_exit_monitor.py` — breakeven closing at 0.00R fix:**
Root cause: `profit > 0` armed BE on any positive P&L (even $0.01); `mt5_move_sl_to_breakeven` placed SL exactly at entry; spread immediately stopped the trade out at 0.00R.

Two-part fix (both config-driven under `TIMED_EXIT`):
1. **`breakeven_min_profit_r: 0.20`** — BE only arms when `profit >= risk_amount × 0.20`. For a $100 risk trade that's $20 minimum. Falls back to $0.01 floor for rows without `risk_amount`.
2. **`breakeven_buffer_r: 0.05`** — BE SL placed at `entry + sl_dist × 5%` (LONG) or `entry − sl_dist × 5%` (SHORT). On a 20-pip SL that's 1 pip above entry — ensures close price > 0.00R. Applied to both MT5 and Bybit handlers.

The "immediate close" path (`close_min <= be_min` case) retains `profit > 0` unchanged — that path banks profit immediately rather than setting a deferred BE stop.

---

## 2026-04-18: Engine A scorer-is-noise confirmation (factor-level probe)

**Conclusion:** Engine A's scorer output is statistically uncorrelated with trade outcome at both the aggregate and per-factor level. No single-factor polarity flip will rescue WR. Entry-logic redesign is the next branch, not gate/weight tuning.

**Chain of evidence this session:**
1. `diagnose_score_direction.py` (n=265 closed audit rows): Spearman(score, R) = **−0.018**. Score quintile WR is **inverted** — Q5 (highest score) WR 30.2% < Q1 37.7%. Directional hit-rate **42.3%** (worse than coin-flip).
2. Factor-level probe via `diagnose_factors.py` showed only 41/265 audit rows had populated `factors_json` (15.5% coverage) — most historical rows pre-dated the audit-writer fix. Too sparse to trust any per-factor correlation.
3. **camelCase `factors_json` bug found and fixed** at `athena.py:4788` and `athena.py:6050`. Both audit-write sites used `sig.get("factor_scores")` (snake_case) but the signal dict exposes `factorScores` (CLAUDE.md Hard Rule 23). New rows now populate correctly.
4. Built `instrumented_backtest.py` (Engine A backtest with factor sidecar JSONL) + `analyze_factor_dump.py` (Pearson per factor vs R). Additive fields-only edit to `backtest_runner.py` (three `trades.append` sites: swing/intraday/scalp) — logic unchanged.
5. Ran across BTC/USDT, ETH/USDT, EUR/USD intraday → **190 factor-populated trades**. Killed mid-run; sample adequate.

**Factor-level findings (n=190, pooled; intraday):**
- **No factor crosses |ρ|≥0.20.** GBP/USD smoke (n=32) had `fvg_bonus`/`fvg_overlap` at −0.447 but this washed to −0.091 when pooled with EUR/USD — pure small-sample noise.
- Strongest negative: crypto `trend` factor ρ=−0.175 (mean signed value −1.35). Heaviest weight in `factor_scoring.py`. Suggestive but below threshold — needs a bigger crypto sample to confirm, and even at −0.175 it's not a polarity bug.
- Strongest positive: crypto `volume_strength` ρ=+0.130, forex `liquidity_sweep` ρ=+0.097. Weak.
- Verdict matches `diagnose_score_direction.py`: **SCORER-IS-NOISE branch**, not DIRECTION-IS-RANDOM. A polarity flip on any single factor will not rescue WR.

**What was NOT the problem (ruled out this session + prior sessions):**
- BT gate level (2.3× range, flat WR across gates)
- Hurst gate (within noise — measurement revert on 2026-04-18)
- Context propagation (funding/OI 100%/99.8% coverage)
- External-audit `pairMaxScore=3.0` forex claim (stale JSON from 2026-04-05, pre-scale-fix)
- External-audit `bisect` lookahead claim (all sites use `bisect_left`)
- Individual factor sign inversion

**Open next steps (in priority order):**
1. **LONG/SHORT split** on the 190-trade dump — 42.3% dir hit-rate may be one-sided (scorer right on LONG, broken on SHORT, or vice versa). `analyze_factor_dump.py` needs a `--split direction` flag.
2. **Regime split** on same dump — scorer may work in TRENDING and fail in RANGING.
3. **If neither split isolates the issue:** escalate to entry-timing redesign discussion with explicit scope approval. Options include: feature engineering (new factors from orderflow/swing structure), model replacement (ML-ranked entry instead of linear weight), or style pruning (drop whichever style contributes most to random portion).

**Code changes this session (non-scoring):**
- `scoring.py:179-186` — fixed BT fall-through trap where LIVE `MIN_CONFLUENCE_GROUP` bypassed class-level `BT_MIN` when `BACKTEST_USE_BT_MIN_THRESHOLDS=true` (prior session bug).
- `athena.py:4788-4795` + `athena.py:6050-6057` — fixed camelCase `factors_json` bug.
- `athena.py` `_update_trade_outcome` — added |R|>50 sanity clamp + asset-typed fallback gate (prevents the −44554R EUR/CHF corruption from contaminating aggregates).
- `forex_scoring.py` — added `BACKTEST_DISABLE_HURST_GATE` opt-in measurement flag (reversible, backtest-only).
- `config.yaml:355` — `BACKTEST_DISABLE_HURST_GATE: false` (measurement complete, reverted).
- `backtest_runner.py` — three `trades.append` sites now carry `factors` + `factor_weights` for sidecar analysis. Additive only; scoring logic untouched.

**New read-only diagnostic scripts (no writes, `mode=ro` sqlite):**
- `diagnose_score_direction.py` — Spearman / quintile WR / directional hit-rate
- `diagnose_factors.py` — per-factor Pearson correlation (audit_log-based; low coverage pre-fix)
- `diagnose_style.py` — WR/SL/TP/avgR breakdown by (class, style)
- `probe_bt_context.py` — BTC/USDT funding/OI coverage probe
- `instrumented_backtest.py` — runs `backtest_pair` across a pair universe, dumps `factor_dump.jsonl` sidecar (gitignored)
- `analyze_factor_dump.py` — per-factor Pearson(factor, R), splits by class and style

---

## 2026-04-17: Engine A edge root-cause investigation (read-only, diagnostic)

**Conclusion: the Engine A edge loss is NOT the BT score gate.** Three backtest runs on same pair set at different gates produced essentially identical WR:

| Pair | Gate 1.4 (live) | Gate ~0.94 (old BT) | Gate 2.15 (p75) |
|------|------|------|------|
| BTC/USDT | 349 trades, WR 30.1%, PF 0.63 | 411 trades, WR 29.7%, PF 0.58 | 172 trades, WR 29.7%, PF 0.71 |
| GBP/USD | — | 199 trades, WR 36.2%, PF 0.80 | 84 trades, WR 31.0%, PF 0.70 |

WR is flat across a 2.3× gate range. Raising `BT_MIN` cannot rescue Engine A.

**Code-path trap found — `scoring.py:179-182`:** `get_score_threshold` has an **unconditional** fall-through to LIVE `MIN_CONFLUENCE_GROUP` BEFORE reaching class-level `BT_MIN`. So if `BT_MIN_GROUP` is empty or missing a subgroup, the BT lookup silently uses the LIVE group value and `BT_MIN` is never read. To make `BT_MIN` class-level actually bind, **every** subgroup in `MIN_CONFLUENCE_GROUP` must have a matching entry in `BT_MIN_GROUP`. This is a policy-locked scoring file — document the trap but do **not** refactor without explicit user request.

**Config changes this session (BT-only, live gates untouched):**
- `BACKTEST_USE_BT_MIN_THRESHOLDS: false → true`
- `BT_MIN` raised to class-level p75 of observed live Engine A scores: crypto 2.15, commodity 1.80, forex 1.20, stock 1.10, index 1.17
- `BT_MIN_GROUP` repopulated with p75 per subgroup (prevents the `scoring.py:179-182` LIVE fallback from binding)
- `PAIR_PROFILES.XAU/USD.bt_min` and `XAG/USD.bt_min` raised 0.65 → 1.80 (were bypassing class gate)

**Real Engine A findings from `diagnose_edge.py` (read-only, n=266 closed trades):**
- Score Q1→Q4 WR is **not monotonic** — factor scoring doesn't discriminate winners from losers at any threshold
- SL_HIT share: **crypto 56.2%, forex 50.8%** — half the trades never reach TP
- WR by direction: roughly symmetric (no LONG/SHORT tie-break bias)

**SL-mechanics findings from `diagnose_exits.py`:**
- Median SL-hit hold time: crypto 3.67h, forex 5.71h, stock 3.35h, commodity 3.12h — stopped out within a single H4 bar
- TIGHT SL tercile SL_HIT rate: forex **77.3%**, crypto **66.7%** (vs MID/WIDE terciles much lower)
- Current `STYLE_ATR_MULTS` (`config.py:155-186`) are already at industry benchmarks (scalp 1.0-1.2×, intraday 1.5-2.0×, swing 1.8×) — **not globally "too tight"**. Next hypothesis to separate: style mix (scalp dragging) vs entry timing (chasing extremes).

**Data-integrity bug found — not an edge issue but pollutes ALL aggregates:**
EUR/CHF row `ts=2026-03-16T08:18:14` has `r_multiple=-44554.48` on a normal -$106.54 loss. True math:
- Risk: `|0.90389 - 0.90945| = 0.00556`
- Adverse: `|0.90389 - 0.90584| = 0.00195`
- Actual R: **-0.35** (a completely normal loser)

Bug lives in `_update_trade_outcome` in `athena.py` — `risk_amount` likely recorded in wrong units (pip distance vs dollar risk). A single row at -44554R skews every WR/PF/expectancy average across `/api/performance`, `ai_learning.py`, `advisory_thresholds.py`. **Must be fixed before trusting any `audit_log` stats.**

**New read-only diagnostic scripts (`mode=ro` sqlite, no writes):**
- `diagnose_edge.py` — WR by score quartile, exit-reason mix, WR by direction per asset class
- `diagnose_exits.py` — time-to-SL, SL% terciles, SL_HIT by regime, anomaly row dump

**Open next steps for next session (in priority order):**
1. **Write `diagnose_style.py`** (read-only) — break down SL_HIT / TP_HIT / WR / avg_r by **style** (scalp / intraday / swing) within each class. Separates two hypotheses: "scalp style is broken" vs "entry timing is broken across all styles." If scalp SL_HIT is 80% and swing is 40%, the decision is to drop or tighten scalp. If WR is flat across styles, entry timing is the real fix.
2. **Fix `_update_trade_outcome` r_multiple bookkeeping bug** in `athena.py`. First dump all rows where `abs(r_multiple) > 10` to measure pollution scope, then patch the risk-amount unit handling. Re-compute WR/PF/expectancy aggregates after fix.
3. **Only after #1+#2:** decide on a scoring-adjacent change (drop/tighten scalp, widen SL style-specifically, or accept that Engine A entry timing needs deeper redesign). Any of these touch policy-locked scoring files — require explicit user approval.

**Branch state:** `claude/review-codebase-RjgKT` ahead of `main` with BT p75 diagnostic config + 2 read-only diagnostic scripts. **Live gates (`MIN_CONFLUENCE_*`) untouched** per locked-scoring policy. Revert BT chain by flipping `BACKTEST_USE_BT_MIN_THRESHOLDS: true → false`.

---

## 2026-04-17: Engine A forex soft-gating + Engine D gate tightening

**Engine A forex (Option B — session/ADX softening, breakout window extension):**

Diagnostic (`diagnose_engine_a_forex.py`) showed only **3/24 UTC hours** produced signals under ideal bull conditions because of stacked hard gates (session=strict + ADX hard floor 25 + Hurst veto + 3-hour London breakout window). Applied three reversible config-level softenings in `config.yaml` `FOREX_ENGINE`:

- `session_mode: "strict"` → **`"soft"`** (shoulder hours partially active, not blocked)
- `trend_gate_adx_soft_enabled: false` → **`true`** (ADX below hard floor scales confidence, not veto)
- New keys `london_breakout_window_lo: 7`, `london_breakout_window_hi: 11` — extend breakout path from 07:00–09:00 to **07:00–11:00 UTC**. `forex_scoring._london_breakout_score()` reads these (with safe fallback to the old `[_LONDON_OPEN[0], _LONDON_OPEN[0]+2]` window if CONFIG missing)

**No scoring gate, weight, or threshold changes.** These are **soft-gate** flips — live scoring logic untouched per locked policy. Revert by flipping any of the three keys back.

**Engine A forex class floor (unchanged):** `MIN_CONFLUENCE_CLASS.forex = 1.0`, scale 0–2.0. Current active contract.

**Engine D (Scalp Lab) tightening:**

1. **`config.py` FACTOR_MIN_DIRECTIONAL_CRYPTO drift fix:** hardcoded default was 0.20, `config.yaml` was 0.15 → aligned to **0.15**. Runtime value unchanged when yaml present; now consistent when yaml is stripped/replaced.
2. **Crypto trade-bucket VP fallback logging** (`scalp_engine.py` ~line 2060): when Binance aggTrade buckets are stale/unavailable and VP falls back to candle volume, emit `log.info("[SCALP-VP] trade_bucket fallback: %s reason=%s — using candle VP", ...)`. Lets us measure real fallback rate without changing behavior.
3. **`SCALP_ENGINE.MIN_GRADE: "C" → "B"`** in `config.yaml`. Scan gate now filters grade C (0.25× size) setups. `MIN_GRADE_AUTO_EXECUTE` was already "B"; this aligns the scan display to the auto-execute floor.
4. **`MT5_ABSORPTION_MIN_COUNT: 2`** new config key + `_classify_setup(asset_type=...)` parameter. For non-crypto (MT5) pairs, mean-reversion one-of-three confluence now requires `absorption.count >= 2` to count as "absorption present" — MT5 tick-volume absorption on a single bar is too noisy. Crypto (Binance real bid/ask trade volume) unchanged. Both callers — `scalp_engine.py` live scan and `backtest_runner.py` scalp BT — pass `asset_type`.

**Non-forex threshold verification (no change):** Analytical verification under variance sqrt-scaling + `_SCORE_SCALE=2.11` stretch confirms current `MIN_CONFLUENCE_CLASS` (crypto 1.20, stock 1.10, commodity 1.10, index 1.05) are reachable with 2–3 aligned factors at z≥1.5. Did **not** change any non-forex thresholds this session — previous regressions were candle-data issues, not gate level.

---

## 2026-04-13: Engine B forex intraday D1 override removed

**Problem:** All Engine B scan paths (standalone `/api/scan-naked`, Engine C `/api/engine-c-scan`, `scanner.py`, and the `simulate_trade` backtest wrapper) contained logic that silently upgraded forex intraday style to swing when `ENGINE_B_FOREX_STRUCTURE_TF = "D1"`. This meant:
- An explicit **intraday** scan on any forex pair used **swing** thresholds: `min_score=5.0` (needs 5/5 checklist), `zone_tf=D1`, `entry_tf=H4`
- Intraday should use: `min_score=4.0` (needs 4/5), `zone_tf=H4`, `entry_tf=H1`
- Result: forex pairs that would pass intraday gates were rejected and surfaced as WATCHLIST in Engine C

**Fix:** Removed the intraday→swing upgrade blocks from:
- `execution.py` (Engine C scan)
- `athena.py` `/api/scan-naked` and `/api/naked-analysis`
- `scanner.py` (Engine A+B combined scan)
- `market_structure.py` `simulate_trade` — entry candle selection now uses `style_profile["entry_tf"]` instead of the D1 check

**`ENGINE_B_FOREX_STRUCTURE_TF`:** The config key still exists and still controls `analyze_structure` internal logic (D1 swing detection, BOS/CHoCH analysis) — that is correct and untouched. The only thing removed is the code that **upgraded the style profile thresholds** based on this config.

**`backtest_runner.py`:** Retains the upgrade but only for `requested_style in ("auto", "naked")` — explicit intraday backtests are unaffected.

**Intraday forex Engine B TFs (current):** `zone_tf=H4`, `entry_tf=H1`, `atr_tf=H4`, `min_score=4.0`.

---

## 2026-04-05: Engine A threshold recalibration

**Root cause:** `MIN_CONFLUENCE_CLASS` gates for stocks (1.55), ETFs (1.45), commodities (1.40), and indices (1.35) were above the scoring formula ceiling.

The multiplicative formula with binary trend factor produces:

- **Maximum achievable score:** 1.42 (all factors at theoretical peak)
- **Typical good signal:** 0.79

Previous gates made it structurally impossible for stocks/ETFs to pass, and commodities/indices could only pass under rare perfect conditions.

**New gates (`config.yaml`):**

- stock: **1.10** (was 1.55)
- commodity: **1.10** (was 1.40)
- index: **1.05** (was 1.35)
- crypto: **1.20** (was 1.40)
- forex: see historical **2026-04-07** decision below (superseded by the current 1.0 live class floor)

These are calibrated at ~75–85% of maximum achievable to allow strong signals through while maintaining selectivity. Backtest **30+ trades per pair** before further adjustment.

---

## 2026-04-05: Engine A score scaling fix

**Problem:** The multiplicative formula `dir_score × quality_mult × dir_conf` produced a maximum `final_score` of ~1.42 instead of the documented 0–3.0 range. Root cause: the trend factor is binary (max ±1.0) and has the highest weight (2.0), so the weighted average of directional factors is structurally capped at ~1.4 regardless of how strong momentum/derivatives/carry are.

This made `MIN_CONFLUENCE_CLASS` gates unreachable:

- stock (1.55): impossible — max was 1.42
- commodity (1.40): only at theoretical perfect conditions
- index (1.35): only at theoretical perfect conditions

**Fix:** Added `_SCORE_SCALE = 2.11` in `factor_scoring.py` line ~1169 to stretch output to the documented 0–3.0 range. Now:

- A score of 1.5 = 50% of theoretical maximum
- A score of 2.0 = 67% of theoretical maximum
- Gates at 1.35–1.55 are achievable with strong (but not perfect) signals

**Impact:** All Engine A backtests before this fix used the unscaled 0–1.42 range. Re-run backtests after applying to get valid results. No threshold changes needed.

---

## 2026-04-07: Forex scoring scale fix (0–2.0)

**Problem:** Forex scoring had two `min(1.0, …)` caps compressing the score range. True max raw was ~1.97, but any score above 1.0 displayed as **1.0**. An intermediate fix used `_FOREX_SCORE_SCALE = 0.507` into 0–1.0 and lowered gates to **0.50** — that collapsed trade counts (e.g. USD/CHF) because the threshold drop did not fully compensate (needed raw ≥ ~0.99 vs old ≥ ~0.80).

**Final fix:**

1. Removed caps on `trend_score` and on the multiplicative `final_score` (no 0.507 scaling).
2. **0–2.0** display scale: `result.final_score` capped at **2.0** (matches achievable max ~1.97).
3. Historical thresholds at that point: **`MIN_CONFLUENCE_CLASS.forex`** and **`MIN_CONFLUENCE_CLASS.forex`** ? **1.60** (~80% of 2.0, similar selectivity to old **0.80** on 0?1.0). **`BT_MIN.forex`** ? **1.50**; **`BT_MIN_GROUP`** forex ? **1.50 / 1.55 / 1.65**; **`MIN_CONFLUENCE_GROUP`** forex aligned to the same ladder.
4. **`advisory_thresholds.py`:** `_BT_LIMITS.forex` → **(0.80, 1.90)**; `_LIVE_LIMITS.forex` → **(1.00, 2.00)**.
5. **`maxScoreOverride` / Engine C forex path:** **2.0** in `athena.py` and `backtest_runner.py`; **`normalize_engine_a`** treats **`max_score ≤ 2.01`** like forex for the A-side floor. Engine C **conviction** calibration / `record_signal_event` stay **`max_score=1.0`** (0–1 normalized).
6. Historical fallback sync at that point: **`config.py` fallbacks** and **`AUTO_TRADE_MIN_SCORE.forex`** ? **1.60** (informational; matched the then-current class gate on 0?2.0).

**Score mapping (illustrative):**

| Old (capped 0–1) | New (0–2.0) | Meaning |
|-------------------|-------------|---------|
| 0.80 gate | 1.60 gate | ~same selectivity |
| 1.00 (cap) | 1.97 | True max visible |

**Expected at that time:** Trade counts closer to pre?0.507 fix; elite scores **1.8?2.0** separable from good **1.4?1.6**. Re-run forex backtests.

---

## 2026-04-07: Forex threshold correction (too high) ? historical / superseded again later

**Problem:** After the 0–2.0 scale fix, forex thresholds remained at **80% of max** (1.60), which was unreachable without rare SMC bonuses (FVG overlap + liquidity sweep). Typical good forex signals scored 0.8–1.1, so no signals passed.

**Fix:** Lowered forex thresholds to match other asset classes (~47-52% of max):

| Threshold | Old | New | % of max (2.0) |
|---|---|---|---|
| `MIN_CONFLUENCE_CLASS.forex` | 1.60 | **0.95** | 48% |
| `forex_majors` | 1.50 | **0.85** | 43% |
| `forex_crosses` | 1.55 | **0.95** | 48% |
| `forex_exotics` | 1.65 | **1.05** | 53% |
| `BT_MIN.forex` | 1.50 | **0.85** | 43% |
| `BT_MIN_GROUP` forex | 1.50-1.65 | **0.75-0.95** | 38-48% |

**Impact at that time:** Forex signals now appear in main scan. BT_MIN thresholds scaled proportionally to maintain BT/live ratio.

Current active contract:
- Engine A forex scale is **0?2.0**
- `MIN_CONFLUENCE_CLASS.forex` is **1.0**
- `MIN_CONFLUENCE_CLASS.forex` is **1.0**
- `AUTO_TRADE_MIN_SCORE.forex` is **1.0** and remains informational/status-only

---

## 2026-04-07: H1 preload regression fix

**Problem:** The Engine C double-fetch fix added `if h1 is not None:` in `analyze_pair()`. Empty list `[]` from `fetch_candles` satisfied this, bypassing `candle_manager` which has WebSocket data. Result: forex pairs got empty H1 → `analyze_pair()` returned None → no signals.

**Fix:** Changed to truthiness check `if h1:` so empty/missing H1 falls through to `candle_manager` as before.

---

## 2026-04-07: Engine C double-fetch elimination

**Problem:** `api_engine_c_scan()` fetched candles for Engine B, then `analyze_pair()` fetched them again internally. This caused:
- 6 API calls per pair instead of 3 (EODHD rate limits)
- Different H1 data between Engine A and B (different Hurst exponent → different forex scores)
- Higher latency and cost

**Fix:** Restructured per-pair loop:
1. Fetch candles ONCE per TF (`_tf_map_full` stores full bars, `_tf_map` stores last-bar-dropped)
2. Pass `{D1, H4, H1}` from `_tf_map_full` to `analyze_pair(preloaded_candles=...)`
3. Use `_tf_map` for Engine B (last bar dropped as expected)

**Result:** ~40% reduction in EODHD API calls per pair, identical candle data between engines.

---

## 2026-04-16: Engine A MT5 Direct Fetch (TTL Cache Bypass)

**Problem:** The candles_cache TTL (H1=55min, H4=235min, D1=23h) caused Engine A to use stale data for MT5-sourced pairs. CandleBuilder WS was also overriding MT5 OHLC for forex/commodity/index pairs, creating data inconsistencies.

**Fix:** Modified `candles_cache.py`:
1. Excluded MT5 pairs from CandleBuilder WS routing (line 364-365)
2. Bypass TTL cache entirely for MT5 pairs (lines 479-495) — MT5 IPC is fast (1-2ms)
3. Removed MT5+WS merge logic for forex/commodity/index

**Result:** Engine A/B non-crypto pairs now fetch fresh MT5 data on every scan. Crypto pairs still use Binance WS/REST via candles_cache.

**Engine D (Scalp Lab):** Unaffected — already uses `mt5_fetch_scalp_candles()` directly.

**Volume source badge:** Shows "mt5_tick" for forex/commodity/index (correct — OTC markets have no real exchange volume) and "binance_ws" for crypto (correct — Binance WebSocket data).

---

## 2026-04-14: Engine D (Scalp Lab) — Full VP+OrderFlow rebuild

**Previous implementation:** Zone-based M15 structure + M5 trigger system (`detect_m15_zone`, `detect_m5_trigger`, `confirm_momentum`). Signal keys: `entry`, `rr`, `zone_desc`, `trigger_desc`, `risk_level`.

**New implementation:** Fabio Valentini Pro Scalper methodology — three-pillar orderflow system.

**New files/modules:**
- `volume_profile.py` — fixed-range VP computation (POC, VAH, VAL, LVN)
- `indicators.py` — appended: `calc_vwap`, `detect_absorption`, `calc_cvd`, `detect_range_contraction`
- `scalp_engine.py` — fully replaced (~1327 lines)
- `backtest_runner.py` — `backtest_pair_scalp()` inserted before `run_full_backtest`
- `athena.py` — `/api/backtest-scalp` route added; backtest import updated
- `tests/test_scalp_engine.py` — fully replaced to match new API

**Pipeline:** VP (M15) → Absorption/CVD/AAA (M5) → VWAP lean (M15) → HTF bias (H1 EMA stack) → `_classify_setup` → `calculate_scalp_levels` → `ai_quality_grade`

**Signal shape change (breaking — UI updated same session):**

| Old key | New key | Notes |
|---|---|---|
| `s.entry` | `s.price` | entry price |
| `s.rr` | `s.rr1` | R:R ratio |
| `s.symbol` | `s.display \|\| s.pair` | `symbol` is null for MT5 pairs |
| `s.zone_desc` | `s.zone_type` + `s.zone_conditions` | VP-based |
| `s.trigger_desc` | `s.trigger_type` + `s.momentum_method` | orderflow-based |
| `s.risk_level` | `s.ai_grade` (A/B/C/D) | grade drives card border colour |
| — | `s.vp_poc`, `s.vp_vah`, `s.vp_val` | VP levels displayed on card |
| — | `s.absorption_count`, `s.cvd_direction`, `s.aaa_complete` | confluence pills |
| — | `s.size_multiplier` (1.0/0.5/0.25) | position sizing by grade |
| — | `s.htf_bias`, `s.htf_bias_tf` | H1 EMA stack direction |

**Backtest result column fix:** `eval_threshold` → `bt_min` (column name in `backtest_results` table — was mismatched causing `table backtest_results has no column named eval_threshold` error).

**UI changes (`static/index.html`):**
- Scalp Lab panel subtitle: "M15 Structure + M5 Tactical" → "VP + OrderFlow (Fabio Valentini)"
- `buildScalpCard()` fully rewritten for new signal shape
- `renderScalpSignals()` keyed by `display||pair` not `symbol`
- Backtest panel: **⚡ Engine D (Scalp VP)** button (purple gradient) added
- `selectBacktestEngine('D')` → routes to `/api/backtest-scalp`
- `renderScalpBtSingle()` added — shows VP analysis block (trigger/setup/grade breakdowns), equity curve, trade table with scalp-specific columns

---

## 2026-04-14: Engine D M1 execution + session filtering + scratch exits

**M1 execution support:**
- Added `M1_CANDLES` config and `EXECUTION_TIMEFRAME` (default "M1", fallback "M5")
- `mt5_fetch_scalp_candles()` now supports M1 timeframe
- Execution/aggression pillars run on M1 candles for precise entry timing
- Signal includes `execution_tf`, `context_tf` (M5), `structure_tf` (M15)

**Session filtering overhaul:**
- Replaced `is_valid_session()` with `scalp_session_window()` function
- Config-driven `SESSION_MODE` (live) and `BT_SESSION_MODE` (backtest)
- NY open cooldown: `NY_OPEN_SKIP_MINUTES` / `BT_NY_OPEN_SKIP_MINUTES` (default 30)
- Returns `(allowed, reason)` where reason can be "NY_OPEN_COOLDOWN"

**Backtest enhancements:**
- Session filter applied in walk-forward loop via `scalp_session_window(backtest=True)`
- Scratch exit logic: `BT_SCRATCH_ENABLED`, `BT_SCRATCH_BARS`, `BT_SCRATCH_MIN_R`
- Tracks `best_favorable_r` during walk-forward; exits if no early follow-through
- `active_exit_indices` tracking replaces simple `open_positions` counter
- Trade records include `session` field from session window

**Helper interface fixes (not yet applied):**
- VP external helper: `num_bins` vs `bins` kwarg mismatch
- LVN config: `VP_LVN_THRESHOLD` vs `VP_LVN_FACTOR` key mismatch
- Absorption: filter to `absorbed=True` rows only
- CVD: `cvd_raw` vs `cvd` key mismatch
- VWAP: handle list return, take last non-None value

**Size multiplier support:**
- `ai_quality_grade()` reads explicit `GRADE_A/B/C/D_SIZE_MULT` config keys
- Backtest and live execution pass `sizing_override` to `risk_check()`

**Config additions:**
```yaml
SCALP_ENGINE:
  M1_CANDLES: 300
  SESSION_MODE: "new_york"
  NY_OPEN_SKIP_MINUTES: 30
  EXECUTION_TIMEFRAME: "M1"
  MIN_GRADE_AUTO_EXECUTE: "C"  # overrides MIN_GRADE for execution
  BT_SESSION_MODE: "new_york"
  BT_NY_OPEN_SKIP_MINUTES: 30
  BT_SCRATCH_ENABLED: true
  BT_SCRATCH_BARS: 3
  BT_SCRATCH_MIN_R: 0.10
```

---

## Hard Rules

1. Never bypass `risk_check()` for any execution
2. Never hardcode thresholds in Python — use `config.yaml`
3. Never import from `athena.py` in unit tests — use `indicators` or `scoring` directly
4. Never commit `*.db` files — gitignored (runtime/market data)
5. Never add `e200s` back to `calc_confluence()` — intentionally removed
6. Never use string matching on warning text for signal classification — use structured flags
7. `PAIR_PROFILE_VOTES` / `PAIR_PROFILE_FILTERS` live in `config.py` only
8. Cache TTL dict keys are **uppercase** `"H1"/"H4"/"D1"`
9. `ALL_PAIRS = FOREX_PAIRS + COMMODITY_PAIRS + INDEX_PAIRS + US_STOCK_PAIRS + ETF_PAIRS + JSE_PAIRS + CRYPTO_PAIRS` — JSE_PAIRS must stay in this concatenation
10. Never hardcode pair lists in `static/index.html` — backtest selector fetches `/api/pairs` dynamically
11. `CandleBuilder.seed()` and `bulk_update_d1()` skip `enabled:False` pairs — do not remove these checks
12. `_resolve_scan_style(requested_style, pair)` — use for per-pair style resolution; do not replace
13. Non-blocking I/O during scans: `carry_feed`, `cot_feed`, `duka_volume` must never block the scan thread
14. SQLite: `PRAGMA journal_mode=WAL`, `timeout=15.0`, explicit commits
15. `"ws": False` opts out of WS only — EODHD plan cap is 50 tickers; do not add `ws:True` without removing others
16. BybitWS: `ping_interval=None` in `websockets.connect()` required — disables library keepalive, prevents 1011 errors
17. Engine B AI is **review-only** — do not reintroduce AI as a pass/fail gate without explicit user request
18. Scoring/confluence/candle complaints require tracing data → engine → API fields → dashboard display — never conclude "engine is wrong" from UI alone
19. Feed routing is **locked** - MT5 sources use `fetch_mt5()` for OHLC/live price; EODHD may overlay volume only and live Engine D must use cache-only EODHD volume lookups; no CandleBuilder for MT5 pairs; no stale bar close into `_live_prices`.
20. Scoring gates are **locked** — do not modify thresholds, weights, or gate logic unless user explicitly requests it
21. `_build_signal_message` reads `"engine_b"` first then `"naked_data"` for ENGINE B section (Engine A signals use `"engine_b"`, Engine B scan signals use `"naked_data"`)
22. Vision structured footer: preserve machine-readable lines — `RIGHT EDGE: CONFIRMS|REVIEW|POTENTIAL REVERSAL` (line immediately before `TF ALIGNMENT`) plus `TF ALIGNMENT` + 3× `RATING` + 3× `LEVELS` in single/dual/triple modes — required by `_extract_vision_structured()` parser; do not reword tokens
23. `confidenceDetail` and `factorDiagnostics` keys are camelCase on the signal dict — do not use snake_case when reading from signal
24. Lottery Lab — never bypass `_normalize_game()` before any DB or analytics call
25. Lottery Lab — `simulate_generator()` incremental counters must never revert to full-history rescan per draw
26. **Chart Vision vs Lottery AI:** both are xAI-backed via OpenAI-compatible SDK (`api.x.ai`) but use different prompts/routes. `/api/chart-analysis` uses `VISION_MODEL`; `/api/lottery/ai-analysis` uses `LOTTERY_AI_MODEL` or `XAI_MODEL`. Do not mix prompts, parser contracts, or route payload schemas between them.
27. **Scalp Lab (Engine D)** is a separate pipeline (`scalp_engine.py`, `volume_profile.py`, `/api/scalp-scan`, `/api/scalp-execute`, `/api/backtest-scalp`) — not produced by `analyze_pair()` and not blended in Engine C. Signal keys use `price` (not `entry`), `rr1` (not `rr`), `display||pair` (not `symbol` — `symbol` is null for MT5 pairs). UI card and `_scalpSignalsById` must both key by `display||pair||symbol`.
28. **Vision ENTRY QUALITY section:** Must appear between **KEY RISKS** and **FINAL VERDICT** in all three prompt modes (single/dual/triple). System prompt rules **9–12** (entry positioning, volatility-regime interaction, move maturity, RR reality check) are mandatory — do not remove or weaken.
